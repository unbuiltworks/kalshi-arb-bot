# L5 habitat match: what his cycles require, and which venue supplies it

Read-only. No gate change, no Gemini order, no PMUS risk change. The question
is deliberately **successful L5 habitat → candidate habitat**, never "how do
we make Gemini fit".

## 1. What a successful L5 cycle actually demands

Derived from his own realised cycles (clean redemption identity only), so
these are demands **on the venue**, not aspirations.

| cohort (profitable only) | fills | switches | span | fills/min | inter-fill p50 | notional | net bp |
|---|---|---|---|---|---|---|---|
| BTC5m | 7 | 2 | 166 s | 2.9 | 11 s | $139 | +2026 |
| ETH5m | 5 | 1 | 141 s | 2.4 | 18 s | $53 | +2460 |
| BTC15m | 109 | 29 | 775 s | 8.9 | 2 s | $924 | +552 |
| ETH15m | 7 | 2 | 532 s | 1.0 | 27 s | $84 | +1730 |

**The binding requirement is 2.4–2.9 executable fills per minute on 5m, and
8.9 on BTC15m.** Everything else (spread, depth, volatility) only matters
insofar as it produces those fills.

### A correction this analysis forced

Earlier in the session I recorded "DEARER-first is coupled to a long
inventory-working sequence". That is true **only** inside the expensive
first-switch (0.97–1.00), ≥20-switch subpopulation, which is a **BTC15m**
phenomenon. In the 5m cohort DEARER-first has p50 **2** switches and its
profitability is set by pair cost, not length:

| DEARER-first 5m, net bp | sw ≤4 | sw ≥10 |
|---|---|---|
| pair ≤0.96 | **+1289 (n=1852)** | +276 (n=23) |
| 0.96–1.00 | −270 | −287 |
| >1.00 | −1483 | −315 |

Profitable DEARER-first cycles have first-switch pair cost p50 **0.9000**;
losing ones **1.1300**. Long sequences do not rescue an expensive 5m open.
This **reinforces** `≤0.96` paired with a short action budget — the existing
R5FT configuration — rather than arguing for relaxation.

## 2. Candidate habitats, from data we already had

No new collector was built. Sources: the frozen Gemini collector's own
digests, `data/transfer_backtest/`, and `analysis/l5/cross_shadow_live/`.

| habitat | prints/min | two-sided | spread p50 | depth p50 | complement fills | verdict |
|---|---|---|---|---|---|---|
| **PMUS sports** | — (25 complement fills / 16.7 h) | **100%** (424/424) | **$0.0100** | 2,341 | **25** | **best available** |
| Gemini BTC5m | 0.59 (hist. 1.00) | 57.8% | $0.20–0.28 | 10–390 | **0** | 3–5× short |
| Gemini BTC15m | 0.55 | 76–90% | $0.06–0.09 | 1–500 | **0** | short |
| Gemini ETH5m | **0.00** (15/15 zero) | 18–64% | $0.29–0.32 | 241–394 | 0 | **dead** |
| Gemini ETH15m | **0.00** (5/5 zero) | 4–71% | $0.29–0.34 | 217–306 | 0 | **dead** |
| Polymarket Politics/Weather/Commodities/Economics | **0.0157 per series** | — | — | — | 0 | far too thin |

A venue with attractive quotes and no prints scores poorly by construction:
the score is anchored on fills/min, and every other column is a modifier.

**Correction to an earlier claim of mine:** I reported Gemini prints as
"p50 0 per window". That pooled BTC with ETH and the zeros are entirely
ETH's. BTC5m is p50 3 prints/window; ETH5m is 15/15 zero. The park decision
stands but the reason is "BTC 3–5× short of 2.8 fills/min, ETH has no flow",
not "no prints anywhere".

## 3. Polymarket vs Gemini terminal liquidity — apples to apples

This matters because normal resolution behaviour must not be scored as a
defect. It is not symmetric:

* **Gemini 5m**: 11/11 intact windows end in an **absorbing** one-sided
  episode, onset TTE p50 90.13 s, duration p50 90.00 s, never recovering.
  The last two-sided quote is extreme (bid ≥0.62 in 7/11, ≤0.04 in 4/11), so
  it is outcome resolution — genuine venue behaviour, not a collector fault.
* **PMUS**: **424 of 424** cohort observations served both sides, at a 1-cent
  median spread.

**Caveat, stated rather than buried:** PMUS observations are captured at
order-placement time and may select for moments when a quote existed, so
100% is an upper bound. Even discounted heavily, 25 complement fills against
0 is the decisive difference, and complement fill is the step the whole L5
sequence depends on.

Gemini **can** supply ≥290 s of two-sided state — `BTC15M 18:45` 297.15 s and
`BTC15M 19:00` 294.68 s — so the 15m tail is not the limitation. Flow is.

## 4. CHEAPER-first vs DEARER-first: the regime discriminator

Chronological split, train 11,932 / test 5,114. Surface is
`P(DEAR | cohort, |price−0.50| bin, TTE bin)`, cells shrunk toward the cohort
rate with k=25 fixed in advance. No settlement outcome is used as a feature.

TEST log loss: **SURFACE 0.6252** < COHORT_RATE 0.6521 < 50/50 0.6931.

**But accuracy is 0.6455 against an always-DEAR baseline of 0.6377** — just
+0.8 pp. The surface is genuinely better calibrated, and it is a weak
classifier. Reporting only the log loss would overstate it.

The reproducible structure is an inverted U on distance from 0.50
(train → test):

| \|price−0.50\| | train | test |
|---|---|---|
| <0.05 | 0.4564 | 0.5145 |
| 0.05–0.15 | 0.6360 | **0.7440** |
| 0.15–0.25 | 0.6756 | **0.7561** |
| 0.25–0.35 | 0.6725 | 0.6404 |
| ≥0.35 | 0.5421 | 0.4455 |

Balanced books are a coin flip and the extremes **collapse and invert** out of
sample. The DEAR preference is a mid-band phenomenon. By cohort, BTC15m shows
essentially no preference (0.5040 → 0.5570) while ETH15m is strong
(0.6848 → 0.7020).

**This does not license replacing CHEAPER with DEARER anywhere.** It says
where a regime *choice* is even predictable.

## 5. Recommendation for the next shadow target

**PMUS sports, continuing bounded production unchanged.** It is the only
habitat in our possession that has produced the complement fill, at 100%
two-sidedness and a 1-cent spread, and it is already instrumented end to end
(Ledger A–D, cohort, settlement audit).

Gemini stays **PARKED** on executable flow. Re-test only if BTC print density
recovers toward the historical 1.00/min and beyond 2.8/min; ETH is not a
candidate at any horizon on this evidence.

Not recommended: building new collectors for Polymarket Politics / Weather /
Commodities / Economics. At 0.0157 prints per series per minute they are
~180× short of the requirement, and that is already known from data in hand.
