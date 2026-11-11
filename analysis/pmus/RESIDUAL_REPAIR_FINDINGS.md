# Residual-repair counterfactual — FINDINGS

Run under `RESIDUAL_REPAIR_PREREG.md`, committed before any candidate was
scored. `L5_NATIVE` unchanged. No deployment.

## The question, answered

> **Can we materially reduce the −$2.580880 residual loss without destroying
> the +$1.027880 matched engine?**

## **NO — not by suppression.**

Residual loss *can* be materially and robustly reduced. It cannot be reduced
**without** proportionally destroying matched P&L, because **the actions that
create residual risk are the same actions that create matched pairs.**

An acquisition is not "residual" or "matched" when it is taken. It becomes one
or the other retrospectively, depending on whether its complement ever fills.
Any overlay that refuses an acquisition to avoid residual necessarily refuses
the same acquisition's chance of becoming half of a matched pair. The two lines
are not separable by refusal.

## The exchange rate

Residual dollars eliminated per matched dollar destroyed, every treatment as an
overlay on B', every split. **A ratio above 1.0 is required for the idea to
work at all.**

| treatment | AUG_DEV | AUG_OOS | SPRING |
|---|---|---|---|
| T1 HEAVY_EXTEND_BLOCK m=1 | 0.999 | 1.168 | 0.423 |
| T2 COMPLEMENT_ONLY | 0.535 | 0.652 | 0.727 |
| T3 RESIDUAL_AGE 105s | 0.335 | 0.260 | 1.234 |
| T3 RESIDUAL_AGE 252s | 0.385 | 1.027 | 0.755 |
| T4 TTE_COMPLEMENT 78s | 1.079 | 0.310 | 1.272 |
| T4 TTE_COMPLEMENT 168s | 0.703 | 0.693 | 0.862 |
| T4 TTE_COMPLEMENT 357s | 0.607 | 0.667 | 0.809 |

**Below 1.0 in 15 of 21 cells.** The six cells above 1.0 are scattered with no
consistency: no treatment holds a ratio above 1.0 across both August splits.
The ratio is noise around ≈0.7. There is no setting of these knobs at which
suppression buys residual safety cheaply.

## The cleanest demonstration

`B' + T1_HEAVY_EXTEND_BLOCK(m=1)`, cluster bootstrap over windows (4,000
resamples; the window is the unit of draw because one window is one settlement
draw, not N independent bets):

| split | residual Δ /win | matched Δ /win | net Δ /win |
|---|---|---|---|
| AUG_DEV | **+0.6423** [+0.3392, +0.9511] | **−0.6430** [−1.0184, −0.2822] | +0.0455 [−0.3620, +0.4558] |
| AUG_OOS | **+0.4997** [+0.1704, +0.8119] | **−0.4280** [−0.7429, −0.1005] | +0.1192 [−0.2579, +0.5127] |
| SPRING | +0.1152 [−0.1058, +0.3403] | **−0.2725** [−0.5216, −0.0520] | −0.1574 [−0.4261, +0.1039] |

Bold = 95% CI excludes zero.

The residual improvement is **real and confirmed out of sample** — the CI
excludes zero on DEV *and* on OOS. So is the matched damage. On DEV the two
cancel to three decimal places (+0.6423 against −0.6430). **Every net CI covers
zero on every split.**

That is the finding in one line: *the effect is real on both legs, and the legs
cancel.*

## Overfitting caught by the pre-registration

The DEV-selected winner was `B' + T4_TTE_COMPLEMENT(78s)`: +0.3553/win on DEV,
residual loss eliminated outright (−$1,866.99 → +$181.71), 93.5% matched and
94.5% maker retained, peak residual unchanged. It looked decisive.

**On AUG_OOS it scores −0.5872/win — a sign flip**, with the residual gain
collapsing from +$2,048.70 to +$356.55 and the ratio from 1.079 to 0.310.

Had the OOS split not been fixed in advance, that candidate would have been
returned. It was selected on DEV exactly as pre-registered, and OOS rejected
it. This is the pre-registration doing its job.

## Decision-rule result

Pre-registered gate: beat B' on AUG_OOS net $/settled window; retain ≥60%
matched contracts and ≥50% maker volume; do not increase max |residual|.

| candidate | OOS net Δ/win | matched ret. | maker ret. | peak resid | passes? |
|---|---|---|---|---|---|
| B'+T1 m=1 | +0.1192 | 0.950 | 0.939 | 276.0 vs 388.9 ✓ | **letter: yes; substance: no** |
| B'+T4 78s | −0.5872 | 0.948 | 0.961 | 199.5 ✓ | no |
| B'+T1 m=1 +T4 78s | −0.4960 | 0.898 | 0.902 | 197.5 ✓ | no |
| B'+T3 252s | +0.0037 | 0.976 | 0.971 | ✓ | no (negligible) |
| B'+T2, B'+T3 105s, B'+T4 168s/357s | all negative | — | — | — | no |

`B' + T1_HEAVY_EXTEND_BLOCK(m=1)` satisfies criteria 1–4 **by the letter**: its
OOS point estimate beats B' by +$0.1192/window (+$123.04 over 1,032 windows),
it retains 95.0% of matched contracts and 93.9% of maker volume, and it lowers
peak residual from 388.9 to 276.0.

**I am not returning it as a candidate that beats B'.** Its net advantage is
statistically indistinguishable from zero (CI [−0.2579, +0.5127]), it reverses
sign on SPRING (−0.1574/win), and its entire residual gain is paid for at a
0.999–1.168 exchange rate in matched dollars. Reporting it as a win would be
claiming an edge the evidence does not carry. Per the pre-registration: *"If no
candidate passes, the honest answer is NO and that is what will be reported.
Failure to find one is a result, not a reason to widen the grid."*

## Method limit, stated because it bounds the conclusion

This replay can only ever **refuse** actions that were recorded. It cannot
invent a fill that never happened. Therefore it can evaluate *suppression*
overlays and nothing else.

The conclusion is correspondingly bounded: **suppression-based residual repair
does not work.** It says nothing about *completion-side* intervention — making
the complement leg actually fill rather than declining the first leg. Those
would ADD fills (a taker completion inside a bounded cost, a repriced
complement, a widened complement tolerance), and this method is structurally
incapable of scoring them, because the counterfactual fill is not in the
record.

That is the honest next question, and it needs a different instrument — a
book-tape simulation against resting depth, not an action-suppression replay.

## D/U/V accounting

Strict determinate accounting was implemented and reported, and it is why the
full-window numbers above are labelled UNCERTAIN.

- **VOID** — contracts of every suppressed action, never credited.
- **DETERMINATE** — the prefix up to and including the first action on which
  candidate and comparator **disagree**. While two policies make identical
  decisions their inventories are identical and nothing downstream is yet
  counterfactual, so the cut belongs at the first divergence, not at the first
  suppression by either. (A first implementation cut at "either suppressed",
  which compared a truncated candidate against a full-window comparator and was
  not like-for-like; corrected.)
- **UNCERTAIN** — everything after the divergence, for both policies.

**The determinate share is only 0.12–0.17 of the action stream.** B' is the
most restrictive policy in the set, so it almost always diverges first and
truncates the common prefix early. Strict D/U/V can therefore measure only *the
marginal value of the first divergent decision*, not a whole-window policy.

Reported both ways, and the two agree in sign for the finally-considered
candidate. The full-window figures carry the fill-invariance assumption —
surviving actions still fill as recorded — and no conclusion above rests on the
full-window numbers alone; the bootstrap CIs, which cover zero on net in every
split, are what decide.

## Supporting figures

Corpus: 2,216 settled windows, 71,984 actions. AUG_DEV 968 / AUG_OOS 1,032 /
SPRING 216.

Maker economics counted at **$0** — rebates confirmed zero, nothing ever
credited, a zero commission is not a rebate. Fees: PMUS
`fee = theta * C * p * (1-p)`, taker theta 0.0695, taker fills only; taker
rebate tiers start at $250k and this account is below, counted at zero.

Context, not selection: mature August L5 carries a median |residual| of **9.04×
its median clip**. Our live k=2 cap is far tighter than L5's own behaviour and
still lost on residual — consistent with residual exposure being intrinsic to
the sequence rather than a tuning error.

`NATIVE` (take everything) scores −1.1318/window on AUG_DEV against B's
+0.7443. **B' is a large, real improvement over taking every action.** The
finding here is only that no further *suppression* overlay improves on it.

## Live validation

The four frozen PMUS windows are consistent with this picture and were not used
in selection: matched +$1.027880 against residual −$2.580880, with residual
settling worthless in all four. The corpus result explains why tightening
suppression further would not have rescued them — it would have removed matched
production at roughly the same rate.
