# Gemini Politics / Weather / Commodities — frozen habitat score

Observation only, one 10-minute live session, 2026-09-14 ~16:20Z. Same frozen
`l5_habitat_score`, same L5 reference, **no category-specific tuning**. No
orders, no risk change, nothing deployed.

## Ranking

| cohort | n | raw | shrunk | spread (ticks) | depth | attainable pair | osc | 1–4 s | price chg/min | trades/min | band cov | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Weather** | 17 | 0.0278 | 0.0220 | 3.0 | 100 | **0.97** | 0 | 0.333 | **1.0014** | **0.0** | 0.827 | RANKABLE |
| Crypto 5m (baseline) | 26 | 0.0 | 0.0004 | 1.0 | 750 | 0.99 | 0 | 0.748 | 0.0 | 0.0 | 1.0 | RANKABLE |
| Commodities | 1 | 0.1267 | 0.0230 | 20.0 | 10 | **0.80** | 0 | 0.313 | 1.7491 | 0.0 | 0.828 | INSUFFICIENT_SAMPLE |
| **Politics** | — | — | — | — | — | — | — | — | — | — | — | **NO DATA (see below)** |
| **Economics** | — | — | — | — | — | — | — | — | — | — | — | **NO DATA (see below)** |

Cohort medians are dominated by dead windows; the **best individual markets**:

| market | category | score | obs | spread | pair | osc | chg/min | prints |
|---|---|---|---|---|---|---|---|---|
| GEMI-BTC05M…1610-UP | Crypto | **0.4287** | 182 | 25 | **0.75** | 12 | **21.63** | 2 |
| GEMI-BTC05M…1615-UP | Crypto | 0.4093 | 491 | 21 | **0.79** | 6 | 12.63 | 14 |
| GEMI-ETH05M…1610-UP | Crypto | 0.2905 | 146 | 31 | **0.69** | 0 | 10.46 | 0 |
| GEMI-WXHIGH-LA-…-81TO82 | Weather | 0.1293 | 567 | 4 | 0.96 | 0 | 6.20 | 0 |
| GEMI-XAU2609142100-HI4300 | Commodities | 0.1267 | 308 | 20 | **0.80** | 0 | 1.75 | 0 |

## Against canonical L5 (reference: 2.34 price changes/min p50, 10.77 p90)

* **Weather is conclusively below** — 1.00 chg/min against L5's 2.34, and the
  score's inference runs one way: below the reference is a conclusive habitat
  deficit. **Zero trades in 10 minutes across 26 markets.**
* **Commodities (n=1)** is also below (1.75). Interesting only for its
  **attainable pair of 0.80**, which clears the ≤0.96 spring target — but 21
  of its 22 markets **never quoted two-sided at all** (308 two-sided
  observations against 12,171 one-sided), so the cohort is effectively
  unmeasurable rather than good or bad.
* **Gemini crypto 5m remains the only cohort above the reference**, at
  12–22 chg/min on live windows, and the only one producing prints.

**No category materially approaches Gemini crypto 5m, so no behavioural
shadow was run on them** — the trigger the owner set was not met.

## Two-sided coverage, which is the real story

| category | markets | two-sided obs | one-sided obs | markets with ZERO two-sided | prints |
|---|---|---|---|---|---|
| Crypto | 26 | 13,403 | 1,343 | 0 | 16 |
| Weather | 26 | 9,641 | 5,102 | 9 | **0** |
| Commodities | 22 | **308** | **12,171** | **21** | **0** |

Weather quotes two-sided when it quotes at all but **never trades**.
Commodities barely quotes two-sided at all.

## Politics / Economics: NO DATA, and the cause is ours

26 Politics and 8 Economics contracts were subscribed and **zero frames were
attributed to them**. Diagnosed: Gemini's depth frames for these instruments
come back with an **empty `s` (symbol) field**, so the observer cannot map a
frame to a market and drops it. The data *is* flowing — a probe saw a live
two-sided book (bid 0.2900 × 250, ask 0.3000 × 795) on a Politics contract —
but it arrives unattributable.

**This is a gap in my observer, not a finding about Politics.** No Politics or
Economics habitat number is reported, and none should be inferred from their
absence. Fixing it needs a per-connection subscription (or another
attribution field) and is the first thing to do before these categories can
be scored.
