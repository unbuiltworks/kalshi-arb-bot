# Canonical fee reconstruction — P0

`analysis/l5/fees/reconstruct.py` · 761,022 source-supported fills.

## The relationship, tested rather than asserted

    observed fee = usdcSize − price × size          (BUY; sign-flipped on SELL)
    proposed     = 0.07 × p × (1 − p) × shares

| | |
|---|---|
| fills tested | 761,022 |
| fee-bearing | 173,668 (22.8%) |
| fee-free | 587,354 — max abs fee/share **5e-11**, i.e. exactly zero |
| model error on fee-bearing, p50 | **6.9e-17** (exact) |
| exact within 1e-6 | 152,607 / 173,668 = **87.9%** |
| p95 / max abs error per share | 3.08e-4 / 7.68e-4 |
| total reconstructed fees | **$53,537.13** |
| total \|model − observed\| | $101.74 = **19 bp of fees** |

The model is **validated**, and the economics below use the **observed wedge**,
not the model — so the 19 bp of model error touches nothing downstream.

**Terminology is fee-bearing / fee-free.** That is what the tape shows. It is
not maker/taker, nothing is relabelled, and `L5_OPERATING_MODEL.md` §4.4 stands.

## By cohort

| cohort | fills | fee-bearing | gross acquisition | reconstructed fees | fee bp | redemption | trading net | incentives |
|---|---|---|---|---|---|---|---|---|
| BTC15m | 693,916 | **15.4%** | $6,352,044 | $27,550 | 43 | $6,396,030 | **+$16,436** | separate |
| BTC5m | 51,808 | **100%** | $1,023,096 | $22,187 | 217 | $1,043,581 | **−$1,700** | separate |
| ETH5m | 11,008 | **100%** | $130,687 | $2,843 | 218 | $134,754 | **+$1,225** | separate |
| ETH15m | 4,290 | **100%** | $48,713 | $957 | 196 | $49,417 | **−$253** | separate |

## By month

| month | fills | fee-bearing | gross acquisition | fees | fee bp | redemption | trading net |
|---|---|---|---|---|---|---|---|
| 2026-03 | 1,931 | 0.0% | $7,789 | $0 | 0 | $5,958 | −$1,829 |
| 2026-04 | 34,224 | 11.0% | $312,152 | $826 | 26 | $317,732 | +$4,754 |
| 2026-05 | 89,122 | 39.7% | $703,837 | $8,491 | 121 | $713,897 | +$1,569 |
| 2026-06 | 77,255 | 40.1% | $665,374 | $7,915 | 119 | $677,727 | +$4,438 |
| 2026-07 | 134,015 | 28.9% | $1,275,648 | $11,904 | 93 | $1,294,865 | +$7,312 |
| 2026-08 | 424,475 | 15.3% | $4,589,739 | $24,401 | 53 | $4,613,603 | **−$537** |

Incentives are kept out of every cell above and are never netted in.

**Trading-only across all four cohorts: ≈ +$15,708 over 5.5 months ≈ $95/day.**
The $480/day target is therefore not a trading-P&L target; on this evidence it
is predominantly an incentive number, consistent with the earlier finding that
incentives were 82% of economics. August — L5's largest month at $4.59M gross —
was **trading-net negative**.

## What the fee does to a "cheap pair"

A pair pays the fee on both legs, and `p(1−p)` peaks at 0.5, so balanced legs
are the worst case:

| quoted pair | fee/pair | **effective pair** | gross margin |
|---|---|---|---|
| 0.90 | 0.0347 | **0.9346** | +0.0653 |
| 0.94 | 0.0349 | **0.9749** | +0.0251 |
| 0.96 | 0.0349 | **0.9949** | **+0.0051** |
| 0.98 | 0.0350 | **1.0150** | −0.0150 |
| 1.00 | 0.0350 | **1.0350** | −0.0350 |

Skewed legs pay less: a quoted 0.96 as (0.10, 0.86) is an effective 0.9747.

**And it only applies where the fill is fee-bearing.** Expected fee per pair:

| cohort | fee-bearing | E[fee per pair] |
|---|---|---|
| BTC15m | 15.4% | **0.0036** |
| BTC5m / ETH5m / ETH15m | 100% | **0.0227 – 0.0234** |

This is the mechanism behind the regime split. The same quoted pair leaves
~2 cents of margin on BTC15m and ~0.3 cents on 5m.

## Pair-cost bands, EFFECTIVE, chronological OOS

Quoted and effective bands are different numbers and must never be compared.

| cohort | ≤.90 | ≤.96 | .96–1.00 | >1 |
|---|---|---|---|---|
| BTC5m | **+1845 bp** | +725 | −141 | **−1208** |
| ETH5m | **+2547 bp** | +257 | −447 | **−1754** |
| BTC15m | **+247 bp** | +171 | +3 | **−328** |
| ETH15m | **+2499 bp** | +342 | −1559 | **−1223** |

Monotone, same sign pattern, in **all four cohorts** out of sample. Unlike the
switch signal, the pair-cost gate is cohort-universal. Fees move ~5% of BTC5m
windows out of the profitable bands: `>1` grows from 40.9% quoted to 46.1%
effective, `≤.90` shrinks from 33.8% to 29.4%.

## Residual-cap frontier, fee-inclusive and redemption-corrected

10,521 cycles (BTC5m+ETH5m); cap selected on TRAIN, reported on TEST.

| cap | matched P&L | residual P&L | fee | capital | net | bp/$ | %done | ≤.96 eff |
|---|---|---|---|---|---|---|---|---|
| 2 | 0 | −9 | 1 | 34 | −9 | −2640 | 0.4% | 0.0% |
| 4 | −2 | −8 | 4 | 161 | −10 | −622 | 0.9% | 0.0% |
| **6 (live)** | **−11** | +30 | 28 | 1,219 | +19 | +155 | 3.2% | 0.4% |
| 8 | −2 | +54 | 40 | 1,662 | +52 | +315 | 4.1% | 0.6% |
| 10 (TRAIN-best) | −5 | +107 | 74 | 3,156 | +102 | +324 | 6.2% | 1.1% |
| 15 | −40 | +36 | 130 | 5,607 | −4 | −7 | 8.7% | 2.1% |
| 30 | −276 | +508 | 444 | 19,011 | +232 | +122 | 16.8% | 4.8% |
| none | +1,848 | −1,745 | 7,024 | 330,503 | +103 | +3 | 100% | 31.1% |

**The answer to the frontier question is negative, and it is not close.**
`matched P&L` is ≈0 or negative at **every bounded cap**. Whatever net appears
there is directional residual — the thing shown to have no skill and a −100%
tail. Only 3.2% of cycles complete at cap 6 and 0.4% reach an effective ≤.96.
No bounded region delivers positive OOS expectancy *from matched-pair
economics*, which is what was being asked.

## SUPERSEDED

Mechanical criterion: any claim that (a) costed fills at `price × size` rather
than `usdcSize`, or (b) conditioned on a REDEEM row.

| claim | status |
|---|---|
| `FRONTIER.md` "test bp peaks at cap 4 (810), cap 6 = 613; residual P&L turns negative above cap 8" | **SUPERSEDED** (redemption-conditioned). Replaced by the table above: TRAIN peaks at cap 10, TEST cap 6 = +155. |
| `frontier.py` docstring "FEES ARE NOT SEPARABLE from this tape … gross of any unobserved fee" | **WRONG AND CORRECTED IN PLACE.** Its costs summed `usdcSize` and were always fee-inclusive; the disclaimer was false and the numbers it disclaimed were already right. `pair_cost` there is an EFFECTIVE pair and must never be compared to the quoted live ceiling. |
| 5m motif economics | current — built fee-inclusive and redemption-corrected from the start. |
| BTC15m switch AUC 0.674 | unaffected — a ranking statistic, not a P&L claim, and BTC15m carries only 0.2% redemption bias. |

Quoted pair costs in `analysis/l5/first_side/cycle.py` (`pair_cost = wavg + px`)
are **quoted by construction** and remain correct for comparison against a
quoted gate. They are not effective pairs and are not P&L.

## Open, and deliberately not inferred

1. **Maker/taker.** The natural reading of fee-bearing vs fee-free, and it would
   corroborate the BTC15m market-maker finding from an independent signal. Not
   established; not asserted.
2. **Whether PMUS sports carries this fee at all.** The relationship is measured
   on Polymarket crypto up/down windows. Our own live PMUS maker fills are the
   only direct evidence and they show **no wedge**: 1.88 @ exactly 0.4600
   ($0.8648) and 1.81 @ exactly 0.4400 ($0.7964). n=2, exact, far too small to
   generalise — but it points away from assuming the live 1.00 ceiling sits on a
   fee-bearing venue. **This must be checked against venue-confirmed fills
   before any inference about live policy**, and no policy change is proposed
   here (owner directive F).

---

# E — redemption selection bias, quantified exactly

Redemption is complete whenever it pays (`redeem_qty / winning-side qty` =
1.000 at p05, p50 and p95, at $1.00/share), so a long-settled window with **no**
REDEEM row is one where the held side lost. Conditioning on a REDEEM row
therefore keeps only winners. The dataset is no longer conditioned on it:
absence after complete settlement coverage is scored as the loss it is, and only
windows inside the 24 h capture tail are excluded as genuinely indeterminate.

| cohort | windows | no-redeem | dropped | CONDITIONED net | CORRECT net | overstatement | ratio |
|---|---|---|---|---|---|---|---|
| BTC5m | 7,866 | 916 | 11.6% | +$25,637 | **−$1,889** | $27,526 | **sign flip** |
| ETH5m | 2,655 | 486 | 18.3% | +$7,042 | **+$809** | $6,233 | 8.71× |
| ETH15m | 563 | 54 | 9.6% | +$398 | **−$251** | $648 | **sign flip** |
| BTC15m | 5,469 | 13 | 0.2% | +$15,808 | **+$15,421** | $388 | 1.03× |

**Two of the four cohorts flipped sign.** BTC5m was overstated by $27,526 on a
true result of −$1,889.

**BTC15m is essentially immune** (0.2%, 1.03×) — a two-sided book always has a
winning leg to redeem, so a trader who is continuously two-sided leaves a REDEEM
row almost every window. That is why BTC15m conclusions survive this correction
and 5m conclusions did not: the bias lands precisely on one-sided windows, which
is exactly where the "does residual have skill" question is decided.

Within the 5m motifs the same correction moved SAME_SIDE_RUN from +3,194 to
**+723 bp/$** and SINGLE_FILL from +3,109 to **+779 bp/$** — and equal-weighted
both are significantly *negative* (t = −2.63, −3.11).
