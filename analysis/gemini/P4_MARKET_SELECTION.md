# P4 — Market selection: no candidate passes the habitat screen, and the target was wrong

**Status: MEASUREMENT ONLY. No live orders, nothing armed, no new pipeline built.**
All Gemini figures are from the venue's own PUBLIC endpoints
(`/v1/prediction-markets/events`, `/v1/trades/{symbol}`), read today
2026-09-22. All L5 figures are from the existing corpus on disk. PMUS figures
are from the existing `analysis/pmus/` collectors.

---

## 0. The finding that reframes the search

P3 concluded "L5's edge is inseparable from a venue printing 122 trades and
$1,087 per 15-minute window". **Ranking L5's own lanes shows that is the wrong
target.** Decomposition below reconciles to his own
`pnl_derived_resolution` column **lane by lane, to the dollar**:

| lane | windows | trades | trades/win | $ bought/win | matched $ | m/cost | residual $ | r/cost | **m+r** | L5's own column |
|---|---|---|---|---|---|---|---|---|---|---|
| **BTC 15m** | 527 | 63,569 | **120.6** | **$1,089** | +18,598 | 3.5% | **−19,712** | **−54.4%** | **−1,115** | −1,115 |
| BTC 5m | 957 | 6,229 | 6.5 | $135 | +598 | 0.7% | −540 | −1.2% | **+58** | +58 |
| ETH 5m | 703 | 2,069 | 2.9 | $35 | −233 | −2.6% | +230 | 1.5% | **−3** | −3 |
| ETH 15m | 29 | 117 | 4.0 | $36 | −50 | −11.1% | +59 | 10.0% | **+10** | +10 |
| **TOTAL** | 2,216 | | | | | | | | **−1,051** | **−1,050** |

**BTC15m is simultaneously his densest lane and his worst.** It carries 91% of
his trades and essentially all of his loss. Over 22 days across all four
lanes he is **−$1,051**.

So the habitat we spent P3 trying to reach is the habitat where the reference
trader loses money. **There is no demonstrated profitable L5 habitat in this
corpus to select for.** That is the single most important result here, and it
changes the question from "where can we find L5-like density" to "what
property, if any, made any of this profitable".

*(An earlier pass of this ranking gave +$2,739 by walking the trade sequence
file directly. It did not reconcile to L5's own column and is withdrawn; the
table above, built from the validated summary columns, does.)*

---

## 1. Gemini hourly BTC — the owner's ~$10k observation, verified

**The displayed figure is real and is not executable per-contract flow.**

```
Gemini contracts with a genuine ~1-hour life     16   (8 BTC, 8 ETH)
  ...with ANY trade at all                        2
  median trades per contract                      1
  median two-sided fraction                    0.00
```

The `$766,242` that BTC "EVENT" markets show as `volume24h` is an **EVENT**
figure, shared across ~40 strike contracts, and those events have a **median
life of 7.5 days** (min 60 min, max 30 days). Per contract:

```
GEMI-BTC2609222100-HI85000    displayed event volume24h   $40,750
                              ACTUAL TAPE                  8 trades, $347,
                                                           over 13 hours
```

**Verdict: Gemini hourly/event BTC is not a habitat.** It is large displayed
notional distributed over many strikes and many days, with a median gap
between prints of 923 seconds.

---

## 2. The Gemini slate, measured

171 contracts sampled across every category, normalized two ways: over the
contract's nominal life, and over its **active tape span** (first print to
last), which cannot be diluted by a long-dated market.

| lane | n | with trades | median trades/mkt | trades/min (active) | $/min | two-sided | median gap | event vol24h |
|---|---|---|---|---|---|---|---|---|
| **L5 BTC15m (reference)** | | | **122** | **10.72** | **$72** | — | **2s** | — |
| Crypto BTC 15M | 5 | 1 | 0 | 1.795 | $28.13 | 0.43 | 39s | $887 |
| Crypto ETH 15M | 5 | 1 | 0 | 1.429 | $0.18 | 0.50 | 86s | $6 |
| Politics | 6 | 6 | 6 | 0.043 | $0.00 | 0.15 | 62s | $31 |
| Weather | 6 | 1 | 0 | 0.037 | $0.13 | 0.25 | 0s | $2,656 |
| Crypto ZEC event | 8 | 8 | 6 | 0.025 | $0.23 | 0.21 | 303s | $62,090 |
| Crypto ETH event | 8 | 6 | 10 | 0.023 | $0.39 | 0.14 | 1,323s | $73,208 |
| Crypto SOL event | 8 | 8 | 8 | 0.017 | $0.26 | 0.18 | 2,096s | $57,161 |
| Sports (all) | 40 | 34 | 9 | 0.013 | $0.09 | **0.05** | 2,428s | $81,142 |
| Crypto XRP event | 8 | 8 | 10 | 0.011 | $0.26 | 0.26 | 3,366s | $62,076 |
| Economics | 5 | 5 | 31 | 0.007 | $0.09 | 0.07 | 0s | $22,762 |
| **Crypto BTC event** | 40 | 33 | 6 | **0.005** | $0.15 | 0.29 | 923s | **$766,242** |
| Crypto BTC 05M | 13 | 1 | 0 | 0.000 | $0.17 | 0.50 | 2s | $2 |
| Crypto ETH 05M | 13 | 0 | 0 | — | — | — | — | $0 |

**Distance from L5's BTC15m (10.72 trades/min):**

```
<2x       nothing
2-5x      nothing
5-10x     nothing
>10x      EVERYTHING on the Gemini slate
          best measured lane is 6x short even on its most favourable
          normalisation, and that lane (BTC 15M) is the one where WE were
          33-100% of the tape
```

The BTC 15M and ETH 15M rows read high only because a single contract had a
handful of prints inside a very short active span; `with trades` is 1 of 5.
They are not evidence of a habitat, and P3 already established we are most of
that tape ourselves.

**Two-sidedness is the harsher filter.** Sports sits at **0.05** — essentially
one-directional flow. The L5 mechanism needs
`EXTEND -> opposite acquisition -> matched inventory -> continued oscillation`,
and a book where 95% of prints are one way cannot supply the opposite leg.

---

## 3. PMUS sports — already screened, and it fails on oscillation

Not re-measured, per the instruction not to rebuild an existing pipeline.
`analysis/pmus/sports_habitat_discovery.json` ran a preregistered test
(thresholds fixed before any PMUS data was seen) over 127 markets / 25,908
observations:

```
qualifying categories   0
near miss               mlb|moneyline|LIVE
```

The diagnostic that matters, because it separates three different failures:

| | spread | touch depth | touch moves/min | reach@120s | median reach |
|---|---|---|---|---|---|
| **Polymarket Intl BTC15m (L5's actual habitat)** | 0.01 | 65 | **4.88** | **80.6%** | 16s |
| PMUS MLB moneyline LIVE | 0.005 | **3,951** | 0.60 | 29.9% | 40s |
| Gemini BTC5m/15m | **0.28** | 217 | 5.18 | 24.4% | 72s |

**PMUS sports fails for the opposite reason to Gemini.** Its books are tight
and very deep but nearly static — 0.6 touch moves per minute against L5's
4.88, and the price reaches a complementary level within 120s only 29.9% of
the time against 80.6%. Deep and slow is as unusable as thin and wide: the
oscillation that creates the opposite-side acquisition is missing.

**Do not assume Polymarket International liquidity transfers to PMUS** — the
existing measurement says it does not, and that is a measurement, not an
assumption.

*Limitation carried forward from that study, stated rather than hidden:* it is
ONE 28-minute sample taken 01:45-02:15 UTC, the tail of the US sports evening.
A daytime NFL/NBA sample could differ. `analysis/pmus/live_rankings_and_shadow.json`
notes real heterogeneity inside the pooled number (one soccer segment reached
71.5% on n=144), so a targeted re-sample of the best segments during peak
hours is the one PMUS follow-up that could change this verdict.

---

## 4. What break-even actually requires

Rather than chasing a fills/minute target, the condition is a **ratio**:

```
net = matched_cost x mRet  +  residual_cost x rRet
break-even  <=>  residual_cost / matched_cost  <=  mRet / (-rRet)
```

| | matched cost | mRet | residual cost | rRet | actual r/m | break-even r/m | |
|---|---|---|---|---|---|---|---|
| L5 BTC15m | $531,371 | 3.5% | $36,235 | −54.4% | **6.8%** | 6.4% | fails, narrowly |
| L5 BTC5m | $85,429 | 0.7% | $45,000 | −1.2% | **52.7%** | 58.3% | passes, narrowly |
| COHORT-2 | $10.12 | 28.5% | $10.00 | −80.0% | **98.8%** | 35.6% | fails by ~3x |

At COHORT-2's own measured economics a matched pair returned **+$0.2218** and
a residual contract **−$0.80**, so a window breaks even at

```
matched_pairs >= 3.6 x residual_contracts
```

COHORT-2 ran **13 matched pairs against 30 residual contracts**; it needed
~108. **The requirement is not 122 fills per window — it is roughly four
matched pairs for every residual contract left over.** That is a completion
problem, not a participation problem, and P1/P2 already showed completion
cannot be bought back after the fact on this venue.

---

## 5. Selection matrix — advancement decisions

| lane | density vs L5 | two-sided | oscillation | verdict |
|---|---|---|---|---|
| Gemini BTC 1h / event | >100x short | 0.29 | 923s between prints | **REJECT** — 2 of 16 hourly contracts ever traded |
| Gemini BTC 15m | ~6x short, and we are 33-100% of it | 0.43 | reach120 24.4% | **PRESERVE as reference lane only** |
| Gemini BTC 5m | >20x short | 0.50 | reach120 24.4% | **REJECT** on current flow |
| Gemini sports | >800x short | **0.05** | 2,428s between prints | **REJECT** |
| Gemini ZEC/XRP/SOL/ETH event | >400x short | 0.14-0.26 | 303-3,366s | **REJECT** |
| Gemini politics / weather / economics / commodities | >250x short | 0.07-0.25 | — | **REJECT** |
| PMUS live sports | depth ample, flow static | — | **reach120 29.9%** | **REJECT** on current evidence; one targeted peak-hours re-sample is the only thing that could overturn it |
| Polymarket International BTC15m | **this is the reference** | — | reach120 80.6% | not executable for us — no access path established |

**Nothing advances to sequential replay or shadow.** Running the frozen
controller over Gemini hourly BTC was on the instruction list; it is not
worth doing, because 2 of 16 one-hour contracts printed a single trade each
and there is no tape to replay against. Building a flow-gated 5m/15m/1h
selector is likewise premature: a selector allocates capital when flow
thresholds are met, and no threshold derived from L5's habitat is ever met on
this venue.

---

## 6. What I did not do, and why

- **No live orders, nothing armed.**
- **No new collector.** Gemini measurement used the public endpoints
  directly; PMUS reused the existing screened output.
- **No flow-gated selector, no hourly-BTC replay, no longer-retention test on
  hourly BTC.** All three were instructed, and all three are downstream of a
  habitat that measurement says does not exist. Building them would produce
  numbers with no market behind them.
- **Gemini BTC15m preserved** as a reference/test lane, as instructed. No new
  BTC15m cohort is proposed.

## 7. Where this leaves the programme

Two independent results now point the same way:

1. **No reachable venue supports the mechanism.** Everything measurable on
   Gemini is 10x to 1000x short of L5's density, and PMUS sports fails on
   oscillation rather than depth.
2. **The mechanism did not pay for L5 either**, over 2,216 windows and 22
   days: −$1,051 in total, with his densest lane carrying the loss.

The honest reading is that "replicate L5's habitat" is no longer a
well-posed objective, because the corpus does not show the habitat paying.
The questions that remain answerable are narrower and worth stating plainly:

- Was there a **period** (rather than a lane) where L5 was profitable — the
  $480/day target implies one — and what was different about it? The corpus
  spans 2026-03-19 to 08-31; a date-sliced version of the table in section 0
  is cheap and is the natural next measurement.
- Does the **maker-rebate line** change any of this? That is the 21:15Z
  reconciliation, still pending and still separate. Section 4 says COHORT-2
  needed a 3x improvement in the matched:residual ratio; a rebate would have
  to be very large to substitute for that, and the published schedule caps at
  5% of notional.

**Rebate reconciliation remains separate and unaffected**
(`trig_01YUSyE8mwwzdHAmKb61oKZH`, 21:15Z). Actual credited economics get
added when they arrive, and if they materially change BTC15m expectancy this
selection is revisited with the real numbers.

---

## 8. Addendum — the date slice, and where the $480/day could have come from

Run because section 7 named it as the cheap next measurement. Same validated
columns, sliced by day:

```
        day  wins  trades   matched$   residual$      net$   r/m cost
 2026-03-19     3       5          0          -0         0       0.0%
 ... (18 sparse March-April days, 3-28 windows each) ...
 2026-04-20    25    1416        345        -139       206       5.1%
 2026-08-28   401   12325      3,382      -5,045    -1,662      17.2%
 2026-08-29   567   20026      5,471      -4,917       554      15.0%
 2026-08-30   558   19460      4,075      -6,317    -2,242      16.9%
 2026-08-31   474   14904      4,959      -3,061     1,898      14.7%

22 days, 16 profitable (73%).  CUMULATIVE -1,050.36
days at or above the $480/day target: 2
```

**The corpus is bimodal and the money is entirely in the last four days.**
March-April is 18 sparse days of 3-28 windows; 2026-08-28 to 08-31 is four
days of 400-567 windows and 12k-20k trades each. Those four days run
−1,662 / +554 / −2,242 / +1,898 = **−$1,452 net**.

Two observations that matter more than the total:

- **73% of days are profitable and the cumulative is negative.** Many small
  wins, a few large losses — the signature of a short-volatility position,
  which is exactly what an uncompleted directional residual is. A
  day-win-rate is therefore a misleading way to evaluate this strategy, and
  any future gate should read the loss tail, not the hit rate.
- **Even in his busiest period the residual/matched cost ratio is 14.7-17.2%**,
  against the ~6.4% his own returns need (section 4). He is carrying two to
  three times more residual than his matched economics can pay for, on his
  highest-activity days. **The $480/day target is visible on exactly 2 of 22
  days and is not the central tendency of this corpus.**

*Coverage caveat:* the March-April days average 12 windows against August's
~500. That is far more likely to be incomplete harvesting than a real change
in his activity, so the March-April rows should not be read as evidence about
his behaviour, only the August ones. That also means the corpus supports
conclusions about **four days**, not twenty-two, and every figure in section 0
is dominated by them.
