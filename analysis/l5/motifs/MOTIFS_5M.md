# L5's 5m sequences, on their own evidence

BTC5m and ETH5m kept separate throughout. No BTC15m behaviour is borrowed, and
no 5m switch classifier is fitted — the 5m cohorts have 1 and 4 OOS switches
against a pre-fixed ≥40-row threshold, so a classifier there would be BTC15m
wearing a 5m label. Motifs describe what the sequences are, which the sample
size does support.

`analysis/l5/motifs/motifs.py` · 8,070 BTC5m + 2,795 ETH5m scored windows.

## Headline: the fee is in the tape, and in 5m it is the whole story

`usdcSize` exceeds `price × size` on a BUY by **exactly `0.07 · p · (1 − p)`
per share**, or by nothing at all. The ratio is bimodal at 0.00 and 0.07 with
essentially nothing between (761,020 fills). Measured implied fee in cents:

| quoted p | 0.01 | 0.10 | 0.30 | 0.50 | 0.70 | 0.90 | 0.98 |
|---|---|---|---|---|---|---|---|
| measured | 0.069 | 0.630 | 1.470 | 1.750 | 1.470 | 0.630 | 0.137 |
| `7·p(1−p)` | 0.069 | 0.630 | 1.470 | 1.750 | 1.470 | 0.630 | 0.137 |

Two consequences.

**1. Fees were never unavailable.** They are recoverable per fill, exactly.
`usdcSize` is the authoritative cash; using it makes the matched/residual
decomposition reconcile to realized cash to 1e-6 (it previously diverged by a
median $2.20/window, which is what exposed this).

**2. The cohorts are not the same venue relationship.**

| cohort | fills | fee-bearing |
|---|---|---|
| BTC15m | 693,916 | **15.4%** |
| BTC5m | 51,806 | **100%** |
| ETH5m | 11,008 | **100%** |
| ETH15m | 4,290 | **100%** |

It is not an era effect — monthly shares move between 0% and 40% while the
cohort split is absolute. L5 pays the fee on **every** 5m fill and on 15% of
BTC15m fills.

**What this does NOT establish.** The natural reading is maker vs taker, and it
would independently corroborate the BTC15m market-maker finding from a
completely different signal. But the tape shows fee-bearing vs fee-free, not
maker vs taker. `L5_OPERATING_MODEL.md` §4.4 — "do not proxy or infer
maker/taker as fact" — stands. This is a measured fee, and a hypothesis about
its cause.

## The motifs

Motifs 1–3 partition every window by switch structure, so frequencies sum to 1.
SINGLE_FILL is the discovered motif (#5): it is 13–18% of windows, it behaves
nothing like a multi-fill same-side run, and pooling the two hid that.
RAPID_LATE is a crosscutting overlay, reported separately rather than folded in.

### BTC5m — 8,070 windows, 2026-03-19 → 08-31 · train n=5,649 / TEST n=2,421

| motif | n | freq tr | freq TE | tte p50 | px p50 | pre-entry | fills | sw | matched | sw pair | resid | matched P&L | resid P&L | cash | fees | bp/$ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| DEARER_FIRST→complement | 3390 | 38.1% | 51.2% | 257 | 0.640 | −0.000 | 7 | 2 | 0.704 | 0.9700 | 74.3 | −2796 | −9281 | **−12079** | 12123 | **−210** |
| CHEAPER_FIRST→complement | 2111 | 24.1% | 31.0% | 237 | 0.370 | +0.010 | 7 | 2 | 0.709 | 0.9900 | 69.7 | +4552 | −4522 | **+30** | 7386 | **+1** |
| SAME_SIDE_RUN | 1556 | 22.4% | 11.9% | 198 | 0.620 | +0.000 | 3 | 0 | 0 | — | 109.1 | 0 | +9040 | +9040 | 2240 | +723 |
| SINGLE_FILL | 1013 | 15.4% | 5.9% | 153 | 0.620 | −0.010 | 1 | 0 | 0 | — | 50.0 | 0 | +1765 | +1793 | 421 | +779 |
| **ALL** | 8070 | | | | | | | | | | | | | **−1216** | **22169** | **−12** |

OOS bp/$: DEARER −253→−142 · CHEAPER +53→**−89** · SAME_SIDE 726→708 · SINGLE 802→651
RAPID_LATE overlay: n=584 (7.2%), fills p50 8, **−293 bp/$**

### ETH5m — 2,795 windows, 2026-08-13 → 08-31 · train n=1,956 / TEST n=839

| motif | n | freq tr | freq TE | tte p50 | px p50 | pre-entry | fills | sw | matched | sw pair | resid | matched P&L | resid P&L | cash | fees | bp/$ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| DEARER_FIRST→complement | 1071 | 40.9% | 32.3% | 240 | 0.650 | −0.020 | 5 | 1 | 0.620 | 0.9900 | 35.0 | −1637 | +96 | **−1542** | 1518 | **−218** |
| CHEAPER_FIRST→complement | 476 | 19.0% | 12.5% | 227 | 0.300 | −0.020 | 5 | 2 | 0.619 | 0.9600 | 40.0 | +365 | +286 | **+651** | 715 | **+204** |
| SAME_SIDE_RUN | 661 | 22.2% | 27.1% | 206 | 0.610 | +0.000 | 2 | 0 | 0 | — | 55.0 | 0 | +1321 | +1321 | 457 | +568 |
| SINGLE_FILL | 587 | 17.9% | 28.1% | 168 | 0.590 | +0.010 | 1 | 0 | 0 | — | 20.0 | 0 | +1141 | +1141 | 143 | +1533 |
| **ALL** | 2795 | | | | | | | | | | | | | **+1570** | **2833** | **+118** |

OOS bp/$: DEARER −72→**−765** · CHEAPER +166→+365 · SAME_SIDE 367→1003 · SINGLE 1076→2181
RAPID_LATE overlay: n=219 (7.8%), fills p50 6, **−332 bp/$**

## Two findings that only survive because the method was fixed

**A missing REDEEM row is a loss, not missing data.** Redemption is complete
whenever it pays — `redeem_qty / winning-side qty` = 1.000 at p05, p50 and p95,
at exactly $1.00/share. So a long-settled window with no REDEEM row is one where
the held side lost. Scoring only redeemed windows keeps winners, and 99.5% of
no-redeem windows are SINGLE_FILL or SAME_SIDE_RUN. Under that bias the
one-sided motifs returned **+3,194 and +3,109 bp/$**; scored with their losses
they return +723 and +779. Only the 55 windows inside the 24 h capture tail are
genuinely indeterminate, and those are excluded rather than assumed.

**Dollar-weighted and equal-weighted disagree, and the sign flips.**

| cohort | motif | n | win% | mean bp | sd bp | t | p50 bp | p05 bp |
|---|---|---|---|---|---|---|---|---|
| BTC5m | DEARER_FIRST | 3390 | 57.4% | −49 | 3263 | −0.87 | +493 | −6174 |
| BTC5m | CHEAPER_FIRST | 2111 | 47.8% | **+280** | 4566 | **+2.82** | −142 | −5792 |
| BTC5m | SAME_SIDE_RUN | 1556 | 66.1% | **−640** | 9585 | **−2.63** | +1546 | −10000 |
| BTC5m | SINGLE_FILL | 1013 | 62.2% | −670 | 13112 | −1.63 | +920 | −10000 |
| ETH5m | CHEAPER_FIRST | 476 | 52.3% | **+556** | 6131 | +1.98 | +181 | −6452 |
| ETH5m | SAME_SIDE_RUN | 661 | 62.5% | **−992** | 8204 | **−3.11** | +1698 | −10000 |

The one-sided motifs are **significantly negative per window** (t = −2.63,
−3.11) despite positive dollar-weighted totals: most windows win a little, a
minority lose 100% (p05 = −10,000 bp is a total loss). Their positive aggregate
is a handful of large winners, not an edge. This is independent confirmation of
`L5_OPERATING_MODEL.md` §4.6 — residual directional exposure has no
demonstrated skill — from a cohort and method that did not previously test it.

## What this says about the clone

The motifs our engine actually reproduces — OPEN → EXTEND → SWITCH → COMPLETE,
i.e. the two complement-seeking motifs — are the two that **lose money in 5m**:
−210 and +1 bp/$ on BTC5m, −218 and +204 on ETH5m. The motifs that carry the
positive dollar totals are the ones the clone is explicitly built not to do, and
they are negative-expectancy anyway.

Fees are the whole margin. BTC5m paid **$22,169** in fees against **−$1,216** of
net cash on $1.04M of spend. Fee-free, the same sequences return roughly
+$21,000. The 5m cohorts are not where $480/day comes from, and the difference
between them and BTC15m is 15.4% vs 100% fee-bearing fills.

**No production change is proposed from any of this.** Cap 6, the pair ceiling,
sizing, concurrency and execution policy are untouched. CHEAPER_FIRST is the
only motif with a positive mean and a positive t in both cohorts, and it is
flat dollar-weighted on BTC5m and OOS sign-unstable there (+53 → −89), which is
not enough to move a gate.
