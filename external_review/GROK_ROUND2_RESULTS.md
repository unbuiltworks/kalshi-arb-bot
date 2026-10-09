# Grok round 2 — revised predictions, tested against the full corpus

All 760,817 fills / 17,046 windows. Script:
`../scripts/81_grok_round2_predictions.py`. Raw output:
`GROK_ROUND2_PREDICTIONS_RESULT.json`.

Same verdict rule: SUPPORTED only when the whole 95% bootstrap CI clears
the stated threshold; NOT_SUPPORTED only when the whole CI is on the wrong
side; INDETERMINATE otherwise.

## Tally — 4 SUPPORTED, 4 NOT_SUPPORTED

| # | prediction | measured | verdict |
|---|---|---|---|
| R2-P1 | P(same leg) flat across residual-VWAP deciles in **every** month, ±0.05 | ranges 0.064–0.084 in all 5 months | **SUPPORTED** |
| R2-P2 | mean same-leg run ≥1.5× longer in Aug than Mar–May | **1.044×** (3.30 → 3.44) | **NOT_SUPPORTED** |
| R2-P3 | share of windows with longest run ≥10 rises early→late | **0.570 → 0.195, reversed**, CIs disjoint | **NOT_SUPPORTED** |
| R2-P4 | P(switch) higher after a *small* fill than a large one | **0.212 small vs 0.341 large, reversed** | **NOT_SUPPORTED** |
| R2-P5 | residual in final 60s not smaller than middle third | **+54.6 contracts** [51.6, 57.9] | **SUPPORTED** |
| R2-P6 | Δpair-cost on switch ≈ Δ on same-leg fill | −0.000105 vs −0.000048, CIs overlap | **SUPPORTED** |
| R2-P7a | mass on {30,50,80} higher in August | **0.037 → 0.135**, CIs disjoint | **SUPPORTED** |
| R2-P7b | mode set itself unchanged | only **3 of 6** modes shared | **NOT_SUPPORTED** |

## A correction to the round-1 framing — this one is on the analysis, not Grok

Round 1 reported the regime shift as *"switching is near-universal in
March–May (0.978) and declines to 0.730 in August"*. That metric is the
**fraction of windows containing at least one switch**, and it is
confounded by window length. The per-fill switch probability is:

| month | per-fill P(switch) | n transitions | median fills/window | windows |
|---|---|---|---|---|
| 2026-03 | 0.421 | 1,843 | 21 | 88 |
| 2026-04 | 0.322 | 33,675 | 54 | 548 |
| 2026-05 | 0.281 | 88,176 | 93 | 919 |
| 2026-06 | 0.281 | 76,066 | 66 | 1,031 |
| 2026-07 | 0.278 | 130,330 | 8 | 3,666 |
| 2026-08 | 0.272 | 413,681 | 5 | 10,794 |

**Mar–May 0.294 vs August 0.272.** Near-constant. The 0.978-vs-0.730 gap
was almost entirely an artifact of **median fills per window collapsing
from 73 to 5**: a 73-fill window is nearly certain to contain a switch at
~0.29 per fill, a 5-fill window often is not.

So the corrected statement is: **per-fill switch propensity is roughly
constant at 0.27–0.29 across the entire corpus.** What changed between
regimes is not switching behaviour and not run length — mean run length
moved only 3.30 → 3.44 — but **how many fills a window receives at all**,
plus the size mix.

This correction cuts both ways. It removes the fact Grok's revised
run-length story was built to explain, so R2-P2 and R2-P3 failed for the
same underlying reason: there was no run-length change to find.

## What round 2 establishes positively

**Price-insensitive switching is now well supported, by two independent
routes.** R2-P1 holds in every single month (decile ranges 0.064–0.084,
inside the ±0.05 band), and R2-P6 shows the pair-cost consequence of a
switch fill is indistinguishable from a same-leg fill (−0.000105 vs
−0.000048, overlapping CIs on n=193,447 and n=503,293). Combined with the
near-constant per-fill rate above, the picture is a **constant-hazard,
price-blind leg-selection process** — not an optimiser.

**No late closing, decisively.** Residual in the final 60 seconds is
**54.6 contracts larger** than in the middle third of the same window
(n=5,738 windows, CI [51.6, 57.9]). Residual inventory is not worked down
as expiry approaches; it grows.

**R2-P4 reversed is informative.** Switching is *more* likely after a
large fill (0.341) than a small one (0.212). Grok's reasoning was that
larger clips produce longer runs; the data says the opposite.

**R2-P7's split verdict matters.** Mass on {30,50,80} does rise sharply
(0.037 → 0.135), but the mode set is not stable: August's top six include
**0.02 and 2.0** — fractional and near-zero sizes absent from the early
regime. Those are the signature one would expect from partial fills of
larger resting orders, which bears directly on the order-size ambiguity
flagged in `../LIMITS_OF_PUBLIC_FILLS.md`.

## Caveats on round 2 as evidence

* **Round 2 is not blind.** It was written after seeing round-1 results on
  this same corpus, so these are no longer independent predictions.
* **R2-P2 and R2-P3 were flagged as non-independent before testing** —
  both restate the round-1 switch-rate decline. They failed anyway, which
  is a worse outcome for the revised account than passing would have been
  good for it.
* Cumulative threshold tests across both rounds now exceed 20. A
  Bonferroni-adjusted 0.05 is below 0.0025. All verdicts here are
  CI-based, not p-value-based.

## Suggested round-3 message

> Round 2: 4 supported, 4 not.
>
> **Supported, and now strongly:** price-insensitive switching. P(same
> leg) is flat within ±0.05 across residual-VWAP deciles in *every* month
> (ranges 0.064–0.084), and Δpair-cost on a switch fill is
> indistinguishable from a same-leg fill (−0.000105 vs −0.000048,
> overlapping CIs). Also supported: no late closing — residual in the
> final 60s is **54.6 contracts larger** than in the middle third of the
> same window.
>
> **But your run-length explanation is wrong, and so was the fact it was
> built on.** Correcting my own round-1 framing: the 0.978→0.730 figure I
> gave you was the fraction of *windows* containing a switch, which is
> confounded by window length. The per-fill switch probability is
> near-constant: **0.294 in Mar–May, 0.272 in August**, and 0.27–0.28 in
> every month from May onward. Mean same-leg run length is **3.30 → 3.44
> (1.04×, not your 1.5×)**, and windows with a run ≥10 went **0.570 →
> 0.195** — the opposite of R2-P3.
>
> What actually changed across regimes is **median fills per window: 73
> in Mar–May, 5 in August** (and the size mix: mass on {30,50,80} rose
> 0.037→0.135, while August's top modes now include 0.02 and 2.0).
>
> R2-P4 also reversed: P(switch) is **higher** after a large fill (0.341)
> than a small one (0.212).
>
> So: leg selection looks like a constant-hazard, price-blind process at
> ~0.28 per fill, stable across five months. Given that, explain the
> regime change in terms of **fills per window and size mix** rather than
> run length. Specifically: what would cause a window to go from ~73 fills
> to ~5 while per-fill switch propensity, the 2-second cadence and the
> pair-cost target all stay fixed? And what do the 0.02 and 2.0 fill sizes
> appearing only in August tell you?
