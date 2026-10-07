# Breakeven incentive rate — the arithmetic

**Lane C.** Computed 2026-10-07 from frozen artifacts, read-only.
Script: `scratchpad/be.py` (throwaway, outside the repo).

## 0. The rate we are solving for, and the ceiling it must live under

PMUS maker rebate per fill (primary source `docs.polymarket.us/fees`, captured
2026-09-10T17:14Z):

    rebate = round_half_even( 0.0125 × C × p × (1 − p) , $0.01 )

As a **rate of maker notional** (`notional = p·C`):

    rebate / notional = 0.0125 × (1 − p)

| avg fill price p | rate of maker notional |
|---|---|
| 0.30 | 87.5 bps |
| 0.40 | 75.0 bps |
| 0.50 | 62.5 bps |
| 0.60 | 50.0 bps |
| 0.70 | 37.5 bps |

**Absolute ceiling 125 bps** as p→0 (unreachable: that is where the cent floor
bites hardest and the directional residual is worst). Any breakeven rate above
125 bps is **arithmetically unreachable under the published PMUS formula at any
size**.

Measured on our own credited fills: **73.9 bps** (25 prints, $0.4200 on
$56.8624) and **54.9 bps** (20 sports fills, $0.1700 on $30.9563). Both sit
inside the table — 73.9 bps implies an average fill price of
`1 − 0.00739/0.0125 = 0.409`, which is exactly where that cohort traded. The
formula and our credits agree.

## 1. The frozen Q1 no-rebate corpus

Source `analysis/l5/norebate/Q1_NOREBATE.json` (protocol
`PREREGISTRATION_NOREBATE.md @ 3fb8fccb`). Maker-notional denominator = Σ
`buy_debits` over `Q1_EVENTS.jsonl`, L5 being effectively buy-only (2 SELL
records in 760,817 trades), so gross buy notional is the best available
maker-notional proxy. **Caveat: L5 also received TAKER_REBATE, so some of that
notional was taker and the true maker denominator is smaller — which makes
every required bps figure below an UNDERSTATEMENT.**

| cohort | events | observed days | net ex-incentives | buy notional | notional/day |
|---|---:|---:|---:|---:|---:|
| FULL_CORPUS | 2,218 | 22 | **−$2,745.53** | $766,992.94 | $34,863.32 |
| E1 pre-rebate | 211 | 17 | **−$168.46** | $28,123.54 | $1,654.33 |
| E2 early-rebate | 5 | 1 | −$1,855.08 | $1,855.08 | $1,855.08 |
| E3 incentive era | 2,002 | 4 | **−$721.99** | $737,014.32 | $184,253.58 |

### The arithmetic

    required $/day   = deficit / observed days
    required bps     = deficit / buy notional × 10,000
    implied (1 − p)  = required bps / 125          [from rate = 0.0125·(1−p)]

| cohort | required $/day | required bps of maker notional | implied avg fill price p | reachable? |
|---|---:|---:|---:|---|
| **FULL_CORPUS** | $2,745.53 / 22 = **$124.80/day** | 2,745.53 / 766,992.94 = 0.003580 = **35.8 bps** | 0.714 | **YES** — 35.8 bps is well under the 125 bps ceiling and under our own measured 73.9 bps |
| **E1** | 168.46 / 17 = **$9.91/day** | 168.46 / 28,123.54 = 0.005990 = **59.9 bps** | 0.521 | **YES, but only just** — needs the full formula rate at p≈0.52, i.e. maker-only execution at mid-book, every fill above the cent floor |
| **E3** | 721.99 / 4 = **$180.50/day** | 721.99 / 737,014.32 = 0.000980 = **9.8 bps** | 0.922 | **YES, comfortably** — E3's deficit is trivial against its $184k/day of notional |
| E2 | $1,855.08/day | 1,855.08 / 1,855.08 = **10,000 bps** | impossible | **NO.** Those 5 events lost 100% of their debits. No rebate at any rate recovers a total loss. 1 day, n=5, CI95 [−2,878, −882]. |

**Read this correctly.** These rates are **not** "incentives would have saved
L5". They say: *if* a maker-rebate stream at that rate had applied to that much
notional, it would have exactly cancelled the ex-incentive deficit. The
deficits are **L5's historical cash under a rebates-=-$0 accounting and are not
our realized profit** (the artifact says so in its own
`NOT_OUR_REALIZED_PROFIT` field). And the headline rates are driven almost
entirely by notional, not by skill: E3 needs 9.8 bps only because it traded
$184k/day.

## 2. The $480/day target in CLAUDE.md

    maker notional per day required = $480 / rate

| rate applied | source / status | maker notional/day needed |
|---|---|---:|
| 125.0 bps | formula absolute max, p→0, unreachable | **$38,400** |
| 87.5 bps | formula at p=0.30 | $54,857 |
| **73.9 bps** | **our own 25 credited PMUS prints** | **$64,953** |
| 62.5 bps | formula at p=0.50 | $76,800 |
| 54.9 bps | our 20 credited PMUS sports fills | $87,432 |
| 97.59 bps | **GEMINI, 7 credits — NOT TRANSFERABLE, shown only to be dismissed** | ($49,185) |

**At our own measured PMUS rate, $480/day of maker rebate requires about
$65,000 of maker notional every day.** Our current bounded-probe session
notional cap is **$7.50**. That is a factor of **~8,700**. At $7.50/day the
maker rebate accrues **$0.055/day**, which the cent floor pays out at a few
cents or nothing.

Lifetime venue-confirmed PMUS incentives are **$16.18 over 32 account-days =
$0.506/day**. Reaching $480/day of incentive alone is **~949×** our entire
observed incentive run rate.

## 3. Is the 97.59 bps "anywhere near sufficient"?

**It is not ours, and even if it were it would not be the binding constraint.**

1. **Venue.** 97.59 bps is **Gemini** — 7 `AdminCredit` transfers,
   `purpose: "Predictions maker rebate"`, $0.51 on $52.2573. It is not a PMUS
   number and does not belong in a PMUS breakeven.
2. **Already forbidden.** `REBATE_APPLICABILITY_1.md §6` rules it out for
   prospective expectancy on three independent grounds: it aggregates trading
   that was not this programme's; it embeds an average fill price (~0.535) our
   policy does not produce; and below ~$1.02 of daily eligible notional the
   realized rate is **exactly 0 bps** because of the round-down.
3. **Rate is not the problem; notional is.** Compare like for like against the
   PMUS formula: 97.59 bps sits between the p=0.30 (87.5) and p=0.20 (100) rows
   — it is an ordinary rate, not an exceptional one. Pushing our rate from the
   measured 73.9 bps up to 97.59 bps would cut the notional needed for $480/day
   from $64,953 to $49,185 — a 24% improvement on a requirement we miss by
   nearly four orders of magnitude. **Rate optimisation cannot close a
   ~8,700× notional gap.**

**Blunt answer: no. Not near sufficient, and not even the right lever.** The
only variables that move $/day materially are **maker notional per day**
(frequency × size × concurrency) and **not being wrong on the residual**. At
73.9 bps a single −$3.90 directional residual costs **$528 of maker notional**
to repay; at 62.5 bps, **$624**. The rebate pays for completing. It does not
pay for being wrong. This is consistent with `PMUS_INCENTIVE_BREAKEVEN.md`:
above a pair cost of 1.008 a COMPLETE loses money at every size and price
studied **even with every observed credit counted**.

## 4. Where incentive capture is actually available

| lane | maker rebate | liquidity program | verdict |
|---|---|---|---|
| **BTC15m (`btc-updown-15m`)** | YES, exchange-wide, `0.0125·(1−p)` | **none found live** (up/down `reward_state: EXPIRED`, `rewards_daily_rate: null`; PMUS program census returns 0 for all 239 of our slugs) | fine for rebate capture, **zero** program capture |
| **Sports (ML / spread / total / props)** | identical rate — no category variation in our primary capture | **ACTIVE and large**: 174/175 MONEYLINE, 5,682/7,362 SPREAD, 3,690/5,050 TOTAL, 7,036/12,952 PROP | the **only** lane with program money available to us |

**But the sports program pays for a behaviour we do not perform.** Its payout
is `P · X/(X+D) · duty / (2M)`: it rewards *resting breadth*, sampled every
second, across many markets. Our engine rests ~5 s, holds one outstanding
order, works one market at a time, and chases fills. Duty ≈ 0 by design, and
`1/(2M)` punishes single-market occupancy — a 1,314-market pool pays a
single-market participant 1/2,628th of it. **40 of 46 sampled programs do not
clear the $1.00 minimum at 100% duty, so their correct expected value is
exactly $0.00.**

**Therefore:** BTC15m is not the wrong venue for incentive capture — on the
maker rebate, which is the only incentive we have ever actually been credited
at scale, it is identical to everywhere else. Sports is the only lane where a
*second* incentive stream exists, and capturing it would require a different
engine (breadth, duty, passive resting that does not want to fill) that
contradicts the current spring probe's one-market, fill-seeking, maker-first
discipline. **Switching category to chase it is a strategy change, not a
parameter change, and nothing here authorises it.**

## 5. Labels

Every bps figure here is DERIVED arithmetic on OBSERVED numbers. The 73.9 bps
and 54.9 bps rates are **credited**. The 35.8 / 59.9 / 9.8 bps figures are
**counterfactual requirements against another trader's frozen historical
corpus** — they are not forecasts and not our realized anything. The 97.59 bps
is **credited on Gemini and not transferable to PMUS**. No estimate has been
moved into the credited column.
