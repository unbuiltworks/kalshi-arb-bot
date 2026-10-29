# Kalshi KXBTC15M — adversarial flow validation, real economics, adapter status

Everything below is read from Kalshi's **public, unauthenticated** REST API. No
credentials were used, requested, or are held in this container. Nothing was
submitted to Kalshi.

## 1. The flow claim survives. It is not a tail.

A single window had reported 21,703 trades / ~$622k / 1,738 trades·min⁻¹. That
was treated as a claim. Fourteen **consecutive** 15-minute windows
(2026-09-23 06:00Z → 09:15Z) were re-pulled to cursor exhaustion, deduplicated
on `trade_id`, and the notional recomputed from count × price rather than from
any fixed-point volume field.

| | p10 | p25 | **p50** | p75 | p90 |
|---|---|---|---|---|---|
| trades / window | 17,527 | 20,326 | **24,701** | 26,825 | 29,809 |
| contracts / window | 1.19M | 1.35M | **1.71M** | 1.81M | 1.85M |
| notional / window | $246k | $256k | **$673k** | $1.03M | $1.07M |
| trades·min⁻¹ | 1,172 | 1,360 | **1,767** | 1,815 | 1,998 |

Every one of the fourteen: `dup=0`, `cursor_exhausted=True`, `two_sided=True`.

**Classification of the original 21,703-trade window: TYPICAL — in fact slightly
below median.** It sits between p25 and p50. The enormous flow is the normal
state of this market, not an anomaly and not a high tail.

For scale, Gemini's *entire* BTC15m tape over 24h is $19,935/day across all
participants. A single Kalshi KXBTC15M window is a median $673k. Kalshi's
15-minute BTC market is roughly **three thousand times** Gemini's daily BTC15m
volume, per fifteen minutes.

## 2. Fees: shape confirmed, coefficient not

Source-confirmed from `GET /trade-api/v2/series/KXBTC15M`:

```
fee_type        = "quadratic"
fee_multiplier  = 1
```

and `GET /trade-api/v2/series/fee_changes` returns **zero** pending changes, so
that wiring is current. The same holds for KXETH15M, KXCRYPTOLEAD15M,
KXGOLD15M, KXWTI15M, KXSILVER15M, KXNATGAS15M and KXCOPPER15M.

The numeric coefficient behind "quadratic" lives in Kalshi's published fee
schedule. **This container cannot read it** — `kalshi.com` returns HTTP 429
through the egress proxy, and no `assets.kalshi.com` mirror resolves. It is
therefore recorded as `UNVERIFIED_FROM_SOURCE` and carried as a sensitivity
rather than hard-coded:

| coefficient | pair fee at p=0.50 | max combined YES+NO price that still breaks even |
|---|---|---|
| 0.000 | $0.0000 | 1.0000 |
| 0.035 | $0.0175 | 0.9825 |
| 0.070 | $0.0350 | **0.9650** |
| 0.100 | $0.0500 | 0.9500 |

Two consequences matter regardless of which coefficient is right:

1. **The fee is maximal at 0.50 and vanishes at the tails.** A two-sided
   inventory recycler wants to quote near the middle, which is exactly where
   Kalshi charges the most. This is structurally different from a flat fee and
   from Gemini's own maker schedule.
2. **Even at a coefficient of 0.07, the break-even combined pair price is
   0.9650** — very close to the `≤ 0.96` spring-discipline ceiling already in
   force. The existing pair ceiling is, by coincidence, about the right number
   for this venue. It is not loose here.

Price ticks are *not* uniform: `price_level_structure = "tapered_deci_cent"`,
with a $0.001 step below $0.10 and above $0.90 and a $0.01 step in between. Any
Kalshi placement logic must read `price_ranges` rather than assume one tick.

## 3. Incentives: the decisive finding

`GET /trade-api/v2/incentive_programs` is public and exact. The full corpus was
pulled to cursor exhaustion: **238,339 programs**, 215,979 `liquidity` and
22,360 `volume`. `period_reward` is in centi-cents ($0.0001), per the published
`IncentiveProgram` schema.

Kalshi posts roughly **$100k–$350k per day** in liquidity incentives across all
series. The L5 mechanism — quote two-sided, run trading P&L near flat, collect
the maker subsidy — is a *live business model on this venue*.

But not on the market with the flow:

| series | programs ever | live now | last program started | type |
|---|---|---|---|---|
| **KXBTC15M** | 9,781 | **0 ($0.00)** | **2026-05-12** | volume |
| KXETH15M | 8,299 | 0 ($0.00) | 2026-05-12 | volume |
| KXCRYPTOLEAD15M | 14,365 | **5 ($100.00)** | 2026-09-24 | **liquidity** |
| KXNATGAS15M | 1,922 | 1 ($20.00) | 2026-09-24 | liquidity |
| KXCOPPER15M | 1,922 | 1 ($20.00) | 2026-09-24 | liquidity |
| KXGOLD15M / KXWTI15M / KXSILVER15M | 3,388 each | 0 | 2026-09-19 | liquidity |

**KXBTC15M's subsidy was withdrawn on 2026-05-12**, and every one of its 9,781
historical programs was `volume` type and `paid_out: true`. The market that
prints $673k a window pays a maker nothing today.

The market that *does* pay — KXCRYPTOLEAD15M, $20 per 15-minute window per asset
— is **empty**. Its most recent five windows:

```
KXCRYPTOLEAD15M-26SEP230915-BTC    13 trades   $177 notional
KXCRYPTOLEAD15M-26SEP230915-ETH    10 trades   $61
KXCRYPTOLEAD15M-26SEP230915-SOL    10 trades   $80
KXCRYPTOLEAD15M-26SEP230915-HYPE    6 trades   $0
KXCRYPTOLEAD15M-26SEP230915-XRP     3 trades   $0
```

**On Kalshi today, the flow and the subsidy are in different markets.** That is
the single most important economic fact this survey produced, and it is the one
that decides whether the lane is worth capital.

### What is NOT established

- **How a liquidity program's pool is split.** `target_size_fp` and
  `discount_factor_bps` are visible; the scoring rule is not in the API.
- **Whether an ordinary non-market-maker account is eligible at all.** Kalshi's
  API-key docs gate key creation on "Premier or Market Maker API usage levels".
  Whether the LIP itself is similarly gated is unknown.

Per standing accounting rules, **every incentive is carried at $0.00** in all
economics below. A posted pool is `HEADLINE POOL ONLY` — not estimated
obtainable reward, and certainly not credited reward. Nothing becomes income
without an attributable cash credit.

## 4. Read-only adapter: it already exists, and it ran

No new adapter was written, because building one would have duplicated working
code. `src/kalshi_l5_collector.py` (791 lines, keyless producer + loop) and
`src/kalshi_l5_shadow.py` (349 lines, frozen L5 model consumer) already run the
existing market-agnostic inventory engine over Kalshi 15-minute windows, keying
the frozen `pmus_l5_persistence_v2` action model on literal TTE.

Verified today by running it:

```
python3 -m src.kalshi_l5_collector --once
  boot        8 series, certified_for_net_economics = [KXBTC15M, KXETH15M]
  collect     12 markets, 728 events written
  incentive   KXBTC15M active=0 upcoming=0 past=0
              "a zero here is a withdrawn subsidy, not an absent one"
  heartbeat   cycle 1, rss 23.9 MB
  shadow_only true, order_entry false
```

It independently reproduces the §3 finding from its own code path. It holds no
credentials and has no order endpoint reachable from it.

## 5. Verdict on the Kalshi lane

**Flow: PASSED, decisively.** Real, two-sided, continuous, ~1,800 trades·min⁻¹
median, and the original claim was below median.

**Economics: NOT ESTABLISHED, and currently unattractive.** Quadratic fees at
their maximum exactly where a recycler quotes; zero maker subsidy on the liquid
market since May; the subsidised markets have no flow to recycle. The L5
business on Polymarket was −$80 trading and +$2,557 incentives — *the rebate was
the entire business*. Kalshi KXBTC15M offers the volume without the rebate,
which is the half that did not make money.

**Recommended next step: keep the lane read-only.** The collector runs 24/7 at
no cost and no risk. Two cheap observations would change the verdict:

1. a liquidity program reappearing over KXBTC15M or KXETH15M (the collector
   already watches for exactly this and logs `KALSHI_INCENTIVE_STATE` every
   cycle);
2. volume arriving in a subsidised 15-minute series.

Until one of those happens, Kalshi does not justify capital, and no Kalshi
authorization is requested.
