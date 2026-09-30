# THE MAKER-COMPLETION LEAK: measured, then suppressed

**Label: SHADOW / HISTORICAL REPLAY. No live order. No capital added.**
θ=0.96, variant B, PROFIT_LOCK=$5, hold=60s, residual_cap_k=2 are read from the
frozen engine and never written. Nothing is fitted to the 49 out-of-sample
windows. R7 is unarmed and its mandate is not renewed.

Module: `src/l5_maker_leak.py`. Runner: `scripts/l5_maker_leak_report.py`.
Data: `analysis/l5/r7_maker_leak.json`.

## What was measured

Every acquisition that increases matched inventory, with the incremental
effective matched-pair cost computed from the cost basis actually available
**before** the action:

- **maker resting fill** → `fill_px + avg(opposite side)`. No maker fee at PMUS (maker volume earns a modelled rebate, it is not charged).
- **taker ECONOMIC_COMPLETE** → `avg(held side) + cost/n + fee/n`, the θ-gated `marginal_complete` walk, with its per-level fee.

## THE HEADLINE, and it is not the one the tail table implied

**Every pair completed above $1.00 is a maker fill.** At q10 the taker quantity
in the above-$1 buckets is **zero** on both cohorts. θ's gate works exactly as
designed on the path it governs — 1,440 of the 1,590 contracts matched at
≤$0.96 out of sample are taker completions. The leak is entirely in the
ungated path: `NativeEngine.on_book` step 4 rests one clip per side at OUR
touch with no basis test and no θ test, so a maker fill on the second side
completes a pair at whatever price the market has reached.

**And suppressing those fills makes the result WORSE, on both cohorts.** That
is the finding. The expensive completions are hedges: they cap a residual loss
that is larger than the pair overpay.

### Bucket distribution and attribution

**frozen25, clip q10** (n=25, baseline mean net 1.0565)

| pair-cost bucket | events | matched qty | cash deployed | P&L attributed | of which maker qty | taker qty |
|---|---|---|---|---|---|---|
| ≤0.96 | 77 | 826.8700 | $737.99 | **+88.880** | 130 | 696.8700 |
| 0.96-1.00 | 12 | 120.0000 | $118.71 | **+1.294** | 120.0000 | 0 |
| 1.00-1.05 | 12 | 120.0000 | $123.34 | **-3.337** | 120.0000 | 0 |
| &gt;1.05 | 21 | 210 | $266.49 | **-56.486** | 210 | 0 |

- above **$1.00**: -59.822 total = **-2.3929 / window**
- above **θ=0.96**: -58.528 total = **-2.3411 / window**
- later basis improvement on already-matched inventory (average-cost bookkeeping, not attributable to one acquisition): +26.470 total = +1.0588 / window

**frozen25, clip q30** (n=25, baseline mean net 4.1837)

| pair-cost bucket | events | matched qty | cash deployed | P&L attributed | of which maker qty | taker qty |
|---|---|---|---|---|---|---|
| ≤0.96 | 54 | 1667.8300 | $1489.85 | **+177.980** | 240 | 1427.8300 |
| 0.96-1.00 | 16 | 510.0000 | $502.81 | **+7.194** | 330.0000 | 180.0000 |
| 1.00-1.05 | 12 | 339.5200 | $349.03 | **-9.507** | 309.5200 | 30 |
| &gt;1.05 | 14 | 420 | $503.39 | **-83.390** | 420 | 0 |

- above **$1.00**: -92.897 total = **-3.7159 / window**
- above **θ=0.96**: -85.703 total = **-3.4281 / window**
- later basis improvement on already-matched inventory (average-cost bookkeeping, not attributable to one acquisition): +56.056 total = +2.2422 / window

**oos49, clip q10** (n=49, baseline mean net -0.0676)

| pair-cost bucket | events | matched qty | cash deployed | P&L attributed | of which maker qty | taker qty |
|---|---|---|---|---|---|---|
| ≤0.96 | 152 | 1590.0000 | $1440.30 | **+149.695** | 150 | 1440.0000 |
| 0.96-1.00 | 18 | 180.0000 | $176.96 | **+3.042** | 170 | 10.0000 |
| 1.00-1.05 | 9 | 90.0000 | $91.98 | **-1.983** | 90.0000 | 0 |
| &gt;1.05 | 59 | 589.9200 | $721.58 | **-131.663** | 589.9200 | 0 |

- above **$1.00**: -133.646 total = **-2.7275 / window**
- above **θ=0.96**: -130.604 total = **-2.6654 / window**
- later basis improvement on already-matched inventory (average-cost bookkeeping, not attributable to one acquisition): +59.427 total = +1.2128 / window

**oos49, clip q30** (n=49, baseline mean net -1.4585)

| pair-cost bucket | events | matched qty | cash deployed | P&L attributed | of which maker qty | taker qty |
|---|---|---|---|---|---|---|
| ≤0.96 | 106 | 3330.0000 | $3039.29 | **+290.710** | 330 | 3000.0000 |
| 0.96-1.00 | 18 | 630.0000 | $619.91 | **+10.094** | 330 | 300.0000 |
| 1.00-1.05 | 8 | 240 | $244.62 | **-4.616** | 150 | 90 |
| &gt;1.05 | 45 | 1329.9200 | $1643.32 | **-313.399** | 1329.9200 | 0 |

- above **$1.00**: -318.014 total = **-6.4901 / window**
- above **θ=0.96**: -307.920 total = **-6.2841 / window**
- later basis improvement on already-matched inventory (average-cost bookkeeping, not attributable to one acquisition): +130.698 total = +2.6673 / window

## The leak-suppressed counterfactual

Matched-increasing **maker** fills above the ceiling are refused by the venue.
Nothing else changes: the taker path is untouched (it is already θ-gated, and
changing it would confound the attribution), the engine quotes exactly as it
would have, the walk is sequential, and **a suppressed fill is never deferred,
re-priced, or assumed to fill later**. Suppression is decided from the
pre-action basis only — no future price is consulted.

| cohort | clip | ceiling | mean net | median | losing | ≥$5 | matched-pair | residual | fees | qty suppressed |
|---|---|---|---|---|---|---|---|---|---|---|
| frozen25 | q10 | *baseline* | **+1.057** | — | — | — | — | — | — | — |
| frozen25 | q10 | $1.00 | **+1.034** | +1.560 | 10 | 6 | +3.688 | −2.328 | 0.326 | 530 |
| frozen25 | q10 | θ=0.96 | **+0.593** | +0.654 | 12 | 6 | +3.353 | −2.453 | 0.307 | 750 |
| frozen25 | q30 | *baseline* | **+4.184** | — | — | — | — | — | — | — |
| frozen25 | q30 | $1.00 | **+2.389** | +5.385 | 7 | 16 | +6.920 | −3.892 | 0.639 | 1,260 |
| frozen25 | q30 | θ=0.96 | **+2.526** | +5.385 | 7 | 17 | +6.616 | −3.480 | 0.610 | 1,650 |
| oos49 | q10 | *baseline* | **−0.068** | +1.091 | 19 | 9 | +1.602 | −1.346 | 0.324 | — |
| oos49 | q10 | $1.00 | **−0.412** | +1.425 | 19 | 10 | +3.303 | −3.401 | 0.314 | 1,069.92 |
| oos49 | q10 | θ=0.96 | **−0.682** | +0.718 | 20 | 10 | +3.277 | −3.626 | 0.333 | 1,300 |
| oos49 | q30 | *baseline* | **−1.458** | +5.126 | 15 | 28 | +2.316 | −2.999 | 0.776 | — |
| oos49 | q30 | $1.00 | **−2.528** | +5.222 | 15 | 30 | +6.023 | −7.841 | 0.710 | 2,259.92 |
| oos49 | q30 | θ=0.96 | **−3.332** | +5.558 | 15 | 31 | +5.828 | −8.414 | 0.746 | 2,730 |

### The trade the numbers describe

OOS-49 at q10, suppressing above $1.00:

| component | baseline | suppressed | change |
|---|---|---|---|
| matched-pair P&L | +1.602 | +3.303 | **+1.701** |
| residual P&L | −1.346 | −3.401 | **−2.055** |
| fees | 0.324 | 0.314 | +0.010 |
| **net** | **−0.068** | **−0.412** | **−0.344** |

Suppression recovers **exactly the leak it removes** (+$1.70/window of
matched-pair P&L) and gives back **more than that** in residual (−$2.06/window).
Same shape at q30: matched +$3.71, residual −$4.84, net −$1.07.

The arithmetic is plain once stated. `0600z` holds DOWN at basis 0.37 and buys
UP at 0.97, completing a pair at 1.34 — a loss of $0.34 per pair. Refusing that
fill leaves the DOWN inventory unhedged into a settlement that UP won, losing
$0.37 per contract. **Paying $1.34 for a pair is the cheaper of the two bad
options available at that moment.**

### Residual loss remaining after suppression

This is the part that matters for where a fix belongs. After suppressing every
above-$1 maker completion, residual loss is **larger**, not smaller:

| cohort / clip | residual P&L baseline | after $1.00 suppression | after θ suppression |
|---|---|---|---|
| oos49 q10 | −1.346 | **−3.401** | −3.626 |
| oos49 q30 | −2.999 | **−7.841** | −8.414 |
| frozen25 q10 | (−2.3 range) | −2.328 | −2.453 |
| frozen25 q30 | — | −3.892 | −3.480 |

Suppression does not remove the loss; it **relocates** it from
"matched above $1" to "adverse residual", and residual is the more expensive
place to keep it.

## What this licenses, and what it does not

- **It does not license a completion-price ceiling.** Measured on both cohorts, at two clips, a ceiling at $1.00 or at θ makes the mean worse every time. That is eight of eight against.
- It does **not** license advancing q25/q30, and nothing here changes θ.
- **The defect is upstream of completion.** By the time the engine is choosing whether to pay $1.34 for a pair, both options are already losing. What produced that state is the *first leg*: the engine opens and extends at the touch in states where the market then trends away from its inventory, with no notion of whether the state it is quoting into is one where a second leg will still be cheap. The candidate interventions are therefore at OPEN/EXTEND — state admission, an inventory-aware requote that does not chase the touch on the side it is already short of, or a trend/adverse-selection guard — not a price gate at the end.
- One caveat, stated because it cuts against the conclusion: suppression *improves* the median at q10 OOS (+1.091 → +1.425) and at q30 (+5.126 → +5.222), and lifts the ≥$5 count. It hurts the mean because it hurts the tail hardest. If a later mandate is ever written against median rather than mean, this diagnosis would have to be re-read, and that would be a change of objective, not a finding.

