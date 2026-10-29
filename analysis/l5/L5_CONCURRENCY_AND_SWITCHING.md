# L5's own concurrency and market-switching behaviour

Answers OWNER DECISION #167 5564173746 (2026-09-07T02:23:51Z), the parallel
half: *"use the canonical L5 history to quantify L5's own concurrency and
market-switching behavior ... Preserve source provenance."*

Measurement only. Nothing here changes a cap, a rung, the live concurrency, or
a gate. Regenerate with `python3 -c "import json;from src import
l5_concurrency_study as s;json.dump(s.study(),open('analysis/l5/l5_concurrency_switching.json','w'),indent=1)"`.

## Source and what it can and cannot say

`analysis/l5/l5_canonical_sequence.csv.gz` (sha256:16 `3b98847233684612`),
71,984 rows / 2,216 market episodes, built from the public Polymarket activity
feed. `window_id` <-> `market_slug` is 1:1, so one window is one market episode.

* Every number is derived from **executed trades**. A fill is not a quote: L5
  may have had resting orders in markets it never traded in. So these are
  **fill-concurrency** figures — an exact measure of where it traded, and a
  **lower bound** on where it was quoting.
* `maker` is asserted only inside the takerOnly feed's coverage (2026-08-27 to
  08-31); outside it the label is `unknown` and is never counted as maker.
* Coverage is **22 distinct trading days in two disjoint blocks**: 2026-03-19
  to 04-21, then 2026-08-28 to 08-31, with a 129-day hole. Silence in the file
  is missing data, not evidence L5 was idle.

## 1. L5 never ran two markets in the same product lane at once

Pairwise overlap between episodes, split by lane (asset + duration):

| reading | overlap seconds | same-lane | cross-lane |
|---|---|---|---|
| trading (first fill -> last fill) | 201,861 | **0** | 201,861 |
| capital (first fill -> settlement) | 481,667 | **0** | 481,667 |

Checked directly as well: across all four lanes, zero consecutive same-lane
episodes overlap on either reading. The median gap between one window settling
and the next window in that lane getting its first fill is 15-168 s.

Part of this is structural — a BTC-5m window's life *is* five minutes and the
next begins after it, so the venue does not offer same-lane overlap. That is
exactly why the transferable number is lanes, not markets:

**L5's concurrency was a choice of how many product tracks to run. Inside every
track it was strictly one market at a time, held to settlement.**
(`max_markets_open_within_one_lane == 1`, all modes.)

## 2. How many tracks, and when

Time-weighted over active wall-clock, whole history:

| reading | max | mean | p50 | at 1 | at >=2 | at >=3 |
|---|---|---|---|---|---|---|
| trading | 4 | 1.44 | 1 | 64.0% | 36.0% | 8.2% |
| capital (money committed) | 4 | 1.78 | 2 | 49.2% | 50.8% | 25.5% |
| residual books only | 4 | 1.77 | 2 | 49.8% | 50.2% | 25.2% |

But the whole-history average hides the finding that matters:

| era | episodes | actions | gross $ | lanes | max conc | mean conc | time at >=2 | actions/episode p50 | dwell p50 |
|---|---|---|---|---|---|---|---|---|---|
| 2026-03 | 88 | 1,931 | 7,791 | 1 (BTC-15m) | **1** | 1.00 | **0.0%** | 21 | 588 s |
| 2026-04 | 128 | 3,338 | 22,189 | 1 (BTC-15m) | **1** | 1.00 | **0.0%** | 19 | 636 s |
| 2026-08 | 2,000 | 66,715 | 698,523 | 4 | **4** | 1.64 | **52.2%** | 5 | 140 s |

Weekly, the same step: every week from 2026-03-16 through 2026-04-20 is one
lane, max concurrency 1, 0% of time at >=2. Both August weeks are four lanes,
max 4, 51-56% of time at >=2.

## 3. Switching, and whether it left inventory open

| | Mar-Apr (1 lane) | Aug (4 lanes) |
|---|---|---|
| episodes | 216 | 2,000 |
| runs (consecutive actions in one market) | 216 | 10,757 |
| switches | 215 | 10,757 |
| switches leaving **open inventory** behind | **0** | 8,679 (**80.7%**) |
| visits per episode (mean) | **1.00** | 5.38 |
| actions per visit (p50) | 19.5 | 2 |
| seconds between runs (p50, whole history) | 3 | 3 |
| episodes revisited after leaving | 0 | 1,608 (72.6%) |

Early L5 opened a market, worked it to the end, and left — one visit, no
returns, nothing open behind it. Mature L5 interleaved: ~2 actions, move,
come back, roughly five visits per market, four fifths of its departures made
with inventory still open in the market it left.

Distinct markets traded per wall-clock bucket (whole history, "sprayed or
concentrated" with no interval assumption): 5-minute buckets p50 **2**, max 4;
15-minute buckets p50 5, max 8; 1-hour buckets p50 4, max 30.

## 4. Inventory completion

L5 flattened a market's inventory in **23 of 2,216 episodes (1.04%)**. Median
absolute final residual 25.6 shares early, 60.8 shares in August. Holding
residual to settlement is not our safety concession — it is what L5 did in
99% of its markets.

## 5. What this supports, and what it does not

* Concurrency 1 is **on-distribution for L5**, not a deviation from it: it is
  exactly what L5 did for its entire first two months and $30k of turnover,
  in one lane, one market at a time, one visit per market, hold to settlement.
  Our current live stage sits in that regime.
* L5's move to 2-4 came with ~30x the turnover ($22k in April -> $699k in
  August) and with much shallower per-market work (p50 19 actions/10 min ->
  5 actions/2.3 min). Concurrency there is a **consequence of running more
  product tracks**, not of putting two positions into one track.
* The evidence therefore says a future concurrency >1 should be shaped as
  *N tracks, one market at a time inside each*, and should follow turnover
  rather than lead it. It does **not** support spraying independent positions
  across a sports slate, and it does not by itself clear the $110 bankroll or
  the R3L execution bar — those remain the owner's separate (b) and (c) tests.
* Untestable from this file: what L5 quoted without trading, and anything at
  all about 2026-05 through 2026-07.
