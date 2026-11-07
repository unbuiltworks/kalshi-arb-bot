# 1230z live measurement window — venue-confirmed result

Owner-authorized single BTC15m maker-only window, approval recorded on
PR #205 at 2026-11-07T12:04:26Z. Campaign
`camp1.c9b233c26f89b292bec90ca9b99a568b`, document sha256
`b23fbc57ff127fc40b1e966e77bfe3e38843b48b7db6bb5e4b1551da4a2224e3`,
`window_starts: ["cpc-btc-updown-15m-2026-11-07-1230z"]`.

## Settled result

`L5_LIVE_SETTLED` at 12:45:57.588Z, `VENUE_SETTLEMENT_PX`,
`MARKET_STATE_EXPIRED`, `yes_value 0.0000` (DOWN won).

| line | value |
|---|---|
| matched | 1 contract, cost **0.9550**, redeem 1.0000 → **+$0.0450** |
| residual | 1 UP, cost **0.4550**, redeem 0.0000 → **−$0.4550** |
| **settlement P&L** | **−$0.4100** |
| actual fees | **$0.00** (maker-only; the schedule has no maker-fee term) |
| rebate | estimate $0.0152, **credited $0**, `UNVERIFIED_NO_VENUE_CREDIT_RECORD` |
| **realized net** | **−$0.4100** — venue-confirmed settlement only |

Day realized **−$0.9000**; all-time **−$15.6109**. Independently
cross-checked against the account balance: $341.25365 → $340.84365, a
difference of **exactly $0.41000**.

## Every order, by venue id

| coid | side | own px | queue ahead at placement | lifetime | terminal |
|---|---|---|---|---|---|
| `l5f-b195e7360f86e0e4b6c9363a` | DOWN | 0.5000 | — | — | **FILLED** 12:30:27.901Z |
| `l5f-83557f64dd82a6809828ef7d` | UP | 0.4500 | **0** (best bid) | ~35s | **FILLED** 12:30:49.281Z |
| `l5f-2c1c0f64c13ec71c5cc7ab5e` | UP | 0.3500 | **2091.01** | 123s | CANCELED, zero fill |
| `l5f-fc3f9809135cd723b717ab4f` | UP | 0.4600 | **2235.04** | 63s | **FILLED** 12:33:57.270Z |
| `l5f-19329b90120cb4523271ea9f` | DOWN | 0.4900 | **851.01** | 124s | CANCELED, zero fill |

Venue ids `DJV9PWBG14F1` and `DJV9ZZ8G14ET` observed on the lifecycle
stream. All terminals `venue_confirmed: true`. 5 submitted, 5
acknowledged, 0 unacknowledged, 0 rejected, 0 partial fills,
**0 taker orders**.

## The controller's live decision, 12:30:44.177Z

Held DOWN 1 @ 0.5000; our UP complement resting @ 0.4500 → pair **0.9500**.

| action | pair | worst | verdict |
|---|---|---|---|
| **PRESERVE** | 0.9500 | −0.5000 | **selected**, `BEST_WORST_CASE_THEN_BEST_PAIR_AMONG_EXECUTABLE` |
| REPRICE | — | — | `REQUOTE_REFUSED:ALREADY_NEAR_TOUCH` |
| COMPLETE_MAKER | 0.8500 | −0.5000 | `RANKED_BELOW_SELECTED` |
| COMPLETE_TAKER | 0.8800 | **+0.1036** | `NOT_EXECUTABLE_CROSSING_DISABLED` |
| REDUCE | — | +0.1036 | `NOT_EXECUTABLE_CROSSING_DISABLED` |
| HOLD | — | −0.5000 | `RANKED_BELOW_SELECTED` |

**The preserved quote filled 5 seconds later**, completing the pair at
exactly the 0.9500 pair cost. No record read `N/A`. The silence problem is
closed in production.

A striking counterfactual: `COMPLETE_TAKER` was a **certain +$0.1036**
(pair 0.88, fee $0.0164) against PRESERVE's −$0.5000 worst case. Priced,
reported, never acted on.

## What actually cost the window

The first pair completed and the book went flat. The engine then opened a
**second round** at 12:32:54 — a simultaneous UP 0.4600 / DOWN 0.4900 pair.
The UP filled at 12:33:57; the DOWN never filled and was cancelled at
12:34:58. That left **UP 2 / DOWN 1** and a residual UP 1 at the average
basis 0.4550.

    first pair      +$0.0450
    second round's unmatched UP   −$0.4550
    net             −$0.4100

`L5_TS_ROUND_ALLOCATIONS=1` governs splitting the cash envelope across
cycles; it does **not** cap the number of opening rounds once a pair
completes and the book returns flat. **That is the defect that cost this
window**, and it is not any of the four mechanisms PRs #200–#205 addressed.

## Maker-order lifetime evidence — the measurement objective

Five real maker orders observed to terminal state with queue-ahead
snapshots. The first such data this program has.

**Queue depth was not monotonically predictive.** The order with **2235.04**
ahead filled in 63s; the one with **851.01** ahead did not fill in 124s. The
one that filled fastest had **0** ahead — it held the best bid. So being at
the touch mattered; depth behind the touch did not order the outcomes. That
is consistent with the earlier finding that queue position was not the
bottleneck, and it is the first venue-confirmed support for it.

Still only 5 observations. No fill probability is claimed.

## Safety

| check | result |
|---|---|
| windows authorized / spent | 1 / **1**, remaining 0 |
| `permits_this_window` for 1245z | **false** — `windows: ["…1230z"]` |
| submissions spent | **5** — exactly the 1230z orders, no more |
| taker orders | **0** |
| worst-case exposure at peak | ≈$0.50, cap $1.00 |
| daily loss | −$0.9000 against a $5 halt, room $4.1000 |
| defensive budget | `0.49916626650000` — **not reset** |
| post-window state | `SHADOW_ONLY_LOCK`, `shadow_lock: true`, `halt_local_env: true`, `armed: false` |
| live / parked / pending / quarantined | 0 / 0 / [] / [] |
| unresolved accounting | 0 |

The 1245z `l5f-*` records in the tape are **pre-wire captures**, which the
accounting label defines as "counted BEFORE the wire: neither is evidence an
order reached the venue". `submissions_spent: 5` proves none became an order.

## Defects found and fixed after the window

1. **`COMPLETE_MAKER` and `REPRICE` lacked PRESERVE's outcome envelope.** An
   unfilled maker quote leaves us in HOLD whichever one it is, so all three
   must carry the hold worst case and upside. Reporting only the pair
   outcome made PRESERVE's best $0.5000 against COMPLETE_MAKER's $0.1500 and
   let PRESERVE win the 1230z ranking on an **accounting asymmetry** rather
   than on economics. Right answer, wrong reason.

2. **Fixing that exposed a worse problem.** With the envelope applied, worst,
   best and EV all tie among maker actions, so the **pair became invisible**
   to the ranking and the queue chose a *worse* pair — the PR #200 defect
   arriving by a different door. Adding pair-before-queue then flipped
   1230z to the deep-queue 0.8500 quote, which is also wrong: the resting
   order held the best bid with **0** ahead.
   
   Weighing a pair against a queue needs a fill probability, and
   `l5_queue_depletion` establishes that we do not have one. So the scalar
   comparison is **removed**: while a completion rests, `COMPLETE_MAKER` is
   not a peer at all — changing a resting order's price is `REPRICE`, owned
   by `completion_requote`. One owner per decision, which is the whole
   architecture of PRs #200, #202 and #205. It is still priced and reported.

## What this window does not show

It does not demonstrate an edge. One window, net **−$0.4100**, all-time
**−$15.6109**. The pair economics worked exactly as designed (+$0.0450 on a
0.9550 pair); the loss came from an unmatched leg the round-control defect
allowed. Nothing here moves the $480/day target measurably.
