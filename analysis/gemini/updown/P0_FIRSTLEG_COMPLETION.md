# P0 — FIRST-LEG → COMPLETION STUDY

Research only. No orders. No new policy. No OOS unseal. No threshold sweep.

Artifacts: `scripts/gemini_firstleg_completion.py`,
`analysis/gemini/updown/FIRSTLEG_COMPLETION.json`.

## Design actually executed

Target variable = **does a fee-inclusive sub-$1 complement become
executable later in the same window, after measured reaction latency
(0.197 s, our own p90 ack) and before the series ends?** Not settlement
direction, not first-leg P&L.

Candidates are generated causally at **every 1 Hz observation from T+15
onward**, for **both sides independently**, requiring ≥30 s of window
remaining. The frozen underdog entry is one row in this population, not
the design. **5,090 candidate states across 10 discovery windows**
(median 510 per window).

Split is chronological: 16 complete windows → **10 discovery, 6 sealed
OOS** (not read by the script).

Clustering: every rate carries its **independent-window count** and a
**window-level bootstrap** CI. No observation-level interval is reported
anywhere, because 510 observations in one window are one trial.

External/PMUS features **excluded** — the lead/lag study has not yet
admitted those feeds.

## A. Are completing and never-completing states distinguishable at entry?

**Yes, but only on two variables, and both are mechanical rather than
informational.**

| Feature | Bin | Rate | 95% CI (window cluster) | Windows w/ any |
|---|---|---|---|---|
| first-leg price | <0.20 | 9.8% | [0.0, 33.8] | 2/10 |
| | 0.50–0.65 | 29.8% | [5.1, 55.9] | 4/10 |
| | **0.65–0.80** | **81.0%** | **[59.6, 99.0]** | **9/9** |
| | ≥0.80 | 16.0% | [7.9, 24.8] | 10/10 |
| T+ seconds | <60 | 45.8% | [30.8, 62.4] | 10/10 |
| | 60–120 | 49.9% | [36.9, 64.3] | 10/10 |
| | 180–240 | 13.9% | [3.9, 28.6] | 4/10 |
| | **≥240** | **1.5%** | **[0.0, 3.7]** | **2/10** |

Everything else is **not separable**: |D| bps (43.5% → 0% monotone but
CIs overlap throughout and the ≥12 bps cell is one window), D sign
(25.1% [14.6,39.4] vs 41.4% [25.5,59.1]), reference moving toward vs
away from the side (28.9% vs 31.6% — **no directional content at all**),
oscillation, pair-cost delta. Two counterintuitive signs worth recording:
**wider** entry pair cost complements more (1.20–1.35: 42.4% vs
1.00–1.10: 1.6%) and the reference's direction is irrelevant. Both say
the same thing — complementability is about **how much room the book has
left to move**, not about information.

So the separation is real but close to tautological: time remaining
predicts whether a 175 s-median event can still occur, and a mid-high
price means the complement is cheap enough that a small move completes it.

## B. Is the frozen underdog rule materially worse?

**Not on the point estimate; materially worse on window breadth.**

| Population | Rate | 95% CI | Windows w/ any |
|---|---|---|---|
| underdog | 27.2% | [7.8, 50.3] | **5/10** |
| favored | 32.7% | [24.8, 41.3] | **10/10** |

The rates overlap heavily. But under the underdog rule **half the
windows never complement at all**, while a favored first leg finds a
complement in every window. The underdog rule is not selecting *bad*
inventory so much as **concentrating its completions into fewer windows**,
which is exactly the wrong shape for a per-window EV.

## C. Is there a simple preregistrable policy reaching 71.2%?

**No — and the reason is structural, not a tuning failure.**

The one joint candidate implied by the two separable bins (price
0.65–0.80 and T+<120, no sweep, no other combination evaluated):

| Population | Completion | Hurdle | Margin |
|---|---|---|---|
| frozen underdog | 27.2% | 50.4% | **−23.2 pp** |
| T+<120 only | 48.1% | 69.1% | **−21.0 pp** |
| px 0.65–0.80 only | 81.0% | 86.7% | **−5.7 pp** |
| px 0.65–0.80 & T+<120 | 82.6% | 85.9% | **−3.3 pp** |

Completion probability rises from 27% to 83% — and the hurdle rises from
50% to 86% with it. **The margin is negative in every population.**

And the surviving −3.3 pp is **not resolvable**: pooled-observation
weighting gives 82.6% vs 85.9% (**−3.3 pp**), window-mean weighting
gives 88.4% vs 85.9% (**+2.5 pp**). **The sign of the margin depends on
the weighting choice.** That is not a candidate policy; that is noise.
Discovery does not support preregistering anything, so nothing is
preregistered and OOS stays sealed.

## D. What happens to the hurdle under that policy? — 71.2% is NOT universal

Recalculated per population, as required:

| Population | gain / completed lock | loss / failed leg | required rate |
|---|---|---|---|
| frozen underdog | +$0.3134 | −$0.3186 | **50.4%** |
| all candidates | +$0.1957 | −$0.2672 | **57.7%** |
| T+<120 | +$0.1640 | −$0.3668 | **69.1%** |
| px 0.65–0.80 & T+<120 | +$0.1252 | −$0.7610 | **85.9%** |

**The coupling is the finding.** Buying a more expensive first leg makes
the complement cheap and therefore more reachable — but it also makes
the leg that never completes lose **$0.76 instead of $0.32**, while
shrinking the locked gain from $0.31 to $0.13. Completion probability and
the hurdle are two readings of the same quantity: how far the pair is
from $1. Selecting for one selects against the other, almost exactly.

The earlier **71.2%** was specific to the frozen underdog entry under the
forensic's accounting; on this candidate population the same entry's
hurdle is **50.4%**. It must not be quoted as a universal constant.

## E. Required OOS sample size

Window-level SD is large (28–37 pp), so the effective unit is expensive:

| Population | window SD | windows for ±10 pp | ±5 pp | to detect its own observed margin (80% power, 5% one-sided) |
|---|---|---|---|---|
| frozen underdog | 37.4 pp | 54 (~4.5 h) | 215 (~18 h) | 17 (~1.4 h) — margin is hugely negative |
| px 0.65–0.80 & T+<120 | 28.0 pp | 31 (~2.6 h) | 121 (~10 h) | **749 (~62 h)** |

Detecting a margin as small as the one that survived requires **~749
independent windows, about 62 hours of continuous BTC5m collection**. We
have 16. Any OOS read now would be uninformative in either direction.

## L5 comparison (descriptive only; no thresholds ported)

312 BTC Up/Down events, 9,901 deduplicated L5 fills:

| Dimension | L5 | Us (Gemini) |
|---|---|---|
| first acquisition price | median **0.588**, mean 0.561 | underdog rule, ≤0.60 ceiling |
| favored vs underdog first | **69.4% favored**, 30.6% underdog | 100% underdog by rule |
| first acquisition T+ | median **44 s** (p10 6, p90 183) | T+15 earliest eligible |
| ever complemented | **223/312 = 71.5%** | 47.1% (forensic), 27–33% (this population) |
| time to first matched increase | median **52 s** (0–247) | median **174.6 s** |
| same-side EXTENDs before complementing | median 1, mean 1.40; **193/312 accumulate ≥1** | none — single leg held |

Three things transfer as *questions*, not parameters. L5 buys **favored**
first, which is the band where our own completion rate is highest (81%).
L5 complements at a **52 s median inside a 15-minute window**; we wait
**174.6 s inside a 5-minute window** — the venue, not the policy, is the
dominant difference. And L5's realized completion rate of **71.5%** sits
right at the hurdle our own frozen economics imply, which is suggestive
and nothing more: Polymarket's fees, incentives and window length are
not Gemini's, and 312 Polymarket events cannot validate a Gemini rule.

## Verdict

First-leg state **does** separate completion — but the separation is
mechanical (room and time), carries **no directional information**, and
is **economically self-cancelling**: every first-leg choice that raises
completion probability raises the hurdle by at least as much. Per the
instruction, no policy is preregistered and the study stops here.

The binding constraint is therefore **not first-leg selection**. It is
that the Gemini BTC5m complement arrives at a ~175 s median inside a
300 s window, against L5's 52 s inside 900 s.
