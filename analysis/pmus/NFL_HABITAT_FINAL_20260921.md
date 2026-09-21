# NFL Sunday habitat experiment — final (checkpoint 4 of 4)

**Everything here is SHADOW / HYPOTHETICAL.** No order was placed, no fill is
venue-confirmed, nothing is submitted, filled, settled or realized. A "first
fill" is a *modelled* event assuming a maker resting at the observed touch
would have filled — an assumption today's prospective work has shown is not
safe (see the caveat at the end). This is a habitat measurement, not an
execution claim.

**Right-censoring is EXCLUDED from every reach figure, never counted as a
miss.** If a market's observations end before t+N, that state cannot answer
"did the complement become acquirable within N seconds", so it is dropped from
the reach@N *denominator*. Counting it as a failure would penalise exactly the
states nearest the end of collection.

## Coverage — two cohorts could not be measured

| cohort | book | tape | status |
|---|---|---|---|
| International BTC15m | Zenodo 1-s full depth | — | **measured** |
| Gemini BTC5m | **none** | prints only | **NOT COLLECTED** |
| Gemini BTC15m | 51 min / 4 windows (09-15) | prints only | **NOT COLLECTED** |
| PMUS NFL PRE / 1PM / 4PM | local tape | local flow | **measured** |
| NFL SNF LIVE | — | — | **NOT COLLECTED** |

Two gaps, both stated rather than filled:

- **Gemini**, from the backfill's own caveat: *"the external tape is recoverable
  for expired contracts; the BOOK is not."* Spread, %≤1c, reach and median
  reach are all book-derived, so they do not exist for expired Gemini windows.
  The only local Gemini book is `gemdepth`, 51 minutes across 4 BTC15m windows
  on 09-15 — a different regime, too thin to place in this table, and **no
  BTC5m book at all**.
- **NFL SNF**: the local collectors stopped at **2026-09-20T23:55Z** and SNF
  kicked off at 00:20Z. The durable Railway collector has the window on its
  `/state/l5-live` volume, which this container cannot read. **SNF is
  recoverable but not from here.**

## A. Comparison table

Pair target **0.98** (the program's fee-safe target). PMUS maker fee is zero;
**no rebate is assumed anywhere**.

| cohort | spread | ≤1c | mv/min | r30 | r60 | r120 | med reach | matched | basis | after-fee | residual |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **INTL BTC15m** | 1.0c | 96.1% | 11.7 | 69.0% | 77.8% | **85.2%** | **7.0s** | 83.4% | 0.9800 | $0.0200 | 16.6% |
| Gemini BTC5m | — | — | — | — | — | — | — | — | — | — | — |
| Gemini BTC15m | — | — | — | — | — | — | — | — | — | — | — |
| PMUS NFL PRE | 1.0c | 73.4% | 0.0 | 26.4% | 26.4% | 26.4% | 0.0s | 45.7% | 0.9600 | $0.0400 | 54.3% |
| NFL 1PM LIVE | 1.0c | 76.9% | 0.4 | 28.3% | 34.2% | 42.0% | 35.9s | 60.0% | 0.9600 | $0.0400 | 40.0% |
| NFL 4PM LIVE | 1.0c | 70.5% | 0.5 | 34.2% | 38.1% | 43.2% | 50.9s | 75.6% | 0.9400 | $0.0600 | 24.4% |
| NFL SNF LIVE | — | — | — | — | — | — | — | — | — | — | — |

Censored states excluded from denominators — INTL 1,235 / 2,410 / 4,676;
PRE 527 / 1,053 / 2,089; 1PM 469 / 907 / 1,651; 4PM 1,309 / 2,325 / 4,498.

**A correction made during this analysis.** The first run scored reach against
a pair target of **1.00** and every cohort returned median reach 0.0s. Resting
on both touches costs `1 − spread`, which is below 1.00 whenever the spread is
positive — i.e. always — so that test was another **algebraic identity** and
measured nothing. Re-scored at the program's own 0.98 target, which a state can
genuinely fail. (The original code also carried a double-negation that made the
condition `bid + ask ≤ 1`, which is not a pair cost at all.)

## B. Against the preregistered thresholds

Thresholds fixed before the run: median spread ≤2c, ≥95% of states ≤2c,
≥70% reach@120s, median reach ≤30s.

| cohort | verdict | exactly which dimension fails |
|---|---|---|
| **INTL BTC15m** | **QUALIFIES** | none |
| PMUS NFL PRE | FAILS | `share_spread_le_2c` 0.778 (needs ≥0.95); `reach120` 0.264 (needs ≥0.70) |
| NFL 1PM LIVE | FAILS | `share_spread_le_2c` 0.837; `reach120` 0.420; `median_reach_s` 35.9 (needs ≤30) |
| NFL 4PM LIVE | FAILS | `share_spread_le_2c` 0.744; `reach120` 0.432; `median_reach_s` 50.9 |

**Near-qualifier ranking:** NFL 4PM (2 of 4 dimensions closest, best matched
conversion) > NFL 1PM (best spread share of the live cohorts) > NFL PRE (fails
worst on reach, and its books barely move at all).

### Per market type — the requirements trade off against each other

| cohort | type | mkts | spread | ≤2c | r120 | med reach | fails |
|---|---|---|---|---|---|---|---|
| PRE | moneyline | 31 | 0.5c | **95.4%** | 5.5% | 13038s | reach |
| PRE | spreads | 176 | 1.0c | 81.8% | 21.3% | 0.0s | spread share |
| PRE | totals | 320 | 1.0c | 74.4% | 30.7% | 0.0s | spread share |
| 1PM | moneyline | 8 | 0.5c | **99.3%** | 33.0% | 163s | reach |
| 1PM | spreads | 45 | 0.5c | 81.5% | 51.7% | 39.8s | spread share, reach, med |
| 1PM | totals | 69 | 1.0c | 80.0% | 39.1% | 0.0s | spread share |
| 4PM | moneyline | 13 | 0.5c | **100.0%** | 18.3% | 372s | reach |
| 4PM | spreads | 127 | 0.5c | 72.6% | 48.1% | 35.7s | spread share, reach, med |
| 4PM | totals | 191 | 1.0c | 67.7% | 48.7% | 8.4s | spread share |

> **This is the substantive finding of section B.** Within NFL the two
> requirements are anti-correlated. **Moneyline has the tight spreads**
> (95–100% ≤2c, the only markets that clear that bar) **and the worst reach**
> (5.5–33%, median reach in minutes to hours). **Spreads and totals recycle far
> better** (r120 39–52%) **and never clear the spread bar.** No market type is
> good at both, so no NFL slice qualifies — and the failure is structural, not
> a near miss on one dimension.

## C. Same-market PRE → LIVE deltas

Only slugs observed in **both** regimes, paired per market.

| | 1PM (47 paired slugs) | 4PM (52 paired slugs) |
|---|---|---|
| Δ spread (cents) | p50 **+0.0**, 17% widen | p50 **+0.0**, 13% widen |
| Δ reach120 | p50 **+0.318**, **81% improve** | p50 **+0.326**, **81% improve** |
| Δ matched conversion | p50 **+0.562**, 83% improve | p50 **+0.520**, 62% improve |

Going live **substantially improves recycling without widening the spread**.
That is a real within-market effect, not a composition artifact — it is the
same slugs measured in both regimes. It is also why the live cohorts still fail:
they improve on reach from a base too low to clear 70%, while the spread bar
they were already failing does not move.

## D. Hypothetical maker recycling

FIRST FILL → OPPOSITE EXECUTABLE INTEREST → MATCHED. Censoring excluded.

| cohort | @30s | @60s | @120s | remainder | basis |
|---|---|---|---|---|---|
| PMUS NFL PRE | 26.4% | 26.4% | 26.4% | 45.7% | 0.9600 |
| NFL 1PM LIVE | 28.3% | 34.2% | 42.0% | 60.0% | 0.9600 |
| NFL 4PM LIVE | 34.2% | 38.1% | 43.2% | 75.6% | 0.9400 |

Per 100 hypothetical first fills, remainder-of-market:

| cohort | matched | residual | modelled edge/pair | per 100 fills |
|---|---|---|---|---|
| PMUS NFL PRE | 45.7 | 54.3 | $0.0400 | $1.83 |
| NFL 1PM LIVE | 60.0 | 40.0 | $0.0400 | $2.40 |
| NFL 4PM LIVE | 75.6 | 24.4 | $0.0600 | $4.53 |

**The residual is never netted against the pair edge.** 24–54 unmatched first
legs per 100 carry directional exposure whose value is **UNMEASURED**, and an
unmeasured line makes the total unmeasured. These are not profit figures.

## E. Frozen L5 model on a qualifying cohort — DOES NOT FIRE

**The only cohort that qualifies is International BTC15m, which is L5's own
venue: REFERENCE / FIDELITY ONLY and not executable.** Running the frozen
continuous-inventory model on it would be testing the habitat filter on the
data the behaviour was derived from — circular as a habitat test, and it cannot
inform deployment because we cannot trade there.

**No executable cohort qualifies**, so (E) is not run. The preregistered
condition is the gate, and it was not met by any PMUS slice.

## The caveat that governs every number above

These figures assume **a maker resting at the observed touch would be filled**.
Today's prospective recorder shows that assumption is unsafe: observed L5 maker
fills occurred with arriving executable flow **three to four orders of magnitude
below the depth resting ahead** (`arriving_flow_over_depth_ahead` 0.0000–0.0336,
n=16). Whatever fills a maker on these venues, it is not simply "the touch was
reached". So every matched-conversion and per-100-fills figure here is an
**upper bound on a hypothetical**, and the gap between it and reality is exactly
what the prospective work is now measuring.

## What this experiment settles

- **PMUS NFL is not an L5 habitat**, at any daypart or market type, and the
  reason is structural: tight spreads and good recycling do not co-occur.
- **Going live helps recycling and not spreads** — worth knowing, insufficient.
- **Two cohorts were never measurable** and are recorded as such rather than
  estimated. SNF is recoverable from the durable volume if wanted.
