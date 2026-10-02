# Cadence reduction to 5 s, and the gate re-declared for it

Single change: `PMUS_WS_STATUS_EVERY_SECONDS` **10 -> 5** on
`pmus-ws-evidence-recorder`, set 15:59:19Z, deployment `fa239ac6`.
Sole objective: make the existing **400-row cap non-binding**. The 400 cap itself is
unchanged, and no recorder code, strategy, scoring, fill model, threshold or other
variable was touched.

## Establishing the previous value

Railway returns variable values redacted to this client (`valuesRedacted: true`,
OAuth app), so the prior setting was established by **measurement, not assumption**:
emission cadence p50 **10.042 s**, p90 20.039 s, max 20.518 s, against a code default
of 60.0 s. A p50 of 10.04 s with a 60 s default means the variable was explicitly
set to **10**; the ~20 s entries are intervals where a market was absent from one
cycle, i.e. two periods.

## Why 5, as the smallest sufficient reduction

At period 10 s, generation was p50 99 / p99 102 / max **110** states per market per
emission, and the disqualifying documents held exactly **400** — about **3.6
intervals** of accumulated backlog.

| candidate period | typical rows | ~4-interval catch-up | margin to 400 |
|---|---|---|---|
| 7 s | ~70 | ~308 | ~23% — too thin given the 99→110 spread and occasional 2× intervals |
| **5 s** | ~50 | **~220** | **~45%** |

So 5 s is the smallest single step with real margin rather than the largest
reduction available. Halving the period halves rows per emission; it does not change
how many states are captured.

## Deployment boundary, declared now

Deployment **`fa239ac6`** (created 15:59:19Z). The qualifying windows are the **first
two BTC15m windows recorded entirely after it reports SUCCESS and begins emitting**
— to be verified from row timestamps, never assumed from the clock. Any window in
flight across the boundary is excluded.

## The gate, reused unchanged

Both fresh windows must independently satisfy all eight:

1. zero rows lost to cap
2. **zero emissions at 400**
3. per-emission `Δbook_updates − emitted = 0`
4. ≥600 s coverage
5. counter continuity
6. complete BTC-reference coverage and sequence (brackets the window, no loss, no
   restart inside it)
7. **no parse or truncation failures**
8. residual book gaps not attributable to batching or cap behaviour

Also to be reported: maximum rows per emission; maximum document bytes and % of
81,920; status/log-volume increase; and whether trade collection changes.

**If both pass**, the first two qualifying windows are scored **together** with the
already-frozen synchronized study (`pmus_prefill_realtime_study.py`,
`pmus_btcref_sync.py`, unmodified). **If either fails, stop and report why — no
further recorder change.**

## Window classification, unchanged

1300z frozen discovery, never rescored. 1430z, 1445z, **1515z and 1530z** are
recorder-validation windows only and are **not** scored with the synchronized study.
Frozen `82714061` scoring continues independently. No live orders.

## Expected cost, to be measured rather than assumed

The status cycle also carries the status report, the gateway cross-check and trade
emission, so halving its period should roughly **double** status line count and
worker wake-ups. Total book and trade rows captured should be **unchanged** — the
same states split across twice as many documents, each about half the size. Document
bytes should fall well back from the 69.4% of limit seen at cap 400 / 10 s.
