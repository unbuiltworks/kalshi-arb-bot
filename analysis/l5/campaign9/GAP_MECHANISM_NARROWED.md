# The ~30s inter-order gap: the mechanism, narrowed read-only

Measured 2026-10-29 13:05:35Z → 13:20:08Z (873s, 15 heartbeat samples) on
the live deployment `e0fc7bc1`, **not armed**, no campaign running. The
heartbeat's `stats` counters are cumulative, so two samples give a rate.
Nothing was placed, cancelled or changed to obtain this.

## The rates

| counter | delta over 873s | one per |
|---|---|---|
| `ingest:polls_ok` | +260 | **3.36s** |
| `ingest:new` | +63 | 13.86s |
| **`ingest:admitted`** | **+62** | **14.08s** |
| `shadow_decisions:ARGMAX` | +260 | 3.36s |

Cross-check from an independent period: Campaign 7 admitted **366** actions
across 7 windows = 52/window over 900s = **one per 17.3s**. Two periods a
day apart agree, so ~14–17s is the real L5 action arrival interval.

## What this rules out

**The engine is not poll-bound.** The loop polls every 3.36s (matching
`L5_LIVE_POLL_S=3`) and the shadow arm reaches a decision on *every* poll —
260 decisions in 873s. Whatever stops the live arm is not the cadence of
looking.

**The engine is not purely follower-bound either.** This was my leading
hypothesis and the measurement weakens it: L5 actions are admitted every
**14.08s** while our live orders went out every **~35s** (Campaign 8's
0200z: 9 orders in 318s; pooled median idle gap 30.1s plus ~12s resting).
If the cadence were simply "act when L5 acts" we would submit every ~14s.

**It is not the settlement sweep.** Already refuted separately:
`settlement_sweep_read` was 34 *cumulative* over ~3.4h, so the Oct-27
treadmill is not running and `PMUS_SETTLEMENT_SWEEP_CAP` is working.

## What it leaves

We submit on roughly **every 2.5th admitted L5 action**. Something refuses
or discards about 60% of them, and that something is internal — not the
feed, not the poll loop, not the sweep.

## The headroom, quantified

| | |
|---|---|
| admitted actions per 600s eligible window | ~42.6 |
| live orders per window, Campaigns 6–8 | **5.33** |
| conversion of admitted actions to orders | **12.5%** |
| orders/window the $12 cash envelope supports | ~14 (0200z: $7.3353 for 9 ≈ $0.82/order) |

So the capital envelope permits ~2.6× the current order rate, and the
action feed permits ~8×. **Neither is the constraint.** Raising conversion
from 12.5% to 25% would roughly double orders per window, and pairs track
orders closely (C8: 9→4, 7→3, 5→2, 5→1, 5→1, 1→0).

At the measured matched economics of **$0.0926 per completed pair**, going
from 1.83 to ~3.4 pairs/window is **+$0.145/window**; the 0200z-rate bound
is +$0.225/window. Those remain the same two bounds as before — what is new
is that the denominator is now measured, and the blame is now narrowed to
one internal step.

## What it does NOT tell us

**Which** internal step refuses the other ~60%. The candidates are the ones
the Campaign 9 metric was designed to separate: no intent generated from an
admitted action / intent generated but gate-refused (and which gate) /
an order already resting / no economically acceptable quote at the book.

`ts_completion:NO_INVENTORY_TO_COMPLETE` stood at **418** in an earlier
sample, which says the completion path is evaluated constantly and refuses
for lack of inventory — consistent with most evaluations being completions
with nothing to complete, and a hint that the missing step is on the OPEN
side.

This is a narrowing, not an answer, and it does not need a live campaign to
go further: the same counters can be sampled per-window during any armed
period, and the decision tape classifies the rest.

## Status

Read-only. No code, no strategy, no limit, no deployment touched. BTC15m
remains the primary implementation priority.
