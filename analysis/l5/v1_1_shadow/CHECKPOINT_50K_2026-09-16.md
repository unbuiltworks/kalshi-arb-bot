# 50,000-observation checkpoint — 2026-09-16 17:55Z

Preregistered checkpoint from `MATURITY.md`. Run exactly as written over the
**complete** measured sample, initial transient included, per explicit owner
instruction. **No gate relaxed, rewritten or reinterpreted.**

Sample: deployment `e3ef4cdd` / commit `6fdbed69`, booted 13:33:58Z. Cycles
**1→779, no gaps**, verified before any number was trusted. 57,894 observations,
831 distinct markets, 4.36 h, 17 blocks.

## VERDICT: STILL NOT MATURE — and V1.1 moved AWAY from its gates

| Gate | | Measured vs target | vs M1 floor |
|---|---|---|---|
| **M0** validity | **PASS** | 0 restarts · 0 `act_artifact_missing` · 0 `no_status` | held |
| **M1** coverage | **PASS** | 57,894 ≥ 25,000 · 831 ≥ 100 · 4.36 h ≥ 3.0 · 17 ≥ 12 | held |
| **M2** baseline steady | **FAIL** | V1 = 0.9009 departed 1.00; V1's own M4 now **passes** (1.14 pp) but its M3 still fails (2.37 pp) | improved, still failing |
| **M3** precision | **FAIL** | V1.1 **6.57 pp** vs 2.0 | 8.10 → 6.57, better |
| **M4** doubling | **FAIL** | V1.1 **5.23 pp** vs 2.0 | 3.04 → 5.23, **WORSE** |
| **M5** no trend | **FAIL** | V1.1 **15.80 pp** vs 3.0 | 8.52 → 15.80, **MUCH WORSE** |

Under the owner's refined rule, C/D are produced when **V1.1's own** gates pass
(M0, M1, M3, M4, M5). Three of those fail, two by more than at the previous
checkpoint. **C and D are therefore not produced.**

M2 also still fails and is recorded here as an **UNRESOLVED CONTROL LIMITATION**,
not as a passed gate. It is not what blocks the package.

## The rates

```
                        M1 floor (35,567)    50k checkpoint (57,894)
V1 proposal rate              0.8873                 0.9007
V1.1 proposal rate            0.2580                 0.3076
Stage-1 suppression           0.7391                 0.6895
reduction vs V1               70.9%                  65.8%
markets / cycle                65.9                   74.3
```

## Which gates fail, and why — measured, not theorised

M5's last four 30-minute window deviations for V1.1 are
**−3.74, −5.91, +15.80, +8.91 pp**. The rate did not settle; it **jumped
upward** in the two most recent windows. V1 moved the same way at the same
time (+5.28 pp, its own worst window).

What is directly observable alongside that, from the cycle lines themselves:
the universe grew from ~50 markets/cycle at boot to ~66 at the M1 floor to
**~103 by 17:17Z** as the evening slate filled out, and `residual_cap_rate`
**fell** from 0.149 (16:35Z) to 0.086 (17:46Z) even as participation rose.

Both controllers rising together, while the cap binds *less* often and the
universe roughly doubles, is consistent with the sample's composition changing
underneath the measurement rather than the measurement converging.

**That is the boundary of what this read establishes.** Per the owner's
ordering — report which gates fail and why before any new research — the
composition question is named and NOT investigated here.

## Distributions (latest rolling 5,000-row window, 17:56Z)

```
n 5,000 · distinct markets 186 · elapsed 952 s
propose_rate 0.3962 · v1_propose_rate 0.9072 · stage1_wait_rate 0.5958
p_act        p10 0.201  p50 0.329  p90 0.405   threshold 0.35 on every row
p_act range  min 0.0928  max 0.8135   (first rows above 0.8 in the sample)
stage2_tier      full 4,995 / base 5            = 99.9%
v1_stage2_tier   full 1,429 / base 3,571        = 28.6%
act_tiers        full 4,909 / base 91
divergence   NONE 4,415 · RISK_CAP_FORCED 585 · MODEL 0 · VENUE 0
residual_cap_rate 0.1170 · exposure_cap_rate 0.0000
desired_inv_l5   p10 10.0   p50 126.70  p90 794.10   L5 share units
scaled_desired   p10 0.062  p50 0.784   p90 4.915    our contracts
our_inventory    p10 0.0    p50 0.71    p90 4.84     max 6.53
spent_usd        p10 0.0    p50 0.414   p90 2.987    max 3.14
last_rel         extend 3,552 · open 1,424 · complete 24
vs_v1            STAGE1_SUPPRESSED 2,555 · SAME 2,445
```

Unchanged and reconfirmed: **the binding constraint is residual, not exposure.**
`exposure_cap_rate` is still **0.0000** across the whole sample; `spent_usd`
still tops out near $3.00 against Envelope A's $30 exposure ceiling.
`MODEL_DISAGREEMENT` and `VENUE_QUANTIZATION` remain **0** everywhere.

The prospective coverage lift holds and widened: V1.1 Stage-2 full tier
**99.9%** against the control's **28.6%**.

## Recommendation

**No.** Not sufficient to ask for live authorization.

At the M1 floor the participation rate was imprecise. At this checkpoint it is
imprecise **and moving**: the doubling delta and the trend deviation both grew
as the sample grew. A number that is drifting away from stability as more data
arrives cannot size a bounded probe.

## Next

The 100,000-observation checkpoint is the last preregistered one, and the
**stopping guard trips there**: if M1 is satisfied at 100k and M3 still fails,
the conclusion recorded is that the rate is **NOT CONVERGING** — reported as
such, rather than extending the sample further in search of a stable number.
