# Habitat validation + the cross-venue question

Read-only. No production, risk, cap, ceiling, sizing, concurrency, shell,
admission or maker/taker change. The score gates nothing.

---

# 1. DOES THE SCORE PREDICT STATE VISITATION? YES — AND IT DOES NOT PREDICT FILLS

The XCHECK habitat baseline covers markets the RECORDER seats; the order
corpus covers markets the PROBE selects at concurrency 1. Their intersection
was **2 markets of 6** — far too small to relate habitat to behaviour. So the
score was recomputed from the probe's OWN observations: every order row
carries the `recorder_book` it priced against, which makes a habitat window
for **19 of the 48 markets actually traded**.

| state-visitation measure | n | r | crit .05 | verdict |
|---|---|---|---|---|
| **distinct prices touched** | 19 | **0.5546** | 0.456 | **SIGNIFICANT** |
| **side-switch rate** | 19 | **0.5189** | 0.456 | **SIGNIFICANT** |
| order price span | 19 | 0.4222 | 0.456 | not significant |
| orders placed | 19 | 0.2496 | 0.456 | not significant |
| COMPLETE attempts | 19 | 0.1807 | 0.456 | not significant |
| COMPLETE conversion *(secondary)* | 11 | 0.3377 | 0.602 | **not significant** |

**This is the split the score was built for.** It tracks how much of the
state space a market makes the engine visit — oscillation and price-space
coverage — and it does **not** track whether an order fills. The execution
metric is the one that fails to reach significance.

**The cleaner of the two significant results is the SWITCH RATE**, and it
matters which: `distinct_prices` and `price_span` are computed from our ORDER
prices while the habitat score's own `bands_visited` / `price_span` come from
the BOOK mid, so those two share a source and could be mechanically linked.
`switch_rate` is computed from order SIDE, which the book-mid series does not
contain. It is the result that is not suspect.

Limits: n=19, one venue, coarse cadence (0.2–6.0 obs/min against the XCHECK
stream's tens), Pearson on a small and probably non-normal sample. These
scores are NOT interchangeable with the XCHECK baseline and are never pooled
with it.

---

# 2. THE CROSS-VENUE QUESTION

> **Does Gemini naturally visit L5-like inventory/economic states more
> frequently than PMUS sports?**

## **YES — Gemini 5m by roughly 3.6x on the median, at full data coverage.**

| environment | n | p25 | **p50** | p75 | coverage | discounted p50 |
|---|---|---|---|---|---|---|
| **Gemini 5m** | 97 | 0.3305 | **0.3944** | 0.4584 | **1.000** | **0.3944** |
| **Gemini 15m** | 59 | 0.2122 | **0.2706** | 0.3064 | **1.000** | **0.2706** |
| PMUS corpus baseline | 14 | — | 0.1080 | — | 0.857 | 0.0926 |
| PMUS post-recovery window | 6 | — | 0.1555 | — | 0.857 | 0.1333 |
| best single PMUS market (nicpar-gabpen) | 1 | — | 0.2117 | — | 0.857 | 0.1815 |

**Gemini's WORST quartile (p25 0.3305) beats the best single PMUS market ever
measured (0.2117).** And Gemini carries **full coverage** — `tte_p50_s` is
recoverable from the symbol name, where PMUS XCHECK rows have no TTE at all —
so its discounted figure carries no penalty while PMUS always pays one.

**Identical definition, no Gemini tuning.** Same `window_features`, same
`score()`, same `CALIBRATED_FEATURES`, scored against the same
`l5_reference.json` that `run.py` wrote for the PMUS baseline. Tick choice
affects only the UNCALIBRATED spread diagnostic — no calibrated feature
consumes it — so it cannot move a score.

## Where the advantage actually comes from

| feature | bias | Gemini 5m | Gemini 15m | PMUS |
|---|---|---|---|---|
| price_changes_per_min | DOWN | 0.8280 | 0.8285 | — |
| tte_p50_s | NONE | **0.8030** | 0.1762 | UNKNOWN |
| bands_visited | DOWN | 0.3750 | 0.3750 | — |
| share_move_ge_1c | **UP** | 0.1805 | 0.0923 | 0.0726 |
| share_move_ge_2c | **UP** | 0.2185 | 0.0797 | 0.0546 |
| share_move_ge_5c | **UP** | 0.0913 | 0.0318 | 0.0227 |
| price_span | **UP** | 0.1906 | 0.1769 | 0.1404 |

**The UP-biased features are the honest test** — measurement error favours the
candidate there — and Gemini 5m still beats PMUS on all four, by 1.4x to 4x.
That is the stronger reading and it survives.

**`tte_p50_s` is the single largest structural difference: 0.8030 for Gemini
5m against 0.1762 for 15m.** L5's own median time-to-expiry at his fills is
**171 s**. A 5-minute window lives natively in that regime; a 15-minute one
does not. If L5's behaviour is TTE-conditioned — and the canonical model keys
on TTE — that is the most specific transferable fact this analysis has
produced.

`price_changes_per_min` is flagged `sampling_limited` on 31 of 97 Gemini 5m
windows, so its 0.828 is a LOWER bound there, not an overstatement.

## A BUG I MADE THAT NEARLY INVERTED THIS ANSWER

My first Gemini run returned **p50 = 0.0 for both 5m and 15m** — i.e. "Gemini
is worse than PMUS". It was wrong. I was grouping every tape row by symbol
without restricting to the window's own active period, so each series was
dominated by the pre-open placeholder quote (a static 0.4800/0.5000 x 500 that
can run for the better part of an hour). `GEMI-BTC05M2608310400-UP` showed
**597 observations over 3271 s with `price_span` 0.0, `bands_visited` 1 and
`tte_p50` 2026 s — for a market that is 300 s long.**

Restricting each window to `0 <= tte <= duration` (300 s / 900 s) drops
58,661 rows for 5m and 19,423 for 15m and produces the table above. **The
uncorrected version would have reported the exact opposite conclusion**, which
is why the drop counts are in the audit output and this paragraph is in the
report rather than in a commit message.

## What is still missing, exactly

- **Period and asset are not controlled.** Gemini tape is BTC, 2026-08-30 →
  09-02 (~2.5 days). PMUS is sports, 09-06 → 09-14. Different assets,
  different weeks. This is a habitat comparison, not a matched experiment.
- **Both series are BBO MID**, like-in-kind with L5's trade-price series and
  not identical to it. That label travels with every row.
- **No Gemini per-fill trajectory.** The tape gives quotes; it does not give
  our own fills, inventory, `gain_per_unit` or attainable pair cost on Gemini,
  because we have never traded there. The demand-side half of the canonical
  schema is **UNKNOWN for Gemini** and no number here estimates it.
- **Median in-window span is 159 s (5m) and 269 s (15m)** — the tape does not
  cover the full window for most symbols, and 23 of 120 5m symbols were too
  short to score at all.

To close the demand side a read-only Gemini collector would have to record
book + prints through whole windows, not partial ones. The BBO half already
populates; the fill/inventory half cannot be populated by observation alone.
