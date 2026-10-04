# CF_MATCH — replacing Cell C's SELL exits with opposite-outcome accumulation

Read-only counterfactual on the **frozen Cell C action stream**. Not a
strategy, not a controller, and it introduces **no threshold**. Committed
before any CF number was computed. Cell C, V1 and D0–D4 are unchanged.

## The architecture this serves

**BUY BOTH SIDES → EXTEND THE CURRENT FAVORITE → SWITCH EXTENSIONS WHEN
LEADERSHIP CHANGES.** V1 killed one implementation of it (unconditional
minute accumulation held to settlement). The 2×2 showed the value lives in
movement/reversal dynamics, and the anatomy showed explicit SELL round trips
are what consume it. This counterfactual asks whether the switching
behaviour can be kept **without** the SELL.

## Construction — entirely from frozen events

- **Purchase opportunities**: exactly Cell C's own 387 purchase timestamps.
  None added, none moved.
- **Extension side**: a state variable. It starts as the favorite at the
  first mark, and **toggles to the opposite outcome at each timestamp where
  frozen Cell C fired a reversal exit**. Those 279 reversal events are the
  only switching signal, and they are read from the frozen run, not
  recomputed and not re-parameterised.
- **No SELL ever happens.** The exit is deleted, not replaced.
- Each purchase is $1 taker at the contemporaneous executable price of the
  extension side, capped by displayed depth, canonical fees.
- Everything is held to settlement.

Two arms, because the architecture names a seed and Cell C has none:
**CF_MATCH** (no seed, exactly C's action stream) is primary;
**CF_MATCH_SEED** adds the manual experiment's $5 UP + $5 DOWN seed at the
first mark, as V1 used.

## YES/NO accounting — kept economically distinct from the wire

A DOWN purchase costs `1 − bid` per share and consumes the UP **bid**
depth; selling UP would *receive* `bid`. These are different cash flows and
are recorded as such. **Buying the opposite outcome is never treated as
selling the original.** UP and DOWN shares, cash and average costs are
carried separately throughout; `matched = min(UP, DOWN)` and
`residual = UP − DOWN` are derived, never assumed.

The venue's single long-side wire price (`price.value` is always the long
side; a NO at X is wired as 1 − X) stays a serialization detail and never
enters the economics.

## Reported

Eliminated SELL notional and fees; replacement opposite-side BUY notional
and fees; matched shares created; the distribution of effective matched-pair
acquisition cost and the fraction matched below $1.00 after fees; residual
through time and peak residual; peak capital; settlement residual P&L; total
gross; total fees; net/window; median; positive-window fraction;
leave-best-1 and leave-best-3; worst window; turnover; gross/turnover;
gross/fees.

Economics decomposed into **matched-inventory economics**, **directional
residual economics**, **fees** and **settlement**, reconciling exactly, and
compared directly against frozen **Cell C** and **V1**.

## What follows only if it is promising

A true portfolio controller — state = YES inventory / NO inventory / matched
/ residual / average costs / current favorite / recent movement / time
remaining, rather than independent episodes — would then be preregistered
separately. Target unchanged: **≥$5 net per eligible/traded window within
≤$200 capital**. No threshold optimization, no maker assumptions, no
scaling, no live orders.
