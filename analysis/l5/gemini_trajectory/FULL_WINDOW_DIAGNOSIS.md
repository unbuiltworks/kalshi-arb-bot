# Why we have 0 FULL windows — first evidence from the new instrumentation

The ≥290 s FULL criterion is **not** lowered. Classes preserved:
`FULL ≥290s · NEAR_COMPLETE ≥280s · PARTIAL <280s`.

## The field that made this answerable

`span_s` counts only **two-sided** samples, so a window we kept sampling while
the venue's book sat one-sided looked identical to one where our collection
stopped. Those are opposite diagnoses. The digest now also carries
`tte_first_any / tte_last_any / span_any_s` over **any** in-window sample.

## First two windows on the instrumented build

| market | tte_first | tte_last | span_s | **tte_first_any** | **tte_last_any** | **span_any_s** |
|---|---|---|---|---|---|---|
| BTC05M…1720 | 247.88 | 36.46 | 211.42 | **247.88** | **0.52** | **247.36** |
| ETH05M…1720 | 247.88 | 94.13 | 153.75 | **247.88** | **0.82** | **247.06** |

## What it shows

**The miss is at the START, not the end.**

* `tte_last_any` is **0.52 s / 0.82 s** — we sampled continuously all the way
  to expiry. **Collection did not stop.** CONNECTION_GAP and EARLY_RESOLUTION
  are both ruled out for these windows.
* The gap between `tte_last` (36.46) and `tte_last_any` (0.52) on BTC is
  **genuine ONE_SIDED_BOOK** — real venue behaviour, and it must not be
  "fixed". It costs ~36 s of two-sided span.
* But `tte_first_any` is **247.88, not ~300** on *both* markets — identical to
  the centisecond, so it is systematic, not market-specific. **We lost the
  first ~52 seconds of the window before observing anything at all.**

**Classification for these two: LATE_SUBSCRIBE / DISCOVERY_DELAY.**

Even a perfectly two-sided book could not have reached 290 s from a 247.88 s
start. The start-side loss alone is fatal to FULL, independently of the
one-sided tail.

## Mechanism — SUPERSEDED, see `LATE_SUBSCRIBE_CAUSATION.md`

> **This section was wrong and is kept only for provenance.** It read: a
> window is subscribed only on a discovery pass, `DISCOVER_SECONDS` is 45, so
> the loss is bounded near 45 s and we observe ~52 s.
>
> That cannot be right, because **the loss is constant**: 330 − 247.88 = 82.12
> and 330 − 247.65 = 82.35 on batches 600 s apart. A 45 s tick with arbitrary
> phase produces a loss uniform on [0, 45); it cannot produce the same 82 s
> twice. The paragraph also measured the loss against the *window open* at
> `expiry − 300` rather than against our own due time at `expiry − 330`, which
> understated it as ~52 s when it was ~82 s.
>
> The real cause is four compounding defects — a 45 s blind start per session,
> the subscription roll being fused to the REST tick, session-local buffers,
> and a 600 s session period that is an exact multiple of the 300 s window
> period and so phase-locks the loss instead of averaging it out. The venue is
> exonerated: it lists windows ~4,100 s ahead. Full evidence and the fix are in
> `LATE_SUBSCRIBE_CAUSATION.md`.

**Deliberately not changed at the time.** These were the first two windows on a
build that restarted at 17:09:20, so a restart confounder could not be
separated from steady-state cadence on n = 2. That caution was right; the
mechanism attached to it was not.

## What does NOT change

* The FULL criterion stays at ≥290 s.
* One-sided book periods stay as measured; they are venue behaviour.
* The A/B remains at **n = 2 NEAR_COMPLETE**, both BTC, with no new
  qualifying windows this cycle.
