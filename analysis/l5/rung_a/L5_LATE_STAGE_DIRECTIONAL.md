# Late-stage directional test: is L5 stacking the winner?

**ANSWER: No. Buying the favored side late LOSES money, and L5 tilts toward
the UNDERDOG as settlement approaches, not the favourite.**

Analysis and shadow only. **R6 is untouched.**

## Data

11,331 deduped L5 BTC-15m BUY fills over 330 markets; 11,108 fills over 304
markets join to real settlement from `markets.jsonl` `outcomePrices` with
`umaResolutionStatus == resolved`. Window end = slug epoch + 900 s.
"Favored" means the own-side price paid was > 0.50 — the market's own
pricing at that instant, never the outcome. **Settlement is used only as an
outcome label, never as a feature or a decision input.**

## Buying intensity and side mix

| bucket | fills/s | shares/s | favored share |
|---|---|---|---|
| 900–600s | 11.20 | 228 | 49.4% |
| 600–300s | 12.30 | 244 | 40.2% |
| 300–120s | 15.76 | 359 | 46.3% |
| **120–60s** | **16.50** | **433** | 44.8% |
| 60–30s | 12.73 | 292 | 36.1% |
| 30–15s | 4.13 | 117 | 38.7% |
| 15–5s | 1.10 | 27 | **18.2%** |
| <5s | 0.00 | 0 | — |

Intensity **peaks at 120–60s and then collapses**. L5 places **zero** fills
inside the last 5 seconds. The favored share **declines** into settlement
(49.4% → 44.8% → 36.1% → 18.2%).

**He is not stacking the winner. He is increasingly buying the underdog.**

## Expectancy by bucket

| bucket | group | n | mkts | avg px | win | E/share | E/$ |
|---|---|---|---|---|---|---|---|
| 120–60s | favored | 444 | 125 | 0.8280 | 77.6% | −0.0474 | **−1.1%** |
| 120–60s | underdog | 546 | 115 | 0.1434 | 14.9% | +0.0012 | **+14.3%** |
| 60–30s | favored | 138 | 44 | 0.8588 | 86.4% | +0.0243 | **−8.5%** |
| 60–30s | underdog | 244 | 67 | 0.1404 | 24.2% | +0.0959 | **+29.3%** |
| 30–15s | favored | 24 | 13 | 0.8550 | 95.7% | +0.1265 | +7.5% |
| 30–15s | underdog | 38 | 14 | 0.1165 | 42.9% | +0.2847 | +178.5% |
| 15–5s | favored | 2 | 1 | 0.9251 | 100% | +0.0750 | +8.1% |
| 15–5s | underdog | 9 | 2 | 0.0810 | 0% | −0.0767 | −100% |

The favored side wins often and **still loses money**, because the price
already contains the win rate: 77.6% wins at an average 0.828 is a losing
trade. The underdog's positive E/$ is real but rests on small numbers in the
later buckets.

## Favored buys by own-side price (<120s)

| price | n | mkts | avg px | win | E/$ |
|---|---|---|---|---|---|
| 0.60–0.70 | 80 | 32 | 0.6401 | 55.4% | −7.0% |
| **0.70–0.80** | 81 | 33 | 0.7517 | **46.8%** | **−25.6%** |
| 0.80–0.90 | 108 | 41 | 0.8478 | 78.8% | −8.8% |
| 0.90–0.95 | 93 | 37 | 0.9201 | 96.6% | **+5.5%** |
| 0.95+ | 187 | 83 | 0.9694 | 98.9% | **+2.4%** |

Only the near-certain buckets are positive. **0.70–0.80 is badly mispriced
against the buyer — a 46.8% win rate at a 0.75 average price.**

## Same-side vs residual-reducing

| bucket | same side as residual | reduces residual |
|---|---|---|
| 120–60s | n=546, win 40.5%, E/$ +2.4% | n=441, win 44.9%, E/$ −0.1% |
| 60–30s | n=205, win 44.7%, E/$ +3.3% | n=176, win 48.8%, E/$ −5.6% |
| 30–15s | n=39, win 63.2%, E/$ +55.5% | n=23, win 65.0%, E/$ +3.2% |

Extending the residual is not worse than reducing it — mildly better on E/$
— which is consistent with the earlier finding that residual carriage is
genuine L5 behaviour rather than a defect.

## Earlier in the window, for comparison

| bucket | favored n | avg px | win | E/$ |
|---|---|---|---|---|
| 900–600s | 1662 | 0.6548 | 68.1% | +4.0% |
| 600–300s | 1482 | 0.7542 | 79.7% | +7.1% |
| 300–120s | 1314 | 0.8097 | 81.8% | +3.5% |
| **120–60s** | 444 | 0.8280 | 77.6% | **−1.1%** |

**Favored-side expectancy is positive early and turns negative late.**
Whatever edge exists in buying the favourite is a mid-window phenomenon that
decays before settlement.

## THE COUNTERFACTUAL: +1 q10 clip on the favored side

Assumptions, stated because they decide the answer: taker at L5's observed
fill price **plus one tick** (no touch fill); frozen fee model
`0.0695 · px · (1−px)` per share; **one clip per market per bucket**, so a
market with 40 late fills cannot be counted 40 times; a clip pricing ≥ 1.00
all-in is skipped as unexecutable.

| bucket | clips | cost | fees | net | per clip | return on cost |
|---|---|---|---|---|---|---|
| 120–60s | 115 | $974.14 | $8.66 | **−$42.80** | −0.3722 | **−4.39%** |
| 60–30s | 41 | $356.99 | $2.66 | +$0.36 | +0.0087 | +0.10% |
| 30–15s | 12 | $109.20 | $0.56 | +$0.24 | +0.0201 | +0.22% |
| 15–5s | 1 | $9.40 | $0.04 | +$0.56 | +0.5608 | +5.97% |
| <5s | 0 | — | — | — | — | — |
| **ALL <120s** | **124** | **$1054.54** | **$9.20** | **−$43.74** | −0.3527 | **−4.15%** |

Sensitivity — the loss is **not** an artifact of my execution assumption:

| variant | clips | net | return on cost |
|---|---|---|---|
| taker +1 tick, with fees (reported) | 124 | −$43.74 | −4.15% |
| taker +1 tick, **no fees** | 124 | −$34.54 | −3.28% |
| **at L5's exact price**, with fees | 127 | −$31.66 | −2.95% |
| at L5's exact price, no fees | 127 | −$21.84 | −2.04% |
| mid-window control 300–120s, +1 tick | 206 | −$49.17 | −2.86% |

Even paying **exactly what L5 paid** and charging **zero fees**, the strategy
loses 2.04%. The tick and the fees make a losing trade worse; they do not
create the loss.

## Conclusions

1. **L5 does not stack the winner.** Favored share falls into settlement and
   he stops entirely inside 5 s.
2. **Adding a late favored q10 clip destroys money** — −4.15% on $1,054
   deployed, and still negative under every relaxation tested.
3. The only positive favored buckets are 0.90+ where the edge is ~2–5% and
   one adverse tick erases it.
4. If any late-stage directional edge exists in this data it is on the
   **underdog**, not the favourite — but the later buckets are thin (30–15s
   underdog is n=38 across 14 markets) and I would not act on that without a
   far larger sample and a book-aware fill model.

**Recommend no change.** This does not alter frozen LOCK5 and does not touch
R6.
