# Preregistered n=100 DESCRIPTIVE read — Gemini BTC5m executable book

Milestone reached 2026-10-03 07:58Z: **104 usable windows** by
`ie_usable_count.py` (≥240 s book cover, depth chain intact, Coinbase ≥30/min
with gap ≤5 s, settled with strike). Split 62 yes / 42 no, all settled on
`LS-LRRR-XBTUSD`.

Authorized by `PREREG_POWERED_COHORT.md` line 145: *"n = 100 usable —
DESCRIPTIVE ONLY — availability, price, size, spread, depth and correct-rate
distributions across all checkpoints. No strategy run, no model fitted, no
edge computed, sealed OOS untouched."*

**What this is not.** No strategy was run. No model was fitted. **No edge was
computed** — `P(correct | state)` must be estimated on DEV and applied
out-of-sample, so it stays `None`, exactly as the circularity guard requires.
The predictor here is the preregistered **placeholder** `sign(coinbase −
strike)`: mechanical, crude, and explicitly not the model. The rule built on
it was already killed on DEV; it appears here only as the labelled yardstick
the preregistration names.

Population note: the ladder reports **133 settled windows with book
coverage**, a looser filter than the 104 the usable-window counter certifies.
Both figures are stated rather than reconciled into one.

Tool: `analysis/gemini/info_edge/ie_tte_ladder.py`, extended from T−60..T−1 to
the preregistered **T−120..T−5** ladder (120/90 added; the earlier points kept
so the n=15 preview stays comparable).

## 1. Availability — can you buy the UP side at all?

| TTE | n | has_ask | has_ask% | ask p50 | ask size p50 | spread p50 | two-sided% |
|---|---|---|---|---|---|---|---|
| 120 | 133 | 85 | 63.9% | 0.450 | 192.0 | 0.330 | 42.1% |
| 90 | 133 | 69 | 51.9% | 0.330 | 182.0 | 0.325 | 22.6% |
| 60 | 133 | 62 | 46.6% | 0.170 | 180.0 | 0.360 | 9.8% |
| 45 | 133 | 63 | 47.4% | 0.160 | 178.0 | 0.335 | 10.5% |
| 30 | 133 | 45 | 33.8% | 0.230 | 9.0 | 0.575 | 1.5% |
| 20 | 133 | 35 | 26.3% | 0.280 | 6.0 | — | 0.0% |
| 15 | 132 | 33 | 25.0% | 0.240 | 6.0 | 0.190 | 1.5% |
| 10 | 132 | 31 | 23.5% | 0.260 | 6.0 | 0.290 | 0.8% |
| 5 | 132 | 30 | 22.7% | 0.270 | 6.0 | — | 0.0% |

Two things dominate: **availability decays** from 64% at T−120 to 23% at T−5,
and **displayed size collapses** from ~180–190 contracts to ~6 at T−30 and
inside. The book is **not two-sided** late: 42% at T−120, 9.8% by T−60, and
effectively 0% from T−20 on. A single-sided book is the normal late state, not
an anomaly.

## 2. DOWN side (acquire DOWN = 1 − best UP bid)

| TTE | has_bid% | down px p50 | down size p50 |
|---|---|---|---|
| 120 | 77.4% | 0.350 | 192.0 |
| 90 | 67.7% | 0.220 | 180.0 |
| 60 | 56.4% | 0.160 | 178.0 |
| 45 | 57.9% | 0.160 | 178.0 |
| 30 | 45.1% | 0.165 | 89.5 |
| 20 | 33.8% | 0.440 | 12.0 |
| 15 | 35.6% | 0.390 | 9.0 |
| 10 | 34.8% | 0.390 | 10.5 |
| 5 | 34.1% | 0.360 | 9.0 |

DOWN is **consistently more available than UP** at every checkpoint (34% vs
23% at T−5). The asymmetry is a property of the single published UP ladder: a
bid is likelier to exist than an ask.

## 3. Settlement vs Coinbase distance (descriptive, not a signal)

| TTE | n | cb_dist p50 | UP won% | cb>0 n | UP won% | cb≤0 n | UP won% |
|---|---|---|---|---|---|---|---|
| 120 | 133 | 1.94 | 55.6% | 71 | 85.9% | 62 | 21.0% |
| 60 | 133 | 2.17 | 55.6% | 72 | 94.4% | 61 | 9.8% |
| 30 | 133 | 2.81 | 55.6% | 75 | 96.0% | 58 | 3.4% |
| 15 | 132 | 2.83 | 56.1% | 73 | 97.3% | 59 | 5.1% |
| 5 | 132 | 4.12 | 56.1% | 75 | 97.3% | 57 | 1.8% |

Separation by sign of distance is very large and **sharpens monotonically** as
TTE shrinks — 86%/21% at T−120 to 97%/2% at T−5. This reproduces the earlier
DEV finding that BTC state is informative about settlement. It is a
description of the joint distribution, **not** evidence that the information
is tradeable.

## 4. The predicted side, side-symmetrically (prereg 9a59a418)

Predicted UP → best UP ask + ask size. Predicted DOWN → 1 − best UP bid + bid
size. Predictor is the placeholder.

| TTE | n | avail | avail% | px p50 | px p10 | px p90 | size p50 | correct% | correct given available% |
|---|---|---|---|---|---|---|---|---|---|
| 120 | 133 | 56 | 42.1% | 0.89 | 0.64 | 0.98 | 250.0 | 82.7% | 71.4% |
| 90 | 133 | 30 | 22.6% | 0.90 | 0.78 | 0.98 | 26.0 | 87.2% | 66.7% |
| 60 | 133 | 15 | 11.3% | 0.69 | 0.42 | 0.93 | 357.0 | 92.5% | 73.3% |
| 45 | 133 | 17 | 12.8% | 0.64 | 0.26 | 0.97 | 283.0 | 94.7% | 82.4% |
| 30 | 133 | 4 | 3.0% | 0.48 | 0.26 | 0.85 | 291.0 | 96.2% | 100.0% |
| 20 | 133 | 2 | 1.5% | 0.79 | 0.69 | 0.89 | 0.6 | 96.2% | 100.0% |
| 15 | 132 | 3 | 2.3% | 0.51 | 0.41 | 0.84 | 1.1 | 96.2% | 100.0% |
| 10 | 132 | 3 | 2.3% | 0.51 | 0.41 | 0.84 | 0.6 | 98.5% | 100.0% |
| 5 | 132 | 3 | 2.3% | 0.83 | 0.57 | 0.90 | 2.0 | 97.7% | 100.0% |

**This is the table that matters, and it is a negative description.**

- The predicted side is **available in only 42.1% of windows at T−120, and
  1.5–3.0% from T−30 inward.** Availability of the *favored* side is far worse
  than raw `has_ask`/`has_bid`, because the two conditions are anti-correlated:
  the side the state favors is the side nobody is offering.
- Where it is available late, **displayed size is 0.6–2.0 contracts** — too
  small to matter regardless of accuracy.
- The `correct given available` column at T−30 and inward reads 100% on
  **n = 2–4 observations**. That is not a 100% hit rate; it is an empty cell
  with a number printed in it. No weight belongs on those rows.
- At the only checkpoints with usable counts, **availability selects against
  correctness**: 82.7% → 71.4% at T−120, 87.2% → 66.7% at T−90. This
  reproduces the adverse-selection penalty measured on DEV (−0.203) rather
  than contradicting it.
- Prices on the predicted side are **high where it is available**: p50 0.89 at
  T−120, p10 0.64. Buying the favored side costs most of its own probability.

## 5. Fixed-bucket cross-tab (buckets predeclared, not fitted)

At T−30, by Coinbase distance: `cb≤−20` 0.0% UP-won (n=32, 29 have an ask);
`−20..−5` 0.0% (n=19, 13 asks); `−5..0` 28.6% (n=7, 1 ask); `0..+5` 87.5%
(n=16, 2 asks); `+5..+20` 95.8% (n=24, **0 asks**); `cb>+20` 100.0% (n=35,
**0 asks**).

The pattern is stark and consistent at T−15 and T−5: **where UP is nearly
certain to win, no UP ask exists at all.** Asks concentrate in exactly the
buckets where UP loses — 29 of 32 in the `cb≤−20` bucket at T−30, median ask
0.160. What is offered is the side that is about to be wrong.

## 6. What this read does and does not establish

Establishes, descriptively:

1. Late-window availability is low (23–34%) and late displayed size is tiny.
2. The book is single-sided from T−20 onward in essentially every window.
3. BTC state separates settlement outcomes strongly, and more sharply as TTE
   shrinks.
4. The *favored* side's availability is far lower than either side's raw
   availability, and the two are anti-correlated.
5. Where the favored side is available at usable n, availability selects
   against correctness.

Does **not** establish, and must not be read as:

- any edge figure — none was computed, by construction;
- any "best" entry time — the ladder is descriptive and no checkpoint was
  selected;
- any claim from the T−30-and-inward `100%` cells, which rest on n = 2–4;
- any validated signal — the predictor is the labelled placeholder, and the
  real signal must still be specified and frozen on DEV, confirmed once on
  VALIDATION, before any prospective evaluation.

## 7. Next preregistered step

Unchanged by this read: specify and **freeze** the signal and the A/B/C rules
on DEV, confirm once on VALIDATION, record the SHA — and only then evaluate on
genuinely prospective windows, **read once**. Collection continues toward 200
and 300.
