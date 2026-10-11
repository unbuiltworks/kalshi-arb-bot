# DECISION: >$1 rescue through 1.03 — REJECTED for live promotion

**Date:** 2026-09-10 · **Decided by:** owner · **Status:** CLOSED
**Live policy unchanged:** whole-position matched-pair ceiling stays at
**1.00**.

## Decision

> **REJECT for live promotion:** >$1 rescue through 1.03.
>
> **Reason:** reliable reduction in residual exposure, but no statistically
> supported improvement in trading P&L before incentives. Higher ceilings
> monotonically increase variance.
>
> **Revisit trigger:** genuinely new out-of-sample L5 data — **not** another
> parameter sweep over Sep 1–10.

## What separates the two claims that were being conflated

- **L5's >$1 completion behaviour is real**, and it reliably reduces residual
  exposure (−33.9% residual quantity at full L5 vs a 1.00 ceiling).
- **Copying it has not been shown to improve pre-incentive trading
  expectancy.** Best point estimate 1.010: +$2,163 / +16.5 bp, **t = 1.31**
  against a critical value of 2.262. 1.020 is *worse* than doing nothing.
  sd/day rises monotonically 420 → 1,168 with the ceiling.

Evidence: `RESCUE_CEILING_BACKTEST.md`, commit `1a3ef5dc`.

## Standing rule against manufacturing significance

**Do not re-test 1.01 — or any ceiling — on the Sep 1–10 sample again.**
Repeated sweeps over a fixed sample eventually produce a significant-looking
result by chance. Any future test must run on dates not used here.

## PRE-REGISTERED out-of-sample protocol (frozen 2026-09-10, before the data exists)

Fixed now so nothing can be tuned after seeing results:

| element | frozen value |
|---|---|
| ceilings | 1.005, 1.010, 1.015, 1.020, 1.025, 1.030 — **exactly these** |
| baseline | B = 1.000 (current live policy) |
| reference | A = ∞ (L5 actual) |
| primary metric | trading P&L **before incentives**, in USD, vs B |
| unit of observation | **one UTC day** |
| significance test | one-sample t on daily deltas vs B, **two-tailed, α = 0.05** |
| critical value | t from the realised df, no adjustment for looking |
| secondary metric | residual quantity at settlement (reported, never decisive) |
| accounting | `src/l5_resolved_pnl.py` — membership by resolution, zero-redemption losers included |
| simulation | fills replayed in timestamp order; refusal changes later state |
| dates | **only dates not present in the Sep 1–10 harvest** |

**Promotion requires the primary metric to clear α = 0.05 at some ceiling on
unseen dates.** Residual reduction alone does not promote — it was never in
doubt and it is not what the live risk budget is being spent on.

A ceiling that clears OOS is a candidate for a *bounded* live test, not an
automatic change.

## Audit queue (does not block the live programme)

- **Simulator fidelity gap $825** (simulated A −$4,208.75 vs actual
  −$5,033.94, ~16%). **$691 of it sits in one market,
  `btc-updown-15m-1788448500`.** Five of 4,217 markets differ by >$0.50.
  Likely a partial or multi-row redemption the reconstruction flattens.
  Common to every arm, so it does not affect the rejection above. Fix before
  any OOS run is used for promotion.

## What is now permanent regardless of this decision

`src/l5_resolved_pnl.py` + 13 tests: resolved-market P&L includes
zero-redemption losers by construction, and a pre-filtered set is refused
rather than answered. 2,317 tests green. Every subsequent L5 P&L claim rests
on this.
