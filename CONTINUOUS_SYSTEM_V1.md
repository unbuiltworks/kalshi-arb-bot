# Continuous L5 system V1 — lifecycle, failure states, budget

Built directly on the CONTINUOUS-8 execution path. **Nothing about the
controller, the pricing, the 0.01 sizing, the maker-only rule, the pair-cost
policy or the per-window envelope changes.** What changes is that the same
proven window lifecycle runs again, under a budget that can only tighten.

## The two models, kept apart

| | question | changed by Gemini outcomes? |
|---|---|---|
| **A — L5 fidelity** | *what would L5 do in this state?* | **No.** Never. |
| **B — venue / economic** | *does that behaviour pay here?* | Yes — this is where Gemini execution evidence goes. |

A poor Gemini result is evidence about **B**. It is never a reason to edit
**A**. The habitat measurements below feed B only, and nothing in them is
reachable from the controller, the pricing or any bound.

## One window

```
discover eligible BTC5m contracts
  → wait for the entry band (240:280, bounded 420s)
  → authoritative preflight and recovery, BEFORE anything is spent
  → run the frozen policy against authoritative state
  → bounded terminal cleanup, cancel-only, on its own budget
  → ROLLOVER GATE: prove the previous window is authoritatively safe
  → only then consider the next one
```

The entry band is preserved as an **experimental operating condition**, not a
claim that it is optimal. The evidence behind it is n=4.

### The rollover gate is the load-bearing part

CONTINUOUS-5 stopped its controller and left two maker orders resting; one
filled 57 seconds later and settled against us. **A continuous system that
rolls forward without proving the previous window is flat would do that on a
loop.**

So the gate demands **positive evidence** and treats everything else as a
stop:

* a **readable** open-order list — an unreadable one is not an empty one;
* **none of our orders in it**; with no id list to match against, *any*
  resting order blocks, because assuming a stranger's order is a stranger's
  is exactly the guess that costs;
* a **readable execution view with no contradictions**.

## Failure states — all of them stop the run

There is no retry loop, no "assume flat", no sleep-and-hope.

| state | means |
|---|---|
| `ENTRY_BAND_UNREACHABLE` | no contract reached the band within the bound |
| `PREFLIGHT_DISAGREES` | the venue's answer differs from the frozen assumption |
| `STATE_UNREADABLE` | a required authoritative read failed |
| `EXECUTION_CONTRADICTION` | the execution sources disagree about what happened |
| `UNKNOWN_TERMINAL` | an order left the book and nothing explained it |
| `ORDERS_LIVE_AFTER_STOP` | cleanup could not prove the book is clear |
| `RISK_BUDGET_EXHAUSTED` | a bound said stop |
| `DAILY_LOSS_STOP` | realized loss hit the stop |

## The bounded continuous-live risk budget

**Per window — the CONTINUOUS-8 envelope, unchanged:**

| bound | value |
|---|---|
| order size | **0.01** |
| spend per window | **$0.15** |
| residual exposure | **0.04** units |
| inventory imbalance | **0.04** units |
| per leg | **0.10** units |
| concurrent markets | **1** |
| execution | **maker-only (MOC)**, no taker cross, no SELL, no IOC/FOK, no forced flatten |
| pair-cost ceiling | **none** |

**Per run — what continuous operation adds. It multiplies windows, never
size:**

| bound | value |
|---|---|
| `max_windows` | **12** |
| `max_total_spend_usd` | **$1.00** |
| `daily_realized_loss_stop_usd` | **−$0.25** |
| `max_run_seconds` | **7200** |

`RiskBudget` is a **frozen** dataclass whose only public method is `labels`.
It has no `grant`, `raise` or `extend`, and a test asserts that list stays
exactly `["labels"]`. **Bounds tighten or stop; nothing can self-increase.**
Tightening means constructing a narrower budget, which is an act outside the
module.

## Evidence: every decision fully reconstructable

Each cycle persists one row carrying the owner's chain in order:

```
market state → book/depth/flow → inventory → L5 action → execution plan
```

and the window summary carries what follows it — submitted orders, fills,
next inventory, next action, settlement, realized economics — joined by
symbol and cycle. Accounting stays **SUBMITTED → FILLED → SETTLED →
REALIZED**, never collapsed; `realized_usd` moves only on a venue-confirmed
cash movement.

## Habitat measurement (model B, collection only)

Recorded per cycle and summarised per window: spread · touch depth · depth
1–3 ticks away · trades/flow from the public print tape · quote resting time
· repricing frequency · fill conversion · adverse movement after fills · TTE
· complement opportunity.

Our own resting size at a level is **flagged, not subtracted**. In C8B the
bid at our price read `0.01` for seven consecutive cycles — our own order,
alone. Reporting that as market depth would have been the most misleading
number in the record; silently removing it would be a different kind of lie.

**No live controller tuning from these measurements.** Collect first.

## SHADOW vs LIVE

**SHADOW** runs the identical lifecycle with **no mutation path**: it never
constructs a session and never calls `place` or `cancel` — a property of what
it calls, not a flag it sets.

**What shadow cannot prove, stated rather than assumed:** with nothing
resting there are no fills, so inventory stays flat and the policy stays in
`OPEN`. **KEEP/CANCEL and the fill-driven transitions are not exercised in
shadow.** C8B proved those live. Shadow proves discovery, banding, state
maintenance, evidence completeness, rollover and the failure states.

**LIVE** additionally sends orders and requires an owner authorization
installed in the driver. This module never installs one and cannot supply
one.
