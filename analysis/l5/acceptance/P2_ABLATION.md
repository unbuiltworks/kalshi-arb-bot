# P2 — counterfactual ablation

**Control reproduces L5 exactly**: 60,132 / 60,132 events, spend and quantity
relative error < 1e-9. Real fills only; every ablation is a strict
subsequence and no event is ever substituted. State, class and pair deltas are
**recomputed from the retained sequence** at every step — an event that was a
residual extension in L5's path becomes a match expansion once an earlier event
is dropped, and the tests pin that.

527 windows; 13 have no establishable winner and are excluded from settlement
economics rather than guessed.

## L5's own BTC15m trading economics are ≈ zero

| | |
|---|---|
| spend (514 settled windows) | \$532,821.71 |
| settlement cashflow | \$532,670.84 |
| **total economics** | **−\$150.87** |
| matched economics | **+\$19,132.21** |
| residual economics | **−\$19,352.34** |
| return per dollar | **−0.000283** |

The 0.9624 pair cost is real and worth **+\$19.1k**. The residual inventory
that produces it destroys **−\$19.4k**. They cancel.

**Whatever the \$480/day is, it is not this trading P&L.** It is not in the
matched leg, not in the residual, and not in their sum. The remaining
candidates are incentives — maker rebates and liquidity rewards, which this
public tape cannot attribute per window — or a cohort or era not in this data.

*(matched + residual = −\$220.14 against a total of −\$150.87: the \$69.27 gap
is the 21 one-sided windows, which have a total but no pair cost and so
contribute to neither leg.)*

## The panel

| ablation | events | spend | matched | pair | imbal | matchEc | residEc | **totalEc** | ret/$ |
|---|---|---|---|---|---|---|---|---|---|
| **CONTROL (L5)** | 60,132 | 100% | 100% | 0.9603 | .098 | 19,132 | −19,352 | **−151** | −0.0003 |
| *causal* REMOVE_WORSENING_RESIDUAL | 53,689 | 85% | 87% | **0.9462** | .106 | 25,013 | −25,251 | −169 | −0.0004 |
| *causal* REMOVE_IMPROVING_RESIDUAL | 45,711 | 86% | 85% | 1.0018 | .030 | −2,976 | 3,235 | **+328** | +0.0007 |
| *causal* **REMOVE_ALL_RESIDUAL** | **0** | 0% | 0% | — | — | 0 | 0 | 0 | — |
| *causal* REMOVE_WORSENING_MATCH_EXP | 43,991 | 53% | 37% | 0.7986 | .577 | 36,070 | −40,436 | −4,297 | −0.0152 |
| *causal* REMOVE_ALL_WORSENING | 32,109 | 23% | 18% | 0.6859 | .650 | 38,640 | −47,161 | −8,452 | −0.0704 |
| cap runs at 2 | 30,526 | 63% | 57% | 0.9945 | .094 | −747 | 3,698 | **+2,989** | +0.0089 |
| cap runs at 3 | 37,842 | 75% | 70% | 0.9883 | .083 | 1,117 | 1,473 | +2,639 | +0.0066 |
| cap runs at 5 | 46,568 | 87% | 84% | 0.9814 | .078 | 4,894 | −2,966 | +1,997 | +0.0043 |
| **cap runs at 8** | 53,018 | 94% | **93%** | 0.9744 | .082 | 10,230 | **−8,444** | **+1,855** | +0.0037 |
| cap runs at 13 | 57,333 | 98% | 98% | 0.9666 | .092 | 14,978 | −14,013 | +1,034 | +0.0020 |
| cap runs at 21 | 59,381 | 100% | 100% | 0.9627 | .096 | 17,824 | −17,543 | +350 | +0.0007 |
| stop at TTE 600 | 17,691 | 30% | 26% | 0.9913 | .175 | −198 | 793 | +759 | +0.0047 |
| stop at TTE 300 | 39,228 | 64% | 62% | 0.9757 | .101 | 5,867 | −4,853 | +1,079 | +0.0031 |
| stop at TTE 120 | 53,151 | 88% | 87% | 0.9689 | .096 | 12,724 | −11,847 | +957 | +0.0020 |
| stop at TTE 60 | 57,655 | 97% | 96% | 0.9637 | .097 | 15,717 | −15,243 | +544 | +0.0011 |

Static-oracle variants are in `ablation.json`; they move the same direction and
are kept separate because they score against a path the ablation destroys.
Hindsight progress stops are recorded and flagged **not executable** — a
window's eventual event count is unknowable mid-window.

## Three findings

**1. A pair-forming rule cannot bootstrap.** `REMOVE_ALL_RESIDUAL` under causal
replay retains **zero events across all 527 windows**. Requiring every fill to
expand a match refuses the *first* fill, because there is no opposite side yet
— so nothing ever accumulates. R/S/D was not mistuned; a strategy of that shape
is structurally incapable of taking its first position.

**2. Pair cost and P&L move in opposite directions.** Every ablation that
improves pair cost makes money worse, and the extremes are stark:
`REMOVE_ALL_WORSENING` reaches a pair cost of **0.686** — spectacular by the
metric the whole program has optimised — while losing **−\$8,452**, the worst
result in the panel. Meanwhile capping runs *degrades* pair cost to 0.994 and
*earns* +\$2,989.

**Chasing 0.96 has been chasing the wrong objective.**

**3. Nearly every restraint beats L5.** All six run caps and all four TTE stops
produce positive total economics against L5's −\$151. The accumulation that
drives pair cost to 0.96 is the same accumulation that builds the directional
exposure destroying the P&L.

## Against the P3 gate

The gate asks for a rule preserving a substantial share of matched quantity
*and* ~0.96 pair economics *and* bounded residual economics.

`cap runs at 8` is the only row that comes close: **93% of matched quantity,
pair 0.9744, residual economics −\$8,444 (57% less negative than L5), and
+\$1,855 total**. But this is an **in-sample choice over six cap values**, and
no chronological OOS test has been run. Per the freeze criterion it is a
candidate to *test*, not to build.

**TRAJECTORY_ACCUMULATOR_V0 is NOT created.** `NOT_DETERMINED` stands for the
latent side/quote-selection mechanism.

## Not done

The dependency test (worsening residual → subsequent same-side improvement,
bucketed) and the reference-price rule search are not started. Neither is P4.

R/S/D frozen. PMUS halted. No live money. No 0.96 entry gate anywhere.
