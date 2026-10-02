# 1300z: first synchronized PMUS + BTC window

Instrumentation and mechanism validation only. **No rule is promoted, whatever the
numbers below show.** Collection continues unchanged.

Window `cpc-btc-updown-15m-2026-10-02-1300z`. Frozen refs: `4cfc293f`,
pre-registration `82714061`. Measured RTT: min 0.187 / p50 0.266 / p90 0.612 s.

## 0. Coverage proof — PARTIAL, and it governs everything below

The coverage test was run before scoring and it does **not** fully pass.

| feed | in-window rows | gaps > 2 s | restarts | reconnect errors |
|---|---|---|---|---|
| BTC reference | 5,800 | **3** (2.139 s, 2.035 s, 2.502 s) | 0 | 0 |
| PMUS book | 4,126 | **87**, up to 5.126 s | — | — |
| PMUS trades | 2,111 | — | — | — |

The reference brackets the whole 890.2 s span at both ends, and its log line
sequence is **unbroken (seq 7–246, 240 lines, 0 missing)**, so its three gaps are
quiet-market periods on a change-driven ticker, not lost data.

The 87 PMUS book gaps are a different matter: they cluster near 5 s, which is the
recorder's emission cadence, and the recorder's **50-row book cap is hit on every
emission**. These are almost certainly capped-away book states, i.e. **real
missing L2 coverage inside the window**. So 1300z is continuous on the reference
side and *not* continuous on the PMUS book side. Everything below is scored over
a book stream with known holes and is provisional for that reason alone.

Two corrections made during this harvest, both my own errors:

1. **A 0.7 s fragment passed the coverage test.** The 1245z harvest arrived as
   three rows spanning 0.7 s and satisfied a pure overlap ratio trivially — the
   same class of artifact as the 1345z tape truncation. A `MIN_SPAN_S = 600 s`
   gate now applies and 1245z is correctly excluded.
2. **Summed `pending` is not a loss measure.** `pending` is the remainder of the
   same batch going out on the next line; the first harvest summed 320 with
   nothing actually missing. The real truncation test is `seq` continuity, which
   passes. The harvester now reports both, with the pending sum labelled.

## 1. Frozen 82714061 result (unchanged, scored independently)

Cumulative over all windows now in the tape, BASELINE and all 13 candidates
intact, cutoffs untouched:

| config | acc | qual | frcd | fRej | net | net/leg | net/win |
|---|---|---|---|---|---|---|---|
| BASELINE | 346 | 317 | 29 | 16 | −74.61 | −0.2156 | −8.29 |
| A300 | 177 | 172 | 5 | 136 | +11.16 | +0.0631 | +1.24 |
| A420 | 129 | 128 | 1 | 169 | +28.79 | +0.2231 | +3.20 |
| A540 | 95 | 95 | 0 | 194 | +20.44 | +0.2152 | +2.27 |
| B5-1c | 358 | 194 | 0 | 138 | −53.91 | −0.1506 | −5.99 |
| C | 129 | 98 | 1 | 197 | −3.80 | −0.0295 | −0.42 |

A420 and A540 remain positive but accept 37% and 27% of BASELINE's legs, so both
still fail the pre-registered 50%-capacity criterion. **No candidate passes. No
promotion.**

## 2. Genuine RESTING-order shadow (fixed-price, lifecycle persisted)

A defect was found and fixed before these numbers were trusted — see §8.

| arm | places | fills | expires | cancels | repriced |
|---|---|---|---|---|---|
| TTL5 | 363 | 352 | 222 | 141 | **0** |
| TTL30 | 143 | 295 | 30 | 113 | **0** |
| WINDOW | 68 | 175 | 0 | 66 | **0** |

`repriced = 0` in every arm: no code path moves a live order's price, which is
the mechanism property being validated. Median quote age at fill 6.108 s.

## 3. QUALIFYING vs TOXIC/FORCED first legs (baseline exit rules, all ceilings)

| ceiling | TTL5 Q / T | TTL30 Q / T | WINDOW Q / T |
|---|---|---|---|
| 0.99 | 294 / 58 | 226 / 69 | 117 / 58 |
| 0.98 | 291 / 61 | 219 / 76 | 111 / 64 |
| 0.97 | 280 / 72 | 206 / 89 | 111 / 64 |
| 0.95 | 255 / 97 | 180 / 115 | 99 / 76 |

Figures in §4–§7 use WINDOW at c.97 (**64 TOXIC, 111 QUALIFYING**) — the
strictest arm, one ceiling shown for legibility, not selected.

## 4. EXTERNAL / QUEUE / BOTH / NEITHER precursor counts

At EXTERNAL ≥ 1.0 bps, QUEUE ≥ 0.25 (every other pre-registered pair is in the
JSON and none is selected):

| horizon | TOXIC B/E/Q/N | QUALIFYING B/E/Q/N |
|---|---|---|
| 1 s | 0 / 7 / **33** / 24 | 4 / 12 / 19 / 76 |
| 2 s | 4 / 8 / **30** / 22 | 12 / 27 / 13 / 59 |
| 5 s | 4 / 20 / **30** / 10 | 28 / 37 / 11 / 35 |

QUEUE dominates the toxic group at every horizon (30–34 of 64) and stays a
minority of the qualifying group (11–19 of 111). EXTERNAL alone is *more* common
before qualifying fills than toxic ones at 2 s and 5 s — the opposite of the
hypothesis that external moves mark toxicity.

## 5. Onset-to-fill versus cancel RTT

Onset p50 is **5.0 s**, the longest horizon measured, so for most fills carrying a
signal the warning is present at the earliest point looked at and the true onset
is left-censored by the 5 s window. Against measured RTT this is comfortably
actionable: p50 0.266 s and p90 0.612 s are an order of magnitude shorter. Only
**1 of 64** toxic fills had its signal arrive inside the p90 RTT.

The 1 s and 2 s horizons sit far above p90 too, so unlike the 4cfc293f study
nothing here is unactionable by construction.

## 6. Cancellable toxic vs sacrificed qualifying — no threshold selected

At every pre-registered level pair, onset ≥ RTT p90:

| EXTERNAL / QUEUE | avoidable toxic (of 64) | sacrificed qualifying (of 111) | too late |
|---|---|---|---|
| 1.0 bps / 0.25 | 59 | 82 | 1 |
| 1.0 bps / 0.50 | 59 | 80 | 0 |
| 2.0 bps / 0.25 | 51 | 74 | 1 |
| 2.0 bps / 0.50 | 51 | 67 | 0 |
| 5.0 bps / 0.25 | 40 | 47 | 1 |
| 5.0 bps / 0.50 | 39 | 34 | 0 |

Every level pair sacrifices **more qualifying fills than it saves toxic ones**, or
at best breaks even (39 vs 34 at the strictest). The precursors are real and
early enough to act on; they are not selective. That is the finding, and it is
not a threshold.

Strongest single actionable separator, for the record only: `queue_depletion ≥
0.5 @ 2 s`, P(signal|toxic) 0.531 vs P(signal|qualifying) 0.180.
`pmus_mid_adverse` runs the *wrong* way (0.500 toxic vs 0.820 qualifying), and so
does `trade_imbalance ≥ 0.75` (0.141 vs 0.432).

## 7. Measured lead/lag, and one toxic event sequence

**The reference does not lead PMUS.** Scanning −2 s…+2 s, peak correlation is at
**lag 0.00 s (corr 0.5414)**, with a symmetric decay (0.5343 at −0.05 s, 0.5409 at
+0.05 s, 0.4894 at +0.25 s, 0.2138 at +1.0 s). n = 4,142. Reference skew is now
p50 −0.011 s, p90 0.006 s, so a lead of a few hundred ms would have been visible
had one existed. On this window the two venues move together within one book
update, and **no exploitable external lead exists at this resolution**.

Per-toxic-fill `BTC_REF → PMUS_BOOK → PMUS_TRADE → SHADOW_FILL` sequences are in
the JSON. The typical shape: heavy same-direction taker flow walking our level
down, queue-ahead collapsing, and the reference moving essentially in step rather
than ahead — consistent with §4 and §7 both.

## 8. The defect found and fixed during this report

The first revision of the resting arm kept charging **queue-ahead measured at
placement** even after the market traded past the order. One toxic lot showed a
0.44 bid still "queued" behind 2,383 shares while the book walked down to 0.37
over **424 s**, then filling on a 0.37 print. That is not a resting order, it is
an unreachable one, and it both inflated quote ages and distorted the labels
(WINDOW arm: 16 fills, 8/8 split before; 175 fills, 111/64 after).

The fix applies the trade-through rule already established on the Gemini tape and
missing here: a print **strictly through** our price proves the level was cleared,
so a quote there fills regardless of queue position. Pre-fix output is preserved
as `PMUS_PREFILL_REALTIME_STUDY.QUEUEBUG.json`. Every number in §2–§7 is post-fix.

## Standing caveats

- One window, 64 toxic lots, book coverage with 87 known holes. Nothing here
  supports a rule and none is proposed.
- The 50-row book cap is now the binding data-quality limit, ahead of anything
  about the strategy.
