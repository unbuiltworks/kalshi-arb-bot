# Kalshi live-probe gate: **NO LIVE PROBE JUSTIFIED**

Date of determination: 2026-10-01.
Benchmark frozen at `1f43db3b` (`analysis/l5/economics/CANONICAL_L5_BENCHMARK.md`).
Census dump: `data/kalshi/census/incentive_programs.json.gz`, fetched
2026-10-01T23:34:44Z from `GET /trade-api/v2/incentive_programs`, 273 pages,
272,372 programs.

This is a gate verdict, not a recommendation to keep looking. Per the standing
instruction: if no candidate passes, STOP and report.

## The decisive arithmetic

Kalshi liquidity programs scale payout by how much of `target_size_fp` is
actually supplied, and split what is earned pro-rata among qualifying makers.
A q1 (one-contract) resting order is therefore bounded by the **minimum** of
two ceilings:

    pro-rata ceiling  = pool x 1 / (1 + competing resting size at the touch)
    supply ceiling    = pool x 1 / target_size_fp

Both are generous: they assume the entire period pool is payable to the single
side we rest on, count only competitors already resting at that touch, and
apply no decay for the fraction of the period we are actually resting. The real
allocation is spread- and depth-weighted across both sides and several price
levels, so the realistic number is materially smaller than either.

Across **all 8,142 active programs**, `target_size_fp` takes four values
(1000.00 x 8,014; 2500.00 x 73; 300.00 x 36; 500.00 x 19) and the largest
`period_reward` is 10,000,000. The supply ceiling `pool / target_size` is
therefore maximised globally at:

> **$1.00 per program period — and the period is 2.33 days, not one day.**

That is the best obtainable q1 compensation anywhere on the venue right now,
before any of the generosity above is removed. It is achieved only in NFL
player-prop ladder/escalator markets (`KXNFLLADDER*`, `KXNFLESCALATOR*`,
`KXNFLFFPTSLADDER`) on the 26OCT01PITCLE event.

## Why that fails the gate

The probe was authorised to teach us **the cost of producing $1 of venue
compensation**. On the current venue state, producing $1 of compensation
requires resting one contract for a full 2.33-day period in a market where:

- competing qualifying liquidity at the touch runs 25 to 1,380 contracts, and
  total book depth 105,000 to 274,000 contracts, against a 1,000-contract
  target — the pool is already fully supplied by other makers;
- the quoted spread at the touch is 1 tick ($0.0100) at best and $0.0001 in
  the deepest names — there is no spread to capture alongside the credit;
- the instrument is a cubic scalar payout on a single player's receiving
  yards, resolving on a live game — adverse selection against a 2.33-day
  resting quote is not bounded by anything we can observe in advance.

A one-tick ($0.01) adverse fill consumes 1% of the maximum credit, and the
maximum credit is itself an upper bound we expect to miss by an order of
magnitude. There is no configuration of a $1.00-exposure, q1, maker-only,
single-market probe in which the measured credit is distinguishable from zero
against the measurement noise of a single fill.

## The L5-analogue series pay nothing

The short-horizon crypto series that most resemble L5's PMUS operating regime
carry no live compensation at all. Counted from the dump, not from memory:

| Series | programs in dump | active now | upcoming |
|---|---|---|---|
| KXBTC15M | 9,781 | **0** | **0** |
| KXETH15M | 8,299 | **0** | **0** |
| KXCRYPTOLEAD15M | 18,605 | 5 | 565 |

Every other BTC/ETH/SOL/XRP series in the dump is a monthly or yearly
max/min market (`KXBTCMAXMON`, `KXETHMINMON`, ...) with a settlement horizon
of weeks, not minutes. There is no Kalshi venue on which a like-for-like L5
transfer is compensated.

This re-confirms the repository's prior finding from fresh data a month later.

## Why this ends the transfer question, not just the probe

The frozen benchmark records that **L5's trading was negative over the captured
population (-$6,338.70) and venue incentives (+$23,047.40) made the business
profitable (+$16,708.70)**. The mechanism being transferred is a paid-liquidity
mechanism whose historical profitability came entirely from compensation.

Kalshi offers that mechanism no compensation on the instruments it fits, and
offers at most $1.00 per 2.33 days — fully supplied by incumbents — on
instruments it does not fit. Transferring the mechanism would transfer the
negative trading edge without the positive term that paid for it.

## Evidence classes

| Quantity | Class |
|---|---|
| program fields (pool, target, discount, dates, ticker) | SOURCE-OBSERVED |
| orderbook touch prices and sizes | SOURCE-OBSERVED |
| `period_reward` -> USD conversion | **MODELED, UNVERIFIED UNITS** |
| q1 payout ceilings | MODELED from SOURCE-OBSERVED inputs |
| our eligibility for any program | ACCOUNT-OBSERVED, **unobtainable** |
| any credited reward | ACCOUNT-OBSERVED, **unobtainable** |

### The unit hypothesis is corroborated but not confirmed

`period_reward` is read as centi-cents (1e-4 USD). The largest active value,
10,000,000, is exactly $1,000.00 under that reading, matching Kalshi's
advertised per-market ceiling. That is corroboration, not confirmation: no
account-credited reward has been observed to calibrate it, because no Kalshi
credentials are present in this environment. **If the unit is wrong, every
dollar figure here is wrong by a constant factor.** The ranking and the
relative comparison survive any such factor; the absolute gate threshold does
not.

### Two things this analysis cannot see

1. **Account state.** No `KALSHI_*` environment variables and no `.env` are
   present, so current Kalshi positions, open orders, exposure, fee tier, and
   program eligibility are all unobtainable. The verdict is NO PROBE, so this
   does not block it — but it would block any later advancement.
2. **The allocation coefficients.** Kalshi's exact spread/depth weighting is
   not published on the endpoint. Both ceilings here are upper bounds that do
   not depend on those coefficients, which is why the verdict holds without
   them.

## A defect found and corrected in this analysis

The first revision of `scripts/kalshi_candidate_rank.py` applied only the
pro-rata ceiling. It reported `KXTRUMPAPPROVE-26OCT02-U38.5` at **$447.76 per
period** because the thinner touch held 0.34 contracts — it read an empty book
as a free pool. Adding the supply ceiling moved that candidate to $0.60 and
inverted the ranking. Recorded here rather than silently fixed: the loose bound
would have manufactured a passing candidate.
