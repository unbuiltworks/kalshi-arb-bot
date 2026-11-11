# P(fill) for a resting maker order, from our own outcomes

_Calibrated on 965 real submitted orders. A fill is an event; a cancelled order is CENSORED, not a permanent no-fill._

## The horizon we cannot estimate

Longest rest ever observed: **32.2 s**. Orders resting past 60 s: **0**. Past 120 s: **0**.

Requested horizons that are NOT estimable: [60.0, 120.0]. The estimator returns None for these rather than extrapolating.

**L5's median complement gap is 47 s — beyond our entire observed range.** We have never operated at the horizon his behaviour lives at.

## P(fill by t)

| leg | orders | filled | horizon | P(30s) | P(60s) | P(120s) |
|---|---:|---:|---:|---:|---:|---:|
| first_leg | 528 | 68 | 32.2s | 0.432 | **not estimable** | **not estimable** |
| complement | 437 | 73 | 32.2s | 0.427 | **not estimable** | **not estimable** |

## No flow term

the campaign recorded no contemporaneous flow, so no flow coefficient is calibrated here; flow enters forward from the completion shadow and its effect is a hypothesis to test, not an assumption baked in

Labels: event = the order filled; time-to-event is its rest time; censored = the order was cancelled or hit its deadline unfilled; it is censored at its rest time, NOT counted as a permanent no-fill; unestimable = beyond the longest rest ever observed there is no data; the estimator returns None instead of extrapolating
