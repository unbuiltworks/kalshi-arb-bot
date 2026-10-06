# L5 BTC15m allocation / sizing policy — results

Protocol frozen at **5cab0496** before any result below was computed.
120 snapshots, 12 BTC15m windows, 3,166 verified L5 fills. BTC5m excluded.
No P&L computed or optimized. S3 spec unchanged
(`8b20a259…87df`). No live orders.

## Headline

**Yes — L5 has a reproducible BTC15m allocation function, but it is a
TERMINAL constraint, not a time-varying policy.** Every preregistered bar
failed on its own terms; the surviving finding is a descriptive one that the
bars were specified too early, or on pooled data, to detect.

## Preregistered endpoints — all four fail as specified

| endpoint | bar | result | verdict |
| --- | --- | --- | --- |
| **A1** split law fits | median L1 < 0.10 at T+60 **and** best in ≥9/12 **and** LOWO ≥9/12 | L-C best in **10/12** windows and **10/12** LOWO folds, but median L1 at T+60 = **0.134** | **FAIL** (precision only) |
| **A2** error correction | beats B-light by ≥10 pp in ≥9/12 | **0/12** for L-A, L-C and D-1; **4/12** for L-B. Median margins **−0.14 / −0.48 / −0.49 / −0.01** | **FAIL** decisively |
| **A3** windows converge | IQR < 0.15 at T+30 **and** T+60 | IQR **0.395** / **0.250** (shares); full 0.0–1.0 range | **FAIL** |
| **A4** 80-clip unit scales | \|rho\| ≥ 0.30 in ≥9/12 | best is **1/12** (p), else **0/12** | **FAIL** |

By the letter of the frozen decision rule — A1 does not pass — the verdict
class is **NO ALLOCATION STRUCTURE**. I am not reclassifying it. What
follows is reported as descriptive, not as a passed test.

### A1 detail: the law's identity reproduces, its precision does not

| law (share space) | median L1 @T+60 | window wins | LOWO |
| --- | --- | --- | --- |
| **L-C** equal shares ≡ equal payout ≡ ∝p(1−p) | 0.134 | **10/12** | **10/12** |
| L-A ∝ p | 0.145 | 1/12 | 0 |
| L-B ∝(1−p) ≡ ∝1/p ≡ equal dollars | 0.240 | 1/12 | 0 |

The *shape* is highly reproducible; the *fit* is loose. Pooling all
snapshots is why: it mixes a divergent early phase with a converged late
one.

**The degeneracy recorded before results holds.** L-C's win is **one**
result, not three: equal shares, equal potential payout and ∝p(1−p) are the
same target. Likewise L-B is simultaneously ∝(1−p), ∝1/p and equal dollars.

### A2 detail: "error correction" is entirely explained by the lighter side

As preregistered, **B-light is the binding baseline** and nothing beats it.
The margins are strongly *negative* for L-C and D-1 (−0.48, −0.49): L5's
actual next clip moves *away* from a balanced target far more often than
"buy whichever side is lighter" would. That is a positive finding in its own
right — it is direct evidence of first-class same-side **EXTEND**, and it
refutes any model of L5 as a balance-seeking controller at clip resolution.

## The surviving finding: convergence to a terminal allocation

Cross-window dispersion at every preregistered snapshot. No snapshot and no
normalization was added — all are in the frozen protocol — but **this is not
a preregistered test and is not counted as one.**

| snapshot | matched frac median | matched IQR | w_up median | **w_up IQR** | equal-shares error |
| --- | --- | --- | --- | --- | --- |
| T+15 | 0.000 | 0.336 | 0.000 | 0.369 | 0.500 |
| T+30 | 0.336 | 0.528 | 0.200 | 0.395 | 0.300 |
| T+45 | 0.505 | 0.943 | 0.275 | 0.293 | 0.225 |
| T+60 | 0.719 | 0.312 | 0.366 | 0.250 | 0.134 |
| T+90 | 0.761 | 0.480 | 0.412 | 0.217 | 0.088 |
| T+120 | 0.796 | 0.354 | 0.476 | **0.125** | 0.025 |
| T+180 | 0.933 | 0.142 | 0.490 | **0.063** | 0.010 |
| T+300 | 0.939 | 0.077 | 0.506 | **0.054** | 0.006 |
| T+600 | 0.962 | 0.014 | 0.486 | **0.035** | 0.014 |
| **shutdown** | **0.955** | **0.031** | **0.481** | **0.036** | **0.019** |

Dispersion collapses **monotonically**. The A3 bar of IQR < 0.15 would have
been met from **T+120 onward** in every later snapshot. A3 failed because it
was specified at T+30/T+60 — before the process has converged. The early
allocation is genuinely unconstrained and frequently 100 % one-sided
(w_up ranges the full 0.0–1.0 at T+30).

**These are one finding, not two.** Algebraically
`matched_fraction = 2·min(w, 1−w)`, so w_up → 0.481 *entails* matched
fraction → 0.962 (observed 0.957). They are the same statement and are not
counted as independent confirmations.

Terminal state across all 12 windows: **w_up 0.481 (IQR 0.036, range
0.379–0.527)**, matched fraction **0.955 (IQR 0.031)**, effective matched
pair cost **0.983 (IQR 0.029)**.

### A parametric convergence law was tested and REJECTED

An exponential decay `|w_up(t) − 0.5| = 0.5·exp(−(t−15)/τ)` fits the medians
well by eye, with per-window τ median **119.5 s** (IQR 35.3). It does **not**
survive out of sample: under LOWO it loses to the trivial "already balanced"
baseline — median absolute error **0.108** vs **0.075** — winning only
**51 of 96** held-out points (53 %). *(My initial eyeball estimate of
τ ≈ 35 s was also simply wrong; the per-window least-squares fit gives
≈120 s.)* **No trajectory law is claimed.**

## Probability-bin table (preregistered 0.10-wide bins)

Reported with the power caveat up front: at snapshot resolution only
**1 of 10 bins** reaches n ≥ 30, so every row but `[0.4,0.5)` is
**UNDERPOWERED** and excluded from any verdict. The replenishment and
withdrawal columns are at clip resolution (n = 111–298 per bin) and are
sound.

| bin | n snaps | median w_up | median residual | replenish P | withdraw P | n clips |
| --- | --- | --- | --- | --- | --- | --- |
| [0.0,0.1) | 3 ⚠ | 0.518 | 176 | 0.050 | 0.044 | 161 |
| [0.1,0.2) | 2 ⚠ | 0.480 | 49 | 0.063 | 0.000 | 111 |
| [0.2,0.3) | 8 ⚠ | 0.486 | 75 | 0.051 | 0.000 | 217 |
| [0.3,0.4) | 12 ⚠ | 0.369 | 82 | 0.099 | 0.000 | 202 |
| **[0.4,0.5)** | **31** | 0.275 | 119 | 0.042 | 0.000 | 216 |
| [0.5,0.6) | 28 ⚠ | 0.480 | 60 | 0.057 | 0.005 | 194 |
| [0.6,0.7) | 9 ⚠ | 0.481 | 145 | 0.025 | 0.000 | 161 |
| [0.7,0.8) | 5 ⚠ | 0.501 | 133 | 0.056 | 0.000 | 198 |
| [0.8,0.9) | 9 ⚠ | 0.473 | 177 | 0.044 | 0.003 | 298 |
| [0.9,1.0) | 1 ⚠ | 0.398 | 640 | 0.068 | 0.043 | 162 |

Median w_up sits near 0.5 in 8 of 10 bins with **no monotone trend in p** —
consistent with L-C and against L-A/L-B. Withdrawal probability is ~0
everywhere except the extreme bins, matching the §3 finding that withdrawal
is clock-driven, not probability-driven.

## A4 detail, and two bugs I had to fix to get it

Per-window, at clip resolution, inferred clip size (fraction of 80) against
state at placement:

| feature | median rho | \|rho\| ≥ 0.30 | sign split |
| --- | --- | --- | --- |
| p | −0.033 | 1/12 | 4+ / 8− |
| \|p − 0.5\| | +0.042 | 0/12 | 9+ / 3− |
| residual (inventory error) | +0.061 | 0/12 | 8+ / 4− |
| TTE | **−0.136** | 0/12 | **1+ / 11−** |

All fail the bar. The one consistent *direction* is TTE: clip size grows as
expiry approaches in **11 of 12** windows, but far too weakly to qualify.

**Two bugs in my first version of this test, both of which would have
produced false findings:**

1. **Tautology.** I first correlated *cumulative* clip count with TTE and
   got rho = −0.877. Cumulative counts rise mechanically as a window
   elapses, so that number measures the passage of time, not scaling. Now
   tested on rates and per-clip sizes.
2. **Vacuity.** The per-window test ran at snapshot resolution, where each
   window has only 9 points against the rank test's n ≥ 10 minimum — so
   every per-window rho was `None` and I reported "0/12 windows" as if the
   test had *failed* rather than not run. Now at clip resolution
   (100–300 observations per window).

## Final answer

**Does L5 have a reproducible BTC15m allocation function even though his
next-fill sequence is unpredictable?**

**Yes, as a terminal constraint only.** Across all 12 windows L5 ends in the
same place to within an IQR of 0.036, and the convergence toward it is
monotone across the preregistered snapshots. This is structure the
next-fill tests could not have seen, and it vindicates the instinct to look
for it. But it is reached, not maintained: the early allocation is
unconstrained and often fully one-sided, no trajectory law survives
out-of-sample testing, and at clip resolution L5 moves *away* from balance
more often than a lighter-side rule would.

**Smallest mathematical rule consistent with the evidence:**

> Complete to equal shares by window end.
> $$w_{up}(T) = 0.5 \pm 0.04 \quad\Longleftrightarrow\quad \text{matched fraction}(T) \to 0.96$$
> subject to an effective matched pair cost of $0.983 \pm 0.03$,
> with the path to it unconstrained.

That is **one** parameter and **one** economic constraint. It is a
*completion* rule, not an allocation schedule: it says nothing about which
side to buy next, how much to expose, or when — and the data say those are
not determined.

### What this does and does not license

It does **not** license fitting S3 from these results, and I have not. The
spec is unchanged.

It is worth noting what the rule implies for S3 as already frozen: S3's
reservation price (combined pair cost ≤ 0.96) is **tighter** than L5's
observed terminal 0.983, and S3's residual cap of 80 shares is a
*path* constraint where L5's evidence is a *terminal* constraint. Whether
those are the right choices is a question this study cannot answer, and
changing them on the strength of a descriptive result that failed its own
preregistered bars would be exactly the fitting the brief forbids.

A properly powered follow-up would preregister **convergence time** as the
endpoint — IQR crossing a fixed threshold, with the threshold and the
snapshot set fixed in advance — since that, not the terminal value, is where
the remaining uncertainty lives.
