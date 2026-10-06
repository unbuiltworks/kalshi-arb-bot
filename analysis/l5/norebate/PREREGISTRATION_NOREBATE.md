# Preregistration — no-rebate economics audit

**Frozen before any analysis.** S3 untouched at `8b20a259…87df`; no live or
shadow strategy is modified. No orders. Historical L5 cash is **never**
called our realized profit.

## 0. Identifiability, established before the protocol

| fact | value |
| --- | --- |
| activity rows usable | 78,000 |
| coverage | 2026-03-19 → 2026-08-31 |
| **coverage HOLE** | **May, June, July entirely absent** — the feed hit `max historical activity offset of 5000 exceeded` |
| TRADE | 75,864 (**75,862 BUY, 2 SELL**) |
| REDEEM | 2,126, of which **2,097 carry non-zero proceeds** |
| MAKER_REBATE / TAKER_REBATE / REWARD | **4 / 3 / 3** records — **daily aggregates**, not per-trade |
| fully observed incentive days | **2026-08-29, -30, -31**, plus one 2026-04-21 maker rebate |

**Two facts that shape everything below.**

1. **L5 essentially never sells: 2 SELL trades out of 75,864.** He buys and
   redeems, so **redemption is the entire proceeds side** and sell proceeds
   total **$2.18**.
2. **Observed incentives are 6.7×–7.8× the stated $480/day target:**

| day | maker rebate | taker rebate | reward | **total** | × target |
| --- | --- | --- | --- | --- | --- |
| 2026-04-21 | 29.94 | — | — | **29.94** | 0.06× |
| 2026-08-29 | 1,233.57 | 192.11 | 1,794.66 | **3,220.34** | **6.7×** |
| 2026-08-30 | 1,346.88 | 192.21 | 2,205.72 | **3,744.81** | **7.8×** |
| 2026-08-31 | 1,298.91 | 203.01 | 1,841.42 | **3,343.34** | **7.0×** |

The April maker rebate ($29.94) to August (~$1,300) is a **~43× scale-up**.

**What this means for scope, fixed now:** only **4 incentive days** are
observed. The corpus total of rebates/rewards is therefore
**UNIDENTIFIABLE**, and no study here will report one. Incentive magnitude
is quoted only for those 4 days, as a per-day rate.

## 1. Fee accounting — embedded, so reported by reconstruction

Per `L5_OPERATING_MODEL.md`, `usdcSize` on a BUY exceeds `price × size` by
exactly `0.07·p·(1−p)`. **Fees are therefore already embedded in the BUY
debits**, and a net computed from `usdcSize` is fee-inclusive.

Fixed now: fees are reported **separately** as
`Σ (usdcSize − price × size)` over BUYs, and the identity is **verified**
against the `0.07·p·(1−p)` model rather than assumed. If the identity fails,
the measured residual is reported as `FEE_IDENTITY_UNCONFIRMED` and the fee
line is labelled empirical, not modelled.

## 2. Q1 — historical accounting, rebates set to exactly $0

Per market/event and per window:

```
net = REDEEM proceeds + SELL proceeds − BUY debits ,   rebates ≡ $0
```

Reported **separately**, never pooled into one number:

| line | definition |
| --- | --- |
| **matched-pair economics** | `matched × (1.00 − pair_cost)`, where `matched = min(up_shares, down_shares)` and a matched pair redeems exactly $1.00 |
| **directional residual** | `residual × (1 if the residual side won else 0) − residual × vwap(residual side)` |
| **charged fees** | `Σ (usdcSize − price·size)`, per §1 |
| **rebates / rewards** | **exactly $0.00, by construction** |
| **net** | the cashflow identity above |

### Eras, fixed now

| era | definition |
| --- | --- |
| **E1 GENUINELY_PRE_REBATE** | before 2026-04-21, the first observed MAKER_REBATE |
| **E2 EARLY_REBATE** | 2026-04-21 through end of April coverage |
| **E3 INCENTIVE_ERA** | August coverage |
| **FULL_CORPUS** | all observed |

**May–July is a HOLE, not a zero.** No era may be described as continuous
across it, and no $/day figure may be computed by dividing a total by the
calendar span.

### Required statistics — aggregate total alone is forbidden

For every era: **N**, total $, **$/event**, **$/day** (over *observed* days
only), **median event**, **win rate**, **bootstrap CI** (10,000 resamples,
percentile), **max drawdown** on the event-ordered cumulative curve,
**concentration** (top-1 / top-5 / top-10 event share of total, and net with
the top event removed), and **monthly + weekly stability**.

*Explicitly:* a positive total with negative median, or a total dominated by
a handful of events, is reported as **CONCENTRATED, NOT ROBUST**.

## 3. Q2 — counterfactual: can the mechanism stand without rebates?

Executable historical replay of the **smallest evidence-supported maker
process**, on the 12 BTC15m L2-overlap windows (the only data with a
contemporaneous book).

Mechanism, taken from the frozen findings and **not re-tuned**:

1. **near-touch maker exposure** — offset 0 ticks, the measured median at
   every level rank;
2. **partial fills treated as execution, never as chosen quantities** —
   91 % of irregular quantities are partial consumption;
3. **exposure moves with the touch** — the coordinate-system result; no
   leave-behind persistence is assumed, since that was
   CONSISTENT_WITH_REPOSTING;
4. **inventory and TTE controls** — residual cap and the TTE stop already
   evidenced.

### Hard rules
- **No future L5 fill** decides our placement or evidences our fill.
- Only contemporaneously observable market information.
- **Strictest defensible fill evidence**; the frozen classifier is imported
  verbatim. **No touch=fill. No invented queue priority.**
- **No rebates or rewards**, at all.
- Matched economics, residual settlement P&L and fees reported separately.
- Bids only; the archive's own `crossed` column read and exposure reported.

### Prior expectation, recorded so a null is not a surprise
S2 — a near-identical maker process under the less-biased third-party-print
arm — returned **−5.58 %** and **−6.33 %** net per dollar spent. Q2 is
expected to be negative. It is run to quantify *how* negative and on which
line, not in hope of a positive.

## 4. The regime comparison — why deleting rebates is not enough

**Essential, and fixed as a first-class endpoint:** deleting rebate payments
from an incentive-era strategy is **not** equivalent to observing that
strategy in a world without incentives.

Compared across E1/E2 vs E3 on: trade count and notional per observed day ·
maker share where observable · matched accumulation · residual exposure ·
pair economics · execution characteristics (price, size, fill cadence).

*Bar for a material regime change:* any of Cliff's delta ≥ 0.33 on the
per-day distributions, **or** a ≥2× shift in median daily notional.

**If a material change is found, Q1's E3 "rebates = $0" figure may NOT be
read as the no-incentive counterfactual**, and the conclusion must say so.
The confound with the May–July hole is stated wherever the comparison is
quoted.

## 5. PMUS rewards — quantified separately

PMUS's own maker-rebate / reward program is quantified on its own line and
**never** mixed into Q1 or Q2. If its terms cannot be source-confirmed for
our account from available data, the answer is **UNKNOWN**, per the standing
rule that no rebate is assumed until actually credited.

## 6. Required conclusions — separate, never merged

1. **OBSERVED L5 CASH POSITIVE / NEGATIVE EX INCENTIVES** — a statement
   about *L5's* historical cash, with its concentration and stability
   qualifiers, and explicitly **not our realized profit**.
2. **NO-REBATE MECHANISM SUPPORTED / NOT SUPPORTED / UNIDENTIFIABLE** — a
   statement about the mechanism's expectancy without incentives.

A null or UNIDENTIFIABLE outcome is a complete answer. The May–July hole and
the 4-day incentive observation are both reported as limits on every number
they touch.
