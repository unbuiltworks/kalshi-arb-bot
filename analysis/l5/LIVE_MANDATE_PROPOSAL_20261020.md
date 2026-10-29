# Proposed bounded live mandate: sequential two-sided BTC15m

Written 2026-10-20 against running commit `f2091849`. **Nothing is authorized
by this document and no order has been submitted.** It states the precise
limits an owner authorization would have to name.

## Current live status

| | |
|---|---|
| service | `l5-pmus-live`, deployment `be28085c`, commit `f2091849`, SUCCESS 04:51:49Z |
| shadow lock | **ENGAGED** -- `L5_LIVE_SHADOW_LOCK {"locked":true,"blocked":["submit"]}` |
| authorization | **EXPIRED** |
| HALT latch | not latched; preflight PASSED |
| watchdog | independent service, sweeping every 20s, cancel-by-id only, cannot submit, cannot clear a halt |
| available cash | `$344.44` (venue `/v1/account/balances`) |
| account-wide realized | `-$8.3672` today, with `-$84.62` of UNATTRIBUTABLE history |
| verified L5 realized | **`+$0.0276`** -- the only trustworthy figure |
| exchange-confirmed fills to date | ONE: `D6XA5J4G10JH`, 0.31 UP @ 0.8900, commission 0.0000, settled UP, **+$0.0341 realized** |

## The exact blockers to live submission, distinguished

1. **`SHADOW_ONLY_LOCK` (ours).** Blocks `submit()` ahead of everything else.
   Lifted by configuration, not by code. **This is the only blocker that is
   there by choice.**
2. **Owner authorization EXPIRED (yours).** `armed()` needs
   `PMUS_Q1_LIVE=YES`, a non-empty `PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM` equal
   to it, and `L5_LIVE_AUTH_NOT_AFTER` in the future. I do not read, generate
   or touch the arm token.
3. **HALT latch** -- NOT latched. Not a blocker today.
4. **Account-wide historical losses** -- `-$84.62` unattributable. Checked:
   `unresolved_accounting.blocks_live_authorization` is **false**, and the
   loss gates read the L5-attributed line. Preserved as UNKNOWN, not zeroed.
   **Not a blocker.**
5. **L5-specific losses** -- `+$0.0276`. Nowhere near the `$5` daily halt.
   **Not a blocker.**
6. **Capital limits** -- `$4.65` cash gate against `$344.44` available.
   **Not a blocker**, but it is what sizes the pair.
7. **Preflight** -- PASSED on every window observed. **Not a blocker.**
8. **`L5_TS_COMPLETION_CROSS` unset** -- not a blocker to submission, but it
   means the late taker rescue for stranded inventory is OFF. See below.

So: **two blockers, both deliberate.** Everything else is green.

## The sequential model, which is what L5 actually does

Not simultaneous YES/NO pairing -- the deployed `max_live_orders=1` makes
that impossible anyway, and the evidence is sequential:

```
OPEN      rest the CHEAPER leg, post-only, 2 ticks behind its touch,
          sized so the WHOLE pair fits $4.65 before either leg is placed
WAIT      <= 120s per order; re-read the venue, never trust the ack
FILL?     no  -> cancel by exact id, prove terminal by re-read, and either
                 replace the same leg (attempt budget) or RETIRE the round
                 if the book has left its plan, then re-plan at the new touch
          yes -> book the fill, update inventory and remaining capital
COMPLETE  derive the complement from the CURRENT book and the UNMATCHED
          remainder, priced against the cost ACTUALLY paid. Place it only if
          the pair still clears the fee floor AND <= 0.96 without any rebate.
          Otherwise REFUSE and carry the residual.
TERMINAL  T+600: cancel anything resting, by exact id
SETTLE    matched pairs redeem $1.00/share; residual scored on the outcome
```

Both legs are never assumed to fill. The worst case is the bound that matters.

## Proposed mandate

| term | value |
|---|---|
| market | ONE BTC15m market, `cpc-btc-updown-15m-*`, one at a time |
| maximum capital | **$4.65** gross cash out, `NO_RECYCLING` (a matched pair releases nothing until settlement) |
| order size | set by the gate, not by me: `qty = floor(4.65 / (up_px + 1.00))`, ~3.2-3.6 contracts |
| concurrent orders | **1** |
| rounds per window | 1 full-size pair is all $4.65 funds |
| per-order life | <= 120s, then cancel by exact id |
| entry window | T+0 to T+540 |
| terminal | T+600, nothing rests past it |
| daily realized loss halt | **$5**, unchanged |
| per-market worst case | **$5**, unchanged |
| total exposure | **$5**, unchanged |
| max unhedged inventory | 5 contracts (`max_unmatched_contracts`) |
| expiry | one window, named explicitly |

**Entry conditions (all required):** book two-sided and < 2s old, from the
authenticated feed; pair <= 0.96 and positive on BOTH outcomes with NO rebate
assumed; whole pair fits $4.65 before either leg is placed; inside T+0..T+540;
nothing live; no unresolved accounting; preflight PASSED; HALT not latched.

**Abort conditions (any one):** daily realized loss reaches $5; any order
state unverifiable after a cancel; book stale, disconnected or one-sided;
authorization deadline reached; HALT latched by us or by the watchdog;
terminal reached.

**Expected net economics, per completed pair, at the current book:**

| | per contract | pair of ~3.3 |
|---|---|---|
| post-only both legs, **no rebate assumed** | +0.056 to +0.066 | **+$0.18 to +$0.22** |
| if both legs paid taker | +0.016 to +0.026 | +$0.05 to +$0.09 |
| maker rebate upside, never assumed | +0.006 | +$0.02 |

**Against:** a stranded single leg settling the wrong way, **-$1.70 to
-$1.85**. So one completed pair pays about an eighth of what one stranded leg
costs: the mandate is only rational if completion is likely, and the observed
completion rate is **n=2**, which supports nothing. The first live window's
purpose is to produce a denominator, not a profit.

## Does this need your authorization?

**Yes, and nothing will be submitted without it.** Two things only you can do:

1. Set `PMUS_Q1_SESSION_ID`, `PMUS_Q1_ARM` (equal to it) and
   `L5_LIVE_AUTH_NOT_AFTER` for the window you choose. I will not read,
   generate or overwrite the token.
2. Decide `L5_TS_COMPLETION_CROSS`. Unset, a stranded leg is carried to
   settlement. Set to `YES`, the engine may cross as a TAKER in the last two
   minutes, and only when crossing STRICTLY improves the worse settlement
   outcome. My recommendation: **set it**, because the stranded leg is the
   dominant loss and this is the only thing that caps it -- but it is a
   separate authorization from resting a maker quote and it is yours to give.

I will also lift `SHADOW_ONLY_LOCK`, which is mine, only once you have armed
and every preflight, watchdog and reconciliation check has passed.
