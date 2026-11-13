# PMUS pivot check: sports spreads and liquidity-incentive pools (2026-11-13)

This was a read-only check. It used only public, unauthenticated GETs on `gateway.polymarket.us` (User-Agent `research-client`, at most 2 requests per second) plus files already in the repo. No order was placed, no credential was used, and nothing was deployed.

Labels used in this file:
- **REPLAY**: recorded data played back.
- **SNAPSHOT**: an order book read live today.
- **PUBLISHED**: venue program parameters.

No number in this file is realized, filled or credited.

Context: maker pair-making on BTC15m loses without a rebate (`analysis/l5/btc15m_pair_gate_measure.py`), and this account is maker Tier 1, which gets no maker rebate.

---

## A. Sports

### A1. Recorded PMUS sports top-of-book in the repo

| artifact | what | markets | dates / interval | settled outcome? |
|---|---|---|---|---|
| `data/pmus/sports_opportunity_2026-09-10/snapshot_{A,B}.json.gz` | full books | 340 game markets (UCL-heavy) | 2026-09-10, two snapshots at 18:15Z and 18:25Z | **no** |
| `data/transfer_backtest/pmus_live_sample.jsonl` | top of book | 2 MLB moneylines | 2026-09-02, 18:23-18:35Z, ~6 s rows | **no**; covers 12 minutes, not the market to its close |
| `data/pmus/sports_incentives_2026-09-10/depth.json` | book depth | 61 markets, 1 per program | a single instant | no |
| `data/pmus_book_tape/manifest_pmus_book_20260917.json` | manifest + sweep result only | 325 markets, 34,127 rows, 150 min | 2026-09-17 14:14-17:15Z | **raw tape is not in the repo or on any local branch** (only the hashes are) |
| `analysis/l5/live_tape_2026-09-13/r5ft_live_tape.jsonl` | our own probe's order/settlement log | ~10 | 2026-09-13 | yes, for the probe markets, but it is an order log, not a book tape |

**There is no recorded sports book tape that runs from a market's first row to its close with a settled winner.** Because of that, the A3 pair-gate replay (depth 0/2/3/5, target 0.99-0.90, static and chase completion) **cannot be run**. Only spread evidence is available.

Spreads in the recorded data (`recorded_sports_spreads.json`, REPLAY of snapshots):
- 2026-09-10, 423 two-sided books: median 1c, p75 2c, p90 9c. 13% were at or above 3c and 12% at or above 5c. The wide tail is mostly MLS and CS2 stub books with a 97-98c spread.
- 2026-09-02, MLB: median 0.5c (0.1c tick). One market was at or above 3c in 9% of rows.

### A2. Live SNAPSHOT: 60 open sports markets, 3 rounds about 1 minute apart (15:54-15:57Z, a Friday)

Sample: 25 in-play and 35 pre-game (starting within 12 hours), spread round-robin across leagues (`raw/sports_book_snapshots.json`). The spread is the YES ask minus the YES bid. That is the same number for the NO side, because the NO bid is 1 minus the YES ask.

| group | quoted obs | median | p75 | p90 | >= 3c | >= 5c |
|---|---|---|---|---|---|---|
| ALL (books with spread < 50c) | 162 | 1c | 3c | 6c | 27% | 14% |
| ALL, tradeable books only (spread < 20c) | 149 | 1c | 2c | 3c | 21% | 6% |
| in-play (< 20c) | 59 | 1c | 2c | 5c | 15% | 10% |
| pre-game (< 20c) | 90 | 1c | 2c | 3c | 24% | 3% |
| tennis / soccer / ATP / ITF / NBA / CFB / CBB-W / esports | — | 0.5-1c | 1-2c | 1-3c | 0-11% | 0% |
| hockey (AHL, SHL, KHL) | 9 | 3c | 3c | 3c | 78% | 0% |
| darts / e-basketball / 3x3 / minor table tennis | — | 3-5c | — | 5-7c | 33-100% | 13-67% |

Other observations:
- 18 observations were unquoted (one side missing, or a spread of 50c or more).
- Another set were stubs with 36-47c spreads: UTR events dated 2026-11-09, cricket ADT10, boxing, and table tennis in Moldova.

**Markets at or above 3c in every round, excluding stubs: 9 of 60.**
- Four of the nine have less than 2 contracts at the touch (ATP doubles, darts, table tennis, e-basketball). They are wide because nobody is quoting them, not because a maker is getting paid.
- The other five are pre-game minor-league books (AHL, SHL, Austrian basketball, Ligue 2) at 3-4c with 30-1,600 contracts at the touch, and the book did not move across the 3 rounds.
- Median of the smaller touch size: 114 contracts in-play, 12 pre-game.

**A verdict: no evidence of a positive-expectancy path, and the replay data is missing.**
- In every liquid sports book, the spread is the same 1c (or less) as BTC15m.
- Wide spreads occur only in thin or stale books with no visible flow.
- A pair joining the touch in a 3c book would cost 0.97, but the BTC15m result shows lone legs, not the pair cost, decide P&L. That cannot be measured here without a tape that runs to settlement.

The missing measurement is a recorded sports book tape (minor hockey, basketball and darts, both pre-game and in-play) run to settlement. Collecting one would require a new recorder, which this task did not authorize.

---

## B. Liquidity incentive programs (PUBLISHED)

### B0. The earlier walk was broken, and it hid almost everything

`GET /v1/incentives` returns 100 rows per page. Its `nextPageToken` brings back page 1 again: `raw/inc_p1.json` and `raw/inc_p2.json` are identical, and the token decodes to the same cursor with only a new timestamp. `offset`, `page`, `limit` and `pageSize` are all ignored. `category=` works (FIN, TECH, MAC, POL, CUL, CRY, SPR, CLI, GEO, SCI) but is still capped at 100 rows.

So the "40-page walk, no btc/cpc" line in `PMUS_REBATES_INCENTIVES_20261113.md` came from **one page read 40 times**.

The method that works is to repeat `symbols=<slug>` (joining the slugs with commas returns nothing). I listed all 24,358 open markets with `/v1/events?active=true&closed=false&limit=500&offset=N` (10 pages) and queried them in batches of 60. That took 406 requests, with 0 errors. The result is 18,292 unique rows, deduplicated by (marketSlug, programId, start), across **131 programs** (`raw/incentives_census.json.gz`). The endpoint is active-only.

### B1. Active programs by category

**The pool basis is not stated by the API, and it changes the answer by 10-100x.** A shared basis is supported by two texts in the repo:
- The doc calls a daily pool "a per-day pool **on an event**".
- An earlier repo quote of polymarket.us/rewards (September 2026) says pools are "shared across the program's markets — never summed per market".

The basis is therefore reported three ways:

| reading | rewardPool is paid over |
|---|---|
| **PER_MARKET** | each market (an upper bound) |
| **PER_EVENT** | shared by the program's markets within one event (the doc's wording; my central reading) |
| **PER_PROGRAM** | shared by every market in the program |

| category | open mkts | mkts w/ program | daily pools $/day PER_MARKET / **PER_EVENT** / PER_PROGRAM | game-period pools $ (early+day_of+live) PM / **EV** / PROG |
|---|---|---|---|---|
| sports | 14,956 | 10,873 | 251,775 / **14,208** / 1,452 | 185,427 / **153,897** / 15,902 |
| crypto | 1,212 | 1,197 | 128,628 / **5,686** / 2,229 | — |
| politics | 4,786 | 3,641 | 121,019 / **9,145** / 877 | — |
| culture | 2,395 | 1,030 | 44,865 / **3,350** / 115 | 7,500 / 1,250 / 250 |
| finance | 114 | 104 | 15,750 / **4,400** / 2,600 | — |
| macro | 446 | 353 | 15,220 / **1,960** / 450 | — |
| climate | 173 | 100 | 500 / 100 / — | 16,650 / 2,775 / 185 |
| technology / geopolitics / science | 276 | 110 | 5,320 / 1,150 / 175 | — |

Distributions of the per-row parameters (from `incentive_census_summary.json`):

| category | pool per row | targetSize | discountFactor | maxSpread |
|---|---|---|---|---|
| sports, daily | median $10 (max $600) | median 5,600 (p25 3,200, p75 11,000, max 625,000) | median 0.30 (0.10-0.90) | on 3% of rows, 0.06-0.07 |
| sports, live | median $30 (max $5,000) | (same row as above) | (same) | (same) |
| sports, day_of | median $10 | (same) | (same) | (same) |
| crypto | median $60 (p75 $150, max $400) | median 500 | 0.3-0.5 | on 96% of rows, 0.03-0.06 |
| politics | median $2 (max $500) | median 2,000 | 0.4 | on 85% of rows, 0.06 |

**Sports and crypto both have programs.**
- Crypto programs cover BTC, ETH, SOL and XRP above/range markets (hourly and weekly), hit-price markets, and **up/down 1h and 1d** (`crypto_btc_updown_1d` pays $400 per day on a single market).
- **There is no program on the BTC 15m up/down markets.**
- Sports programs cover futures, moneyline live/day_of/early periods (ATP/WTA live $184 per match, doubles $70, esports $80, table tennis $17), and the rugby World Cup ($600 per day).
- Every row has status `active`. 525 rows sit on markets in `INSTRUMENT_STATE_CLOSED`.

### B2. A small resting quote at the touch: 20 high-pool markets that are paying now, 4 book snapshots 60 s apart (16:08-16:12Z)

Method (`incentive_share_estimate.py`, `incentive_interpretations.py`): put Q contracts at the best price on each side. On each side, walk out from the best price, adding up resting size, until the program's targetSize is reached. Everyone else's score on that side is the sum of DF^ticks × size over that range; a side below targetSize scores 0. Our share is Q / (Q + others' score). Each side carries half the period pool, because the venue normalizes each side independently. The book is held static as observed, at 100% duty, with no fills.

Example arithmetic, using `apdc-kimpow-nvda-vp-2026-11-30` (pool $100/day, DF 0.3, target 500, book 0.38/0.39):
- Q = 10 contracts on each side. Others' discounted score is about 69 on the bid side and about 51 on the ask side, so our shares are 0.127 and 0.164.
- PER_MARKET: $100/2 × (0.127 + 0.164) = **$14.58/day**.
- PER_EVENT (M_event = 2): **$7.29/day**.

| market | period | pool $ | M ev/prog | Q=10 bid/ask share (4 rounds) | Q=10 $/period PM / **EV** / PROG | Q=50 PM / **EV** | capital Q10/Q50 |
|---|---|---|---|---|---|---|---|
| apdc-kimpow-nvda-vp-2026-11-30 | daily | 100 | 2/2 | .127/.164 stable | 14.58 / **7.29** / 7.29 | 45.9 / **22.9** | $9.9 / $49.5 |
| apdc-kimpow-nvda-vp-2026-12-31 | daily | 100 | 2/2 | .045/.052 | 4.85 / **2.43** / 2.43 | 20.3 / **10.2** | 9.9 / 49.5 |
| cpcsol-sol-updown-1d | daily | 150 | 1/3 | .015-.036/.039-.064 | 5.82 / **5.82** / 1.94 | 24.7 / **24.7** | 9.6 / 48 |
| cpceth-eth-updown-1d | daily | 150 | 1/3 | .01-.03/.002 | 1.77 / **1.77** / 0.59 | 8.1 / **8.1** | 9.9 / 49.5 |
| cpausp-gold-updown-1d | daily | 1000 | 1/1 | .002/.002-.007 (**0.29 one snapshot earlier**) | 2.57 / **2.57** | 12.7 / **12.7** | 9.8 / 49 |
| cpc-btc-updown-1d | daily | 400 | 1/1 | .001-.002/.002-.005 | 0.78 / **0.78** | 3.85 / **3.85** | 9.9 / 49.5 |
| cpagsp-silver-updown-1d | daily | 750 | 1/1 | 0/0 (target not met) | 0 | 0 | — |
| tec-rlwc (rugby WC) ×2 | daily | 600 | 2/2 | ~0 | 0.06-0.09 | 0.3-0.4 | — |
| enwc-uspres-nom-dem-2028-abdels | daily | 500 | 19/177 | .008/.095-.106 | 26.4 / **1.39** / 0.15 | 97.5 / **5.13** | 9.9 / 49.5 |
| enwc-…-aleocc | daily | 500 | 19/177 | 0/0 | 0 | 0 | — |
| aec-atpdb-locklock-mackinnedunc (live) | live | 70 | 1/28 | **.79/.85 → .26/.11 → .42/.25 → .75/.96** | 38.4 / **38.4** / 1.37 per match | 54.9 / **54.9** | 9.5 / 47.5 |
| aec-atpdb-dudapereir-pankinperego (live) | live | 70 | 1/28 | .04/.14 | 6.35 / **6.35** / 0.23 per match | 21.9 | 9.9 / 49.5 |
| ATP live ×2, ITF live ×2, CS2 live ×2, UTR | live | 50-184 | 1/37-149 | 0-.02 | 0-1.6 / **0-1.6** / ≈0 | 0-6.7 | — |

How to read these numbers:

1. **The figures are one book at one moment and assume nobody reacts.** The gold 1d bid side gave a 0.29 share in one snapshot ($146/day) and 0.002 four minutes later ($2.57). Professional makers size exactly at the target (5,001 contracts at the touch against a 5,000 target) and sit one tick back. If we take the touch, they can step up and dilute our share.
2. **The doubles-tennis number ($38 per match) comes from an empty in-play book.** Its touch held 0.24-8 contracts. In-play tennis reprices on every point, so a quote resting at the touch in that book is the order that gets picked off.
3. **Rewards below $1.00 per period are not paid.** Under PER_PROGRAM, almost every row is under $1. Under PER_EVENT, 10 of the 20 clear $1 at Q=10 (several only barely).
4. **Credited so far: $0.00.** `PMUS_INCENTIVE_BREAKEVEN.md` records that liquidity programs have never credited this account. Nothing here is credited or realized.
5. **Fill risk.** A quote at the touch fills exactly when the price moves through it, which is adverse selection. The candidates differ in how fast they move:
   - Daily up/down markets (gold, BTC, ETH, SOL) move with the underlying.
   - Live tennis and esports move on every point or round.
   - The NVDA-VP and 2028-nomination markets are long-dated and slow.

   No tape here measures fill frequency or the post-fill move for any of these markets, so **no fill-loss number is given.** Break-even framing instead: at Q=10, the PER_EVENT $7.29/day on NVDA-VP covers 7.29 / (10 × 0.01) = **73 one-tick adverse moves per day on a full Q=10 fill**, or about 15 five-tick moves. For the 1d crypto and metals markets ($0.78-5.82/day at Q=10), a single 5-10c move on one Q=10 fill ($0.50-1.00) uses up most or all of a day's estimate.
6. **maxSpread semantics are unconfirmed.** I did not fetch the current doc, because this run was limited to gateway hosts. An order at the best price is 0 ticks from best and should qualify under a distance-from-best reading. Under a distance-from-midpoint reading, it qualifies when the spread is no more than 2 × maxSpread, which every crypto and metals candidate above meets.

**B verdict: plausible, but unproven. The data is insufficient to call it positive-expectancy.**
- At the PER_EVENT reading, a handful of slow markets (NVDA-VP 11-30 and 12-31, SOL/ETH 1d, the 2028 nomination long-shot ask) would pay about $1-7/day each at Q=10 (about $10 of capital per market).
- That gives roughly $15-25/day across 6-8 markets with about $60-80 of capital. This is SNAPSHOT × PUBLISHED, uncredited, and assumes a static book.
- That scale is plausibly larger than adverse selection in the slow markets, and plausibly smaller in the 1d crypto and metals markets. Fill frequency has not been measured in any of them.
- It is not on the L5 path:
  - The payoff is for resting and not trading, which is the opposite of how the probe's duty cycle is designed.
  - It needs many concurrent markets.
  - It conflicts with the one-market, one-order learning-stage rule.
  - Pursuing it is a strategy change and needs owner authorization.

Three facts would settle B:
- **Which pool basis applies**, ideally from a single credited payout.
- **The fill rate and post-fill move** for a touch quote in the slow candidates, measured by a read-only book and trade recorder.
- **Whether depth reacts when we join the touch.**

---

## Files
- Scripts:
  - `sports_spread_snapshot.py` and `sports_spread_report.py` produce `sports_spread_summary.json`.
  - `recorded_sports_spreads.py` produces `recorded_sports_spreads.json`.
  - `incentive_walk.py` runs the symbols census and writes `raw/incentives_census.json.gz`.
  - `incentive_census_report.py` produces `incentive_census_summary.json`.
  - `incentive_share_estimate.py` writes `raw/incentive_share_books.json`.
  - `incentive_interpretations.py` produces `incentive_estimates.json`.
- Raw files in `raw/`:
  - `sports_events_all.json.gz` and `all_events_slim.json.gz` (the event listings).
  - `probe_live.json.gz`.
  - `inc_p1.json` and `inc_p2.json`, which show the pagination loop.
  - `inc_cry_p1.json` (the CRY category page).
