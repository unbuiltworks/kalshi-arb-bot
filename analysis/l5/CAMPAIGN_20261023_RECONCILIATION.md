# Bounded four-window campaign: venue reconciliation, 2026-10-23

**Status: CLOSED, fully settled, shadow lock restored and verified.**
Every figure below is venue-reported or arithmetic over venue-reported figures.
Source: `l5-pmus-live` deployment `d18f402a` (trades) and `bbe5868f` (current).

## The campaign traded

Three windows were armed; **six** orders were submitted, not three. All three
fills carry `manualOrderIndicator: MANUAL_ORDER_INDICATOR_AUTOMATIC` -- the
first algorithmic fills in the account's history (all 89 prior fills were
MANUAL) and the first live evidence that the executor submits unaided.

`L5_LIVE_DECISION` records in these windows read `armed: true` and
`order.submitted: true`. An earlier report in this session of
`submitted: false / NOT_ARMED` was a **sampling error**: those records come
from after the authorization expired. There was never a log-versus-venue
discrepancy; the submissions were recorded all along, in the
`L5_LIVE_LIFECYCLE` stream.

## Price semantics, which decide the arithmetic

PMUS wires every price in YES/UP terms (`l5_authoritative_ledger_repair.py:29`,
`l5_expectancy_replay.py:13`). A `BUY_SHORT` at wire `p` SELLS YES at `p`,
taking cash in and owing $1 if YES settles 1 -- so it acquires DOWN at `1 - p`.
The 0230z DOWN leg at wire 0.93 therefore cost **0.07**, not 0.93.

## Window by window

### `...0230z` -- first leg filled, complement missed, defensive unwind closed it

| | order | side | own px | qty | rest | outcome |
|---|---|---|---|---|---|---|
| 02:33:39 | `D8XRXQY011N6` | DOWN (`BUY_SHORT` wire 0.93) | 0.07 | 1.17 | 65.7s | **FILLED** maker |
| 02:35:20 | `D8XSNNGG11MC` | UP maker @ 0.88 | 0.88 | 1.17 | 122s | **CANCELED, 0 filled** |
| 02:39:04 | `D8XVD4G011MM` | UP taker @ 0.97 | 0.97 | 1.17 | 0.002s | **FILLED** defensive |

Planned pair was UP 0.88 / DOWN 0.07 = 0.95, edge 0.05. Realized pair cost
**1.04**. Equivalently: sold YES at 0.93, bought it back at 0.97.

**Realized −$0.0468** (0.04 x 1.17), `commission: 0.0000`.

The 02:39:04 order is unambiguously the defensive path, not a pair completion:
`leg.mode: TAKER`, `post_only: false`, `defensive: true`,
`reason: DEFENSIVE_EXIT_BOUNDED_AND_FUNDED`, `remaining_s: 56.374`,
`exposure.residual_before: -1.17 -> residual_after: 0.00`, under a `mandate`
permitting ONE crossing order. It recorded `improves_expected_value: false`
and `saves_vs_holding_usd: 0.0327` against `hold_downside_usd: 0.0819`.
`MAKER_COMPLETE_AT_CEILING` was `executable_now: false`,
`within_maker_ceiling: false` (pair 1.01). The 0.96 maker ceiling was never
breached by a maker order; the 1.04 pair was permitted only under
`DEFENSIVE_PAIR_CEILING = 1.20`, for that one order.

### `...0245z` -- no fill, no exposure

One DOWN order (`D8Y00QY011TB`), zero filled, resolved 02:50:55 after a ~100s
venue `HTTPError` storm (02:49:12-02:50:51) during which progress and
pending-resolve polling both failed. The recorder recovered and booked the
terminal state from the venue's own order row. **Realized $0.**

### `...0300z` -- first leg filled, complement missed, defensive arrived too late

| | order | side | own px | qty | outcome |
|---|---|---|---|---|---|
| 03:05:29 | `D8Y7G5NG11N2` | UP (`BUY_LONG`) | 0.20 | 1.79 | **FILLED** in 1.44s |
| 03:06:05 | `D8Y7R2GG51R3` | DOWN maker @ 0.73 | 0.73 | 1.79 | **CANCELED, 0 filled** |

Planned pair UP 0.20 / DOWN 0.75 = 0.95, edge 0.05. The UP leg went to
settlement and **settled worthless: realized −$0.3580** (1.79 x 0.20).

At 03:09:47 the complement was `COMPLEMENT_PAIR_ABOVE_CAP` (pair 1.11) and
defensive refused with **`INSUFFICIENT_EXECUTION_TIME`, `remaining_s: 12.463`**,
then `PAST_TERMINAL` (03:10:19) and `PAST_COMPLETE_CUTOFF` (03:12:59).

## Settlement and the ledger

The live heartbeat (11:06:45Z) reports, and both figures are exact:

```
utc_day_realized_usd    -0.404800     = -0.0468 + -0.3580
all_time_realized_usd   -9.30360000   = -8.8988 + -0.4048
windows_settled 269   undated_settlements []   counters_cover_all_settled_windows true
```

Campaign budget $11.10; headroom now **$1.7964** (was $2.2012). The budget
absorbed exactly the realized loss -- no loss erased, no headroom minted.

## What this is evidence for

1. **The executor submits, fills, cancels, unwinds and settles unaided.**
   That was the open question. It is answered.
2. **Budget-aware sizing bound in production and worked.** The 0230z plan
   recorded `sized_by: CAMPAIGN_BUDGET`, obligation `2.1996` against headroom
   `2.20120000` -- sized to fit with $0.0016 to spare, on the same
   dollar-a-share basis the cash gate charges.
3. **The defensive unwind works, and is not edge.** It bounded the 0230z tail
   for 4 cents a contract instead of 7, and said so.
4. **Zero profitable pair completions in two chances.** Both first legs
   filled; neither profitable complement did. n=2 settles no rate, but it is
   consistent with the already-measured depth-2 completion fill rate of
   **14.4% (n=868)**.
5. **Adverse selection, now in live data.** The 0230z DOWN leg at 0.07 filled
   *because* price was rallying to 0.96/0.97 -- straight away from the UP leg
   resting at 0.88 behind `queue_ahead: 20403.02` with
   `requires_price_to_reach_us: true`. The leg that fills is the leg you did
   not want filled. This is the mechanism `test_l5_concurrent_legs_are_
   adversely_selected.py` pins, observed live rather than modelled.

## Versus the model

The model predicted ~72% pairing at +0.136/pair and ~28% stranding at −0.40.

* stranding cost: predicted −0.40, observed **−0.3580** -- close.
* pairing: predicted +0.136/pair, observed **none at any price**. The one
  completion that happened was a defensive loss of −0.0468, not a pair.

So the loss side of the model is calibrated and the profit side went
unobserved. Two windows cannot prove either rate.

## NEW DEFECT: the defensive arm window and the complement deadline are uncoordinated

0230z and 0300z ran the identical sequence and got opposite outcomes for a
timing reason, not an economic one:

| | complement canceled | defensive evaluated | `remaining_s` | verdict |
|---|---|---|---|---|
| 0230z | 02:37:22 | 02:39:04 | 56.374 | **acted**, −0.0468 |
| 0300z | 03:08:06 | 03:09:47 | 12.463 | **`INSUFFICIENT_EXECUTION_TIME`**, −0.3580 |

The complement is allowed to rest until close to the binding terminal
(600s). When it is canceled late, the next defensive evaluation can land
*past* the execution-time floor -- so the position that most needs the exit is
the one least likely to get it. 0230z was saved by 44 seconds of luck. The
7.65x difference in realized loss between the two windows is attributable to
that gap, not to the market.

The fix is to make the complement's cancel deadline a function of the
defensive execution-time floor, so an unfilled complement is always retired
with enough runway left to unwind. **Not implemented here** -- it changes
live order timing and belongs behind its own authorization and tests.

## Authorization state

`L5_SHADOW_ONLY=1` set and **verified in the running deployment** `bbe5868f`
at 11:06:45Z and 11:07:42Z, four independent blocks:

```
shadow_lock true   armed false   submission_gate.blocked true
halt true          approval_state "approval_stale"
```

No campaign is open, no authorization is live, and none is proposed here.
