# Preregistered: the repair adverse-selection study. Frozen before new data.

This file exists so the next dataset cannot quietly change the question. The
definitions, the buckets, the metrics and the baseline result below are
fixed. When new tape arrives the study is **rerun unchanged** and the numbers
land where they land.

## Frozen artifacts

| file | sha256 |
|---|---|
| `src/l5_repair_adverse_selection.py` | `7c18e9c89f7a99cab8fa0eb2bd1c307385f7d590c1acf2861bc31c1ac7626f61` |
| `scripts/l5_repair_adverse_selection_study.py` | `d9e8088f6cd6d720ba9546865effc56e8b1f7552976679ead6e34abeed77c7c5` |
| `analysis/pmus/evidence/probe08/repair_adverse_selection.json` | `0613554fe06fd8046198824eb621334f4f47bb34bf8c308c5b165ec9115019df` |

A rerun that changes either of the first two is a **different study** and must
say so in its own file rather than overwrite this result.

## Frozen definitions

* **Inventory state** — L5's own realized inventory through the window. His
  activity rows are completed trades. Our own fills are never assumed.
* **Own-side price** — wire for UP, `1 − wire` for DOWN. A resting buy
  interacts when `own_side_market_price <= offer_own_price`, either side.
* **Interaction** — `TRADE_AT_PRICE` (equal) or `TRADE_THROUGH_PRICE`
  (strictly below). `MARKETABLE` and `NO_INTERACTION` are not interactions.
  **None of the four is a fill.** Queue position is unknown and unmodelled,
  so every interaction rate is an UPPER BOUND.
* **Adverse move** — max `own_side_market_price − offer_own_price` over the
  rest interval, zeros kept in the distribution.
* **Implied pair cost** — `own_price + opposite-side average cost`, at the
  offer and at the deteriorated price.
* **Arms** — `REDUCE_RESIDUAL` (repair), `INCREASE_RESIDUAL` (paired control:
  same state, same moment, other side), `FLAT_OPEN` (reported, structurally
  tiny).
* **Coverage rule** — a window with **zero prints is dropped**, never counted
  as expiry-without-interaction. Coverage is reported separately and first.

## Frozen buckets

* repair price: `<0.20`, `<0.40`, `<0.60`, `<0.80`, `>=0.80`
* time to expiry: `<300s`, `<600s`, `>=600s`
* spread: `<=0.02`, `<=0.05`, `>0.05`
* residual magnitude: `<10`, `<50`, `>=50`
* quantiles: p50, p75, p90, by plain order statistic, no interpolation

## BASELINE RESULT — 5 windows, all 2026-09-23, 72 offers

| arm | n | interaction | expired w/o | adverse p50 | p75 | p90 |
|---|---:|---:|---:|---:|---:|---:|
| REDUCE_RESIDUAL | **36** | **16.7%** | 83.3% | 0 | **0.2700** | **0.3300** |
| INCREASE_RESIDUAL | **34** | **44.1%** | 55.9% | 0 | 0.0100 | 0.0500 |
| FLAT_OPEN | 2 | 50.0% | 50.0% | — | — | — |

Interaction mix — repair: 30 `NO_INTERACTION`, 5 `TRADE_THROUGH_PRICE`, 1
`TRADE_AT_PRICE`. Control: 19 `NO_INTERACTION`, 15 `TRADE_THROUGH_PRICE`.

Probe 08's +0.19 sat at the **69th percentile** of 36 repair offers.

## The two hypotheses the next dataset tests

1. **H1** — repair interaction rate is materially below the same-state
   `INCREASE_RESIDUAL` control. Baseline: 16.7% vs 44.1%.
2. **H2** — repair adverse-move and implied pair-cost deterioration
   distributions are materially worse than the control's. Baseline: adverse
   p75 0.2700 vs 0.0100.

Both are stated as they stand. Neither is a threshold, and no threshold is
to be fitted to the new data.

## Known weakness the new data must fix

Five windows of **one day**, and — established after the baseline was
computed — **none of those tapes is complete**: the print source was a capped,
deduplicated export (`cap_per_market 200`, state trimmed to the last 500 per
market) spread across 758 markets, so the best-covered window carried 862s of
a 900s window and most carried seconds. Incomplete tape suppresses interaction
counts in **both** arms, which is why the ratio is more trustworthy than the
levels — and why the levels must be re-measured on complete tape.
