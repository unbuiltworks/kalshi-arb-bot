# Shadow S1 — first preregistered cohort (25 COMPLETE markets)

Candidate fingerprint `441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b6fe`,
unchanged from preregistration. Run `37409797204`, SHA `d849a0f5`,
2026-10-06 03:37Z → 05:52Z. Zero-capital shadow: **no order submitted, no
wallet, no signing, no capital at risk.** Nothing below is submitted,
filled, settled or realized.

## Measurement validity (established before any statistic)

| | |
|---|---|
| markets passing every gate | 32 |
| of those COMPLETE | 25 (the cohort) |
| infrastructure failures excluded | 2 |
| book polls / successful two-sided reads | 100% |
| book errors | 0 |
| discovery errors | 0 |

The 2 exclusions are `GATE5_ORDERS_NOT_INSTANTIATED_BOTH_SIDES`: books read
perfectly but **no bid existed on either outcome**, so S1 could not quote.
One of them the collector labelled COMPLETE, and the old aggregator would
have counted it as a market with 0 evidenced fills — understating fill
opportunity with a market where the policy never participated.

## Primary first gate: is there enough evidenced passive fill opportunity?

**PASS, decisively.**

| | |
|---|---|
| evidenced fills | **313** |
| per market, mean / median | **12.52 / 13** |
| markets with zero evidenced fills | **0 of 25** |
| median gap between fills | 2.6 s |
| uncertain fills (EXCLUDED from all economics) | 1,138 |
| uncertain : evidenced | 3.64 |

Inventory formation is L5-like in shape:

| | S1 cohort 1 | L5 benchmark |
|---|---|---|
| markets filling BOTH outcomes | **88.0%** (22/25) | 66.3% of ≥3-fill bursts (corpus), 71.6% (L2 overlap) |
| matched fraction, median | **0.667** | 0.703 |
| pair cost, median | **0.8491** (22/22 below 1.00) | 0.9679 |
| leg quantity | 23.61 by construction | 23.61 (median) |
| terminal residual, median / max | 94.44 / 188.88 shares | — |

Residual exceeding the 80-share cap is **expected, not a breach**: the cap
gates *placement*, so residual plateaus at three units (70.83 < 80, so a
further quote is allowed) plus one more fill. Quote suppression then holds
it. Two markets reached 188.88, which is worth understanding but is not a
parameter failure.

The both-outcome and pair-cost comparisons are **not apples-to-apples**:
ours are per-market over a whole window, L5's burst figure is per burst and
its pair cost comes from a different estimator. Treat them as shape
agreement, not matched quantities.

## Markouts — and why the headline number is mostly an artifact

Raw markout (mid at t+h minus our fill price, signed as a buyer) is
negative at every horizon: −0.029 / −0.034 / −0.041 / −0.047 / −0.028 at
+1/+5/+10/+30/+60s. **Reporting that as adverse selection would be wrong.**

Decomposing `markout = (mid_at_fill − price) + (mid_later − mid_at_fill)`:

| horizon | markout mean | adverse drift mean | drift median |
|---|---|---|---|
| +1s | −0.0295 | **−0.0027** | 0.00 |
| +5s | −0.0340 | −0.0071 | 0.00 |
| +10s | −0.0411 | −0.0141 | −0.01 |
| +30s | −0.0474 | −0.0200 | −0.03 |
| +60s | −0.0279 | −0.0001 | −0.02 |

Spread capture at the fill instant is **−0.0268 mean / −0.0150 median** —
we bought *above* the prevailing mid, the opposite of what a passive bid
should start with. That is built into the conservative fill standard:
`EVIDENCED_LEVEL_CONSUMED` fires precisely when the best bid falls **below**
our price, so a fill is only evidenced at an instant when price has already
moved down through us. The fill definition selects adverse instants by
construction.

So the honest statement is: **−0.027 of the −0.029 one-second markout is
mechanical**, and the genuine post-fill drift is −0.003 at +1s with a median
of exactly 0.00, growing to −0.014 by +10s. Comparing the raw −0.029 to
L5's −0.0041 fill toxicity would be comparing an artifact to a measurement.
A clean toxicity estimate needs an estimator that does not condition on the
adverse instant — a different candidate's question, not a reason to retune
S1.

## Capital

Gross notional of evidenced hypothetical fills: median $138.83/market,
max $265.85. **Not capital deployed, not P&L, not realized.**

## Verdict

**Fill opportunity: GO.** 12.5 evidenced passive fills per market with zero
empty markets makes L5-like inventory formation plainly plausible under
current Polymarket conditions, which was the question this cohort was
preregistered to answer.

**Not established:** that the inventory is profitable. The markout
estimator is confounded by the fill standard, the uncertain:evidenced ratio
of 3.64 means most ambiguous events were discarded, and residual control
has not been shown to be economic.

**Shadow success does not authorize live trading.** No parameter, fill
rule, cap, TTE rule or benchmark was changed after seeing these results,
and S1 is not being rescued or retuned. A separate explicit owner
authorization is required before any order is submitted.
