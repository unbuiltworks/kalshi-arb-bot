# L5's opening and inventory decisions — measured, not assumed

Source: L5's own public activity, deduplicated on
`(transactionHash, asset, timestamp, size, side)` across
`data/l5/raw/activity.jsonl`, `data/l5/raw/activity_gap.jsonl.gz` and
`data/l5/ops/activity_20260901.jsonl`. **767,996 unique rows**, of which
**689,999 are BTC15m**, covering **5,566 BTC15m windows** and **684,275
BTC15m trades**.

Reproduce: `python3 -I analysis/l5/stage_b/l5_opening_inventory_survey.py`
and `python3 -I analysis/l5/stage_b/l5_fill_vs_order_check.py`.

## 1. L5 NEVER SELLS on BTC15m. It buys both outcomes and redeems.

| action | count |
|---|---|
| TRADE **BUY** Down | 345,645 |
| TRADE **BUY** Up | 338,630 |
| REDEEM Up | 2,866 |
| REDEEM Down | 2,858 |
| TRADE **SELL** | **0** |

Zero sells in 684,275 BTC15m trades. The `btc-updown-5m` family has 2 sells
in 52,179 trades, so selling is not absent from the account — it is absent
from this strategy.

**Three consequences that settle open questions:**

- Our **buy-only architecture is behaviorally correct** for BTC15m. The
  stranded-inventory "sell the position" option explored earlier is not what
  L5 does, and PMUS has no true sell anyway.
- L5's exit is **redemption of a matched pair**, so *completing the pair IS
  L5's exit mechanism*. The completion work is behaviorally faithful
  replication, not a detour from it.
- BUY Up and BUY Down are near-symmetric (338,630 vs 345,645 = 49.5% /
  50.5%), consistent with systematic two-sided pair building rather than
  directional betting.

## 2. Opening decision

| quantity | p10 | median | p90 | mean |
|---|---|---|---|---|
| first-trade price | 0.400 | **0.510** | 0.620 | 0.508 |
| first-trade size (contracts) | 3.0 | **19.59** | 50.0 | 21.24 |
| first trade t_in_window (s) | 6 | **16** | 106 | 50.9 |

Against the deployed engine's configuration:

| dimension | L5 median | ours | gap |
|---|---|---|---|
| opening price | 0.510 | band 0.20–0.60 | aligned (L5's p90 0.62 just above our cap) |
| opening size | 19.59 | `ts_opening_leg_max_contracts` = **3** | **~6.5x**, and ~16x at L5's p90 |
| opening time | 16 s | fills observed 30 s–5 min | we are **late** |

L5 opens almost immediately — median 16 seconds into a 900-second window —
and at a size our configuration cannot express.

## 3. Inventory decision: EXTEND dominates, and the window ends balanced

Shape of the first four trades, `S` = same outcome as the opener,
`O` = opposite:

| shape | windows | share |
|---|---|---|
| **SSSS** | 2,521 | **45.3%** |
| SSSO | 673 | 12.1% |
| SOOO | 629 | 11.3% |
| SSOO | 624 | 11.2% |
| SSOS | 334 | 6.0% |
| SOSS | 298 | 5.4% |
| SOOS | 271 | 4.9% |
| SOSO | 180 | 3.2% |

**45.3% of windows open with four consecutive same-side trades.** Same-side
`EXTEND` is not an edge case; it is the dominant early behaviour, which is
exactly what `L5_OPERATING_MODEL.md` asserts and what this now measures.

And the window nonetheless ends two-sided:

- **99.2%** of windows (5,524 / 5,566) buy **both** outcomes.
- Balance at window end, `min(up,dn)/max(up,dn)`: p10 0.612, **median
  0.851**, p90 0.972.

So L5 tolerates real imbalance through the window and closes it to ~85%
matched — not 100%. A residual tail is part of the design, not a failure of
it.

## 4. Activity rate, and it is not a partial-fill artifact

| per window | p10 | median | p90 | mean |
|---|---|---|---|---|
| fill rows | 43 | 108 | 223 | 122.9 |
| **distinct transactionHash** | 42 | **105** | 215 | 119.5 |
| distinct seconds touched | 32 | 72 | 130 | 77.0 |

`rows/tx = 1.03` overall, so fill rows are essentially 1:1 with
transactions. L5 genuinely transacts ~**105 times per 15-minute window** —
about one trade every 8.6 seconds, touching ~72 distinct seconds.

Our Stage A campaign submitted **2 to 9 orders per window**. L5 is roughly
**12x to 50x more active**.

## 5. Why this is the highest-impact blocker

Stage A's failure mode was structural adverse selection: with a single
first-leg fill, `pair = P_first + P_complement` is anchored near
`1 - spread`, the 0.96 cap is unreachable, and the residual *is* the entire
position. `FIRST_LEG_TO_COMPLEMENT.md` showed this held at 8 of 8 fills.

L5 does not solve that with cleverer pricing. It solves it with **frequency
and two-sidedness**: ~105 transactions per window, both outcomes in 99.2% of
windows, ending ~85% matched. At that rate a one-sided fill is a small tail
on a largely matched book. At our rate, the one-sided fill is the whole book.

The binding constraint is configuration, not insight:

- `max_live_orders` = **1**
- `ts_round_allocations` = **4**
- `ts_opening_leg_max_contracts` = **3**

Four rounds of at most a two-order pair caps us near 8 submissions per
window. Reaching even a tenth of L5's cadence is arithmetically impossible
without raising concurrency.

This also matches the operating model's own promotion order —
**PATIENCE → FREQUENCY → SIZE → CONCURRENCY** — and says the next rung is
FREQUENCY, ahead of size.

## 6. What this does NOT establish

- Nothing here is P&L. These are L5's actions, not its realized returns, and
  the $480/day figure remains a measurement target.
- Fill *probability* is still unmeasurable: PMUS publishes no print endpoint,
  and L5's venue is Polymarket, whose queue position is equally unpublished.
  That L5 achieves 105 transactions says nothing about how many orders it
  posted to get them.
- L5's prices are its *fills*, not its quotes. A median opening fill at 0.510
  does not establish where it rested the order.
- Raising our frequency is **not** authorized by this document and is not
  proposed here as a change. It is the measured blocker; the bounded way to
  test it is a separate proposal.
