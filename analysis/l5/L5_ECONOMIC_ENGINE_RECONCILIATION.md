# P0-5 — Was mature L5 making money from standalone trading before incentives?

**Answer: no — and not clearly losing either. Standalone trading is
indistinguishable from zero on every subset tested, with a slightly negative
point estimate. The strategy was economically dependent on incentives.**

Label: **SETTLED-RECONSTRUCTED**. Built from L5's own public trades
(`source=polymarket_public_activity`) and the venue's own settlement outcomes
(`resolution_source=gamma_outcome_prices`). Payout arithmetic is
settlement-derived, not modelled — but this is a reconstruction from public
data, not a cash statement from L5's account. Never "realized".

---

## 1. The decomposition is exact, so no conclusion rests on a pairing convention

Per window, with `avg_u = spend_up/qty_up`, `avg_d = spend_down/qty_down`:

```
NET      = qty_on_winning_side − (spend_up + spend_down)
matched  = min(qty_up,qty_down) × (1 − avg_u − avg_d)
residual = |qty_up−qty_down| × ((1 if residual side wins else 0) − avg_side)
matched + residual ≡ NET          (asserted numerically, max error 0.000000)
```

This disposes of one of the five candidate explanations outright: **the result is
not an artifact of matching methodology.** The split is a presentation of the
same money.

Fees: `dollars == price × quantity` exactly in every sampled row — no fee is
embedded and none is inferred. **Incentives are not in this corpus and are never
inferred.**

## 2. Full corpus, 2,216 windows

| line | value |
|---|---|
| spend | $728,500.10 |
| payout | $727,449.54 |
| matched-inventory P&L | **+$18,912.87** (648,022 units) |
| directional residual P&L | **−$19,963.43** (224,899 units) |
| known fees | $0.00 |
| **STANDALONE TRADING NET** | **−$1,050.57** |
| return on spend | **−0.144%** |
| incentives/rebates/rewards | **NOT IN CORPUS — not inferred** |

## 3. How a 0.9684 pair basis coexists with ~zero standalone net

Median pair basis **0.9705**, 61% below par — the BTC15m figure is confirmed at
**0.9634, 78% below par**. It is real. It is also cancelled:

```
matched P&L per matched unit        +0.0292
residual units per matched unit      0.3471
residual P&L per residual unit      −0.0888
residual cost per matched unit      −0.0308
─────────────────────────────────────────────
net per matched unit                −0.0016
```

**Each matched unit earns 2.9¢ and drags ~0.35 residual units losing 8.9¢ each.**
The two lines are the same size by construction of the strategy, not by
coincidence. That is the whole reconciliation.

### The five candidate explanations, tested

| hypothesis | verdict |
|---|---|
| concentrated in BTC15m while losses occur elsewhere | **No — inverted.** BTC15m has the *best* basis (0.9634) and the *worst* net (−$1,114.64, −0.194%) |
| offset by residual magnitude | **Yes — this is the mechanism** |
| artifact of matching/accounting methodology | **No** — decomposition is exact and convention-free |
| concentrated in particular eras | Partly: 2026-04 +1.816%, 2026-08 −0.208% — but see §5, nothing survives uncertainty |
| genuinely a positive standalone BTC15m sub-strategy | **No** |

## 4. By lane and era

| cohort | n | matched | residual | NET | ret% | med basis |
|---|---|---|---|---|---|---|
| BTC5m | 957 | +598.03 | −540.43 | +57.60 | +0.045% | 0.9763 |
| ETH5m | 703 | −232.85 | +229.77 | −3.08 | −0.012% | 1.0263 |
| **BTC15m** | 527 | +18,597.61 | −19,712.25 | **−1,114.64** | −0.194% | **0.9634** |
| ETH15m | 29 | −49.93 | +59.48 | +9.55 | +0.917% | 1.2334 |
| 2026-03 | 88 | +191.96 | −192.51 | −0.55 | −0.007% | 0.9759 |
| 2026-04 | 128 | +833.90 | −430.95 | +402.96 | +1.816% | 0.9448 |
| 2026-08 | 2000 | +17,887.00 | −19,339.97 | **−1,452.97** | −0.208% | 0.9718 |

## 5. Uncertainty — and it governs everything above

Window-level bootstrap (2,000 resamples; the window is the independent unit),
95% CI on return-on-spend:

| cohort | n | ret% | 95% CI | verdict |
|---|---|---|---|---|
| BTC5m | 957 | +0.045% | [−2.124, +2.331] | **indistinguishable from zero** |
| ETH5m | 703 | −0.012% | [−4.672, +4.563] | **indistinguishable from zero** |
| BTC15m | 527 | −0.194% | [−0.908, +0.548] | **indistinguishable from zero** |
| ETH15m | 29 | +0.917% | [−10.218, +11.642] | **indistinguishable from zero** |
| has_taker | 1707 | +0.530% | [−1.253, +2.382] | **indistinguishable from zero** |
| maker≥90% | 293 | −0.466% | [−1.173, +0.278] | **indistinguishable from zero** |

**No lane, era or execution-style subset has a confidence interval excluding
zero.** The apparent maker/taker split (pure-maker −0.466% vs has-taker +0.530%)
is *not* a finding — both straddle zero.

Chronological 70/30 split on window start time:

| | n | matched | residual | NET | ret% | 95% CI |
|---|---|---|---|---|---|---|
| TRAIN | 1551 | +11,783.68 | −13,453.64 | −1,669.96 | −0.332% | [−1.106, +0.508] |
| OOS | 665 | +7,129.19 | −6,509.79 | +619.40 | +0.275% | [−1.068, +1.666] |
| BTC15m TRAIN | 368 | +9,731.63 | −10,958.68 | −1,227.04 | −0.401% | [−1.229, +0.450] |
| BTC15m OOS | 159 | +8,865.98 | −8,753.57 | +112.41 | +0.042% | [−1.147, +1.225] |

Daily cohorts in the mature era swing −1.227%, +0.264%, −1.099%, +1.269% —
2 of 4 days positive.

### A hard limitation on the OOS claim

The corpus is **not evenly spread**: 88 windows in 2026-03, 128 in 2026-04, and
2,000 in 2026-08 — and the August block spans only **four days, 08-28 to 08-31**.
So the "mature era" is four days, and the chronological hold-out largely splits
*within* those four days. This is a genuine chronological split but a very short
one, and no claim here should be read as a multi-month out-of-sample result.

## 6. Reconciliation with the older full-history accounting

| | older full-history | this corpus |
|---|---|---|
| matched | ≈ +$155,076 | +$18,913 |
| residual | ≈ −$141,390 | −$19,963 |
| standalone net | ≈ +$13,686 | **−$1,051** |
| incentives | ≈ +$68,208 | not in corpus |

The magnitudes differ by roughly 8×, so the older accounting covers substantially
more activity than these 2,216 windows. **Neither analysis is wrong and they do
not actually conflict**: both show `matched ≈ |residual|` with a net that is small
relative to turnover. The older +$13,686 against ~8× this corpus's spend is on
the order of **+0.2% of turnover** — squarely inside the ±0.3–1% bootstrap band
measured here, i.e. a small positive point estimate that this data cannot
distinguish from zero.

**The decisive ratio in the older accounting is incentives ÷ standalone net =
68,208 ÷ 13,686 ≈ 5.0×.** Even taking the older net at face value, incentives
were five times the trading result.

## 7. Bearing on PMUS (6247777f) — the same mechanism, a far worse ratio

| | residual units per matched unit | residual $/unit | net per matched unit |
|---|---|---|---|
| L5 | 0.35 | −0.0888 | **−0.0016** |
| PMUS today | 12.00 | −0.1412 | **−1.6736** |

**The mechanism is identical and the ratio is ~34× worse on PMUS.** L5 kept
residual small relative to matched inventory and landed at break-even; the
adapted policy on sports markets accumulates twelve residual units per matched
unit and loses catastrophically. This is the exposure-duration asymmetry
(86cda1fa) and the settled PMUS economics (6247777f) expressed as one number.

## 8. Decision answer

**Mature L5 was not making money from standalone trading before incentives.**
Standalone expectancy is indistinguishable from zero everywhere tested, with a
slightly negative point estimate on the full corpus (−0.144% of spend). The
favourable pair basis is real and is consumed by residual settlement losses.

**No market/timeframe/mechanism subset shows independently positive standalone
expectancy**, so P0-5 identifies **no new transfer target**. On the older
accounting's own numbers the strategy's economics were ~5× dependent on
incentives.
