# What the frozen controller does across a whole window — and two things it
# does not enforce on its own

**Artifact under review:** `src/gemini_execution/continuous_probe.py` at
`6342171e`, unchanged. **Nothing here edits it.**
**Produced by:** `src/gemini_execution/continuous_preflight.py` (no transport,
no asyncio, no arming, no latch — asserted over the AST).
**Recorded:** 2026-09-18.

Until now the controller had been *read* but never *run past one turn*: `plan()`
decides a single state, and nothing advanced the clock or the inventory. A
reviewer asked to authorize a bounded live probe could not see a window. This
walks it — 60 states, 300s → 0s, five-second cycles, with fills supplied by the
caller's scenario and labelled SIMULATED throughout. **No output here is a fill,
a shadow result, or admissible in any P&L.**

## The behaviour is as documented

```
 i=0  tte=300  WINDOW_OPEN  OPEN   YES 0.55 + OPEN   NO 0.40   fill YES  res 0.01
 i=1  tte=295  WINDOW_OPEN  EXTEND YES 0.55 + COMPLETE NO 0.40  fill YES  res 0.02
 i=2  tte=290  WINDOW_OPEN  EXTEND YES 0.55 + COMPLETE NO 0.40  fill YES  res 0.03
 i=3  tte=285  WINDOW_OPEN  EXTEND YES 0.55 + COMPLETE NO 0.40  --        res 0.03
 i=4  tte=280  WINDOW_OPEN  EXTEND YES 0.55 + COMPLETE NO 0.40  fill NO   res 0.02
 i=5  tte=275  WINDOW_OPEN  EXTEND YES 0.55 + COMPLETE NO 0.40  fill NO   res 0.01
```

From flat it emits **both** rungs in one cycle, and both are **BUY** — it
accumulates a pair rather than quoting both sides of one instrument for spread.
A same-side run classifies as `EXTEND` while the opposite leg simultaneously
offers `COMPLETE`; complement fills reduce the residual (0.03 → 0.01, matched
0.02). The end-of-window floors are real and fire on the clock: `COMPLETE_ONLY`
under 60s, `WITHDRAWN` under 30s, all held by `END_OF_WINDOW`. No `SELL`, `IOC`,
`FOK` or `MARKET` value exists anywhere in the module. Every hard bound held.

That part is good, and it is now demonstrated rather than described.

## FINDING 1 — the action budget is caller-enforced, not self-enforced

`WindowGuard.step()` reads `actions_taken` and passes it to `plan()`. **It never
raises it.** Only `record()` raises it, and nothing in the frozen artifact calls
`record()`:

```
increments actions_taken:  __init__ False   stop False   check_symbol False
                           record  True     step False
record() called inside continuous_probe:     False
```

Measured consequence: a driver that does not call `record()` decides **more than
`MAX_ACTIONS`** actions while the guard reports `actions_taken == 0` and every
bound check passes.

This is the same shape as the defect commit `6342171e` fixed one level down —
*"`plan()` took an `actions_taken` count from a caller that did not exist. A
description is not a control."* The count now has a caller-facing setter, and it
is still the caller's obligation to use it.

## FINDING 2 — the budget counts RE-DECISIONS, not new orders

`plan()` emits the **desired surface** every turn and holds no resting-order
state, so the same YES rung at the same price is decided again on every cycle. A
driver that records each decided action — the only way the budget binds at all —
spends it like this:

```
turns run        6
actions taken    12 / 12       stopped: "max_actions 12 reached"
distinct (leg, price) ever wanted:  2
stopped at       tte 275.0s of a 300s contract
```

**12 actions, 2 distinct prices, 6 cycles, and the probe is over ~30 seconds
into a five-minute contract** — carrying residual 0.01 it then has no budget to
complement.

So `MAX_ACTIONS` is ambiguous between **orders placed** and **actions decided**,
and the frozen artifact cannot disambiguate: it does not know what is resting.
A driver must diff the desired surface against its own resting orders and record
only genuinely new placements. **That diff is exactly the part no reviewed code
contains yet** — and it is a correctness requirement, not an optimisation. A
driver that re-places a standing rung every cycle would also churn queue
position, which is the one thing a passive surface cannot afford.

## What this means for authorization

Neither finding is a reason to change the controller, and neither is a defect in
the L5 model. Both are the seam between the controller and a driver, and both
have to be settled **in the driver**, before it is authorized:

1. the driver calls `record()` for every action it actually places;
2. the driver diffs against resting orders, so `MAX_ACTIONS` means orders;
3. a reviewer confirms 1 and 2 against the SHA being authorized.

Until a driver exists there is nothing to authorize, and now there are two
specific things to check when one does.

## Preflight

`check_frozen` compares a fresh preflight field by field against the assumptions
the envelope was sized on. **An unread field is a discrepancy, not a pass** —
"we could not read it" and "it agrees" are different answers and only one is
evidence. A preflight that merely succeeds against a contract with different
increments would license an envelope that no longer means what it said.
