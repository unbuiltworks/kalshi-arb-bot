# Why our OPENs strand, and what L5 does instead

Source: 5,500 authenticated L5 trades (2026-09-14/15), 5,231 on
`btc-updown-15m`; 46 complete BTC 15m windows. Our side: the durable
cohort ledgers and telemetry. Script: `scripts/l5_vs_frozen_entry_study.py`.
Label: historical observation of a third party plus historical replay of
ours. **The frozen Gemini policy is the control and was not touched.**

## The headline

**L5 completed 46 of 46 BTC 15m windows. Zero stranded.** Our frozen
engine strands almost every OPEN. The entry is not the difference.

## It is not the entry

L5 first-entered within 5c and 120s of our stranded `UP @ 0.64, tte 824`
state **6 times, and all 6 completed**. So L5 *does* enter states like
ours. Whatever separates them happens after the first fill.

## The mechanism is a different order of magnitude

| | L5 (completed windows) | frozen engine |
|---|---|---|
| trades per window | **103 median** (max 375) | 2 (PERSIST4 cap) |
| side switches per window | **27.5 median** | 0 — one OPEN, then EXTEND, then COMPLEMENT |
| alternates more than once | **97.8%** | structurally impossible |
| first entry, time-to-expiry | **885 s median** (within 15 s of the window opening) | OPEN band ends at 840 s |
| first entry price | **0.535 median**, p75 0.56, max 0.70 | 0.64 observed; 0.68/0.75/0.80/0.89 elsewhere |
| favored side at entry (price > 0.50) | 65.2% | 100% |

Two of those are *structural exclusions*, not preferences: our OPEN band
tops out at tte 840 while L5's median entry is 885, so we never see the
state L5 starts from; and our per-window submission cap is 2 while L5's
median is 103.

## The sharpest finding: the .96 gate forbids the path, not the destination

**71.7% of L5's completed windows passed through an in-flight pair cost
above 1.00** — and still finished at a median final pair cost of
**0.9576**, just under our ceiling. Only 52.2% of them ever *ended* at or
below 0.96.

L5 reaches ~0.957 by averaging *through* states that violate 0.96. Our
`want_complement` refuses any action whose post-trade pair cost would
exceed 0.96, evaluated per action. A per-action gate on an averaging
strategy forbids the route to its own destination. That is the most
plausible candidate for the missing decision variable, and it is
measured, not inferred.

## A hypothesis I formed and then rejected

Reading one window it looked like L5 sizes large on the cheap leg and
small on the dear one. Measured across the corpus it is the opposite:
median size 13.16 when a leg costs <= 0.35 versus 25.00 when it costs
>= 0.65. **Asymmetric cheap-leg sizing is not supported** and should not
be carried forward.

## Not answerable from this corpus

Preceding BTC movement, and whether entries follow an excursion or
reversal, both need an underlying price tape over those windows. L5's own
fills are an endogenous proxy and cannot establish it. Reported as
unanswerable rather than answered from a proxy.

## Against the five candidate explanations

- **(3) insufficient oscillation for .96 complements** — contradicted as
  stated. The oscillation is there; L5 harvests it with ~100 trades and
  27 side switches. Our engine cannot act on it at 2 submissions.
- **(4) size-dependent execution destroying completion** — real but
  separate: completion collapses to 0% at q>=10 in our replay.
- **(2) OPEN selection leaving bad residuals** — partially: we enter
  dearer (0.64+ vs 0.535) and later (824 vs 885) than L5.
- **(1) small-sample residual variance** — still unresolved; 3 and 7
  traded windows cannot settle it.
- **(5) combination** — the evidence points here, dominated by action
  frequency and the per-action ceiling.

**No frozen parameter was changed. Nothing here is authorization to
change one.**
