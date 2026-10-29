# Campaign 8: accounting questions resolved, and the dwell verdict

## 1. Why peak cash reads $22.2436 against a $12 envelope

**No limit was breached. The two figures measure different quantities against
different budgets, and neither is cumulative turnover.**

Three distinct quantities exist, and Campaign 8 produced three different
numbers for them:

| quantity | C8 value | its budget | gated? |
|---|---|---|---|
| cumulative gross turnover | **$22.4200** | none | no, and correctly so |
| max simultaneous committed cash | **≤ $9.0833** | **$12.00** | yes, `CASH_COMMITMENT` |
| worst-case high-water mark | **$22.2436** | **$29.30** | no — reporting only |

### `peak_usd` is a worst-case loss high-water mark, not cash

`worst_case_usd` (`src/l5_campaign_budget.py:112`) is

```
max(0, -realized_usd)                      # account-wide standing loss
  + Σ cost_usd   over UNSETTLED legs       # whole committed cost can be lost
  + Σ max(0, -realized_usd) over SETTLED legs
  + Σ leaves_qty * price over resting orders
```

Two properties explain the $22.24:

- **It carries the account's standing realized loss as a floor.**
  `all_time_realized_usd` was **−$10.5112** when Campaign 8 armed, so the
  worst case started at ≈$10.51 before a single Campaign 8 order existed.
  Campaign 8's own contribution to the high-water mark was therefore ≈$11.7,
  not $22.2. The telemetry says this itself:
  *"A fresh campaign nonce resets peak_usd but never this."*
- **It is explicitly not a gate.** Source comment at
  `l5_campaign_budget.py:507`: *"The high-water mark is recorded for the
  heartbeat only. It is deliberately NOT a gate."* The gate is
  `projected = used + new_order_cost > budget_usd` →
  `CAMPAIGN_WORST_CASE_BUDGET`. Peak ended at $22.2436 of $29.30, leaving
  `remaining_usd 7.0564`. Never triggered.

The near-coincidence between the $22.4200 turnover and the $22.2436 peak is
exactly that — a coincidence. The formulas differ (the peak includes the
standing loss and drops a settled winner's cost; turnover counts every leg),
and conflating them is the error the budget's own docstring warns about:
*"otherwise eight sequential $2 windows would consume $16 of a $20 loss
budget while losing $0, and the budget would be a turnover cap wearing a
loss budget's name."*

### The $12 envelope measures instantaneous commitment

`max_cash_deployed_usd` (`l5_live_validation_gates.py:670`) tests

```
cash_committed(filled unsettled legs) + pending_obligation(resting) + this order
```

Under `cash_policy = NO_RECYCLING`, a closing leg's proceeds do **not** reduce
the figure until the market settles, so commitment accumulates within a
window and releases at settlement. The binding number is therefore the
largest per-window total:

| window | gross cash out |
|---|---|
| **0200z** | **7.3353** |
| 0230z | 5.8765 |
| 0315z | 4.0060 |
| 0215z | 2.8518 |
| 0300z | 1.6016 |
| 0245z | 0.7488 |

Worst case: $7.3353 (0200z) + the largest single order notional in the
campaign ($1.7480) = **$9.0833**, against a $12.00 ceiling. With
`MAX_LIVE_ORDERS = 1` no tighter bound is needed. **$12 was never
approached**, let alone breached.

### "Breach" is structurally impossible

All three are **pre-trade refusal gates**: they return a reason string and the
order is not submitted. The failure mode is a refused order, never an
exceeded limit. So the correct statement is not merely "no breach was
observed" — a breach cannot occur by construction, and the observed values
confirm the gates were never even close to refusing.

## 2. Reconciling 24 / 25 / 32 / 142

Every number is now tied to the durable claim semantics in
`src/l5_live_authorization.py:713`.

| count | value | what it is |
|---|---|---|
| live orders submitted | **32** | one `L5_FILL_CALIB_PRESUBMIT`, one `l5f-` coid, one terminal each |
| terminals: FILLED | **23** | `ORDER_STATE_FILLED` |
| terminals: CANCELED | **9** | `ORDER_STATE_CANCELED` (one of these partially filled) |
| orders receiving ≥1 fill | **24** | the 23 FILLED, plus `l5f-d6bb38e5…` which filled 1.45 of 2.47 and was then cancelled |
| fill **events** | **25** | `l5f-c8973455…` filled twice (1.47 then 0.45, cumulative 1.92) |
| shadow presubmits | **35** | `shadow\|ARGMAX\|…` coids, `modelled_only`, never transmitted, never claimed |
| `submissions_spent` during campaign | **22 → 32** | one durable `l5_auth_claim_*.lock` per submitted order |
| `submissions_spent` post-expiry | **142** | **not this campaign** — see below |

### The counter is verified exactly

`submissions_spent` read **22** at 02:50:49Z. Live orders submitted by then,
per window: 0200z 9 + 0215z 5 + 0230z 7 + 0245z 1 = **22**. Exact match. The
final campaign figure is **32 of the 72 budget (44%)**, so the 40→72 increase
never bound and no submission-budget refusal occurred.

### Why 142 is not a campaign figure

`submissions_spent(path, experiment)` globs
`l5_auth_claim_sub1.<experiment>*` and `l5_auth_claim_acc1.<experiment>*`.
At expiry `campaign_id` goes `null`, so `experiment` is empty, the prefix
degenerates to `l5_auth_claim_sub1.` and the glob matches **every submission
claim on the volume, all campaigns, all time**. 142 is that all-time total.

This is the same class of defect as the Campaign 7 `MAX_LIVE_ORDERS`
miscount: a counter rendering a different population than its label implies.
**Rule: never read `submissions_spent` after `campaign_id` goes null.**

## 3. Verdict on longer completion dwell: NOT SUPPORTED

### The C7 dwell finding does not replicate

| campaign | median FILL | median CANCEL | reading |
|---|---|---|---|
| C6 | 10.7s | 7.9s | cancel earlier than fills arrive |
| C7 | 11.0s | 7.8s | cancel earlier than fills arrive |
| **C8** | **7.4s** | **22.1s** | **cancel LATER than fills arrive** |

Campaign 8 reverses the sign. Pooled over 87 live orders, **15 of 34 cancels
had already outlasted the median fill (10.8s) and 3 outlasted the longest
fill (106.9s)**. There is no stable "we pull too early" signal.

### Survival analysis cannot settle it, and that is the finding

A Kaplan-Meier fit over the pooled tape (cancels censored) returns
`P(fill | survived 8s) ≈ 0.86`, which looks like a strong case for holding.
**It is not usable.** KM requires non-informative censoring, and ours is
maximally informative: we cancel *precisely when* the book has moved and the
quote has become unlikely to fill or undesirable to hold. Any fill
probability estimated by holding this tape's cancels open is biased upward by
construction. This is exactly the trap of inferring value from "some later
touched our prices," and it means no amount of re-analysis of existing tapes
can answer the dwell question. Only randomization can.

### The settled counterfactual: completion vs stranding is a dead heat

Seven legs reached window end unmatched across C6–C8 (C6 stranded nothing).
Priced on what they **actually settled for**:

| camp | win | side | qty | own | cost | proceeds | settled |
|---|---|---|---|---|---|---|---|
| C7 | 2000z | DOWN | 1.85 | 0.33 | 0.6105 | 1.85 | **+1.2395** |
| C7 | 2015z | UP | 2.18 | 0.37 | 0.8066 | 0 | −0.8066 |
| C7 | 2100z | UP | 2.06 | 0.45 | 0.9270 | 2.06 | **+1.1330** |
| C7 | 2115z | UP | 1.85 | 0.62 | 1.1470 | 0 | −1.1470 |
| C8 | 0215z | DOWN | 1.94 | 0.41 | 0.7954 | 1.94 | **+1.1446** |
| C8 | 0230z | UP | 1.02 | 0.21 | 0.2142 | 0 | −0.2142 |
| C8 | 0245z | DOWN | 1.92 | 0.39 | 0.7488 | 0 | −0.7488 |

3 of 7 won. Actual residual net **+$0.6005** on $5.2495 of cost basis,
mean +$0.0858/leg, **sd $1.0529/leg**.

Completing all seven instead, at pair cost `c`:

| c | counterfactual net | delta vs actual | sd |
|---|---|---|---|
| 0.90 | +1.2820 | **+0.6815** | 0.0377 |
| 0.92 | +1.0256 | +0.4251 | 0.0302 |
| 0.94 | +0.7692 | +0.1687 | 0.0226 |
| **0.9532** | **+0.6000** | **−0.0005** | 0.0176 |
| 0.96 | +0.5128 | −0.0877 | 0.0151 |
| 1.00 | 0.0000 | −0.6005 | 0.0000 |

**Break-even completion pair cost = 0.9532.** Our achieved
quantity-weighted pair cost in Campaign 8 was **0.9530**. Completion and
stranding are economically indistinguishable at the prices we actually get.

This is the load-bearing result, and it explains *why* a resting completion
is not free money: it fills when the market moves toward it, which is exactly
when the leg we already hold is becoming a winner. Completion sells the
favourable tail for a locked pair edge. At 0.953 the trade is exactly fair.

### What completion *does* buy: measurability

| component | 17 windows | mean/window | sd | t |
|---|---|---|---|---|
| matched-pair economics | +3.2856 | **+0.1933** | 0.2400 | **3.320** |
| residual settlement | +0.6005 | +0.0353 | 0.6462 | **0.225** |
| combined | +3.8861 | +0.2286 | 0.6733 | 1.400 |

Windows needed to reach t=2: **35 on the combined series, 6 on the matched
series.** Eliminating residual variance is worth **6× in sample size**, at an
expected cost of ≈$0.09 per seven legs. For a programme whose binding
constraint is verification, that trade is clearly worth taking — which is an
argument for **keeping completion rescue exactly as it is**, now with a
reason the earlier analysis did not have.

### Queue-priority resets: direction, not dwell

13 requote chains across C6–C8 ended in a fill. A requote surrenders queue
position; what decides whether that pays is the **direction**:

| | n | mean final own | mean move |
|---|---|---|---|
| requoted CHEAPER | 6 | 0.4000 | **−0.1217** |
| requoted DEARER (chasing) | 7 | 0.4829 | **+0.0771** |

The engine chases roughly half the time, paying a mean of **+7.7 cents** more
than the quote it just cancelled. Two concrete cases:

- **0230z, 02:32:25**: DOWN own 0.53 cancelled after **3.8s**, requoted at
  0.60. Against the resting UP leg at own 0.41 the planned pair was 0.94;
  the achieved pair was **1.01**. Swing **−$0.1484** on 2.12 contracts, and
  it is the direct cause of above-$1.00 pair G.
- **0315z, 03:17:59**: UP own 0.58 cancelled after 28.3s, requoted at
  **0.38**. Pair **0.80** — the campaign's best, **+$0.3920**. Holding the
  0.58 quote would have produced a 1.00 pair and zero edge.

So the single largest realized loss *and* the single largest realized gain
among requotes both came from repricing, in opposite directions. **Holding
longer would have made the best pair worse.** Dwell is the wrong variable;
the resulting marginal pair cost is the right one.

### The defect that actually cost money is not dwell at all

**2 of Campaign 8's 3 strandings had zero completion quotes submitted.**

| window | orders submitted all window | completion-eligible time after the open leg filled | completion attempts |
|---|---|---|---|
| 0215z | 5 | 02:22:22 → 02:25:00 (2m38s) | **0** |
| 0230z | 7 | — | 1 (rested 125.1s, partial fill) |
| **0245z** | **1** | **02:47:17 → 02:55:00 (7m43s)** | **0** |

0245z is the campaign's worst window (**−$0.7488**) and the engine submitted
**one order in the entire window**. An affordable completion existed: we held
DOWN at own 0.39, the wire bid was 0.58, so a UP quote two ticks behind at
own 0.56 would have made a 0.95 pair. It was never quoted.

This is intent starvation, the same signature as Campaign 7's 2045z missing
window — not dwell, not queue position, not the ceiling. **No dwell policy
fixes a window in which no completion intent was generated.**

## 4. Smallest bounded test that can resolve the dwell question

Since informative censoring makes the existing tapes unable to answer it, the
only valid test randomizes. But the arithmetic says do not run it yet:
matched-series sd is 0.24/window, so detecting a +$0.10/window dwell effect
at t=2 needs ≈**46 windows per arm**, ≈8 campaigns per arm. **A dwell A/B is
not fundable at our current size, and that is the answer to "is it worth
testing" — not yet.**

The fundable test is the starvation defect, because it is a *defect* rather
than a parameter, and diagnosing it is **read-only**:

> **Campaign 9, no policy change.** Capture the decision tape continuously.
> For every window that ends with unmatched inventory, classify every
> evaluation between the open-leg fill and the terminal cutoff into: no
> intent generated / intent generated but gate-refused (naming the gate) /
> intent generated and submitted. Report the counts.

Expected yield: 6 windows, ~3 strandings on the C8 rate. If strandings again
show zero submitted completions, the fix is in intent generation and is
worth ≈$0.08/leg in expectation plus the 6× variance collapse. If they show
gate refusals instead, the gate is named and we price *that*.

No new services, no flag changes, no loosened limits.

## 5. Campaign 9 decision-tape capture

Observed `L5_LIVE_DECISION` retention on this deployment is **≈1.6 hours** —
shorter than a 4-hour authorization. Campaign 8's economics survived only
because cash and fills were pulled live.

`analysis/l5/capture_decision_tape.py` is the capture. It is append-only and
**idempotent** (deduped on `(utc, label, coid|slug|window)`), verified by
running it twice over the same dump: `67 appended`, then `0 appended`.
Overlapping polls are therefore safe, so the poll interval only has to beat
retention rather than align to it. It also writes `UNPARSEABLE` to stderr
rather than silently dropping a log line truncated by the harness — losing an
audit line is what it exists to prevent.

Operating rule for Campaign 9: poll `L5_LIVE_DECISION`, `L5_FILL_CALIB_*` and
`L5_LIVE_CASH` into `analysis/l5/campaign9/` on a **≤30-minute** cadence
while armed (≈3× retention margin), committing each time, using this
session's existing scheduled-wake mechanism. No new infrastructure.
