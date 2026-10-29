# Narrow change set, exposure reconciliation, and the crossing-rule verdict

**Nothing deployed, armed, or changed in production.** Verified clean at
2026-10-26T11:56:51Z: `armed:false`, `submission_gate blocked:true
reason:NOT_ARMED`, `halt_latch:CLEAR`, `orders_submitted:0`, `fill_events:0`,
`realized_net_usd:0`.

---

## 1. The $5 vs $5.1738 discrepancy — reconciled, and it was WORSE than stated

**The cash gate counts PRINCIPAL ONLY.** Verified in the deployed commit:
`committed[market] += own_price * qty`, `pending_obligation` is `qty * price`
(or `qty` for a resting short), and the proposed order is `q * px`. **No term
carries a fee.** So `max_cash_deployed_usd` bounds principal and the taker fee
is charged **on top of it**. Nothing was silently raised — the `$5` label was
incomplete.

**And the $5.1738 was itself optimistic.** It assumed the taker fee peaks at
p = 0.50. That is true *per contract*, but under a fixed principal the
contract count is `cap/p`, so

    fee = Theta * (cap/p) * p * (1-p) = Theta * cap * (1-p)

which is monotone **decreasing** in p and therefore maximised at the
**cheapest** admissible price:

| price | contracts on $5 | max fee | all-in |
|---|---|---|---|
| **0.05** (band floor) | 100 | **$0.3301** | **$5.3301** |
| 0.20 (first-leg floor) | 25 | $0.2780 | $5.2780 |
| 0.50 (as previously reported) | 10 | $0.1738 | $5.1738 |
| 0.95 | 5.26 | $0.0174 | $5.0174 |

**The true all-in worst case on $5 of principal is $5.3301** — the reported
figure understated the fee by 90%.

### Reconciliation

To hold the **all-in** worst case at X, set the principal ceiling to
`X / (1 + Theta*(1 - floor_p))`. For X = $5.00 that is **$4.69**.

> **Recommended: `L5_MAX_CASH_DEPLOYED_USD = 4.69`** → principal $4.69, max fee
> $0.3096, **all-in $4.9996 ≤ $5.00.** This *tightens* the limit; it raises
> nothing.

Two defects fixed in the change set (`48bdbd85`): the preflight read a
**hardcoded** `D("5")` — so a tightened ceiling would never have appeared in
the evidence a campaign is activated on — and it used the wrong fee maximum.
It now reads `L5_MAX_CASH_DEPLOYED_USD`, maximises at the band floor, and
states `fee_is_outside_the_cash_ceiling: true` on the record.

---

## 2. Can the worst-outcome rule bleed through repetition? — verified

Simulated on the deployed predicate, not argued.

**Repetition is bounded and self-limiting.** Each cross must pass **two** bars:
`top_up` requires the worse settlement outcome to improve, then
`improvement > taker_fee` requires that improvement to exceed the fee paid for
it. Because each step's `worst_before` is the previous step's `worst_after`,
the improvements telescope — total improvement = `worst_final − worst_initial`
— and since every step had `improvement_i > fee_i`, **total improvement
strictly exceeds total fees.** Repetition cannot invert that. It also
terminates: each cross reduces the deficit by at least `min_qty`, and a matched
book returns `ALREADY_MATCHED`.

**Fees are inside the sizing**, not bolted on: the size shrinks on the venue
grid until `qty*q + worst_fee(qty,q) <= cash`, so a run cannot spend past its
headroom. Independently, a 10 s throttle (`TS_COMPLETION_EVERY_S`) and the
campaign's `COMPLETE` class budget bound the attempt count.

### But the fee bar does NOT constrain dear completions

The taker fee is `Theta*C*p*(1-p)`, which **collapses** at extreme prices while
the worst-case improvement stays positive — so the dearer the cross, the weaker
the bar guarding it. At campaign scale (1.56 UP at 0.44) under the deployed
**$2.00** worst-case ceiling, **every price from 0.40 to 0.95 is permitted**. A
cross at 0.95 makes the pair 1.39 and books a **certain −$0.6484** — 94% of the
$0.6864 acquisition — and still passes, because holding's worst case is
−$0.6864 and the fee is only $0.01.

**The control that actually binds is the existing `max_worst_case_deficit_usd`,
and at $2.00 it is ~3x the entire acquisition cost at campaign scale, so it
never binds.**

| ceiling | permitted completion prices |
|---|---|
| **$2.00 (deployed)** | 0.40, 0.50, 0.55, 0.60, 0.70, 0.80, 0.95 — all |
| $0.25 | 0.40, 0.50, 0.55, 0.60 |
| **$0.10 (recommended)** | **0.40, 0.50, 0.55** — pairs ≤ 0.99 only |

> **Recommended: `L5_MAX_WORST_CASE_DEFICIT_USD = 0.10`.** It admits the real
> 1945z exit instant (pair 0.99) and refuses every ruinous dear completion
> (pair ≥ 1.04). Existing gate, no code change, tightens only.

---

## 3. The permission I previously named would NOT have produced a fill

**`L5_TS_COMPLETION_CROSS=YES` is not sufficient.** The `maker_only` safety
overlay refuses *every* taker order —
`SAFETY_OVERLAY:MAKER_ONLY_NO_TAKER` — with exactly one exception: an order a
**live defensive mandate names exactly** (market, side, price, qty, inside its
window, flag on; anything unreadable means not covered).

**And the residual exit already exists, properly built.**
`src/l5_defensive_completion.py` is a budgeted, per-order taker unwind with:

- `DEFENSIVE_DWELL_S = 30.0` — measured **from the moment the leg became
  unmatched**, explicitly *not* a slot near the terminal. Its docstring
  diagnoses exactly the timing failure I had diagnosed independently: *"A
  policy whose trigger is narrower than its own sampling jitter fires by
  luck"*, citing 0230z/0300z on 2026-10-23 and a −$0.3580 strand.
- `DEFENSIVE_PAIR_CEILING = 1.20` — its own ceiling, above 0.96, so it is not
  gated by the entry cap.
- `DEFENSIVE_LOSS_BUDGET_USD = 2.00` — charged **before** the mandate and
  before the wire.
- `MAKER_COMPLETE_CEILING = 0.96` — never relaxed; defensive action is
  considered only once profitable completion is unreachable.
- It handles the no-resting-complement case: `resting_complement=None` when
  nothing rests, which is precisely 1945z (all 121 samples `order_resting:
  false`).

It is **present and evaluating in production** — `defensive_evals` reaches 140
in a single window.

**Consequence: no new exit feature is needed, and the campaign's permission
question is `L5_DEFENSIVE_COMPLETION` / `L5_DEFENSIVE_DWELL`, not
`L5_TS_COMPLETION_CROSS`.** My `maybe_ts_completion` phase relaxation is
retained as **evaluate-only observability** and is explicitly *not* the strand
fix. What remains unestablished is why the defensive path did not act on 1945z
(`defensive_evals` is 0 on most lines), and its own refusal reasons are the
first diagnostic the campaign must produce.

---

## 4. The narrow change set

Branch `claude/l5-narrow-campaign-prep`, cut from the **deployed** commit
`497b1152`. **7 source files, 2,296 insertions** — versus the 47-commit branch.

| file | why it is required |
|---|---|
| `src/l5_pmus_live_runner.py` | ingestion repair (durable cursor, timestamped counters, three diagnostic clocks) + completion-phase observability |
| `src/l5_window_collector.py` | collector degrades instead of raising on a full volume |
| `src/l5_window_retention.py` | bounds the volume; floor-driven, count bound off by default |
| `scripts/l5_window_collector_run.py` | wires retention and storage reporting |
| `src/l5_live_validation_gates.py` | **daily/cumulative separation** |
| `src/l5_live_execution.py` | `l5_realized_today` + the two call sites that feed it |
| `src/l5_pmus_transport.py` | preflight reads the enforced cap; fee maximum corrected |

**Deliberately excluded:** `src/pmus_spring_l5_session.py` stays at its
**production** version (the branch's +70 lines are unreviewed money-adjacent
code and are not needed), and the two read-only analysis tools
(`l5_residual_reachability_census`, `l5_strand_completion_counterfactual`) stay
on the research branch — nothing imports them, so the image is smaller.
Verified: all imports resolve and no excluded module is referenced.

### Daily vs cumulative, as the owner specified

`max_l5_daily_loss_usd` reads a **UTC-day** figure (`l5_realized_today`);
`max_l5_cumulative_loss_usd` reads the **post-baseline total**
(`l5_realized_since_baseline`), untouched and never reset by the day boundary.
They refuse independently. A configured daily ceiling with no day-scoped figure
**refuses** rather than reading absent as zero, and omitting the input falls
back to the stricter cumulative measure, so no caller can obtain a laxer daily
gate by omission.

---

## 5. Requested envelope, with the two corrections applied

| | |
|---|---|
| series | BTC15m only, `max_concurrent_markets=1` |
| windows | 4 |
| expiry | 3 hours hard wall clock |
| **principal ceiling** | **`L5_MAX_CASH_DEPLOYED_USD=4.69`** → all-in ≤ $5.00 |
| **worst-case deficit** | **`L5_MAX_WORST_CASE_DEFICIT_USD=0.10`** (from $2.00) |
| daily L5 loss | `L5_MAX_L5_DAILY_LOSS_USD=5` |
| cumulative L5 loss | `L5_MAX_L5_CUMULATIVE_LOSS_USD=20`, never day-reset |
| `max_live_orders` | 1, unchanged |
| defensive budget | $2.00, charged before the wire (existing) |
| defensive pair ceiling | 1.20 (existing, unchanged) |
| maker complete ceiling | 0.96, unchanged |

**Still outstanding before live orders**, and neither is mine to decide:

1. **Which permission.** `L5_DEFENSIVE_COMPLETION` (+ `L5_DEFENSIVE_DWELL`) is
   the designed path. I have not established why it did not act on 1945z, so I
   am not presenting it as a finished executable envelope.
2. **Fresh preflight evidence** requires the narrow deploy, which is not
   authorized. The figures above are from the 2026-10-26 production lines plus
   code; the in-container `L5_EFFECTIVE_RUNTIME_CONFIG` and
   `L5_LIVE_PREFLIGHT` must be read after deploy and before arming.
