# Favorite persistence: the intuition is right, and it still cannot be bought

**Gemini lane only. Descriptive. No threshold selected, no cell promoted, no strategy
parameter touched.** Durations never pooled. Hold-to-settlement; early exit stays
sealed. No live orders.

## Answer in three lines

1. **Leadership does become sticky as the clock runs down.** On BTC5m the probability
   of any later leadership flip falls **33.3% → 26.6% → 15.5% → 5.0% → 0.0%** at
   120s / 60s / 30s / 20s / 1–2s remaining.
2. **But that is a time-budget effect, not a different price process.** Flip
   *intensity* per unit of remaining time is the same in both markets: at an equal
   fraction of the window remaining (0.40), **BTC5m 4.04 vs BTC15m 3.86 flips per
   1000s**. Five minutes is stickier only because five minutes is shorter.
3. **Persistence is not expectancy.** Flip probability reaches 0% in the last ~10
   seconds while the leader still wins only **~70%** (BTC5m) — the book *freezes*
   before the outcome is determined. And where a favorite is actually executable late,
   it wins **7–11%**, because the availability of a ≥50¢ ask late in the window is
   itself an adverse signal. No cell in either matrix is an established positive.

---

## 1. Unconditional: does the current leader ultimately win?

Leader identified from whatever price information exists (fixed precedence
MID → BID_ONLY → ASK_ONLY → UNKNOWN; UNKNOWN contributes no observation and gaps are
counted, never interpolated). The leader is **not** required to be executable, which
is why n here exceeds the favorite table.

### BTC5m — 133 windows

| tte | frac of window | obs | leader wins % | CI95 (windows) | P(flip ≥1) % | mean later flips | flips / 1000s |
|---|---|---|---|---|---|---|---|
| 120s | 0.40 | 132 | 83.33 | [76.52, 89.39] | **33.33** | 0.485 | 4.04 |
| 90s | 0.30 | 129 | 86.82 | [80.62, 92.25] | 31.01 | 0.426 | 4.73 |
| 60s | 0.20 | 124 | **91.13** | [85.48, 95.97] | 26.61 | 0.339 | 5.65 |
| 45s | 0.15 | 126 | **90.48** | [84.92, 95.24] | 26.19 | 0.325 | 7.22 |
| 30s | 0.10 | 103 | 78.64 | [70.87, 86.41] | 15.53 | 0.194 | 6.47 |
| 20s | 0.07 | 80 | 67.50 | [57.50, 77.50] | 5.00 | 0.087 | 4.35 |
| 15s | 0.05 | 78 | 69.23 | [58.97, 79.49] | 3.85 | 0.064 | 4.27 |
| 10s | 0.03 | 76 | 69.74 | [59.21, 80.26] | 2.63 | 0.026 | 2.60 |
| 5s | 0.02 | 75 | 73.33 | [62.67, 82.67] | 1.33 | 0.013 | 2.60 |
| 2s | 0.01 | 70 | 71.43 | [61.43, 81.43] | **0.00** | 0.000 | 0.00 |
| 1s | 0.00 | 60 | 70.00 | [58.33, 81.67] | **0.00** | 0.000 | 0.00 |

**The shape is the story.** Leader accuracy *peaks* at 60s (91.1%) and then
**declines** to ~70% while the flip probability collapses to zero. A leader that can
no longer change and is only 70% right is a **frozen quote**, not a resolved market:
inside the last 20 seconds the observation count itself falls from 124 to 60 (the book
empties), and leadership is carried by one-sided BID_ONLY/ASK_ONLY quotes with no mid
at all. 240s and 180s were never collected and are not interpolated.

### BTC15m — 15 windows

| tte | frac of window | obs | leader wins % | CI95 (windows) | P(flip ≥1) % | mean later flips | flips / 1000s |
|---|---|---|---|---|---|---|---|
| 840s | 0.93 | 89 | 57.30 | [34.09, 80.00] | **55.06** | 2.798 | 3.33 |
| 720s | 0.80 | 84 | 69.05 | [43.68, 90.00] | 52.38 | 2.500 | 3.47 |
| 600s | 0.67 | 77 | 66.23 | [43.66, 86.25] | 50.65 | 2.377 | 3.96 |
| 480s | 0.53 | 64 | 51.56 | [28.79, 75.76] | 37.50 | 1.766 | 3.68 |
| 360s | 0.40 | 64 | 56.25 | [24.62, 85.94] | 34.38 | 1.391 | 3.86 |
| 300s | 0.33 | 55 | 61.82 | [28.57, 89.47] | 34.55 | 0.964 | 3.21 |
| 240s | 0.27 | 37 | 45.95 | [12.50, 84.62] | 37.84 | 0.919 | 3.83 |
| 180s | 0.20 | 35 | 62.86 | [20.00, 97.30] | 45.71 | 0.829 | 4.61 |
| 120s | 0.13 | 23 | 30.43 | [0.00, 77.78] | **0.00** | 0.000 | 0.00 |
| 60s | 0.07 | 9 | 44.44 | [0.00, 100.00] | **0.00** | 0.000 | 0.00 |

Every interval is wide because the unit is the window and there are 15 of them. The
late rows (23 and 9 observations, 4 and 3 windows) carry no weight.

### The direct test of the hypothesis

| comparison | BTC5m | BTC15m |
|---|---|---|
| P(flip) at **matched clock time** 120s | 33.3% | 0.0% (n=23, 4 windows) |
| P(flip) at **matched fraction** 0.40 of window | 33.3% (120s) | 34.4% (360s) |
| P(flip) at matched fraction 0.20 | 26.6% (60s) | 45.7% (180s) |
| **flip intensity / 1000s** at fraction 0.40 | **4.04** | **3.86** |
| flip intensity / 1000s, range over the window | 2.60 – 7.22 | 3.21 – 4.61 |

Matched on clock time BTC5m looks far less sticky — but that comparison is
meaningless, because 120s is 40% of a 5-minute window and 13% of a 15-minute one (and
BTC15m has almost no late observations). Matched on **fraction of window remaining**,
and on **flips per unit time**, the two markets are indistinguishable. So the
five-minutes-is-too-short-to-reverse intuition is **correct about the consequence and
wrong about the cause**: leadership in the 5-minute market is more persistent in
absolute terms purely because fewer seconds remain for a flip to occur, at essentially
the same flip rate.

---

## 2. The favorite matrix — win % (distinct windows), `*` = CI low above fee-adjusted breakeven

Favorite = the **dearer executable side**, and only when its executable ask is ≥ 0.50.
A side with no recorded ask is not a favorite at any price. Breakeven = `p + 0.07p(1−p)`
(published taker rate); zero-fee breakeven `p` is in the artifact beside it.

### BTC5m — 314 favorite observations of 1,463

| tte | 50–55¢ | 55–60¢ | 60–70¢ | 70–80¢ | 80–90¢ | 90–95¢ | 95–99¢ |
|---|---|---|---|---|---|---|---|
| 120s | . | 100.0% w1 | 33.3% w6 | 44.4% w9 | 80.0% w15 | 80.0% w10 | 84.6% w13 |
| 90s | 100.0% w1 | . | 0.0% w1 | 66.7% w3 | 66.7% w9 | 70.0% w10 | 54.5% w11 |
| 60s | . | . | 100.0% w2 `*` | 0.0% w1 | 50.0% w6 | 100.0% w3 `*` | 0.0% w1 |
| 45s | . | . | 50.0% w2 | 0.0% w1 | 100.0% w3 `*` | 50.0% w2 | 28.6% w7 |
| 30s | 16.7% w6 | . | 0.0% w2 | 0.0% w3 | 0.0% w3 | 0.0% w4 | 25.0% w4 |
| 20s | 14.3% w7 | 0.0% w1 | 14.3% w7 | 0.0% w3 | 0.0% w1 | 25.0% w4 | 0.0% w8 |
| 15s | 14.3% w7 | . | 0.0% w5 | 0.0% w3 | 0.0% w2 | 33.3% w3 | 0.0% w7 |
| 10s | 14.3% w7 | . | 0.0% w5 | 0.0% w3 | 0.0% w2 | 25.0% w4 | 0.0% w6 |
| 5s | 12.5% w8 | . | 0.0% w5 | 0.0% w2 | 50.0% w2 | 25.0% w4 | 0.0% w6 |
| 2s | 0.0% w9 | . | 0.0% w5 | 0.0% w2 | 50.0% w2 | 33.3% w3 | 0.0% w6 |
| 1s | 0.0% w7 | . | 0.0% w5 | 0.0% w1 | 50.0% w2 | 33.3% w3 | 0.0% w6 |

Edge (win % − fee-adjusted breakeven), the number that actually matters:

| tte | 50–55¢ | 55–60¢ | 60–70¢ | 70–80¢ | 80–90¢ | 90–95¢ | 95–99¢ |
|---|---|---|---|---|---|---|---|
| 120s | . | +43.3 (n=1) | −36.4 | −33.0 | **−5.4** (n=15) | −12.9 | −12.6 |
| 90s | +48.2 (n=1) | . | −65.6 | −12.8 | −19.3 | −22.1 | −42.4 |
| 60s | . | . | +33.9 (n=2) | −78.2 | −37.3 | +7.5 (n=3) | −97.2 |
| 45s | . | . | −18.1 | −78.2 | +12.2 (n=3) | −42.5 | −68.6 |
| 30s | −35.2 | . | −63.2 | −78.6 | −87.8 | −92.8 | −72.4 |
| 20s | −38.2 | −59.7 | −52.0 | −78.6 | −82.1 | −68.0 | −97.4 |
| 15s | −38.2 | . | −65.8 | −78.6 | −83.5 | −59.8 | −97.5 |
| 10s | −38.2 | . | −65.8 | −78.6 | −83.0 | −67.5 | −97.5 |
| 5s | −40.4 | . | −65.8 | −78.7 | −33.0 | −68.2 | −97.5 |
| 2s | −52.6 | . | −65.8 | −78.7 | −33.0 | −59.8 | −97.5 |
| 1s | −52.9 | . | −65.8 | −78.2 | −33.0 | −59.8 | −97.5 |

Pooled by checkpoint, the only rows with real n:

| tte | windows | obs | avg ask | win % | breakeven % | edge pp | CI95 | CI low − BE | EV/share | P(flip) % | P(opposite \| flip) % | depth median |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 120s | 58 | 58 | 0.862 | 70.69 | 87.01 | **−16.32** | [58.62, 82.76] | −28.39 | −$0.162 | 48.28 | 46.43 | 43.5 |
| 90s | 36 | 36 | 0.887 | 63.89 | 89.42 | −25.53 | [50.00, 77.78] | −39.42 | −$0.255 | 52.78 | 42.11 | 26.0 |
| 60s | 13 | 13 | 0.845 | 61.54 | 85.38 | −23.84 | [30.77, 84.62] | −54.61 | −$0.238 | 30.77 | 25.00 | 1250.0 |
| 45s | 16 | 16 | 0.896 | 50.00 | 90.22 | −40.22 | [25.00, 75.00] | −65.22 | −$0.401 | 31.25 | 20.00 | 1181.0 |
| 30s | 24 | 24 | 0.780 | 8.33 | 79.24 | −70.91 | [0.00, 20.83] | −79.24 | −$0.707 | 12.50 | 100.00 | 39.0 |
| 15s | 29 | 29 | 0.769 | 6.90 | 78.14 | −71.24 | [0.00, 17.24] | −78.14 | −$0.710 | 6.90 | 100.00 | 74.0 |
| 1s | 24 | 24 | 0.743 | 8.33 | 75.59 | −67.26 | [0.00, 20.83] | −75.59 | −$0.670 | 0.00 | — | 44.0 |

**Four cells have a CI low above breakeven** — 45s/80–90¢ (**3 windows**), 60s/60–70¢
(2), 60s/90–95¢ (3), and on BTC15m 480s/50–55¢ (2). Three windows is not a finding and
none is promoted. Per the brief, no threshold is selected from this table.

### BTC15m — 537 favorite observations, 15 windows

Edge by cell (n = observations):

| tte | 50–55¢ | 55–60¢ | 60–70¢ | 70–80¢ | 80–90¢ | 90–95¢ | 95–99¢ |
|---|---|---|---|---|---|---|---|
| 840s | −5.2 (4) | +19.8 (23) | −7.6 (40) | −44.1 (22) | . | . | . |
| 720s | +31.8 (8) | +24.6 (6) | −10.9 (20) | −28.6 (23) | +1.2 (27) | . | . |
| 600s | −55.7 (1) | +9.6 (13) | +0.8 (17) | −0.8 (16) | −20.7 (28) | −93.0 (2) | . |
| 480s | +44.3 (2) `*` | −1.0 (12) | −37.0 (11) | −31.7 (7) | −26.8 (15) | −54.9 (8) | −19.0 (9) |
| 360s | . | +3.4 (8) | −26.9 (18) | +3.3 (9) | −52.2 (3) | −31.9 (13) | −36.3 (13) |
| 300s | . | −8.7 (6) | −2.5 (16) | +7.6 (13) | −32.1 (2) | −80.0 (7) | −25.3 (11) |
| 240s | . | +40.6 (3) | −37.7 (10) | −51.8 (9) | −56.6 (7) | −93.9 (2) | +1.9 (6) |
| 180s | . | +24.1 (6) | +11.9 (9) | −14.2 (5) | −84.9 (4) | −93.5 (1) | −27.8 (10) |
| 120s | . | −59.2 (2) | −69.5 (7) | −72.4 (2) | . | . | −39.1 (12) |
| 60s | . | . | . | . | −85.9 (1) | −94.4 (2) | −30.5 (6) |

Pooled by checkpoint the best rows are 720s (edge −5.35pp, CI low −30.50) and 600s
(−9.13pp, CI low −31.96). Sign-wise the 55–60¢ and 60–70¢ columns at long horizons are
mildly positive, which is where a real effect would live if one exists — but on 15
windows every interval straddles breakeven. **No depth was recorded for this duration**,
so none of these cells carries a capacity number.

---

## 3. Why the late BTC5m favorite cells are not what they look like

This is the part worth reading twice. The late cells say an executable 76¢ favorite
wins 7%. That is not a fact about favorites; it is a fact about **which late quotes
remain standing**.

| tte | obs | leadership known | executable favorite present | favorite = leader | leader wins % **(all obs)** | leader wins % **(where a ≥50¢ ask exists)** |
|---|---|---|---|---|---|---|
| 120s | 133 | 132 | 58 (43.9%) | **100%** | 83.33 | 70.69 |
| 90s | 133 | 129 | 36 (27.9%) | **100%** | 86.82 | 63.89 |
| 60s | 133 | 124 | 13 (10.5%) | **100%** | 91.13 | 61.54 |
| 45s | 133 | 126 | 16 (12.7%) | **100%** | 90.48 | 50.00 |
| 30s | 133 | 103 | 21 (20.4%) | 87.5% | 78.64 | **20.83** |
| 20s | 133 | 80 | 29 (36.2%) | 90.6% | 67.50 | **18.75** |
| 15s | 133 | 78 | 26 (33.3%) | 89.7% | 69.23 | **17.24** |
| 10s | 133 | 76 | 25 (32.9%) | 89.3% | 69.74 | **17.86** |
| 1s | 133 | 60 | 20 (33.3%) | 83.3% | 70.00 | **25.00** |

At ≥45s the executable favorite **is** the leader, in 100% of cases. At ≤30s they still
agree on *identity* 83–91% of the time, but the leader's win rate **collapses from
67–79% across all observations to 17–26% on the subset where a ≥50¢ ask exists**.

So the conditioning event — "somebody is still offering this side at 50¢ or more with
30 seconds left" — is itself strongly adverse. In the other two-thirds of late
observations nobody offers the leader at ≥50¢ at all; the book has 0 executable sides
in 53–73 of 133 observations inside the last 20 seconds. What remains offered late is
the stale side.

Together with the earlier structural finding (two executable asks summing to a median
**1.330**, touch spread **0.330**), the late favorite is doubly unbuyable: the quote you
can hit is on the wrong side, and even when it is on the right side you pay 33 cents of
spread for a 5–10 point edge that is not there.

---

## 4. Underdog and favorite are the same trade, and both say the same thing

At 120s on BTC5m, where both views have real n:

- underdog: avg executable ask **0.269**, wins **10.09%** → −18.16pp
- favorite: avg executable ask **0.862**, wins **70.69%** → −16.32pp

These are consistent, not contradictory: 0.269 + 0.862 = **1.131**, and 10.09 + 70.69 =
**80.8%**, so ~19 points of probability mass and ~13 cents of price are simply missing
to the spread and the one-sided book. Both sides of the same trade lose by roughly half
the excess. **That is the whole result.** Nothing in this table changes the verdicts:
BTC5m **KILL**, BTC15m **COLLECT MORE**.

## Artifacts

`favorite_persistence.json` · `favorite_vs_leader_diagnostic.json` ·
script `scripts/gemini_favorite_persistence.py`.
Accounting label: historical replay on Gemini's own recorded books and settlements.
No order was sent. Nothing here is realized profit.
