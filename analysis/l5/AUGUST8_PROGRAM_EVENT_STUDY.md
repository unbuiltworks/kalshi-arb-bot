# August 8, 2026 — what changed, and what L5 did about it

OWNER P0: "determine exactly what Polymarket launched/changed then, because
that's when L5's economics apparently exploded." Then an event study, ±14
days.

## 1. What changed: TWAP settlement + a $1,000,000 August liquidity-reward pool

Polymarket moved crypto up/down markets to **TWAP settlement on 2026-08-07**
(30-second TWAP for 5-minute markets, 60-second for 15-minute and 4-hour) and
attached **$1,000,000 of liquidity rewards through August**, payable *only* on
the crypto 5m / 15m / 4h markets that settle on TWAP, across BTC, SOL, ETH,
HYPE, XRP, BNB and DOGE.

L5's first-ever `REWARD` credit is dated **2026-08-08** — the day after.

### Pool allocation (published)

| horizon | pool | BTC | SOL/ETH/HYPE/XRP each | BNB/DOGE each |
|---|---:|---:|---:|---:|
| 5-minute | $550k | $300k | $50k | $25k |
| 15-minute | $350k | $225k | $25k | $12.5k |
| 4-hour | $100k | $50k | $10k | $5k |

### Scoring — and this is the part that matters most

```
S(v,s) = ((v - s) / v)^2 * b
```

`v` = maximum qualifying spread, `s` = the order's spread from the adjusted
midpoint, `b` = in-game multiplier. Then per market
`Q_normal = Q_min / sum_n(Q_min)`, summed over `Q_epoch = sum_{u=1..10080}`,
and finally `Q_final = Q_epoch / sum_n(Q_epoch)`.

- **Two-sided:** with the midpoint in [0.10, 0.90] the score takes the
  **minimum** of the two sides; one-sided liquidity still scores but divided
  by `c = 3.0`. Outside that band, **two-sided only**.
- **Sampling: 10,080 samples per epoch — once per minute over 7 days.**
- **Payout: daily, at midnight UTC.** This matches our observed credit
  cadence exactly (94 of 109 MAKER credits, 18 of 19 REWARD credits at hour 0).
- Minimum payout $1 per address per day; each market sets its own minimum
  order size and maximum qualifying spread.

**The reward is paid for RESTING, sampled per minute, scored quadratically on
tightness, and requires two-sided quotes for full credit. It is not paid for
fills.** That is the paid-liquidity-provider thesis in
`L5_OPERATING_MODEL.md`, written down by the venue.

## 2. Why the response was breadth and not size — it is in the formula

`Q_normal` is a **share of each market**, and `Q_epoch` sums those shares
across minute samples. Two consequences fall straight out:

- Adding size in a market you already lead has **sharply diminishing** return:
  your share of that market is already near its ceiling.
- Adding a **market** adds a fresh, independent share draw at full weight.

So under this scoring, breadth scales rewards roughly linearly while size
saturates. PATIENCE -> FREQUENCY -> SIZE -> CONCURRENCY is not merely
something L5 happened to do; it is the gradient of the reward function he was
being paid by.

## 3. Event study, ±14 days (7,010 windows, 2026-07-25 .. 2026-08-22)

| metric | PRE (Jul 25–Aug 7) | POST (Aug 8–22) | ratio |
|---|---:|---:|---:|
| windows/day | 164.2 | 371.7 | **2.26x** |
| buy $/day | 58,981 | 182,110 | **3.09x** |
| fills/window | 37.86 | 44.37 | 1.17x |
| buy $/window | 359.24 | 489.92 | 1.36x |
| **first_size (median)** | **32.666** | **30.848** | **0.94x** |
| pair cost (median) | 0.9811 | 0.9792 | 1.00x |
| opposite-leg gap (median) | 54.0 s | 53.0 s | 0.98x |
| window duration (median) | 193 s | 180 s | 0.93x |
| entry TTE (median) | 285.5 s | 282.0 s | 0.99x |
| matched % | 67.9 | 74.7 | 1.10x |
| residual $/day | -715 | -3,370 | 4.71x |

**The per-decision policy did not move.** Pair cost, completion patience,
window duration and entry timing are flat to three digits, and the median
first order got *smaller*. Everything that grew is count: more markets, more
fills per market, more capital in aggregate.

Composition moved onto the eligible set, exactly as the program would
predict:

- asset mix (windows): **BTC 100% -> BTC 70.5% / ETH 29.5%** — ETH is a
  rewarded asset; L5 had never traded it in the prior fortnight
- horizon mix: **5-minute 62.6% -> 73.8%**, toward the $550k pool
- capital stayed 96% BTC, so ETH was entered at token size — consistent with
  buying *presence* in a second rewarded market rather than taking risk in it

### The residual got worse, faster than the book grew

Residual drag went 4.71x on 3.09x capital. Scaling breadth did not scale the
residual problem proportionally — it scaled it *worse*. That is our own open
risk question showing up in L5's data.

## 4. Do the program terms explain the money? Mostly — with one gap

| stream | PRE $/day | POST $/day | ratio |
|---|---:|---:|---:|
| MAKER_REBATE | 179 | 1,192 | 6.65x |
| TAKER_REBATE | 70 | 159 | 2.29x |
| REWARD | 0 | 1,079 | new |
| **total** | **249** | **2,430** | **9.76x** |
| (volume) | 58,981 | 182,110 | 3.09x |

**REWARD checks out.** BTC 5m + 15m pools are $525k over 31 days ≈
**$16,935/day**; L5's $1,079/day is **6.4%** of it. That is a large but
non-dominant maker share, and it is self-consistent.

**TAKER_REBATE checks out** — it grew 2.29x on 3.09x volume, i.e. slower,
which is what a maker-led book should do.

**MAKER_REBATE does not fully check out. Flagging it rather than explaining
it away.** It grew **6.65x on 3.09x volume** — 2.15x faster than the book.
Volume growth alone cannot produce that. Candidates, none yet evidenced:
the maker-rebate rate itself changed alongside the TWAP launch; the rebate is
also pool-shared rather than per-fill; or the market mix shifted toward
higher-rebate instruments. **UNRESOLVED.**

## 5. Capital occupancy — turnover is not bankroll

OWNER: "don't translate $175K/day deployed into 'we need $175K'."

Correct, and by a wider margin than assumed. Sweeping every window's capital
from first fill to market resolution:

| | PRE | POST | ratio |
|---|---:|---:|---:|
| turnover $/day | 58,981 | 182,110 | 3.09x |
| **peak concurrent $** | **2,117** | **4,612** | 2.18x |
| **mean concurrent $** | **680** | **1,830** | 2.69x |
| turns/day | 27.9 | 39.5 | 1.42x |

**Maximum peak concurrent capital across the entire ±14-day window: $9,672.**

$182,110/day of turnover ran on roughly **$4.6k of working capital**, because
5-minute markets resolve and recycle about 40 times a day. The bankroll
implied by L5's reward-era operation is **four figures, not six**. The binding
constraint is reward capacity and execution quality, not capital.

Caveat: capital is released at market resolution in this model. If
redemption lags resolution, true occupancy is higher, so these are lower
bounds.

## 6. What we cannot see, and are not reconstructing

- **Unfilled resting orders are not observable.** The reward is paid for
  resting depth sampled per minute, and our data is *fills*. We can see what
  L5 traded; we cannot see what he quoted and never filled — which is
  precisely the quantity the program pays for. Every statement above about
  quoting behaviour is inferred from fills and is marked as such. No resting
  book is reconstructed anywhere in this document.
- **Maker/taker mix per window** is not carried in this ledger; the
  `isAggressor` field exists on our own PMUS fills, not on this harvest.
- 15 post days vs 11 pre days, one venue, one lane (100% crypto up/down).
  Association, not causation.

## 7. Can any accessible venue reproduce this environment?

The same *shape* of program exists at Gemini today — per-minute snapshot
scoring, spread weighting, two-sided multiplier, daily payout, uptime
requirement — which is why the transfer question is worth pressing. The
difference is scale and competition:

| | Polymarket Aug 2026 | Gemini (measured, live) |
|---|---|---|
| BTC 5m+15m daily pool | ~$16,935/day | **$11.36 per event/day** |
| qualifying makers observed | L5 at 6.4% share | **zero** |

Polymarket's August pool was roughly three orders of magnitude larger. Gemini
has almost no pool but also, on our own live read, **no qualifying
competition** — a different bet entirely: not "capture a share of a big pool"
but "be the only participant in a small one." Neither is established as
profitable for us and neither is authorised here; this is scoping, not a
recommendation.

The August program was explicitly "through August". Whether anything
equivalent is live now on Polymarket International is **not yet established**
and is the obvious next read.

## Sources

- https://docs.polymarket.com/programs/liquidity-rewards
- https://cryptobriefing.com/polymarket-twap-upgrade-liquidity-rewards/
- https://www.kucoin.com/news/flash/polymarket-to-use-twap-for-settlement-adds-1m-liquidity-rewards-in-august
