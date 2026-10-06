# Preregistration — L5 BTC15m allocation / sizing policy study

**Fixed before any result was computed.** No S3 parameter will be fitted from
these results; `analysis/l5/s3/spec.py` stays frozen at
`8b20a25936d3b3a406403f6424eff30d3360d22f188b28a10644f356b65287df`. No P&L
optimization. No live orders.

## 0. Why this is not a repeat of a failed test

The completed tests reject **next-fill prediction**: next-action lift 1.001
vs persistence (S-PERSIST 12/12 windows), and conditional future-inventory
forecasting (E2_POSITIVE = False at every horizon). Neither tests whether L5
targets a **repeatable allocation**. A process can have an unpredictable
event *ordering* and a highly regular *stationary distribution* — a thermostat
is unpredictable second to second and tightly regular in aggregate. That is
the hypothesis here, and it is distinct.

**Primary question.** Does L5 target a repeatable inventory/capital
allocation as a function of market probability, elapsed time and existing
inventory, even though individual fill ordering is unpredictable?

## 1. Population and snapshots

All **12 BTC15m** windows, 3,166 verified L5 fills. BTC5m is excluded
entirely: it is identification-only and no 5m relationship may become a PMUS
rule.

Causal snapshots at fixed seconds after open:
**+15, +30, +45, +60, +90, +120, +180, +300, +600, and shutdown**
(shutdown = L5's last observed action in the window). Each snapshot uses
only information available at or before that second. No future information.

Recorded at every snapshot: market probability and BBO both outcomes;
cumulative UP/DOWN shares; cumulative UP/DOWN dollars; matched inventory;
residual and signed imbalance; inferred ≤80-share clip count; VWAP per
outcome; effective matched pair cost; % of eventual window capital already
deployed; % of eventual matched inventory already accumulated.

**Inferred exposed quantity is kept strictly separate from observed fill
quantity.** Partial fills are not independent sizing decisions — 113 of 613
multi-fill clips sum to exactly 80.00. Clip inference uses the frozen
break rules B1/B2/B3; ≤80 is a ceiling, not an assumed order size.

## 2. Candidate allocation laws — and their degeneracies, established first

In share space for a binary complementary market ($p_{down} = 1-p_{up}$),
**seven of the nine named candidates collapse into three distinct laws.**
Verified algebraically before any data was touched:

| named candidate | share-space target $w_{up}$ | distinct? |
| --- | --- | --- |
| allocation ∝ p | $p$ | **L-A** |
| allocation ∝ 1−p | $1-p$ | **L-B** |
| allocation ∝ 1/p | $\frac{1/p}{1/p+1/q} = q = 1-p$ | ≡ L-B |
| equal dollars | $\frac{q}{p+q} = 1-p$ | ≡ L-B |
| equal shares | $0.5$ | **L-C** |
| equal potential payout | $0.5$ (payout is \$1/share, so payout ≡ shares) | ≡ L-C |
| allocation ∝ p(1−p) | $\frac{pq}{pq+qp} = 0.5$ | ≡ L-C **as a split law** |

So the split-law contest has exactly **three** competitors:

- **L-A** $w_{up} = p$ — buy in proportion to probability (favourite-weighted)
- **L-B** $w_{up} = 1-p$ — equal dollars / inverse-probability (cheap-side-weighted)
- **L-C** $w_{up} = 0.5$ — equal shares / equal payout / p(1−p)

This is recorded now so that a win by L-B is **never** reported as three
independent confirmations.

$p(1-p)$ is only meaningful as a **scale** law, so it is tested as one:

- **S-1** total deployed shares ∝ $p(1-p)$ (maximal at the midpoint)
- **S-2** probability-banded clip counts (step function of the band)
- **S-3** total deployed independent of $p$ (flat null)

And one dynamic law:

- **D-1** inventory-error correction: the next clip moves allocation toward a
  target $w^\*$ from whichever of L-A/L-B/L-C wins, with the side chosen as
  the one reducing $|w_{obs} - w^\*|$.

## 3. Preregistered bins

Probability bins are **0.10-wide, fixed**:
`[0.0,0.1) [0.1,0.2) … [0.9,1.0]`, keyed on the UP mid at the snapshot.
Bins are not merged, moved or re-chosen after seeing results. Any bin with
n < 30 is reported as **UNDERPOWERED** and excluded from a verdict rather
than pooled into a neighbour.

Per bin, reported: median inferred shares exposed/acquired → dollars → clip
count → imbalance tolerance (median |residual|) → replenishment probability
→ withdrawal probability.

## 4. Metrics and baselines

Allocation error is **L1 distance** $|w_{obs} - w_{target}|$ in share space,
and is reported again in each alternative normalization: share, dollar,
potential-payout, clip-count, matched-vs-residual.

Baselines for the error-correction test (D-1), all preregistered:

- **B-rand** random side (0.5)
- **B-persist** same side as the previous clip
- **B-heavy** always the side already heavier
- **B-light** always the side already lighter

B-light is the strongest baseline and is expected to be hard to beat,
because moving toward *any* balanced target correlates with buying the
lighter side. **A win over B-light is the only result that counts for D-1.**

## 5. Thresholds, fixed now

| endpoint | bar |
| --- | --- |
| **A1** a split law fits | one law has median L1 error **< 0.10** at T+60, is the best law in **≥9 of 12** windows, and wins the held-out window in **≥9 of 12** LOWO folds |
| **A2** error correction is real | next clip reduces distance to $w^\*$ more often than **B-light** by **≥10 percentage points**, reproducing in **≥9 of 12** windows |
| **A3** windows converge | cross-window **IQR** of $w_{obs}$ **< 0.15** at both T+30 and T+60, and tighter than a permutation null at p < 0.05 |
| **A4** the 80-clip unit scales | Spearman \|rho\| **≥ 0.30** between clip count and (p, inventory error, TTE) reproducing in **≥9 of 12** windows |

**Window-by-window and LOWO are mandatory. A relationship that exists only
in aggregate does not qualify** and will be reported as AGGREGATE-ONLY.

## 6. Verdict classes, defined before results

- **REPRODUCIBLE ALLOCATION FUNCTION** — A1 passes, plus at least one of
  A2/A3/A4
- **PARTIAL / CONSTRAINED** — A1 passes but neither A2 nor A3 nor A4
- **AGGREGATE-ONLY** — a law fits pooled data but fails the ≥9/12
  window-level or LOWO requirement
- **NO ALLOCATION STRUCTURE** — no law clears A1

A null result is a complete answer and will be reported as one. The final
deliverable states whether L5 has a reproducible BTC15m allocation function
despite unpredictable fill ordering, and if so gives the **smallest**
mathematical rule consistent with it.
