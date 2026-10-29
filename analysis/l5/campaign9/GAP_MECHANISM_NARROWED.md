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

---

# CORRECTION — the conversion figures above are withdrawn

The owner challenged the attribution before it was built on, and the
challenge is right. Two defects in the reasoning above:

**1. The windows are not the same.** The 14.08s admission rate was measured
13:05–13:20Z today with the engine **unarmed**. The ~35s order cadence came
from Campaign 8's **armed** windows, 11 hours earlier. Comparing them
assumes the admission rate is constant across those periods, which was never
established.

**2. The denominator is the wrong population.** Reading
`l5_pmus_live_runner.py:2429-2448`, `ingest:admitted` counts every deduped,
non-stale L5 trade row whose slug starts with `btc-updown-15m-` — **across
all BTC15m windows**, not the single window we are armed on. The heartbeat
shows `distinct_slugs_seen: 39` and `top_slugs_seen` spanning six concurrent
slugs. Our orders are for one armed market. So the numerator and denominator
are not the same population.

### Therefore these are withdrawn

- "we submit on roughly every 2.5th admitted L5 action"
- "something discards about 60% of them"
- "~42.6 admitted actions per 600s eligible window"
- "12.5% conversion of admitted actions to orders"

### What survives

These do not depend on the comparison and still hold:

- **Not poll-bound.** `ingest:polls_ok` and `shadow_decisions:ARGMAX` both
  run at one per 3.36s, and the shadow arm reaches a decision on every poll.
  Whatever stops the live arm is not the cadence of looking.
- **Not the settlement sweep.** `settlement_sweep_read` was 34 cumulative
  over ~3.4h.
- **The idle gap is not market-driven.** Median 30.1s after a fill and
  31.4s after a cancel, across 72 gaps — a market-driven wait would not
  produce the same distribution for both.
- **The capital envelope is not the constraint.** $0.82/order at 0200z
  against a $12 ceiling permits ~14 orders/window versus 5.33 observed.
- The profit bounds, which were derived from pairs-per-window and
  matched economics, not from admissions: **+$0.145 to +$0.225/window**.

### Why I cannot normalize this from existing telemetry

`top_slugs_seen` reports **rows**, not admissions, and rows are inflated
roughly a thousandfold by the feed re-serving open windows
(`ingest:dup` 722,629 against `ingest:new` 466). So the per-slug share of
admissions cannot be recovered from it, and dividing the global rate by an
assumed concurrency produces a number with no evidential basis — at six
concurrent slugs it would imply a per-slug interval of ~85s, which is
*slower* than our order cadence and would mean the opposite conclusion.
That the same data can be made to support either answer is the reason to
report neither.

### What would settle it

Per-slug, per-window counts of admitted actions **for the armed slug**,
over the same interval as that window's orders. `L5_LIVE_DECISION` carries
the slug on each evaluation, so this is exactly the Campaign 9 capture and
nothing short of it. The funnel the owner specified —

> admitted → actionable intent → price/inventory/risk checks → order
> candidate → submitted → acknowledged → filled → paired or stranded →
> settled

— needs the decision tape at every arrow but the first. The first arrow also
needs duplicated or repeated L5 rows for one decision separated from
independent opportunities: `ingest:dup` already removes feed-level repeats,
but several rows of one L5 fill sequence still count separately, and only
the decision records show whether the engine treated them as one
opportunity.

**Status: the funnel is not measurable from what is captured today.** It is
the campaign's purpose, and the campaign is blocked on the one variable
write.
