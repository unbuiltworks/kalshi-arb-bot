# OPENING15 — result

**Run strictly to `PREREGISTRATION_OPENING15.md`, committed before anything was
computed.** `OPEN_A` 0–30 s and `OPEN_B` 30–60 s were the only subdivisions
examined. No feature added, no boundary moved, no rule outside the fixed list,
and economics computed last without touching rule selection. The completed
`NEXTACTION` protocol and its results are unchanged. No S3, no S1 change, no
live orders.

The prior 0–60 s result (n=136, lift 1.158, 0.530 bits) was treated as
**discovery**, and it is not counted as confirmation of anything below.

---

## FINAL CLASSIFICATION: **DESCRIPTIVE**

The opening's structure is real and it **subdivides cleanly** — but **none of
it survives as out-of-sample prediction.**

| | `OPEN_A` 0–30 s | `OPEN_B` 30–60 s | 0–60 s combined |
|---|---:|---:|---:|
| **descriptive** transition lift | **1.287** | **1.195** | 1.158 |
| bits gained | **0.791** | **0.678** | 0.530 |
| n transitions | 75 | 59 | 136 |
| meets n≥50 and ≥1.10 | **yes** | **yes** | yes |
| | | | |
| **LOWO next-side accuracy** | 0.7302 | 0.4510 | 0.6855 |
| best baseline | 0.7778 | 0.5882 | 0.6855 |
| **predictive lift** | **0.939** | **0.767** | **1.000** |
| meets the bar | **no** | **no** | **no** |

Coverage was **100%** in every phase — nothing is hidden behind abstention.

> **Both subdivisions carry *more* descriptive structure than the parent
> phase** — 1.287 and 1.195 against 1.158, and 0.79 and 0.68 bits against
> 0.53. **And both fail the predictive test outright**, 0–30 s at 0.94× and
> 30–60 s at **0.77×**, i.e. the fold-selected rule does *worse* than simply
> repeating the previous side.

The rule selected in **every fold** was `S-PERSIST` — which *is* the
persistence baseline. That is why the combined lift is exactly 1.000: the
procedure, given free choice over six side rules, could find nothing better
than the baseline it is measured against.

## Concentration verdict

The preregistered options do not give one answer, because the two measures
disagree, and collapsing them would hide the whole point:

* **on the descriptive transition measure → (3) consistent across both.** Both
  halves clear n≥50 and lift≥1.10.
* **on the LOWO prediction measure → none meets the bar.** Not (1), not (2),
  not (3).

**The jackknife (§8.4) is vacuous here and is reported as such rather than as
a finding.** It asks whether removing the two top-contributing windows pushes
the 0–60 lift below 1.10; the lift was **1.000 with every window included**, so
a below-bar answer after removal says nothing about those windows. Full lift
1.000 → lift without `1787296500` and `1787300100` 1.000, n=73. Reporting that
as "driven by a few windows" would have been wrong, and an earlier version of
the code did exactly that before this was corrected.

## Window-by-window replay — all 12 windows

No aggregate is reported without it. Per-window side accuracy ranges from
**0.50 to 1.00**, and the windows carrying real weight disagree with each other.

| window | book obs 0–60 s | units | clips | $1 pairs | first unit | scored | **side acc** | opening sequence (first 7 units) |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1787274000 | 18 | 3 | 3 | 0 | t+10 | 2 | 1.000 | D@0.56×5 · D@0.55×35 · D@0.52×79 |
| 1787290200 | 8 | 9 | 8 | 1 | t+6 | 8 | 0.500 | **PAIR** · D@0.68×53 · D@0.66×20.5 · D@0.69×60.7 … |
| 1787296500 | 10 | 33 | 30 | 3 | t+6 | **32** | 0.594 | D@0.42×69.6 · D@0.38×43.7 · D@0.38×4 · D@0.36×14.2 … |
| 1787300100 | 18 | 20 | 20 | 0 | t+7 | **19** | 0.842 | D@0.59×6 · D@0.60×12.4 · D@0.59×17.4 · D@0.60×46.3 … |
| 1787305500 | 34 | 14 | 14 | 0 | t+6 | 13 | 0.692 | D@0.59×119.1 · D@0.59×5 · D@0.57×17.3 … |
| 1787306400 | 18 | 3 | 3 | 0 | t+7 | 2 | 0.500 | D@0.46×80 · D@0.45×53.1 · U@0.44×80 |
| 1787307300 | 20 | 12 | 12 | 0 | t+6 | 11 | 0.909 | D@0.57×20 · D@0.56×17.3 · D@0.57×60 … |
| 1787311800 | 10 | 12 | 12 | 0 | t+6 | 11 | 0.727 | D@0.43×80 · D@0.42×16 · D@0.45×14 · D@0.58×64 … |
| 1787362200 | 18 | 3 | 3 | 0 | t+7 | 2 | 1.000 | U@0.50×4 · U@0.50×7.1 · U@0.51×5 |
| 1787363100 | 54 | 7 | 7 | 0 | t+7 | 6 | 0.667 | D@0.45×80 · U@0.53×18.9 · U@0.55×80 … |
| 1787365800 | 26 | 11 | 11 | 0 | t+7 | 10 | 0.600 | D@0.63×25 · D@0.62×10.4 · U@0.37×25 … |
| 1787369400 | 20 | 9 | 8 | 1 | t+7 | 8 | 0.500 | **PAIR** · D@0.52×74 · U@0.49×2 · U@0.49×9.7 … |

Three windows report 1.000 or 0.500 on **two** scored decisions — noise, not
signal. The five windows with ten or more scored points span **0.594 → 0.909**,
and the single largest contributor (`1787296500`, 32 points) sits at 0.594,
barely above a coin flip on a two-class problem whose baseline is 0.686.

Two structural facts the replay makes visible and the aggregate would not:

* **Opening activity is overwhelmingly one-sided.** Most windows open with a
  run of `D@…` clips before any `U@…` appears, which is exactly why the
  persistence baseline is high (0.686) and hard to beat.
* **$1.00 pairs appear in the opening of only 2 of 12 windows** here
  (`1787290200`, `1787369400`), consistent with the corrected canonical record
  of 2/12, not the 22/23 an early draft asserted.

## Economics — computed last, never used to choose

On the **correctly predicted** actions only, using L5's own realised fills:

| | |
|---|---:|
| correct side predictions | 85 |
| fills matched | 246 |
| shares | 4,955.8 |
| cost | $2,665.28 |
| settlement payout | $2,436.43 |
| **gross** | **−$228.86** |
| model-implied fee (0.07·p·(1−p)) | $71.39 |
| **net after model-implied fee** | **−$300.25** |
| VWAP | 0.5378 |

**The actions the rule correctly anticipated were, in aggregate,
loss-making at settlement.** This is a diagnostic on *L5's own fills*, not a
strategy we ran, and the fee is model-implied rather than credited. It cannot
upgrade the classification, and under the preregistered bar it would have
blocked `PREDICTIVE + ECONOMICALLY PROMISING` even had the predictive test
passed. Economics selected, altered and rejected nothing.

## What this means

1. **The opening's structure is real but retrospective.** A first-order Markov
   fit to the realised sequence recovers 0.68–0.79 bits in both halves. The
   same structure, asked to predict the next side from state available
   beforehand, delivers nothing — and in 30–60 s delivers less than persistence.
2. **The discovery result did not replicate as prediction.** That is the
   intended function of this study, and it is the reason the 1.158 figure was
   labelled discovery rather than validation.
3. **n is small and was declared small in advance.** 63 and 51 scored
   decisions. The protocol's floor was met, but these numbers cannot carry a
   deployment decision in either direction; what they do rule out is a *large*
   predictive effect hiding in the opening.
4. **Nothing here changes the unfilled-order limit.** Side is what we can score;
   where he rests and how much he exposes remain unobservable.

## Position

`OPENING15` is **DESCRIPTIVE**. There is no promotion path from this result,
no S3 follows, and none is proposed. The honest summary of the whole
BTC15m line is now: the only phase with descriptive structure is the opening,
that structure is consistent across both halves of it, and it does not
predict.
