# Sealed C-Series Read — 21:10 UTC 2026-08-31

Predeclared read of the frozen variants on future evidence only
(R0–R4 cutoff 15:45, S1/S2 16:10, C5/C10/C25 16:25 UTC). Evidence class:
SHADOW_MAKER_PRINT_PROXY_NOT_EXECUTABLE (print-proxy shadow fills —
optimistic by construction). Artifact:
analysis/gemini/rescue_frontier_future.json; sealed interim snapshots in
analysis/gemini/snapshots/.

## Decision question

Does any C variant repeatedly turn a first maker fill into a maker
completion (maker-flat) with capped residual and positive
settlement-scored economics?

## Verdict: NO on the mechanics. The C-ladder never even bound.

| | engaged | cycles | maker-flatten % | flat windows | locked-pair P&L | settlement P&L (all) | max 1-window loss | worst-case residual |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| C5 | 14 | 15 | **13.3%** | 1/14 | +$0.45 | +$7.15 | −$3.00 | $23.30 |
| C10 | 14 | 15 | **13.3%** | 1/14 | +$0.90 | +$14.20 | −$6.10 | $46.70 |
| C25 | 14 | 15 | **13.3%** | 1/14 | +$2.25 | +$26.52 | −$15.25 | $98.67 |
| S2 (control) | 14 | 15 | 13.3% | 1/14 | +$0.45 | +$7.05 | −$3.10 | $23.40 |
| R0 (rescue) | 18 | 26 | 7.7% (+38.5% by rescue) | 4/18 | +$0.95 | +$3.65 | −$3.10 | $22.30 |

Key facts:

1. **C5/C10/C25 produced IDENTICAL fills to the plain strike-zone S
   variants** (17 maker fills, 15 cycles, 2 maker-flattened). The
   aggressive completion ladder (0.98 → 0.99 → 1.00 rungs) never captured
   a single extra completion. On these books, in this sample, completion
   aggression within the pair ceiling bought nothing.
2. **13.3% maker-flatten; 1 of 14 windows ended flat.** A first maker fill
   almost never became a second maker fill within the window.
3. **The positive headline P&L is settlement luck, not mechanics.** Locked
   pair margin is $0.45–$2.25; the other ~94% is unhedged residual that
   happened to settle favorably (13 of 14 windows went to settlement
   one-sided). The linear scaling +7→+14→+27 with q is that same luck
   multiplied, and max single-window loss (−$3/−$6/−$15) and worst-case
   residual ($23/$47/$99) scale identically. This is the R0 "+$1.46"
   illusion shape, correctly exposed this time by the decomposition.
4. **Rescues still drag** (R0 nets +$2.83 post-waiver with 2.5 rescues per
   15 pairs), and depth-capped rescue coverage was full when used.
5. **Sample honesty:** 14–18 engaged windows, one afternoon, with a ~55min
   recorder outage (container recycle) inside the evidence period.
   Directional verdicts only. Print-proxy shadow fills are optimistic —
   real queue position would fill less, not more.

## What survives, what dies

- DIES: single-quote entry → aggressive maker completion as the core
  cycle. 13% completion means the "ideal q10 machine" (fill → complete
  → repeat) does not exist with one quote level per side on these books.
- DIES (again): rescues as a route to flat that preserves economics.
- SURVIVES (untested tonight, elevated by the L5 ledger): breadth — L5
  achieves 78–89% matched inventory with a dense two-sided multi-level
  ladder across many simultaneous windows; we achieved 13% with a single
  cycle in ~4 markets. The completion deficit looks structural
  (one rung, one cycle at a time), not a pricing-aggression problem —
  consistent with completion aggression having bought nothing.
- SURVIVES: the subsidy question is untouched — modeled rebates here were
  $0.23–$1.26/afternoon at this engagement level; only Sept 2's live
  credited stack matters.

## Predeclared next steps (fresh clocks, nothing armed)

1. Design the L5-style ladder shadow: multi-level two-sided quotes across
   ALL four assets × both horizons simultaneously, scored on matched %,
   residual %, and the frozen subsidy metric spec
   (docs/GEMINI_SUBSIDY_METRICS.md). New predeclared constants, new
   evidence clock — the C-series stays closed.
2. Sept 2: live verification of credited maker rebate + fee waiver + LR
   stack per dollar of two-sided turnover. Go/no-go inequality:
   reward + pair profit > residual loss + fees.
3. Kalshi evening canary proceeds tonight as planned (separate lane).
