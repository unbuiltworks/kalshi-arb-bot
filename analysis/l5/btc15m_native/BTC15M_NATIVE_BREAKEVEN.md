# BTC15m-native breakeven — our own ledger, finally

Closes `L5_BEHAVIORAL_MISMATCH.md` §8, which has been the designated next
test since 2026-10-23: *"Until it runs we do not know our own breakeven on
this instrument — both available figures are unfit — and every expectancy
claim about BTC15m, including the ones I made, is unsupported."*

**Basis:** our own PMUS ledger. 141 `L5_LIVE_SETTLED` rows read read-only
from the existing `l5-pmus-live` service, of which **106 are BTC15m**. Each
is a market this ledger retired against an authoritative binary settlement.
Cohort preserved at `btc15m_native_cohort.json`. No new service, no new
collector, no historical re-run.

**17 markets excluded**, leaving **89 usable**: their cost basis is the
`$1/share` unknown-basis upper bound, which carries no observed cost. Keeping
them would put fabricated dollars of loss in the numerator against real units
in the denominator — the same exclusion `l5_breakeven.py` makes for
non-reconciling rows.

**Convention:** matched/residual is **average-cost** attribution and is
method dependent (`L5_OPERATING_MODEL` §2c). **The TOTAL is the authoritative
cashflow.** Both are reported; neither split is used as causal proof.

---

## The numbers

| | matched pairs | matched P&L | residual units | residual P&L | **net (authoritative)** |
|---|---|---|---|---|---|
| All usable (89) | 145.42 | +$13.4089 | 81.40 | −$15.0963 | **−$1.6874** |
| TRAIN (first 44) | 41.87 | +$4.4849 | 38.17 | −$9.0831 | **−$4.5982** |
| **OOS (last 45)** | 103.55 | +$8.9240 | 43.23 | −$6.0132 | **+$2.9108** |

| | pair profit / pair | residual loss / unit | units paired | **breakeven rate** | margin |
|---|---|---|---|---|---|
| All usable | +0.092208 | −0.185458 | 78.13% | **66.79%** | +11.3 pts |
| TRAIN | +0.107115 | −0.237964 | 68.69% | **68.96%** | **−0.3 pts** |
| **OOS** | +0.086181 | −0.139098 | **82.73%** | **61.74%** | **+21.0 pts** |

`breakeven` solves `p·pair_profit + (1−p)·residual_loss = 0`.

## What this establishes

**1. We crossed our own breakeven out of sample.** TRAIN paired 68.69%
against a 68.96% requirement — 0.3 points short, net −$4.5982. OOS paired
82.73% against 61.74% — **21 points clear**, net **+$2.9108**. The sign flip
is chronological, not fitted: the split is a date cut and nothing was tuned.

**2. The pair-profit term is no longer absurd.** §0 withdrew the earlier
census because +0.257707/pair was 5–6× the arithmetic maximum of a
0.96-capped pair. **+0.092208 implies an achieved average pair cost near
0.908**, which is both attainable under the cap and consistent with the book
sweep's observed pair costs. This figure is usable where the previous two
were not.

**3. The residual term lands between the two withdrawn figures.**
−0.185458/unit, against −0.150160 (earlier BTC15m census) and −0.331449
(sports cohort, unfit). The sports number overstated our residual cost by
**1.8×**, which is what made 84.52% breakeven look plausible.

**4. The binding variable is confirmed as the paired fraction.** Our pair
profit is healthy and our cap is not what hurts us; moving paired fraction
from 68.69% to 82.73% is what turned −$4.60 into +$2.91.

## What it does not establish

* **Not a forward expectancy.** 89 settled markets at ~1.9 contracts is a
  tiny book; OOS net +$2.91 is 45 windows, not a rate.
* **Not a fill model.** PMUS publishes no print endpoint, so joint maker-fill
  probability remains unmeasurable from replay.
* **Not an incentive claim.** Rebates are $0, never credited.
* **The 17 excluded markets are a real hole**, not a rounding error: 16% of
  our settled BTC15m history has no observed cost basis. Every one traces to
  the unknown-basis adoption path, and the duplicate-fill repair shipped in
  `f5207d81` is what stops that population growing.

## The $480/day frame, honestly

L5's own BTC15m book netted **+$18,191.93 across 5,546 windows** — about
**$3.28/window** — while his account-level incentives in the heaviest month
were **44×** his BTC15m trading P&L. Our OOS net is **+$2.9108 across 45
windows**, about **$0.065/window** at roughly 1.9 contracts a leg against his
median 128.

So the gap to $480/day is overwhelmingly **size and frequency**, not edge
sign — and the measured path to size runs through the unmatched-inventory cap
(3 contracts against his median 127.98), which is a risk control requiring
owner authorization and capital, not a code change. The near-term objective
stays **repeatable positive settled pair economics**, and the promotion order
stays PATIENCE → FREQUENCY → SIZE → CONCURRENCY.
