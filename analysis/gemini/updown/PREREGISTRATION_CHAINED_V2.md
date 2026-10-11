# PREREGISTRATION -- CHAINED-D V2

| artifact | sha256 |
|---|---|
| **V2 (this study)** `scripts/gemini_btc5m_chained_study_v2.py` | `a60e9aebe2a197b4fb1246d311471f91bfeeea5e0d6dadbcd0ad24c7870824a1` |
| V1, UNMODIFIED `scripts/gemini_btc5m_chained_study.py` | `0b746c20fba27dd1891c9dab7a8f922dbfc2ed1bb6721363db19c1545ef553bd` |
| V1's own superseded predecessor (loader defect only) | `b2f6d50a035b56c0edc4efea49d4ade8fc5eec292e2dada2e58e405a142f9bc6` |

**This fingerprint was recorded and committed BEFORE V2 was run against any
outcome.** V2 asserts V1's fingerprint at startup and refuses to run if V1
has moved.

**V1 is not modified or overwritten.** It stands as a valid, documented
NULL-SELECTION result: its candidate thresholds of +/-5 and +/-25 bps sat
largely outside the observed |D| distribution (median 1.48 bps, max 7.00,
zero observations at 25 bps), so 31 of 32 candidates could not fire. That is
a scale mismatch, not evidence about D.

## The economic question -- unchanged

> Does causal `D_t = published LRRR_XBT(t) - the AUTHORITATIVE strike`
> contain information **not adequately reflected in the executable contract
> price**?

## Scale: distribution-derived, never outcome-selected

Thresholds are **quantiles of |D_bps| computed on DISCOVERY OBSERVATIONS
ONLY**, at the preregistered levels **0.50, 0.70, 0.85, 0.95**. The levels
are the fixed constants; the bps values are whatever the feature
distribution yields.

`derive_thresholds()` receives **feature dicts only** -- no settlement, no
price result, no P&L -- and `assert_features_only()` raises on any key whose
name contains won / resolution / settle / eventual / winner / payout /
future / outcome / pnl / ev_. The sealed OOS partition is not read during
derivation. Scale is established in `main()` **before** outcomes are
consulted.

## Inherited from V1 by import, so it cannot drift

CHECKPOINTS, SIZE_LEVELS, the cumulative executable VWAP construction,
`fee_for`/`breakeven` (the actual fee treatment), the integrity and
freshness requirements, `observations()`, `assert_no_leakage`, `capacity()`,
EXCLUDED_TICKERS (the probe-contaminated windows), and OOS_FRACTION with the
discovery/OOS split rule. None is reimplemented. No change to any of them
was independently necessary, so none was made.

## D evaluated as INFORMATION, not a threshold crossing

* settlement probability and fee-adjusted **executable breakeven**
  conditional on D's **sign**, and on its **magnitude bucket** (quantile
  edges at 0, 0.25, 0.50, 0.75, 1.00 of |D|);
* the same by **signed** magnitude, which is what the monotonicity question
  needs;
* an explicit **monotonicity report**: does larger positive D raise
  subsequent UP settlement probability, and larger negative D lower it, in
  order?
* **distinct windows beside every cell**, with a standing caution that
  observations within a window are not independent. This is the lesson from
  the early-window analyzer, where a 77.78% UP win rate was identical at all
  nine checkpoints because it was 7 of 9 windows counted repeatedly.

## Incorporation

After observing D at checkpoint t, does the executable UP price
subsequently move in the direction D implies, and at what lag? Reported at
lags of 1, 2 and 3 checkpoint steps: sign-agreement rate with a Wilson
interval, the correlation of causal D with the later price change, decidable
pairs, zero-change pairs and distinct windows. The predictor is causal; the
outcome is a **price**, never a settlement. Agreement near 50% means the
price already embeds D and there is no lead to trade.

## OOS

**SEALED.** V2 reads the discovery partition only, under V1's own split rule
and OOS_FRACTION, so the sealed window set is identical to V1's. **V2 does
not freeze a candidate-selection procedure and unseals nothing.** OOS
remains sealed until a V2 selection procedure is separately frozen.

## Scope

No live trading authorization. No change to V1, to the frozen early-window
analyzer `f2800e3e...960e90b`, to the maker studies, to any live execution
behaviour, or to any OOS boundary. The early-window analyzer's +UP result is
**not** treated as validation.

---

## ADDENDUM: the preregistered fingerprint was superseded on first execution

| | |
|---|---|
| preregistered, never ran | `a60e9aebe2a197b4fb1246d311471f91bfeeea5e0d6dadbcd0ad24c7870824a1` |
| **executed** | `774f9eb3fde5d97b90c11975c80a1d313f40a67ccbea71d424565fad07b23092` |

`a60e9aeb` crashed on its first run with
`TypeError: fee_for() missing 1 required positional argument: 'size'` --
V1's `fee_for` takes `(price, size)` and V2 called it with one argument. The
crash occurred inside the conditional tables, so **no outcome was ever
evaluated under `a60e9aeb`**; the first and only outcome evaluation is under
`774f9eb3`.

**The sole difference is `V1.fee_for(p)` -> `V1.fee_for(p, D("1"))`**, size
1 contract, matching the q=1 observations V2 is built on. No threshold,
quantile level, bucket edge, checkpoint, D definition, fee rate, inherited
rule, split or seal changed. This is recorded here rather than quietly
refingerprinted, for the same reason V1's loader supersession was.
