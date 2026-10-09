# Grok round 1 — your predictions, tested against the full corpus

All 760,817 fills / 17,046 windows. Script:
`scripts/80_grok_predictions.py`. Raw output:
`GROK_PREDICTIONS_RESULT.json`.

**Verdict rule:** SUPPORTED only when the entire 95% bootstrap CI clears
the threshold Grok stated; NOT_SUPPORTED only when the entire CI is on the
wrong side; otherwise INDETERMINATE. No verdict rests on a p-value.

## Tally

**6 SUPPORTED, 4 NOT_SUPPORTED, 2 not testable as written** (11 threshold
tests across 7 predictions).

| # | prediction | measured | threshold | verdict |
|---|---|---|---|---|
| P1 | P(switch leg \| residual>0) > 0.55 | **0.277** [0.271, 0.284] | >0.55 | **NOT_SUPPORTED** |
| P2a | median final pair cost ≤ 1.00 | **0.980** [0.979, 0.982] | ≤1.00 | SUPPORTED |
| P2b | p75 final pair cost ≤ 1.06 | **1.053** [1.050, 1.059] | ≤1.06 | SUPPORTED |
| P3a | ≥1 leg switch in >70% of windows | **0.761** [0.755, 0.768] | >0.70 | SUPPORTED |
| P3b | switch rate higher in August than Mar–May | **Aug 0.730 vs Mar–May 0.978** | Aug > early | **NOT_SUPPORTED — reversed** |
| P4 | same discrete size set in both regimes | mode overlap 3 of 6; share on Grok's set 0.43 → 0.25 | none stated | not testable; **evidence against** |
| P5a | median inter-fill gap ≤ 3s | **2.0s** [2.0, 2.0] | ≤3s | SUPPORTED |
| P5b | mass at 0s > 30% | **0.373** [0.367, 0.380] | >0.30 | SUPPORTED |
| P6 | fills on residual side > 0.85 when residual > 200 | **0.674** [0.669, 0.680] | >0.85 | **NOT_SUPPORTED** |
| P7a | post-expiry fills < 5% | **0.07%** [0.035%, 0.11%] | <5% | SUPPORTED |
| P7b | post-expiry fills don't improve pair cost more | **−0.00138 vs −0.00006** | post ≥ pre | **NOT_SUPPORTED** |

## The four failures, and what they cost the account

### P1 — the central mechanism does not hold

Grok's engine is driven by opportunity: buy the complement when doing so
improves the pair cost. The measured switch probability given an open
residual is **0.277**, less than half the predicted floor.

Grok nominated its own falsifier: *"if switches are statistically
independent of the price of the opposite leg relative to residual VWAP."*
We tested the non-circular form of exactly that — P(switch) by residual
VWAP decile:

| residual VWAP | n | P(switch) |
|---|---|---|
| [0.0,0.1) | 73,891 | 0.285 |
| [0.1,0.2) | 81,824 | 0.251 |
| [0.2,0.3) | 64,998 | 0.263 |
| [0.3,0.4) | 59,985 | 0.272 |
| [0.4,0.5) | 73,313 | 0.281 |
| [0.5,0.6) | 95,885 | 0.279 |
| [0.6,0.7) | 99,183 | 0.285 |
| [0.7,0.8) | 85,354 | 0.278 |
| [0.8,0.9) | 70,488 | 0.282 |
| [0.9,1.0) | 37,659 | 0.315 |

**Essentially flat** — a 0.25–0.32 band with no monotone trend, across
743k observations. If switching were driven by the pair-cost opportunity,
a dearer residual would need a cheaper complement and P(switch) would rise
steeply. It does not. **Grok's own falsification condition is met.**

Note also that P1 **cannot be tested as written**. It conditions on
`price_B`, and the only opposite-leg price in this data belongs to a fill
that happened — so "did it switch to B, given B was cheap" is evaluated
only where it did switch. The literal figure (0.275, n=473,809) selects on
the outcome and is not evidence. Grok did not flag this; the harness did.

### P3b — the regime direction is backwards

Grok has the late regime as the more aggressive, higher-switching
instantiation. Measured: **March–May 0.978, August 0.730**, CIs nowhere
near overlapping. Switching is near-universal *early* and declines.

### P6 — one-sided accumulation is not as intentional as claimed

In windows ending with a residual above 200, the mean share of fills on
that side is **0.674**, not >0.85, and only **14.5%** of such windows
exceed 0.85. Large residuals arise mostly in windows that were *actively
two-sided* — more consistent with matching that failed to keep up than
with deliberate position-taking. Grok's D1/S3 case studies are the tail,
not the type; selecting on extremes made them look typical.

### P7b — small but real, and the opposite of predicted

Post-expiry fills are vanishingly rare (0.07%, not merely <5%), but the
533 of them improve pair cost **more** than pre-expiry fills (−0.00138,
CI excluding zero, versus −0.00006). Low n, so treat as a lead rather
than a finding — but it points the opposite way.

## What survived

P2 (pair-cost targeting below 1.0), P3a (switching is the norm), P5
(2-second median cadence, 37% same-second) and P7a all hold on the full
corpus. The pair-cost-control half of the account is sound; the
leg-selection half is not.

## Suggested round-2 message to Grok

> Here are your seven predictions tested against all 760,817 fills.
> Four failed. Most seriously, P1: P(switch leg | residual>0) is 0.277,
> not >0.55, and P(switch) is flat at 0.25–0.32 across every residual-VWAP
> decile over 743k observations — which is the falsification condition you
> yourself nominated. Your P1 was also circular as written, conditioning on
> the opposite leg's fill price; see the note in the results.
>
> P3b is reversed: switching is near-universal in March–May (0.978) and
> *declines* to 0.730 in August. P6 is wrong in magnitude: in
> residual>200 windows only 0.674 of fills are on the residual side, and
> just 14.5% of those windows exceed your 0.85.
>
> Your pair-cost half held up (P2, P5, P7a all SUPPORTED).
>
> Given P1 and P3b: revise the leg-selection rule. If switching is not
> driven by the pair-cost opportunity, what is it driven by? Address
> specifically why switching would be near-universal early and decline
> sharply later while P(EXTEND) and the 2-second cadence stay put. Then
> give me a new set of falsifiable predictions.
