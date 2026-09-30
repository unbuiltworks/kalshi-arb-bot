# HOLD_THE_LEVEL arm — result: NO-OP against the control

Status: **built, run, and measured as approved. It changes nothing.**
Scope: print-accurate authenticated WS slice
`data/pmus_ws_stream/raw_l2/pmus_ws_20260930_1330z.jsonl.gz`,
slug `cpc-btc-updown-15m-2026-09-30-1330z`, 4,233 events / 1,282 prints /
**5.01 of 15 minutes**. This is a PARTIAL window: it measures fill and queue
mechanics only. No settled-window economics are produced here.

## Arm definition as approved

Once a fee-safe **complement** maker order is resting, keep it at that exact
price even when the touch moves away; preserve queue position while it still
reduces residual, pair economics remain fee-safe, risk caps hold, and the
window is eligible. Cancel/reprice only on economic invalidity, imbalance
increase, or expiry. Control = existing repricing engine, unchanged.

Implemented in `src/l5_causal_sim.py` (`hold_the_level`, `_still_valid`,
the complement branch of `_reconcile`). 13/13 integrity tests pass.

## Side-by-side measurement

| metric | CONTROL | HOLD_THE_LEVEL |
|---|---|---|
| maker fills | 1 | 1 |
| partial fills | 0 | 0 |
| requotes | 54 | 54 |
| requotes — complement | **0** | **0** |
| requotes — open/extend | **54** | **54** |
| cancels | 0 | 0 |
| holds (level kept) | 0 | 3,929 |
| queue forfeited | 4,848.83 | 4,848.83 |
| queue consumed | 5,754.04 | 5,754.04 |
| order-seconds resting | 42 | 42 |
| median queue-ahead at placement | 900 | 900 |

Identical at every clip (10/20/30/50). Matched P&L, residual P&L, fees,
confirmed incentives and net/window are **not reported**: they require a
settled full window, and only 5.01 of 15 minutes of raw L2 has been
retrieved (blocked on the Railway agent usage limit). Reporting them from
a third of a window would be a fabricated net figure.

## Why it is a no-op — root cause

A complement's desired price is not a touch-following quote. In
`_desired()` it is `cap = θ − economic_basis(held_side)`, floored to the
tick. That value moves **only when a fill changes the basis**. So the
complement order is already never repriced by the control: its desired
price and its resting price stay equal, and `_reconcile` takes the
"already there; keep our queue position" path.

`holds = 3,929` is therefore counting iterations where the hold rule
suppressed a reprice the control was never going to make. The measured
split `requotes_by_type = {complement: 0, open_or_extend: 54}` is the
direct evidence, and it is identical in both arms.

## Consequence for the V1 finding

The queue destruction quantified in `V1_PRINT_LEVEL_FINDING.md`
(4,848.83 queue-positions forfeited against 5,754.04 consumed) is on the
**OPEN/EXTEND leg**, not the complement. The approved arm is scoped to
complements, so it cannot test that hypothesis as specified.

Extending HOLD to the opening leg is a different strategy change than the
one approved, and the mandate said no parameter tuning after seeing
results. It is therefore **not done** and is flagged for an explicit
decision instead.

## Instrumentation added

`requotes_by_type` / `cancels_by_type` on `CausalSim.report()` — permanent,
so a future arm cannot be reported as effective without showing which
order role its churn reduction landed on.
