# L5 Round 8 — PMUS Sequential Maker-Pairing Census

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · continues `5372240c`
**Mode:** read-only. Railway log reads + repo reads. No orders, deploys, services, spend, no engine, no change to replication infrastructure. Gate unchanged.

---

## Headline

**The mechanism you identified has already been measured by existing infrastructure, and it fails at the reachability step — not at fill mechanics, and not at edge.** Across 3,048 virtual first fills, **7 completions**. The system's own diagnostic names the cause:

> `"bottleneck": "REACHABILITY: most complements were never reached by the market. Look at first-leg price, not at fill mechanics."`

No new collection is needed to establish this. One correction to my own last proposal, and one scope caveat, below.

## 1. Your three corrections — accepted

**(a) $26–42/day is not a ceiling.** Withdrawn as a ceiling; it is a model illustration. It assumed the configured minimum edge, q=1, a fixed eligible-market count and a particular slot cadence. Actual edge could exceed 1¢, actual depth could exceed one contract, and achievable fills could be zero.

**(b) A shadow recommendation is not proof of executability.** Accepted. The adapter's `quote / side:NO / inventory_effect:+1 NO` establishes **modelled order semantics only** — not that the order would be accepted, posted, filled and settled on that specific sports contract. I reuse existing PMUS execution evidence below and propose no capability test.

**(c) My proposed next action was wrong, and you were right not to let me assume.** Verified: `pmus-ws-evidence-recorder` contains **exactly one slug** — `cpc-btc-updown-15m-2026-10-13-1630z` (1,945 observations). **Zero sports book rows.** It is a BTC recorder. A `yes_ask + no_ask` census there was impossible.

**(d) And the mechanism point is decisive.** The sports collector emits **one `bid`/`ask` pair per slug** (from `bestBid`/`bestAsk` — `src/pmus_sports_offers_hotfix.py:bbo_book_row_offers`). So on this single complementary book, `YES_ask + NO_ask = ask + (1 − bid) = 1 + spread`. **Simultaneous taker pairing costs more than $1 by construction** — it is not an empirical question, and it can only be profitable on a crossed book. A two-ask census would have measured nothing. The real mechanism is sequential maker pairing, as you said.

## 2. Where the sports evidence actually lives

| Recorder | Covers | Sports book rows? |
|---|---|---|
| `pmus-ws-evidence-recorder` | `cpc-btc-updown-15m` only | **No** |
| `l5-pmus-observer` | `cpc-btc-updown-15m` | **No** — and **failing**: `OSError [Errno 28] No space left on device: /state/l5-pmus-obs`, repeating every ~5s |
| `pmus-sports-forward-collector` | 2,601 sports markets | Book rows written to volume, **not to logs** (logs carry only aggregates) |
| **`pmus-shadow-breadth`** | **boxing, NFL, UFC, CFB** | **Yes — and it already measures the full sequential funnel** |

**Operational note (read-only observation, not actioned):** `l5-pmus-observer`'s volume is full and it has been erroring continuously. I did not modify it.

## 3. The sequential maker-pairing funnel — measured

From live `PMUS_SHADOW_BREADTH` (cycle 52,830, 2026-10-13 16:40:48Z). Its own labels: `order_entry: false`, `realized_pnl: null`, and fill semantics stated as *"virtual fills are decide_market's `possible` class: an upper bound from later trades through the resting price, queue position unproven — never a fill."*

### Funnel by selection tier

| Tier | Selection cycles | Distinct markets | Virtual first fills | Virtual extends | **Completions ≤1** | Residual opening markets | Completing markets |
|---|---:|---:|---:|---:|---:|---:|---:|
| T1_top8 | 24,046 | 557 | 2,956 | 3,498 | **7** | 60 | 4 |
| T2_9_16 | 1,369 | 52 | 88 | 98 | **0** | 23 | 0 |
| T3_17_32 | 147 | 24 | 4 | 7 | **0** | 4 | 0 |
| **Total** | **25,562** | **633** | **3,048** | **3,603** | **7** | **87** | **4** |

**Completion rate 7 / 3,048 = 0.230%. Unpaired residual rate 99.770%.**

### The independent execution ledger

```
total 2,402   resolved 2,011   VIRTUAL_MATCHED 56   NO_FILL 1,955   SUPERSEDED_BY_REQUOTE 391
p_completion_given_first_fill = 0.0278      exposure_hours = 164.29
risk_returned_units = 56.0                  risk_created_units = 0.0
```

**Two completion measures disagree and I am not resolving it by assumption:** the funnel's `virtual_completions_le_1` gives 0.230%, while `p_completion_given_first_fill` gives 2.78% (56/2,011). The field names suggest the former counts completions whose **combined pair cost was ≤ $1** and the latter counts **any** match, which would mean only 7 of 56 matched pairs were actually profitable. But `median_pair_cost` is reported as 0.9433 (T1), which points the other way. **The gap between "matched" and "matched below $1" is unresolved and is a required input before any capacity claim.** Flagging it rather than picking the favourable reading.

### Realistic fill bound — this is the binding constraint

```
resolved 1,956 | reached_our_price 63 | never_reached 1,893 | filled 55 | reached_but_unfilled 8
median_initial_gap_cents 2.0 | median_min_gap 2.0 | median_favorable_excursion 0.0
median_time_to_touch_s 116.8
```

**The market never reached our quoted complement price in 1,893 of 1,956 cases — 96.8%.** Conditional on being reached, fill was 55/63 = 87%. So fill *mechanics* are fine; **price reachability is the failure**. The complement sits a median 2¢ away and the median favourable excursion is **0.0¢** — the market simply does not come to us.

### Queue reality at each pair-cost arm

| Target pair cost | n | Trade-through | Rate | Median ticks from touch | **Median queue ahead** |
|---|---:|---:|---:|---:|---:|
| $0.980 | 6,730 | 277 | 4.12% | −3 | **274,406** |
| $0.985 | 6,730 | 296 | 4.40% | −2 | **219,385** |
| $0.990 | 6,730 | 1,144 | 17.00% | −1 | **98,707** |
| $0.995 | 6,730 | 2,818 | 41.87% | 0 | 0 |
| at touch | 6,730 | 3,274 | 48.65% | 0 | 0 |

This is the core economic trade-off, measured: **profitable pair costs sit 2–3 ticks inside the touch behind ~219k–274k units of queue.** Only at $0.995 (0.5¢ gross, before fees) does queue-ahead reach zero. The ≥1¢ gate and realistic queue position are close to mutually exclusive.

### Timing and the frozen horizon

`frozen_300s`: 56/2,011 filled, median 46.3s, `le_300s` 2.78%.
`research_1800s`: **337 additional fills after the frozen verdict**, median later fill 454.3s, `le_1800s` 19.54%.

The 300-second slot horizon discards roughly 6× more fills than it captures. Extending the horizon raises completion to ~19.5% — but at the cost of holding unpaired directional risk far longer, which is unpriced here.

### Distance bands — where completions actually live

| Band (ticks) | n | Fill rate 300s | Fill rate 1800s | Residual outstanding | Median flow $/min |
|---|---:|---:|---:|---:|---:|
| 0–1 | 21 | **57.1%** | 76.2% | 38 | 273.8 |
| 2 | 57 | **40.4%** | 64.9% | 125 | 361.1 |
| 3–4 | **1,617** | **0.37%** | 2.1% | 4,244 | 14.3 |
| 5–8 | 154 | 5.2% | 15.6% | 360 | 7.9 |
| >8 | 107 | 5.6% | 25.2% | 262 | 186.6 |

**The mass is in the wrong band.** 1,617 of 1,956 observations sit 3–4 ticks away with a 0.37% fill rate and 4,244 units of residual. The high-fill bands (0–2 ticks, 40–57%) hold only 78 observations.

**Predictor separation:** matched markets had median flow **$204.28/min** versus **$15.36/min** for no-fill — a 13× difference. Completions exist only in the highest-flow markets.

## 4. Ranked result — by sport

From `execution_cohorts_by_sport`. **Important scope caveat:** this funnel covers **NFL, UFC, boxing, CFB** — *not* the ITF/ATP/WTA tennis and cricket you prioritised. The sports-forward collector tracks tennis/cricket but writes books to its volume, not to logs, so tennis could not be measured this round.

| # | Sport | Resolved | p(completion \| first fill) | Median pair cost | Median time to fill | Exposure h | Sample sufficient | Verdict |
|---|---|---:|---:|---:|---:|---:|---|---|
| 1 | **CFB** | 27 | **18.52%** | **$0.84** | 46.4s | 1.94 | **No** | Best economics, smallest sample |
| 2 | **Boxing** | 151 | 4.64% | $0.97 | 117.9s | 12.30 | Yes | ~3¢ gross, low completion |
| 3 | **UFC** | 312 | 2.56% | $0.98 | 46.2s | 25.44 | Yes | ~2¢ gross |
| 4 | **NFL** | 1,521 | 2.37% | $0.9167 | 46.2s | 124.62 | Yes | Largest sample, 8¢ gross, 2.4% completion |
| — | ITF / ATP / WTA tennis | — | — | — | — | — | **Not measured** | Book data on volume only |
| — | Cricket (T20/Test) | — | — | — | — | — | **Not measured** | Test cricket also breaches the 4h horizon |

CFB's $0.84 median pair cost (16¢ gross) with 18.5% completion is the standout — on **27 observations and flagged `sufficient_sample: false`**. It is a lead, not a result.

## 5. Capacity and gate

**`UNMEASURABLE`.** No capacity figure is assigned, and I am not multiplying a per-unit edge by a volume figure — the round-5 error.

What is established: a real mechanism, a real universe, genuine per-sport pair costs ($0.84–$0.98), and a measured completion rate of **0.23%–2.78%** against **99.8%** unpaired residual, with **96.8% price non-reachability** as the named bottleneck and **~274k units of queue ahead** at profitable pair costs. What is not established: residual-inventory P&L (`risk_created_units: 0.0` — residual losses are **not priced** in this ledger), the matched-versus-matched-below-$1 discrepancy, maximum size beyond q=1, and settlement joinability.

Residual exposure is the unpriced term that decides the lane: 4,244 residual units outstanding in the 3–4 tick band alone, against 56 risk-returned units across 164 exposure-hours.

## 6. Next action, blocker, cost

**Biggest economic blocker:** **complement reachability, not fill mechanics or edge.** The system's own diagnostic says so. Profitable pair costs require quoting 2–3 ticks inside the touch, where ~219k–274k units sit ahead of us; at the touch, where queue is zero, the pair cost is ~$0.995 and the gross edge is ~0.5¢ — below the 1¢ gate before fees.

**Next concrete action (read-only, $0):** resolve the two unpriced terms that decide this lane, both from data already flowing in `PMUS_SHADOW_BREADTH`:
1. **Price the residual.** `risk_created_units` is 0.0 while 4,244 residual units sit outstanding. Join residual first legs to their market outcomes and compute realized loss per unpaired unit — the same term that killed Gemini and consumed 78% of the Polymarket gross.
2. **Resolve the completion discrepancy** between `virtual_completions_le_1` (7) and `VIRTUAL_MATCHED` (56) by reading `decide_market`'s definitions, so we know whether matched pairs are profitable or merely matched.

Only if both come back favourable is it worth extending the sports-forward collector's tennis book data off its volume — which would be the first justified collection change, and is **not** proposed now.

**Cost:** $0. All reads.

**Handoff to replication (informational, no change requested):** the measured bottleneck is complement reachability — median gap 2¢, median favourable excursion 0.0¢, 96.8% never reached — with fill conditional on reach at 87%. Queue ahead at $0.98 pair cost is ~274k units. Completions concentrate at 13× median flow ($204 vs $15/min). The 300s frozen horizon forgoes 337 later fills (19.5% by 1800s) at the cost of longer unpaired exposure. `risk_created_units` is 0.0, so residual losses are not currently priced anywhere in this ledger.
