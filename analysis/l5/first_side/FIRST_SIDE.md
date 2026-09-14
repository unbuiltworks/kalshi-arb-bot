# Does L5 open on the cheaper touch? PRE-REGISTERED TEST — REFUTED

Evidence from **17,046 markets** of L5's own public fills. Nothing here
changes the side-selection rule; no rule was retrofitted to the nine Gemini
prints, and this test is entirely independent of them.

## Pre-registration (fixed before any result was inspected)

**Hypothesis.** Our engine applies `open_side_yes = "open the cheaper touch,
ties to YES"` **deterministically**. If L5 does the same, his first buy in a
market must land on the cheaper outcome essentially always.

**Statistic.** Share of markets whose FIRST buy filled **below $0.50**.

**Why the proxy is sound, and conservative.** In a binary market the YES touch
is the fill price `p` and the NO touch is `1 − yes_ask ≤ 1 − p`. If `p > 0.50`
then `1 − p < 0.50 < p`, so the complement is unambiguously the cheaper touch
— a spread only widens that gap. A fill above 0.50 is therefore a purchase of
the **dearer** touch, whatever the spread.

**Decision rule, fixed in advance:** SUPPORTED ≥ 0.80 · PARTIAL 0.60–0.80 ·
REFUTED ≤ 0.60. Verdict taken **out-of-sample**: markets split by first-buy
time, earliest 70% in-sample, latest 30% out-of-sample.

## Result

| split | n | share of first buys below $0.50 |
|---|---|---|
| all | 17,046 | 0.3595 |
| in-sample (earliest 70%) | 11,932 | 0.3681 |
| **out-of-sample (latest 30%)** | 5,114 | **0.3395** |

### VERDICT: **REFUTED** (OOS 0.3395, threshold ≤ 0.60)

**L5 opens on the DEARER touch about 64% of the time.** Median first-buy price
is **$0.540**. Our rule predicts the opposite, deterministically.

Distribution of the opening fill: p10 0.250 · p25 0.450 · **p50 0.540** ·
p75 0.650 · p90 0.790. Only 28.3% of opens land below 0.35; **26.0% land at
0.65 or above.**

## Alternatives (reported; they do not move the verdict)

| variable | split | share below 0.50 |
|---|---|---|
| horizon | 15m (n=6,126) | 0.4107 (median 0.510) |
| horizon | **5m (n=10,920)** | **0.3308** (median 0.580) |
| asset | btc (n=13,635) | 0.3732 |
| asset | **eth (n=3,411)** | **0.3046** |
| TTE at open | late open (n=8,572) | 0.3650 |
| TTE at open | early open (n=8,474) | 0.3539 |

TTE does not discriminate. The skew toward the dearer side is **strongest
exactly where we are trying to transfer: 5m windows (0.3308) and ETH
(0.3046)**.

Outcome check, confirming prices are roughly calibrated rather than that L5
has edge: the first-opened side went on to win **54.35%** of the time; cheap
opens won 35.17%, dear opens 65.12% — close to what their prices imply.

## Why this matters more than an execution-shell tweak

The Gemini 5m shadow produced **zero fills** in every tier while one intention
successfully followed 54 requotes from 0.21 → 0.80. All nine observed prints
were taker SELLS, and the cheaper-touch rule had put us on DOWN; **six of the
nine would have interacted with an UP maker.** That was a single window and
proved nothing on its own.

This test says the same thing from 17,046 markets of independent evidence:
**the cheaper-touch assumption is not L5's rule.** The clone has been
systematically selecting the opposite side from the one L5 picks, which is a
far larger error than any rest/cancel parameter.

## What is NOT claimed

* **No replacement rule is proposed or implemented.** "Not the cheaper touch"
  is not "the dearer touch" — the 64/36 split is a tendency, not a rule, and
  the true predictor is still **UNKNOWN**.
* Fill price is a proxy for the touch. It is sound in direction (above) but
  does not recover the full book.
* Variables the public tape cannot see — queue position, L5's cancels, his
  resting orders — remain UNKNOWN and were not modelled.
* Nothing is deployed and no live behaviour changed.

**Next step, pre-registered before it is run:** fit which of {recent price
movement, recent flow direction, spread, depth, prior market's outcome, band,
TTE, trajectory} predicts the opening side, train on the in-sample 70%, and
report OOS accuracy against the 64% base rate. A candidate rule must beat that
base rate out-of-sample to be worth anything.
