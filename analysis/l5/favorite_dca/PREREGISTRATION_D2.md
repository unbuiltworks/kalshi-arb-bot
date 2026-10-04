# D2_MAKER_FIRST — specification

**Disclosure on ordering.** This document was committed *after* the D2 run,
unlike the A/B/C/D preregistrations which preceded theirs. The reason it is
still a freeze and not a post-hoc rationalisation is that **the execution
policy contains no free parameter I could have selected**:

- the resting price is **Am's rule verbatim** — the favored side's own best
  bid (`bid` for UP, `1 − ask` for DOWN) — chosen in an earlier, already
  published experiment, not here;
- the order lives exactly **one evaluation period** and is then cancelled,
  also Am's rule;
- there is **no taker fallback** in D2, by the user's own instruction;
- exits stay D0's taker exits, unchanged;
- the behavioral controller is D0's, untouched.

Every one of those was fixed by the request itself. I am stating the
ordering rather than implying a freeze I did not perform.

## What is identical to D0

Favorite definition, strengthening requirement, 60 s cadence, extension
rule, reversal/trail formula, exit logic, inventory accounting, $1
allocation, window eligibility. The code path is the same function; `D2`
differs only by `entry_mode="maker"`.

The decision sequence is therefore **unchanged by construction**:
`act()` is reached only where D0's own logic says to extend, and the
episode/peak state machine is driven by prices, never by whether a fill
occurred.

## What changes

At each intended entry/extension, a passive bid is placed on the favored
side instead of crossing. Fills obey the **existing defensible
methodology**, unchanged:

- **no touch = fill**;
- queue-ahead is taken from the depth displayed when our level becomes the
  touch;
- only trades consuming our side deplete it (`dir: sell` for a bid);
- a print strictly through our price zeroes the queue;
- a price change of ≥ one tick is a cancel and a new order.

An action with no defensible fill inside its period is recorded as a
**missed D0 action**. It is never crossed.

Maker fills pay **no taker fee**. The maker rebate is modelled, reported on
its own line, and **excluded from net**, as in every other variant here.

## The matched counterfactual

`D0_on_D2_captured` runs D0's taker execution restricted to **exactly the
periods D2 filled** (`only_periods`), everything else identical. The
three-way comparison then separates:

- **execution saving** = D2 vs `D0_on_D2_captured` (same actions, different
  execution);
- **selection effect** = `D0_on_D2_captured` vs full D0 (same execution,
  different actions).

## Not done here

No threshold search, no trail change, no scaling, no live orders, and **D3
(maker-first with a bounded wait then taker) is not run** — it needs its
wait derived from measured latency and preregistered on its own.
