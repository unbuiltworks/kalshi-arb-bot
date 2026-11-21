# Why is BTC15m the exception?

`analysis/l5/btc15m/decompose.py` · 5,546 windows, 693,916 fills, 2026-03-19 →
08-31. Costs are `usdcSize` throughout (Q5); a missing REDEEM row on a
long-settled window is scored as the loss it is; fee-bearing / fee-free is the
label and nothing here is relabelled maker/taker.

## The short answer

**BTC15m is not profitable because its behaviour is better. It is profitable
because 84.6% of its fills are fee-free — and even then, only in the training
period.** Out of sample it is essentially breakeven, and its total is carried by
a handful of windows.

| | net | spend | bp/$ | fee | matched | residual |
|---|---|---|---|---|---|---|
| ALL | **+$16,436** | $6,379,594 | **+26** | $27,550 | **+$162,257** | **−$144,065** |
| TRAIN 03-19→08-10 | +$16,081 | $3,090,767 | **+52** | $22,307 | +$70,025 | −$52,188 |
| **TEST 08-10→08-31** | **+$355** | $3,288,826 | **+1** | $5,242 | +$92,232 | −$91,877 |

Half the money was spent out of sample and returned 1 bp.

## Q1 — would BTC15m survive the fee on every fill? **No, and not close.**

Exact observed trades, only the fee varied:

| | net | bp/$ | fee paid |
|---|---|---|---|
| as observed | +$17,177 | +27 | $27,362 |
| if every fill were fee-free | +$44,538 | +72 | $0 |
| **if every fill bore the fee** | **−$102,865** | **−161** | $147,403 |

The fee it does not pay ($120,041) is seven times its entire net.

## Q2 — would BTC5m be profitable fee-free? **Yes, and better than BTC15m.**

| cohort | as observed | if fee-free |
|---|---|---|
| BTC5m | −$1,915 (−19 bp) | **+$19,734 (+198 bp)** |
| ETH5m | +$811 (+63 bp) | +$3,540 (+282 bp) |
| ETH15m | −$248 (−51 bp) | +$696 (+145 bp) |
| BTC15m | +$17,177 (+27 bp) | +$44,538 (+72 bp) |

**This inverts the regime narrative.** Per dollar deployed, BTC5m's actual
trading is *stronger* fee-free (+198 bp) than BTC15m's (+72 bp). The cohorts are
not separated by skill, patience or horizon. They are separated by who pays the
fee.

## Q3 — inside BTC15m, do fee-free windows do better? **No.**

| fee-bearing share of window | n | net | bp/$ | equal-wt mean | median | win% |
|---|---|---|---|---|---|---|
| 0% (all fee-free) | 642 | +$1,284 | +41 | **−111 bp** | +20 | 51.2% |
| 0–5% | 1170 | **−$3,422** | **−16** | −2 | +10 | 50.5% |
| 5–15% | 1360 | +$7,993 | +41 | +46 | +75 | 54.6% |
| 15–30% | 975 | +$4,883 | +56 | +84 | +93 | 55.9% |
| **30–50%** | 1047 | **+$6,524** | **+75** | **+113** | +79 | 54.4% |
| 50–100% | 352 | −$825 | −30 | +42 | +76 | 53.1% |

The *most* fee-exposed bucket is the best, and OOS AUC of fee-bearing share
against a profitable window is **0.5386 — no separation**.

So the fee explains the difference *between* cohorts and explains nothing
*within* BTC15m. Fee share is confounded with period and size: the 0–5% bucket
is the August mega-volume cohort ($2.1M of spend) and August was the worst month
per dollar.

## Q4 — what distinguishes a fee-free fill? Not the things a strategy controls

Medians, fee-bearing vs fee-free, over 693,916 fills:

| variable | fee-bearing | fee-free | reads |
|---|---|---|---|
| **month** | 0% (Mar) → 39.7% (May) → **5.3% (Aug)** | | **largest factor by far** |
| **size** | 20.00 sh | **11.44 sh** | bearing fills are ~75% larger |
| **first fill of window** | **26.3%** bearing | 15.3% elsewhere | opening is far more often charged |
| **sequence position** | 26.3% → 22.5% → 19.0% → 17.6% → 17.4% → 17.0% → **14.6%** | | monotone decline |
| **price** | 9.8% at p<0.1 → **21.9%** at p 0.8–0.9 | | monotone rise |
| window fills/min | 9.61 | **12.58** | free fills sit in busier windows |
| window volume | $1,023 | **$1,494** | |
| TTE | 350 s | 420 s | bearing slightly later |
| **side** | Up 15.4% / Down 15.3% | | **no effect** |
| **switch vs continue** | 15.8% / 15.2% | | **no effect** |
| **matched fraction** | 0.889 | 0.908 | no meaningful effect |
| **relative residual** | −0.004 | −0.009 | no effect |

Larger, earlier, higher-priced fills in thinner windows are charged; smaller,
later fills in busy windows are not. **That pattern is what liquidity-taking
would look like — and the tape does not label execution mode, so it is not
established and is not called maker/taker here.** One circumstantial parallel,
recorded and not leaned on: Polymarket US publishes a *taker* fee of the same
functional form, `0.06·C·p·(1−p)`, against the `0.07·p·(1−p)` measured here.
Different venue, same shape.

The two variables a strategy would most want to steer on — **side and
switch-vs-continue — have no relationship to fee status at all.**

## Q6 — broad or concentrated? **Severely concentrated, and worse OOS.**

| | ALL (5,546 windows) | TEST (1,664) |
|---|---|---|
| total net | $16,436 | $355 |
| top 1% of windows | $22,942 = **139.6%** of net | $8,709 = **2,455%** |
| top 5% | $64,114 = 390.1% | $24,780 = 6,986% |
| top 10% | $94,327 = **573.9%** | $36,480 = **10,285%** |
| winners / losers | +$179,253 / −$162,817 | +$70,567 / −$70,212 |
| win rate | 53.5% | 51.0% |
| worst chronological drawdown | **−$8,651.73** | |

Equal-weighted per window: mean +37 bp, median +55 bp, p05 −1,545, p25 −430,
p75 +511, p95 +1,518 (OOS: mean +17, median +14, p05 −1,012, p95 +1,064).

So the typical window is mildly positive and the *aggregate* is a coin-flip
between two nearly equal piles of $170k. Remove the best decile and BTC15m is
deeply negative. A strategy sized on the headline +$16,436 would be sized on a
number that 10% of windows produced.

### Monthly, chronological

| month | n | net | spend | bp/$ | mean bp | win% | fee | fee-bearing |
|---|---|---|---|---|---|---|---|---|
| 2026-03 | 86 | −$120 | $7,787 | −154 | −31 | 48.8% | $0 | 0.0% |
| 2026-04 | 548 | +$3,045 | $312,978 | +97 | −32 | 51.5% | $826 | 11.0% |
| 2026-05 | 919 | +$1,569 | $712,327 | +22 | +48 | 53.4% | $8,491 | 39.7% |
| 2026-06 | 667 | +$3,723 | $579,323 | +64 | −3 | 49.6% | $5,784 | 34.1% |
| 2026-07 | 1171 | +$6,603 | $1,002,255 | +66 | +122 | 60.4% | $5,952 | 19.6% |
| **2026-08** | **2155** | **+$1,616** | **$3,764,923** | **+4** | +18 | 51.6% | $6,498 | 5.3% |

August is 59% of all spend and returned 4 bp.

## Q7 — the subset with expectancy that needs no directional skill

`matched_pnl = matched × (1 − effective_pair_cost)`. It needs no forecast: it is
positive exactly when the attained effective pair is below 1.

**TEST (OOS), by effective pair band:**

| band | n | % | matched | residual | net | net bp/$ | **matched bp/$** |
|---|---|---|---|---|---|---|---|
| ≤.90 | 90 | 5.4% | +$16,306 | −$12,165 | +$4,141 | +302 | **+1,190** |
| ≤.96 | 553 | 33.2% | +$61,260 | −$43,516 | +$17,743 | +174 | **+600** |
| .96–1.00 | 676 | 40.6% | +$28,755 | −$28,660 | +$95 | +1 | +197 |
| >1.00 | 343 | 20.6% | −$14,090 | −$7,857 | −$21,946 | −328 | −211 |

**The residual leg is negative in every single band, out of sample, without
exception.** It is not a contributor that sometimes helps; it is a consistent
drag that gives back most of what the matched leg earns — $144,065 of $162,257
across the full sample. Every dollar of BTC15m profit comes from the matched leg
and survives only to the extent the residual is kept small.

**73.1%** of BTC15m windows attain an effective pair below 1.00 (median 0.9725).
That is the structural fact behind the cohort: L5 reliably gets sub-par pairs
there, and the pair-cost gate is the same monotone, cohort-universal signal
found in the fee work.

### Is it steerable in advance? Only partly, and the best signal is not ex-ante

OOS AUC against "window was profitable":

| variable | AUC | |
|---|---|---|
| final matched fraction | **0.6848** | strongest — but known only at the window's end |
| price path range | 0.3940 (0.606 inverted) | quieter windows pay better |
| window volume | 0.4418 (0.558 inv.) | smaller windows pay better |
| fills in window | 0.4499 (0.550 inv.) | |
| switches | 0.4788 | no separation |
| first-fill TTE | 0.4772 | no separation |
| fills/min | 0.4549 | no separation |
| fee-bearing share | 0.5386 | no separation |

**The honest limit: matched fraction and price-path range are diagnostics, not
entry rules.** Neither is knowable when the first leg is placed. What *is*
available at decision time is the attainable pair itself, and that is the one
thing the bands above show to be monotone and OOS-stable.

The answer to the question as posed: **yes, a subset with positive fee-corrected
expectancy and no reliance on directional skill exists — it is "windows where an
effective pair ≤ .96 is actually attained", 33.2% of them OOS at +600 matched
bp/$ — but the identified subset is defined by an outcome we can price at
decision time, not by a state we can screen on in advance.**

## What is NOT available, and is named rather than imputed

L5's incentive credits cannot be attributed per window: the venue publishes no
per-window reward row for another trader's account. Spread, depth, queue
position and the intra-window book are absent from the public tape. All are
reported as unavailable, never as zero.

## Implication for deployment, stated as a finding and not a proposal

The corrected picture removes the reason to prefer BTC15m on behavioural
grounds. Fee-free, BTC5m trades better per dollar. What BTC15m has is fee-free
execution 84.6% of the time, and the conditions that produce it (size, sequence
position, price level, period) are not conditions a policy can simply choose.

No production change is proposed. The probe remains stopped, the −$30 daily
stop is untouched, no risk limit is changed and no executor is started.
