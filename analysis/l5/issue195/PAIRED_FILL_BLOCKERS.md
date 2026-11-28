# What still prevents reliable paired fills

Verified against deployment `a54c5ee4-0f1d-420b-aa91-f85bc0612bf8`
(SUCCESS 2026-11-07T02:13:17.860Z, commit
`5775d929a440f6cf83b5a21047024e68f03fde4e`, branch
`fix/l5-requote-pair-ceiling` = PR #200 head), read from the running
container's own accessors.

## Effective configuration, in-container

| row | source | effective |
|---|---|---|
| `completion_max_pair` | `TS.completion_max_pair()` | **0.96** ✅ |
| `maker_pair_ceiling` | `TS.max_pair_cost()` | 0.96 |
| `completion_pair_ceiling` | `TS.completion_pair_ceiling()` | **1.00** ⬅ the live completion bound |
| `ts_opening_leg_max_contracts` | resolver | 1 |
| `shadow_only` | `R.shadow_only()` | true |
| `taker_crossing_reachable` | policy ∧ owner auth | false |
| `ts_entry_cutoff_s` / `ts_complete_cutoff_s` | | 480.0 / 600.0 |
| `cancel_margin_s` | | 300.0 → cancel cutoff T+600 |
| `complete_cutoff_reachable` | `COMPLETE_CUTOFF_S < cancel_cutoff(900)` | **false** |
| `ts_order_ttl_s` | | 120.0 |
| `max_live_orders` | | 2 |
| `REPLACE` class budget | | 60 max, 39 spent, **21 left** |

## The three controllers, and how they interact

A second leg can be withdrawn or withheld by **four** distinct mechanisms.
They do not consult one another.

**1. `completion_requote` (repricing).** Cancels a resting completion to
chase the touch. Target `min(touch, completion_ceiling, redeem_cap, edge_cap)`.
**Fixed in PR #200:** `edge_cap = completion_max_pair()` now defaults to the
opening ceiling (0.96 in-container) instead of 1.00, so it can no longer
cancel a 0.9500 pair to chase 1.0000.

**2. `recover_stale_leg` / `acc_stale_by_distance` (distance recovery).**
Cancels a resting leg the book has left behind, so the complement path can
re-place it. **Takes no held quantity and no cost basis — it judges drift
only.** Tolerance 4 ticks, minimum rest 15s, **one recovery per market**.

**3. `complement_order` + the submission boundary (placement).**
`complement_order` prices the replacement two ticks behind the touch and
tries the 0.96 opening cap first; on refusal its internal rescue permits any
price up to the **single-leg** `completion_ceiling`, which at a 0.4900 basis
and a 0.95 ceiling is a pair of **1.4400**. Measured directly:

```
touch  price  pair    edge/contract  ok    judged_as
 0.51   0.49  0.9800   0.0200        True  COMPLETION
 0.60   0.58  1.0700  -0.0700        True  COMPLETION
 0.95   0.93  1.4200  -0.4200        True  COMPLETION
```

**That latitude is caught downstream.** The submission boundary
(`pair_cost_refusal` → `FaithfulExecutor.completion_pair_rescue`) requires
three conditions — the order must reduce an unmatched position, must not
cross zero, and `eff <= completion_pair_ceiling()`. So the **system-level**
bound on a completion pair is **1.00**, the redemption line, not 1.44. A
1.42 pair is refused `EFFECTIVE_PAIR_ABOVE_REDEMPTION`.

**4. `acc_order_deadline` (age TTL, 120s)** and the terminal sweep at
T+600. Mandatory.

## What actually happened after the last cancel at 01:01:00

152 decision records in the following 14 minutes and **not one submission
attempt** — no `L5_ACC_LEG`, no complement record at all:

| records | reason | blocker class | verdict |
|---|---|---|---|
| 64 | `DECIDE_COOLDOWN` | `REQUOTE_CHURN` | `missed_profitable_pair: NO`, in-state `NO` |
| 5 | `DECIDE_COOLDOWN` | `REQUOTE_CHURN` | `UNKNOWN` |
| 72 | `PAST_COMPLETE_CUTOFF` | `NOT_AN_OPPORTUNITY` | `N/A` |
| 11 | `PAST_TERMINAL` | `NOT_AN_OPPORTUNITY` | `N/A` |

Two readings, both important:

- **The window was lost in its first 21 seconds.** The 64 cooldown records
  that *did* price the state found `missed_profitable_pair: NO` — once the
  touch ran to 0.71 and beyond, no profitable pair existed again. So the
  only decisions that mattered were the two cancels at 01:00:40 and
  01:01:00.
- **`DECIDE_COOLDOWN` is not the constraint.** `DECIDE_EVERY_S = 5.0`; a
  decision every five seconds across a 900-second window is ample, and the
  record is reporting-only — it places, reprices and withdraws nothing.

## Remaining reasons a profitable second leg can still be lost

1. **The distance recovery has no pair awareness, and this is the largest
   remaining one.** Measured over the recorded authenticated ladders
   (`recovery_vs_pair_value.py`), counting only cases where the resting
   quote's pair is **inside** the 0.96 cap and the replacement's pair would
   be **outside** it — i.e. a good quote cancelled that cannot be re-placed:

   | window | resting pair | samples | recovery fires | good quote destroyed, unreplaceable |
   |---|---|---|---|---|
   | 1215z | 0.8393 (+$0.16) | 57 | 35 | **24** |
   | 1245z | 0.9500 (+$0.05) | 88 | 72 | **72** |
   | 0100z | 0.9500 (+$0.05) | 1 | **0** (`WITHIN_TOLERANCE`, drift 3 of 4) | 0 |

   On 1245z **every** recovery would have destroyed a profitable quote and
   left nothing in its place. It was *not* the 0100z cause — 3 ticks of
   drift is inside the 4-tick tolerance — but on recorded data it is more
   destructive than the requote defect was, because the requote at least
   left a (worse) quote behind.

2. **The completion bound is still the redemption line.**
   `completion_pair_ceiling()` reads **1.00** in the container, so a
   completion may be submitted at a pair of up to exactly $1.00 — zero edge
   — and at 0100z a pair of 0.98 was permitted under an authorization that
   capped pair cost at 0.96.

3. **No explicit hold-to-settlement decision.** Past the cutoff, 83 records
   read `NOT_AN_OPPORTUNITY` / `N/A`. The residual settled for −$0.4900
   without one record stating the position, its worst case and that no
   action was available. The engine stops asking rather than deciding.

4. **`complete_cutoff_reachable: false`.** The completion cutoff (T+600) and
   the cancel cutoff (T+600) coincide exactly, so a completion decided in
   the final moments has no margin in which to be placed and verified.

5. **There is no calibrated fill model, and cancelling early is why.**
   `fill_calibration` holds 17,600 orders with **`usable_for_calibration: 0`**.
   All 17,600 are shadow records (`coid: "shadow|ARGMAX|…"`,
   `venue_actual_status: PENDING`), so `fee_accounting` and
   `lifetime_observation` can never become `PRESENT` for them. Real maker
   orders are the only source — and the three UP orders of 0100z each rested
   15–20 seconds before being cancelled. **Every decision to keep or reprice
   a quote is a bet on a fill probability that has never been measured, and
   the churn that loses the pairs is also what prevents measuring it.**

## The exact next change

**Give `recover_stale_leg` the pair test that `completion_requote` now has.**
Refuse a distance recovery when the resting quote's pair is inside the
ceiling **and** the replacement's pair would be outside it — refuse to trade
a good pair for no quote — and allow it otherwise. Optional inventory
arguments, absent inventory behaving exactly as today.

This is narrower than PR #199's guard and is why that PR should not be
merged as it stands. PR #199 protects **every** reducing order from optional
recovery. That would also have blocked the 01:00:59 recovery at 0100z, which
was *correct*: that order was 20 ticks adrift at a pair of 0.98, above the
approved cap, and releasing it was the right call. The economic test keeps
the useful recoveries and blocks the destructive ones; a blanket guard
cannot tell them apart.

**Second, and owner-decidable:** set `L5_COMPLETION_PAIR_CEILING` below 1.00
so a completion must create edge rather than merely redeem. One variable,
already clamped to `[max_pair_cost(), 1.00]`, already reported by the
verifier, tightening only.
