# Gemini BTC5m — latency and pre-window placement diagnostics

**SEPARATE FROM, AND DO NOT MODIFY, THE FROZEN BACK-OF-QUEUE DECISION GATE.**
`DECISION_GATE.md` stands exactly as committed. These two diagnostics ask
whether *arriving earlier* — by microseconds of latency, or by minutes of
pre-window placement — could have rescued benign maker capture.

**Both answers are NO, and both strengthen the rejection.**

No live orders. No parameter search. No threshold fitting. Queue discipline
identical to the frozen run: no touch = fill, displayed size is a **proxy**
and never proven queue position, a cancellation never advances us,
through-prints decomposed out, post-fill horizons fixed at 5/15/30 s.

---

## A. P6 — EXECUTION LATENCY (`p6_latency_sensitivity.py`)

Latencies 25/50/100/250/500 ms are **counterfactual sensitivity points only.
Nothing here claims our system can achieve them.**

### A1. The structural question, answered directly

> When a new touch first becomes observable, is the giant queue already
> present in that first depth event, or does it build over the following
> 50–1000 ms?

**It is already there. The queue does not build — it is posted atomically,
complete, in the single event that creates the level.**

`size_at_0ms / size_at_1000ms`: **p10 = p50 = p90 = 1.000.**

Within +1000 ms of formation: adds p50 **0.0**, cancels p50 **0.0**, execs
p50 **0.0**. Levels are static after they appear.

| formation type | n | size p50 at first sight | ≥100 at first sight | ≥1000 at first sight |
|---|---|---|---|---|
| **NEW_PRICE** (price absent, appears as touch) | 6,983 | 25.0 | 22.7% | 3.2% |
| **PROMOTED** (already resting deeper, becomes touch) | 1,562 | **306.0** | **77.5%** | 11.7% |

PROMOTED touches are hopeless by construction — their queue was built long
before any arrival of ours. NEW_PRICE touches are the only place early
arrival could matter, and there is **no growth phase to be early into**.

### A2. The latency curve is flat

| latency | q_ahead p10 / p50 / p90 | arrive ≤10 ahead | DEFENSIBLE q1 cap% | q25 cap% | q1 through / same |
|---|---|---|---|---|---|
| 25 ms | 6.0 / 25.0 / 535.0 | 16.3% | **0.07%** | **1.35%** | 28.00 / 4.00 |
| 50 ms | 6.0 / 25.0 / 535.0 | 16.3% | 0.06% | 1.30% | 27.00 / 4.00 |
| 100 ms | 6.0 / 25.0 / 535.0 | 16.3% | 0.06% | 1.31% | 27.00 / 4.00 |
| 250 ms | 6.0 / 25.0 / 535.0 | 16.3% | 0.06% | 1.29% | 25.00 / 4.00 |
| 500 ms | 6.0 / 25.0 / 535.0 | 16.2% | 0.06% | 1.29% | 21.00 / 3.00 |

**The queue ahead at arrival is literally identical at every latency** —
because levels are static within the first second (§A1). A 20× latency
improvement (500 ms → 25 ms) moves q1 capture from 0.06% to 0.07%.

Fills stay **through-price dominated at every latency** (28 of 32 contracts
at 25 ms, q1) and post-fill move stays adverse at every latency and horizon:
+0.025 to +0.030 at 5 s, +0.040 to +0.050 at 15 s, +0.055 to +0.085 at 30 s.

**Latency is not the binding constraint. There is nothing for speed to buy.**

---

## B. P7 — PRE-WINDOW PLACEMENT (`p7_prewindow.py`)

### B1. Coverage bound, measured not assumed

`lane_recorder.py` subscribes at `expiry-330 s`; a 5 m window starts at
`expiry-300 s`. **Maximum observable pre-window lead is ~30 s.** Measured
over 45 BTC05M contracts: first observable event at T-29.5 s (min), T-28.6 s
(p50), 96% before T=0.

**T-5m, T-3m and T-1m are NOT OBSERVABLE in this tape.** Horizons tested:
T-20s, T-10s, T-5s, T=0. T-30s was skipped where it preceded first
observation rather than assumed. No claim is made about earlier horizons
directly — but see §B4, which makes them non-load-bearing.

### B2. The pre-window book is not a market

At T-5 s, **every window observed** shows the identical book:

```
bid 0.02 × 2500.0   |   ask 0.98 × 2498.0   spread 0.96   levels: 1 bid / 1 ask
```

That is a **placeholder seed pinned to the price bounds**, not tradeable
liquidity at a meaningful price. Consequently `q_ahead` for a pre-window
order joining the observable touch is **p10 2498 / p50 2499 / p90 2500 at
every horizon tested**.

### B3. Pre-window placement fills nothing

| horizon | clip | n | q_ahead p50 | fills | contracts | capture % | benign same-price | through | ambiguous |
|---|---|---|---|---|---|---|---|---|---|
| T-20 s | q1–q25 | 86 | 2499.0 | **0** | 0.00 | **0.00%** | 0.00 | 0.00 | 0.00 |
| T-10 s | q1–q25 | 86 | 2499.0 | **0** | 0.00 | **0.00%** | 0.00 | 0.00 | 0.00 |
| T-5 s | q1–q25 | 86 | 2499.0 | **0** | 0.00 | **0.00%** | 0.00 | 0.00 | 0.00 |
| T=0 | q1–q25 | 86 | 2499.0 | **0** | 0.00 | **0.00%** | 0.00 | 0.00 | 0.00 |

**Zero defensible fills from pre-window placement at every horizon and every
clip** — against the frozen back-of-queue model's 33–38 fills. Pre-positioning
is *worse*, not better: it parks the clip behind a 2,500-contract placeholder
at a price 1 cent from the bound. No adverse-selection row is reportable
because there are no fills.

### B4. When does the real blocking liquidity appear? AFTER T=0

5,796 blocking touch-levels (≥100 contracts) observed in-window:

| | |
|---|---|
| first seen relative to T=0 | p10 **+5.5 s**, p50 **+52.8 s**, p90 **+168.3 s** |
| first appeared **before** T=0 | **126 / 5,796 (2.2%)** |
| already present at our first sight (formation unobserved) | 255 / 5,796 (4.4%) |
| size at first sight | p50 252.0, p90 2,500.0, max 73,942.0 |

**This makes the unobserved T-5m/T-3m/T-1m horizons non-load-bearing.** Real
price levels do not exist pre-window — only the placeholder — and the real
blocking size first appears a median of **+52.8 s after the window opens**,
fully formed in one event (§A1). To be early at a real price you would have
to know where the real market will open, which is future information and
explicitly out of bounds.

**Residual uncertainty, named rather than buried:** I cannot exclude that a
genuine level existed at T-5m and was withdrawn before T-5 s. I observed no
such case, and the placeholder-only book at T-5 s makes it implausible, but
this tape cannot rule it out.

### B5. Level survival is not priority survival

A level observable pre-window still existed at T=0 in **86/86 (100.0%)** of
cases at every horizon tested. **This does not establish that our queue
priority would have survived**, and no such claim is made. Level *presence*
is observable from the feed; our *position inside* that level is not. The
distinction is preserved throughout.

---

## C. STRUCTURAL ANSWER TO THE QUESTION ASKED

> Could we have established queue priority minutes before the active BTC5m
> window and remained ahead of the giant liquidity that currently prevents
> benign maker fills?

**No — and for a more fundamental reason than queue mechanics.**

1. **Pre-window, there is no real price to be early to.** The book is a
   single placeholder pair at 0.02/0.98 with ~2,500 contracts a side.
2. **The real blocking liquidity arrives after T=0** (p50 +52.8 s), not
   before it, so pre-positioning cannot get ahead of it.
3. **When it arrives, it arrives complete** (`size_0ms/size_1000ms` = 1.000),
   so no achievable latency gets ahead of it either.
4. Both routes to queue priority are therefore closed, and the fills that
   remain are through-price sweeps that are adverse at every horizon.

**Per the stated decision rule — "if giant queues are already present at
first observation or capture remains poor even at aggressive hypothetical
latency, that strengthens the rejection" — both conditions hold. The
rejection is strengthened. Gemini is not flagged for further
execution-latency investigation.**
