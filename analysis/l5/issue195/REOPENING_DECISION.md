# The reopening decision, and the 1230z window that needed it

Reconstructed from the venue tape of **2026-11-07 12:30z**, replayed against
18 recorded windows. No deployment, no arming, no capital change.

## 1. What actually happened, and what did not

| time | venue event | coid |
|---|---|---|
| 12:30:27 | **DOWN 0.5000 fills** | `l5f-b195e736…` |
| 12:30:49 | **UP 0.4500 fills** — round 0 pairs at **0.9500**, +$0.0500 | `l5f-83557f64…` |
| 12:32:54 | round 1 opens UP 0.4600 / DOWN 0.4900 | — |
| 12:33:57 | **UP 0.4600 fills** | `l5f-fc3f9809…` |
| 12:34:58 | DOWN 0.4900 cancelled, zero fill (UP 0.3500 likewise) | `l5f-19329b90…` |
| 12:45:57 | settlement, `yes_value 0.0000` — DOWN won | — |
| | UP 2 @ basis 0.9100, DOWN 1 @ 0.5000; payout 1.0000 − spend 1.4100 | **−$0.4100** |

**Two decompositions, both correct, and the difference is a convention.** At
the order level the pair is 0.9500 (+$0.0500) and the stranded leg is the UP
at 0.4600 (−$0.4600). The settlement record uses **average basis** — UP
averages 0.4550 — so it reads matched 0.9550 (+$0.0450), residual −$0.4550.
Both sum to **−$0.4100**, which is the only figure that is money. An earlier
draft of this document quoted "UP 0.4750 / DOWN 0.4800" for round 0; those
prices appear in no venue record and were back-derived from the 0.9550
average. The table above is the venue's own, cross-checked against
`WINDOW_1230Z_RESULT.md` and PR #207's independent fixture.

**Nobody broke a rule and nothing malfunctioned.** The owner's reading of
the mechanism was exactly right:

- `L5_TS_ROUND_ALLOCATIONS=1` splits the **cash envelope** across cycles. It
  never limited the number of opening rounds.
- `acc_max_rounds()` is the round bound, and `probe_round`
  (`src/l5_live_execution.py`) returned the first **incomplete** round:

```python
for r in range(self.acc_max_rounds()):
    if self.probe_round_retired(market, r):
        continue
    claimed = self.probe_legs_claimed(market, r)
    if not all(claimed.values()):
        return r
return None
```

No economics. Not a single term. **The round count was never the defect; the
missing decision was.** Nothing anywhere compared what round 1 could earn
against what it could lose.

The low trade count is therefore **not** an execution defect. 5 submitted,
3 venue-confirmed fills, 1 completed pair — consistent with the
implementation as written.

## 2. The decision, and why its expectancy term is not the leg cost

`reopen_decision` in `src/l5_unmatched_disposition.py`, beside `decide`, so
one module owns inventory economics. Two tests, in order, because a hard
bound is not an opinion.

**1. Capacity.** Both legs fill and one settles worthless: `qty × dearer
leg`. That must fit the exposure headroom **and** the daily realized-loss
room, reserving for held inventory and for live orders on either side filling
alone — `max`, not the sum, because if both fill they match each other, and
never the signed net (the 1415z reason, below).

**2. Expectancy**, on measured quantities only:

```
E = p_pair × pair_edge  +  p_single × single_outcome
```

The first draft of this used `−p_single × leg_cost`, and **that was wrong.**
A leg's cost is its *worst case*, not its expectancy: an unmatched leg at own
`p` settles at $1 with the market's own implied probability `p`, so at a fair
price its expectancy is near zero. It is the same identity already pinned in
`decide` — `EV(hold)` and `EV(complete)` are equal exactly; only the worst
case differs.

What actually makes a single leg lose is that the market keeps moving after
it fills a maker quote, and that drift is **measured**:
`QUEUE_DEPLETION_FINDINGS.md` §4 gives `hold $` = **−$0.2440** per leg at
`d=2` out of sample, nearly independent of depth. The gate uses the OOS
figure because it is conservative and rests on 13 windows rather than 4.

Three things it deliberately does not do:

- **It does not assume the cheaper taker disposition.** `taker $` is −$0.0542
  and taker completion is disabled by owner order, so pricing it in would
  assume a disposition we are forbidden to execute.
- **It does not treat "wait for ≤0.96" as an escape.** That completion was
  offered in 1% to 20% of single-leg cases, so `wait $` (−$0.2313) tracks
  `hold $`.
- **It does not finance a residual out of a locked pair.** "+$0.045, so a
  −$0.455 risk is affordable" is wrong twice: the locked profit is a tenth of
  the risk, and netting them spends a realized gain as a loss reserve. Locked
  P&L is **reported** and excluded from both tests, and
  `test_locked_pair_profit_never_changes_the_decision` pins that at $500.

### What it says about 1230z

```
pair_edge = 0.01 + 0.04                   = 0.0500
E         = 0.525 × 0.05 + 0.423 × −0.2440 = −0.0770   → REFUSED
```

Inverted, the number worth quoting: **reopening pays only once an unmatched
leg costs less than $0.0384, against $0.2440 measured — a factor of 6.4.**
Every record carries `breakeven_single_outcome_usd`, so a refusal always
states what would have to be true instead.

**This is a price, not a cap.** It opens the moment pairing improves, the
spread widens, depth increases, or adverse selection is measured smaller, and
`test_reopening_is_permitted_when_the_economics_support_it` is the test a
one-pair-per-window counter cannot pass. But stated plainly: **at the
deployed `d=2` with the usual one-tick spread, no maker-only reopening
currently clears the floor, and no depth in either sample does** (best is
+$0.0020 at `d=6` in sample). That is the honest reading of the evidence.

## 3. Three cases where refusing would have been the defect

Writing this surfaced three states where a naive round-index gate causes
exactly the harm it is meant to prevent. Each is now an exemption with a
test.

**A retired round took no exposure.** Retirement abandons a plan the book
walked away from, and `probe_round_is_safe_to_retire` refuses while anything
is live or filled. The 0330z window re-proposed a plan frozen against a
0.42/0.56 touch 32 times after the book had reached 0.91/0.92; retiring and
re-planning is the fix for that and is not accumulation. So the gate counts
**rounds that actually spent legs**, not the round index.

**A held residual means the next leg reduces.** `probe_leg_order` places the
reducing leg first and `probe_leg_qty_cap` caps it at the residual's
magnitude, so the worst case *falls*. A gate that exists to prevent unmatched
legs must never itself cause one.

**But never on the signed net.** On 1415z UP 2.17 was held while a DOWN 2.17
completion was resting; the signed net was exactly 0.00, a gate read the book
as FLAT, and it exempted an UP 2.08 as a genuine opening leg. The completion
was cancelled unfilled and the position reached UP 4.25 against a
3-contract ceiling. The exemption therefore asks whether residual remains
**not already being worked**, per side.

## 4. A / B / C on identical windows

`analysis/l5/issue195/round_strategy_compare.py`. Sequential per-window
replay, PROVEN fills only (the opposite touch reached our price, so pairing
is a **lower** bound), no look-ahead, rounds never overlapping in time, a
60-frame TTL, maker-only, no rebate assumed.

- **A** stop after the first pair
- **B** repeated rounds, as deployed — this is what ran on 1230z
- **C** inventory-aware and risk-budgeted — every round past the first
  exposure-taking one must clear `reopen_decision`

**OUT OF SAMPLE — 2026-10-25 ×12 windows, 2026-11-06 1130z**

| metric | A | B (deployed) | C |
|---|---|---|---|
| windows | 13 | 13 | 13 |
| rounds opened | 13 | 34 | **15** |
| rounds per window | 1.000 | 2.615 | 1.154 |
| pairs completed | 11 | 18 | 11 |
| unmatched single legs | 2 | **14** | 2 |
| rounds that never filled | 0 | 2 | 0 |
| capped reductions | 0 | 0 | 2 |
| pair P&L | +0.5600 | +0.9200 | +0.5600 |
| single-leg P&L | −0.5500 | **−3.1800** | −0.5500 |
| **net P&L** | **+0.0100** | **−2.2600** | **+0.0100** |
| net per window | +0.0008 | −0.1738 | +0.0008 |
| net per round | +0.0008 | −0.0665 | +0.0007 |
| peak worst-case exposure | $0.86 | **$2.34** | $0.86 |
| windows net positive | 11 | 6 | 11 |
| windows carrying a single | 2 | 8 | 2 |

**IN SAMPLE — 2026-10-28 ×3 windows, 2026-11-06 0300z**

| metric | A | B (deployed) | C |
|---|---|---|---|
| rounds opened | 5 | 15 | 6 |
| pairs completed | 4 | 10 | **5** |
| unmatched single legs | 1 | 5 | **0** |
| **net P&L** | −0.1350 | **−1.3950** | **+0.1800** |
| net per window | −0.0270 | −0.2790 | +0.0360 |
| peak worst-case exposure | $0.68 | $1.75 | $0.68 |
| windows carrying a single | 1 | 4 | **0** |

### What this does and does not establish

**C dominates the deployed B on every metric that matters under this fill
model** (see §8: the ranking reverses at the other end of the bracket, and
the robustness argument is what survives), in both samples: a third of the exposure peak, a seventh of the unmatched legs,
and +$2.27 better out of sample. B is the worst of the three and it is what
is deployed.

**C matches A out of sample and beats it in sample** (+0.1800 against
−0.1350), because a capped reduction completed a single that A carried to
settlement. C also keeps the ability A lacks by construction: it opened 15
rounds against A's 13 and 6 against A's 5, where the economics justified it.

**No variant demonstrates a positive edge.** C is **+$0.0008 per window** out
of sample. At 96 windows a day that is **$0.077/day against the $480/day
target**. Thirteen correlated windows, a lower-bound fill model and no fee
beyond maker-free cannot carry a profitability claim, and none is made here.

## 5. Confirmed settled realized net P&L

Unchanged and reconciled: **−$15.6109 all-time**; 1230z **−$0.4100**; day
2026-11-07 **−$0.9000**. Nothing in this work is realized money.

## 6. The next evidence needed

The gate's own break-even states it precisely: **an unmatched leg must cost
under $0.0384 and it measures −$0.2440.** Closing a 6.4× gap is not a
parameter change. Three candidates, in order of expected information:

1. **A real resting-order fill model.** Every figure above rests on PROVEN
   fills — the opposite touch crossing our price — which over-samples the
   adverse case. Real queue fills are less adverse, and the size of that bias
   is unmeasured. It is measurable only from our own resting orders.
2. **Maker rebates, source-confirmed.** None has been credited. A zero
   commission is not a rebate.
3. **Taker completion**, which the same table shows caps the single's cost at
   −$0.0542 and is the only measured action that moves the sign. Disabled by
   owner order; priced counterfactually only.

## 7. Depth sweep: is any configuration better than the deployed one?

The A/B/C table above ran at the deployed `depth_ticks=2`. Sweeping depth for
the deployed policy (B) and the risk-budgeted one (C), net per window:

| d | B in-sample | **C in-sample** | B out-of-sample | **C out-of-sample** |
|---|---|---|---|---|
| 0 | — | −0.0040 | −0.2112 | −0.0127 |
| 1 | −0.2230 | +0.0160 | −0.2450 | −0.0177 |
| **2** ⬅ deployed | −0.2790 | +0.0360 | −0.1738 | **+0.0008** |
| **3** | −0.3250 | **+0.0560** | −0.1935 | **+0.0054** |
| 4 | −0.4270 | −0.0660 | −0.1050 | +0.0019 |
| 5 | −0.2140 | −0.0500 | −0.0735 | −0.0154 |
| 6 | −0.3310 | −0.0640 | −0.0327 | +0.0015 |

**Two things this settles.**

**(a) The deployed repeated-round policy is negative at every depth.** B
ranges −0.0327 to −0.2450 per window out of sample. Depth is not what is
wrong with it; the unpriced reopening is.

**(b) `d=3` is the best depth for C in both samples** — +0.0560 in sample and
+0.0054 out of sample, against the deployed `d=2`'s +0.0360 and +0.0008. It
also opens *more* rounds (17 against 15 out of sample) for *more* pairs and
the same 2 unmatched legs, which is the shape the mandate asked for: more
completed pairs without sacrificing pair economics.

**But the effect is not cleanly separated from noise, and saying otherwise
would be overclaiming.** The out-of-sample curve is non-monotonic — `d=4`
+0.0019, `d=5` −0.0154, `d=6` +0.0015 — so neighbouring depths differ by less
than the scatter between them. Thirteen correlated windows cannot resolve a
half-cent-per-window difference.

**No variable was changed.** `L5_TS_DEPTH_TICKS` is a deployed Railway
variable and the freeze prohibits touching it. `d=3` is recorded here as a
**candidate for owner decision**, to be confirmed by live measurement rather
than adopted on this table. Reproduce with
`analysis/l5/issue195/round_strategy_compare.py` (set `DEPTH`).

At +0.0054 per window, `d=3` out of sample is **$0.52/day** against the
$480/day target. It is a better configuration, not a route to the target.

## 8. Fill-model sensitivity, and a correction to §4

The review asked whether the PROVEN fill model biases adverse selection, and
to report sensitivity. It does, and the answer **changes one of my
conclusions.**

`l5_queue_depletion` brackets fills rather than estimating them. **PROVEN** —
the opposite touch reached our price — is *sufficient*, so a **lower** bound
on fills, and it over-samples the adverse case because the market moved
*through* us. **POSSIBLE** — enough size left the queue ahead of us — is
*necessary only*, since a decrease may be a cancellation, so an **upper**
bound. The truth lies between, and neither is a point estimate.

Net per window, both ends of the bracket:

| fill model | d | **B** (deployed) | **C** (risk-budgeted) |
|---|---|---|---|
| PROVEN (lower) | 2 | −0.1738 | **+0.0008** |
| PROVEN (lower) | 3 | −0.1935 | **+0.0054** |
| **POSSIBLE (upper)** | 2 | **+0.1265** | +0.0508 |
| **POSSIBLE (upper)** | 3 | **+0.1681** | +0.0708 |

*(out of sample, 13 windows; in sample the same flip occurs — B goes from
−0.2790 to +0.1500 at d=2)*

**THE CORRECTION.** §4 said C "strictly dominates B in both samples". That
is true **only under the PROVEN model.** Under POSSIBLE the ranking
**reverses**: B's extra rounds convert to pairs (33 against C's 13 out of
sample) and it earns more than twice C's net. I should not have stated
dominance without testing the other end of a bracket I had already
described as a bracket.

**What survives the correction, and it is the part that matters.** The two
policies differ in *robustness*, not just in level:

| | B (deployed) | C (risk-budgeted) |
|---|---|---|
| net/window across the bracket | **−0.1935 … +0.1681** | **+0.0008 … +0.0708** |
| sign depends on the fill model | **yes** | no |
| unmatched legs (OOS) | 1 … **14** | 0 … 2 |
| peak worst-case exposure | **$2.34** | $0.86 |

C is positive at **both** ends and never carries more than 2 unmatched legs.
B is a **bet on the fill model being the generous one**: if real fills look
like POSSIBLE it is the better policy, and if they look like PROVEN it loses
−$0.19 a window and strands 14 legs. Choosing C is choosing the policy whose
sign does not depend on an assumption we cannot currently measure — which is
a different and weaker claim than dominance, and the right one.

**No profitability is claimed at either end.** C's range is **+$0.0008 to
+$0.0708 per window** — **$0.08 to $6.80/day against the $480/day target**,
on 13 correlated windows. Even the generous end is two orders of magnitude
short.

**And this is now the top blocker, stated precisely.** The gap between the
two ends of the bracket is larger than the difference between the policies,
so no further offline work on these recordings can rank B against C. Only
measuring our **own** resting orders — join time, queue ahead, and whether
they filled without the touch crossing — closes it. That is the single
experiment that would convert this from a bracket into a number, and it
needs owner authorization for a bounded maker-only window.

Reproduce with `analysis/l5/issue195/round_strategy_compare.py`
(`FILL_MODEL`, `DEPTH`).
