# CEILING_PINNED_PERSIST — **PARKED** (owner decision, 2026-09-14)

Accepted at `ce3f0294`. **Not deployed. No live persistence arm.**

## Status

| | |
|---|---|
| state | **PARKED — preserved as a candidate execution improvement** |
| deployed | **no**, and no live arm is authorised |
| imported by production | **no** — `src/l5_ceiling_pinned_persist.py` is referenced by nothing in `src/` or `deploy/` |
| in the probe image | **no** |
| in the recorder watch set | **no** |

## What would un-park it

The owner's condition, recorded verbatim so it is not softened later:

> "Preserve it as a candidate execution improvement if later evidence shows
> economically attractive ceiling-pinned COMPLETE orders are being lost to
> premature cancellation."

Both halves are required, and **neither is satisfied today**:

1. **economically attractive** — the ceiling-pinned COMPLETEs measured so far
   are not. The marginal pair on attempts that do not fill is **0.9952**, and
   the whole prize at perfect conversion is **$0.1563** against a $1.3408
   settled residual loss.
2. **being lost to premature cancellation** — in **0 of 53 chains** did the
   complement ever reach our price over the observed windows. There is no
   observed instance of a fill that persistence would have captured. The
   unobserved time is exactly where the question lives, which is why the
   verdict is UNKNOWN rather than negative.

## Why it stays on the shelf rather than being deleted

The mechanism it removes is real and precisely measured: 52.3% of COMPLETE
attempts are ceiling-pinned, and **80** of them are cancels whose re-quote
provably recomputes to the same price, destroying and rebuilding an order at
the same price against the same book. If a future corpus shows ceiling-pinned
COMPLETEs at an attractive pair being cancelled while the complement is within
reach, the arm is already built, tested (40 tests), and pinned to production's
own pricing function by a grid test that goes red if production drifts.

## Superseded as a priority

The economic-state trajectory decomposition
(`analysis/l5/state_trajectory/STATE_TRAJECTORY.md`) found that even **perfect**
COMPLETE conversion leaves PMUS with 7.3× L5's degenerate-state share, 48
informative states against 69,558, and 9 episodes against 2,216. **Execution is
not the binding constraint; habitat is.** Further COMPLETE microstructure
tuning is deprioritised until that changes.
