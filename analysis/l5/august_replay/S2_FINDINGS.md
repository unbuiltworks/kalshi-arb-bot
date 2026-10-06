# Diagnostic S2: do the implicated mechanisms close the L5 gap?

**HISTORICAL DIAGNOSTIC ONLY. No shadow and no live authorization**, and none
is sought here. Not a replacement for the running prospective S1 cohort.
Frozen prospective S1 is imported read-only, asserted at
`441c375e…b6fe`, and **never written**; `assert_s1_frozen()` additionally
checks that S2 still matches S1 on every field it claims to leave alone, so a
silent drift fails the run rather than the review.

Diagnostic S2 fingerprint: `5650ca16943cd3e2da00254578d2e0bc1fbd146bfa76c9e59fc1ba7e396f7865`
— **committed before S2 had been run on anything**, including the diagnostic
window. Both runners assert it and abort if it moved.

## 1. What changed, and what deliberately did not

| | |
|---|---|
| **Changed** | quote maintenance (two causal reprice triggers); mint opportunity *recorded*; an independent evidence arm |
| **Unchanged** | `classify_fill` imported verbatim and not weakened; **residual cap stays 80**; `QUOTE_SUPPRESSION_ONLY`; quantity 23.61; `levels: 1`; **offset 0 ticks**; TTE 60; no 5c EXTEND; no aggressive opposite side |

### The refresh rule, derived from L5 and not from P&L

`evidence_refresh.py` computes no fill, no position and no cash figure. From
L5's own 398 fills in the diagnostic window **only** (the other 11 were not
read, so they stay a clean test):

| observable | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| inter-fill gap, same token | 1 s | 18 s | 48 s | **69 s** |
| offset below the touch at T−1 | 1 | 5 | **6** | 9 |

L5 is **never** absent from a token for more than 69 s and is **never** filled
more than 9 ticks behind the touch. S1 left a bid untouched for **876 s**, 12.7×
the longest gap L5 exhibits, ending **62 ticks** behind the market.

| trigger | threshold | source |
|---|---|---|
| `REPRICE_ON_DISPLACEMENT` | touch > **6 ticks** above us | offset p99 |
| `REPRICE_ON_STALENESS` | below the touch > **69 s** | max inter-fill gap |

Both are taken at the **loose** end on purpose: a larger threshold reprices
less often and therefore manufactures *fewer* hypothetical fills. There is
still no fixed lifetime — a test asserts a quiet book at the touch reprices
zero times however old the order. A reprice is a cancel/replace and is
**never** a fill.

In practice `REPRICE_ON_STALENESS` fires 0–7 times per window; displacement
does nearly all the work, so the mechanism reduces to *"reprice when the touch
walks more than 6 ticks away."*

## 2. The selection bias, stated wherever these fills are quoted

`EVIDENCED_LEVEL_CONSUMED` fires **iff** the best bid has fallen *strictly
below* our own price — that is, **only when the market has crossed our bid.**
It therefore mechanically selects the subset of hypothetical maker fills whose
contemporaneous book movement went **against us**, and systematically excludes
benign fills (size leaving our level while the touch holds), which the frozen
classifier returns as `UNCERTAIN_PARTIAL` and excludes from economics.

**The resulting acquisition prices and settlement figures are not an unbiased
estimate of what a real resting order would have experienced.** They are
biased toward adverse selection. Arm C exists because third-party prints can
evidence a fill *without* requiring the touch to cross us — the only way to
see past that bias with this data.

**Arm A** = book only, reproducing the frozen module's own condition
(`last_prints` never assigned). Kept unchanged for comparability.
**Arm C** = third-party prints with **every leg involving L5 removed** (1,964
of 2,355 maker-order legs in the diagnostic window), so S2 is never credited
off L5's own activity.

## 3. The diagnostic window, 2026-08-21 08:15:00Z–08:30:00Z

| arm A | L5 | frozen S1 | diagnostic S2 |
|---|---:|---:|---:|
| fill legs | 398 | 10 | **63** |
| fills / min | 26.53 | 0.67 | 4.20 |
| inter-fill gap p50 / max | 0 / 28 s | 13 / 78 s | 11 / 85 s |
| shares acquired | 9,855.33 | 236.10 | 1,487.43 |
| matched shares | 4,813.44 | 70.83 | **708.30** |
| residual | 228.45 | 94.44 | 70.83 |
| **matched fraction** | 0.977 | 0.600 | **0.952** |
| VWAP up / down | 0.765 / 0.226 | 0.410 / 0.457 | 0.697 / 0.355 |
| **pair cost** | **0.9911** | 0.8671 | **1.0518** |
| both sides active | yes | yes | yes |
| ticks quoting both sides | n/a | 199 | **581** |
| **side-ticks > 6 ticks stale** | n/a | **896 / 1,099** | **0 / 1,422** |
| worst displacement | n/a | **62 ticks** | **6 ticks** |
| quote replacements | n/a | 0 | **26** (all displacement) |
| mint opportunities | n/a | — | 18 ticks, 519.42 sh bounded |
| spend → payout → net *(diagnostic)* | 4,822 → 4,813 → **−8.64** | 104.6 → 70.8 → **−33.76** | 770.2 → 708.3 → **−61.86** |

Arm C (third-party prints, L5 excluded): S2 **83 legs**, matched **944.40**,
matched fraction 0.964, pair cost **1.0347**, net −55.72.

### Trajectories through the whole 15 minutes (arm A)

| at | UP/DN bid | L5 matched / legs / pair | S1 matched / legs / pair | S2 matched / legs / pair |
|---|---|---|---|---|
| 01:00 | 0.50 / 0.49 | 95 / 31 / 0.996 | 70.8 / 6 / 0.940 | 70.8 / 6 / 0.940 |
| 03:00 | 0.67 / 0.32 | 1,050 / 85 / 1.010 | 70.8 / 9 / 0.890 | 141.7 / 16 / 0.964 |
| 05:00 | 0.68 / 0.31 | 1,509 / 134 / 1.015 | **70.8 / 10 / 0.867** | 259.7 / 22 / 1.069 |
| 08:00 | 0.73 / 0.26 | 1,989 / 202 / 0.991 | **70.8 / 10 / 0.867** | 425.0 / 38 / 1.038 |
| 11:00 | 0.92 / 0.07 | 2,731 / 268 / 0.957 | **70.8 / 10 / 0.867** | 566.6 / 52 / 1.043 |
| 14:59 | 0.999 / — | 4,813 / 398 / 0.991 | **70.8 / 10 / 0.867** | 708.3 / 63 / 1.052 |

S1 is **flat from 04:00 onward** — inert for the last eleven minutes. S2 climbs
continuously and keeps acquiring on both sides, its residual oscillating
0 ↔ 94.4 instead of latching. **And its pair cost degrades monotonically**, from
0.940 at 01:00 to 1.052 at the close: each reprice re-offers at a worse level.

## 4. Generality — frozen S2, the other eleven windows, nothing tuned

| window | cL2 | L5 / S1 / S2 legs | L5 / S1 / S2 matched | matched frac S1 → S2 | S2 pair | S1 → S2 stale |
|---|---|---|---|---|---:|---|
| 1787274000 | Y | 145 / 8 / 33 | 1,366 / 47 / 354 | 0.500 → 0.909 | 0.998 | 789 → **0** |
| 1787290200 | Y | 379 / 32 / 95 | 4,431 / 354 / 1,110 | 0.938 → 0.990 | 1.098 | 849 → **0** |
| 1787296500 | **N** | 241 / 4 / 24 | 3,231 / 0 / 283 | **0.000 → 1.000** | 1.080 | 742 → **0** |
| 1787305500 | Y | 246 / 10 / 78 | 2,657 / 71 / 874 | 0.600 → 0.949 | 1.008 | 1,152 → **0** |
| 1787306400 | Y | 268 / 14 / 48 | 2,943 / 118 / 519 | 0.714 → 0.917 | 1.059 | 813 → **0** |
| 1787307300 | Y | 236 / 16 / 46 | 2,421 / 142 / 496 | 0.750 → 0.913 | 1.000 | 845 → **0** |
| 1787311800 | Y | 238 / 6 / 20 | 3,687 / 24 / 189 | 0.333 → 0.800 | 0.976 | 879 → **0** |
| 1787362200 | Y | 281 / 22 / 86 | 3,549 / 236 / 968 | 0.909 → 0.954 | 1.022 | 755 → **0** |
| 1787363100 | Y | 208 / 37 / 102 | 2,472 / 401 / 1,157 | 0.919 → 0.961 | 1.009 | 719 → **0** |
| 1787365800 | Y | 302 / 35 / 75 | 2,950 / 378 / 850 | 0.914 → 0.960 | 1.021 | 719 → **0** |
| 1787369400 | Y | 220 / 6 / 50 | 2,817 / 24 / 543 | 0.333 → 0.920 | 1.025 | 832 → **0** |

### Aggregate — arm C (third-party prints, every L5 leg removed)

| | all 11 | COMPLETE-L2 only (10) |
|---|---|---|
| fill legs S1 / S2 vs L5 | 0.081× → **0.321×** | 0.081× → **0.321×** |
| matched shares S1 / S2 vs L5 | 0.067× → **0.312×** | 0.067× → **0.312×** |
| matched fraction L5 / S1 / S2 | 0.952 / 0.794 / **0.959** | 0.952 / 0.810 / **0.957** |
| net per dollar spent *(diagnostic)* | L5 −0.18% / S1 **−5.58%** / S2 **−5.96%** | −0.03% / −3.54% / −5.89% |

Arm C **strengthens the behavioural result** (3.95× the fill legs, 4.66× the
matched shares, and a matched fraction that now slightly exceeds L5's) and
**removes the arm-A artifact**: with the less-biased evidence standard S1 is
also negative (−5.58%), confirming that its +8.06% in arm A was residual luck
in four windows rather than an edge. Both policies lose; S2 loses more, on
roughly four times the volume.

### Aggregate (arm A)

| | all 11 | COMPLETE-L2 only (10) |
|---|---|---|
| fill legs S1 / S2 vs L5 | 0.069× → **0.238×** | 0.074× → **0.251×** |
| matched shares S1 / S2 vs L5 | 0.055× → **0.226×** | 0.061× → **0.241×** |
| matched fraction L5 / S1 / S2 | 0.953 / 0.800 / **0.947** | 0.952 / 0.817 / **0.945** |
| net per dollar spent *(diagnostic)* | L5 −0.18% / S1 **+8.06%** / S2 **−6.33%** | −0.03% / +10.07% / −6.29% |

The aggregate is reported twice because three of the twelve L5-active windows
failed the forensic selection gates (one on a 226 s book gap, two because L5
went quiet in the final sub-interval). The COMPLETE-L2 subset agrees, so no
headline is carried by the window with the hole.

**S1's +8.06% is not an edge.** It rests on 4 of 11 windows, at total spend
**1,726** against L5's **32,906**, with outcomes dominated by which side its
residual happened to land on. With n=11 and residual-driven settlement that is
noise, not skill.

## 5. Answers to the five questions

**1 — Repricing.** The evidence bounds staleness at 69 s (cadence) and 6 ticks
(offset); the implemented rule is causal and threshold-free in time. It
**eliminates stranding completely: 0 stale side-ticks in 11 of 11 windows**
against S1's 719–1,152, worst displacement 62 ticks → 6.

**2 — Fill evidence vs adverse movement.** Documented in §2, in the module
docstring and in every output record. Arm C is specified independently on
third-party prints with all L5 legs removed. It raises S2's fills (63 → 83 on
the diagnostic window) and improves pair cost (1.0518 → 1.0347), confirming
the bias is real — but **pair cost stays above par**, so the bias does not
explain away the economics.

**3 — Complete-set mint.** Modelled from both token books and CTF mechanics,
bounded by complement capacity minus everything at or above our price, and
labelled **`MINT_OPPORTUNITY`, never a fill**. Opportunity is sporadic: 0–64
ticks per window, **zero in one window**. Ceiling: **≤ 27% of the fill-count
gap**. The reason is structural — the two touches sum to ≈0.99 for essentially
the whole window, so a bid *at the touch* rarely reaches par. L5's mint fills
priced at or **above the ask**. **Mint access therefore needs a pricing change,
not just representation.** Not implemented.

**4 — Residual control. The cap was not raised, and should not be.** Quote
maintenance **alone** lifted both-side quoting from 3,280 to 5,743 side-ticks
(1.8×) and matched fraction from 0.800 to **0.947**, against L5's 0.953 — i.e.
two-sided acquisition is now essentially L5-like with the cap untouched at 80.
The cap is still *reached* in 7 of 11 windows, but it is **no longer a terminal
latch**: S2 works back below it instead of going dark. The binding constraint
has moved to acquisition *price*, so raising the cap would not address what now
limits S2 and would only enlarge one-sided exposure.

**5 — Side-by-side and trajectories.** §3 and §4; full per-second CSVs in
`TRACE3_arm{A,C}.csv` and `traces/TRACE3_armA_<window>.csv`.

## 6. Verdict

**GO on the mechanism identification.** The August trace named the right
mechanisms, and the repair generalizes: applied **unchanged** to eleven windows
it had never seen, quote maintenance eliminated stale quoting in 11/11, raised
fill count 3.46×, matched shares 4.09×, and brought matched fraction from 0.800
to 0.947 against L5's 0.953. That is a material closure of the **behavioural**
gap, and it is not a one-window artifact.

**NO-GO on S2 as a strategy direction, and no authorization is sought.** Two
reasons, both from the eleven:

1. **Most of the behavioural gap remains.** S2 is still only 0.24× L5 on fill
   count and 0.23× on matched shares. ~76% of the gap is untouched by these
   mechanisms, and the mint ceiling caps what that channel could add at ≤27%.
2. **The economics moved the wrong way, consistently.** S2's pair cost exceeds
   par in **9 of 11** windows (L5: 2 of 11) and its settlement net is negative
   in **10 of 11**. Repricing removes stranding but, against a fill standard
   that can only evidence fills where the market crossed us, converts an inert
   quote into one continuously re-offered at a worse price and filled as the
   move continues. **An inert loss becomes an active one.** The trajectory in
   §3 shows it directly: pair cost degrading 0.940 → 1.052 as the reprices
   accumulate.

So the two defects **interact**, and that is the substantive finding: fixing
staleness while leaving (a) the fill-evidence standard adverse-selecting and
(b) the quote pinned at the touch produces L5-like *activity* at
systematically worse *prices*.

### The next testable mechanism, not implemented here

Both remaining failures point at the same untouched parameter: **placement
price**. S2 still quotes `offset_from_bid_ticks: 0`. L5's resting fills sit
deeper with the touch falling toward him (61.6% beyond −4 ticks at T−30 in the
prior BBO-offset work), and his mint fills price at or above the ask. Neither
is reachable from the touch. A depth/pricing rule is the next thing to derive
from evidence — and it is exactly the §4 tension the earlier offset study
recorded rather than acted on.

**None of this may modify Candidate S1 while the prospective cohort runs.**
