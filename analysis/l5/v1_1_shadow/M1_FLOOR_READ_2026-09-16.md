# M1-floor maturity read — 2026-09-16 16:35Z

Run exactly as preregistered in `MATURITY.md`. **No gate was relaxed, rewritten
or reinterpreted.** A failing gate is recorded as a failure.

Sample: deployment `e3ef4cdd` / commit `6fdbed69`, booted 13:33:58Z. Cycles
1→540, **no gaps**. 35,567 observations, 486 distinct markets, 3.02 h.
The 13:15–13:40Z pilot is excluded.

## VERDICT: NOT MATURE — four of six gates fail

| Gate | Result | Measured vs target |
|---|---|---|
| **M0** validity | **PASS** | 0 restarts · 0 `act_artifact_missing` · 0 `no_status` |
| **M1** coverage | **PASS** | 35,567 obs ≥ 25,000 · 486 markets ≥ 100 · 3.02 h ≥ 3.0 · 12 blocks ≥ 12 |
| **M2** baseline steady | **FAIL** | V1 = 0.8876 has departed 1.00, but fails its *own* M3 (3.01 pp) and M4 (2.84 pp) |
| **M3** precision | **FAIL** | V1.1 half-width **8.10 pp** vs 2.0 · V1 3.01 pp |
| **M4** doubling | **FAIL** | V1.1 **3.04 pp** vs 2.0 · V1 2.84 pp |
| **M5** no trend | **FAIL** | V1.1 max dev **8.52 pp** vs 3.0 · V1 5.42 pp |

**M2 is NOT the sole failure**, so the owner's "if M2 alone fails" contingency
does not apply. V1.1 fails M3, M4 and M5 on its own merits, independently of the
control's defect.

### A measurement error caught and corrected before reporting

The first pull was truncated by `limit: 500` and returned cycles 41→540, so
elapsed hours and block count were artifacts of the window rather than the
sample. On that truncated read V1.1's doubling delta was **0.43 pp (passing)**;
on the complete sample it is **3.04 pp (failing)**. Reported here so the
correction is on the record, not buried.

## Cumulative — the same observations, two controllers

```
observations          35,567        distinct markets   486
V1 proposal rate      0.8873        V1.1 proposal rate 0.2580
Stage-1 suppression   0.7391        reduction vs V1    0.7092
```

## Rolling 5,000-row windows (the three most recent)

| 16:15Z | 16:25Z | 16:35Z | |
|---|---|---|---|
| 0.2684 | 0.2854 | 0.2586 | V1.1 propose rate |
| 0.8764 | 0.8904 | 0.8752 | V1 propose rate |
| 0.7308 | 0.7134 | 0.7402 | Stage-1 suppression |
| 0.1402 | 0.1256 | 0.1492 | risk-cap rate (**all** `residual_ceiling`) |
| 0.000 | 0.000 | 0.000 | exposure-cap rate |
| 98.1% | 98.2% | 98.1% | V1.1 Stage-2 **full** tier |
| 36.4% | 34.9% | 32.2% | V1 Stage-2 **full** tier |
| 0.3103 | 0.3084 | 0.3015 | mean `p_act` (threshold 0.35 on every row) |

## Where the instability lives — an observation, NOT a gate pass

The recent windows span **2.7 pp** (0.2586–0.2854). The full-sample CI is
**8.10 pp**. The dispersion is therefore concentrated in the early blocks, when
the theoretical book was filling from flat and the rate fell from 0.66 through
0.36 to ~0.17 before recovering to ~0.27.

That is a **hypothesis about the next sample, not a result from this one**: a
window beginning after the book has filled might clear M3 and M5. It is
recorded as a hypothesis because the preregistered criterion measures the sample
it was defined over, and moving the start to make the gates pass is exactly the
manoeuvre the criterion exists to prevent.

## Distributions (latest window)

```
p_act            p10 0.153  p50 0.291  p90 0.405   threshold 0.35, every row
desired_inv_l5   p10 10.0   p50 92.97  p90 863.57  L5 share units
scaled_desired   p10 0.062  p50 0.575  p90 5.345   our contracts
our_inventory    p10 0.0    p50 0.50   p90 5.27    max 6.53
spent_usd        p10 0.0    p50 0.319  p90 3.001   max 3.30
last_rel         extend 3,189 · open 1,677 · complete 134
divergence       NONE 4,254 · RISK_CAP_FORCED 746 · MODEL 0 · VENUE 0
```

### The binding constraint is residual, not exposure

`exposure_cap_rate` is **0.000 across the entire sample** while
`residual_cap_rate` runs 0.13–0.15, and `spent_usd` tops out at ~$3.00 against
Envelope A's **$30** exposure ceiling. At k = 0.00619 the controller never
approaches the exposure ceiling, because the **$3.00 residual ceiling** caps it
first. Raising exposure without raising residual would change nothing.

`MODEL_DISAGREEMENT` and `VENUE_QUANTIZATION_FORCED` are **0** everywhere: every
V1.1 WAIT is Stage 1 (`act_probability_below_threshold`), and every risk veto is
the residual ceiling. The two reasons never merged.

## Why this blocks the live decision

The participation rate is the one number C and D are derived from. Its 95% CI
half-width (**8.10 pp**) is larger than the preregistered materiality threshold
(**5.0 pp**).

Concretely, at 180 evaluations/market-hour:

```
p = 0.258        → 46.4 proposals / market-hour
p = 0.258 ± 8.1pp → 31.9 .. 61.1     a 1.9x range
```

For any target proposal rate the implied concurrency varies by roughly 1.9×,
which against Envelope A's 3 concurrent markets is the difference between 3 and
6. That is more than one market, which is precisely the threshold at which the
D recommendation moves.

**Sizing a bounded probe from this number would be sizing from noise.**
