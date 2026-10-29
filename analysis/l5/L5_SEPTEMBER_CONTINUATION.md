# Why L5 is still trading crypto Up/Down at scale — answered

OWNER P0. Harvest: `data/l5/sep2026/l5_activity_sep2026_raw.json`,
**144,394 raw rows, zero window errors**, span
**2026-09-01T00:00:10Z → 2026-09-10T15:01:46Z**, fetched 2026-09-10T15:02:12Z.
Appended alongside the Apr–Aug canonical harvest, which is untouched.

## Answer: the incentives did not disappear. One of three streams ended.

| stream | Aug 8–30 $/day | Sep 1–9 $/day | ratio | status |
|---|---:|---:|---:|---|
| **MAKER_REBATE** | 1,192 | **1,081** | **0.91x** | **CONTINUES, daily, unbroken** |
| TAKER_REBATE | 159 | 121 | 0.76x | continues, **decaying** |
| REWARD | 1,079 | 914 | 0.85x | **ENDED — last payment 2026-09-06** |
| **all incentives** | **2,430** | **2,117** | **0.87x** | |

**L5 is still trading crypto up/down at scale because MAKER_REBATE — a
separate programme from the August TWAP liquidity rewards — is still paying
him about $1,100 a day.** It was never dependent on the $1M pool.

### The three programmes have distinct, stable payout clocks

Every day in the harvest: **REWARD ~00:00–00:05 UTC, TAKER_REBATE ~00:10,
MAKER_REBATE ~00:45.** Three schedules, three programmes.

### REWARD's exact end

```
2026-09-06 00:00:10  REWARD  1515.7071   <- last one
2026-09-07 00:10:12  TAKER_REBATE  85.4691
2026-09-07 00:45:31  MAKER_REBATE 1077.8461
...
2026-09-10 00:45:54  MAKER_REBATE 1380.6088
```

The August programme paid six trailing days into September and stopped. Since
then, **four consecutive days of MAKER + TAKER only** — and MAKER's largest
single payment of the month ($1,380.61) came on **Sep 10, four days after
REWARD ended**.

## CORRECTION: "PMI today = $0.00 per $1 of capital" was wrong

I reported that the reward environment was worth **$0.00** today, from a CLOB
read showing `rates: null` on all 21 up/down families.

The `rates: null` read was accurate **but it only describes the market-level
liquidity-rewards programme — the REWARD stream.** MAKER_REBATE and
TAKER_REBATE are account-level programmes that do not appear in a market's
`rewards` block at all. The larger of the two never showed up in what I
measured, and I generalised from one stream to all three.

| environment | incentive $/day | mean concurrent $ | **$ per $1/day** |
|---|---:|---:|---:|
| PMI Aug 8–30 (3 streams) | 2,430 | 1,830 | 1.33 |
| PMI Sep 1–9 (3 streams) | 2,117 | 1,523 | **1.39** |
| **PMI Sep 7–10 (REWARD gone)** | **~1,280** | 1,523 | **0.84** |
| ~~PMI "today"~~ | ~~0.00~~ | — | **withdrawn** |

Reward-per-capital in September is **higher** than August (1.39 vs 1.33),
because capital occupancy fell faster than incentives did. Even with REWARD
gone entirely, the environment pays **$0.84 per dollar of working capital per
day**. That is not a dead environment, and my "find an ACTIVE reward
environment" framing was built on a measurement that missed the biggest
stream.

## Trading: barely changed, except where the reward function changed

| metric | Aug 8–30 | Sep 1–9 | ratio |
|---|---:|---:|---:|
| BUY $/day | 182,110 | 161,149 | 0.88x |
| SELL $/day | 0 | **0** | — |
| REDEEM $/day | — | 160,148 | — |
| trades/day | — | 14,495 | — |
| **markets/day** | 371.7 | **444** | **1.19x** |
| mean concurrent $ | 1,830 | 1,523 | 0.83x |
| peak concurrent $ | 4,612 | 5,575 | 1.21x |
| turns/day | 39.5 | 28.9 | 0.73x |

- **Volume held.** He did not scale down when REWARD ended: $152K, $154K,
  $162K on Sep 7/8/9, the first three REWARD-free days.
- **Breadth went UP**, 372 → 444 markets/day. Consistent with the earlier
  finding that breadth, not size, is the axis he scales.
- **Still zero SELL rows.** 140,824 trades in ten days, every one a BUY. He
  buys both sides and redeems; he never exits by selling.

### The horizon mix inverted

| | Aug 8–30 | Sep 1–10 |
|---|---|---|
| 5-minute | 73.8% of windows | **6.8%** (BTC) + 1.4% (ETH) |
| 15-minute | 26.2% | **91.1%** (BTC) + 0.7% (ETH) |

In August the $550k 5-minute pool was the largest prize and he moved toward
5-minute. With that pool gone he has moved decisively to **15-minute BTC**.
Turns/day fell 39.5 → 28.9 and peak concurrent rose, which is the mechanical
consequence of a 3x longer settlement cycle.

This is the cleanest behavioural evidence yet that L5 tracks the reward
function in *both* directions.

## What is now resolved, and what is not

**RESOLVED — the MAKER_REBATE 6.65x puzzle.** It grew faster than volume in
August because it is its own programme, not a share of the TWAP pool. It
continues at full rate with that pool gone, which a pool-share stream could
not do.

**STILL UNRESOLVED — what MAKER_REBATE actually pays for.** ~$1,100/day,
daily at 00:45 UTC, on ~$161K/day of maker buying, invisible in per-market
CLOB `rewards` config. Its rate card is not established. That is now the
highest-value unknown in the incentive picture.

**Sports is unaffected either way.** Nothing here is evidence about PMUS
sports economics, which is a different venue and a different programme, and
the live probe continues to gather that separately.

## Provenance

Raw rows verbatim with `fetched_at`, exact query params and window bounds.
`REDEEM` ($160,148/day) is settlement cashflow and is never counted as
income anywhere in this document. Apr–Aug canonical data not replaced,
re-fetched or reinterpreted.

Method note: the activity API caps offset paging near 5,500 rows (~9 hours
for this account). This harvest windows by `start`/`end` in 3-hour slices and
pages within each window — the same approach the original Apr–Aug harvest
used. An earlier single-window read covering only 06:21–15:01Z showed zero
incentive rows and was reported as *not conclusive* precisely because it
crossed no 00:00 boundary; the full harvest shows why that caution was right.
