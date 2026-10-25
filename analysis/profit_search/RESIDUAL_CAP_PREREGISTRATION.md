# Pre-registration: does a residual cap of 2 survive out of sample?

Committed BEFORE the evaluation runs. Nothing below is changed after seeing a
number; the result is written in a separate file.

## Where the hypothesis comes from, and why it needs this test

`analysis/l5/ADVERSE_SELECTION_MEASUREMENT_20261024.md` §4 stratifies 355
settled windows by residual at close:

| \|residual\| | n | net/window | residual component |
|---|---|---|---|
| 0 | 29 | +$0.1341 | 0.00 |
| 1–2 | 159 | **+$0.3248** | **+$33.58** |
| 3+ | 167 | **−$0.6454** | **−$115.77** |

and concludes "carrying residual is not the problem — accumulating it is",
noting that the losing stratum is exactly the windows reaching the deployed cap
of 3. It then says the inventory-aware alternative "is read directly off the
stratification below: stop at 2 residual units instead of 3."

**Reading a threshold off the stratification that motivated it is fitting to
the sample.** The 1–2 stratum is not a policy — it is an outcome. Windows end
at 1–2 residual units partly *because* nothing went wrong in them, so their
profitability may be a consequence of benign windows rather than a cause that a
cap could capture. A cap applied prospectively changes which windows exist.
That is the confound this test exists to break.

## Hypothesis

**H:** capping |residual| at 2 produces higher out-of-sample net per eligible
window than capping at 3 or higher, under the inventory controller, on
venue-native settlement labels.

## Pre-declared grid, fixed now

`max_resid_qty ∈ {1, 2, 3, 5, 10, 50}` — the full grid is reported, not the
best cell. `kappa` and every other parameter stay at their committed defaults.
`adverse_shift` is the measured 0.0827.

## Datasets, both venue-native labels only

1. **PMUS**, `analysis/profit_search/pmus_windows.json` — 19 windows settled
   from the venue's own terminal print after `MARKET_STATE_EXPIRED`.
2. **Kalshi**, `analysis/profit_search/windows_clean_labels.json` — 53 windows
   settled from the market's own terminal print. The 77 cross-venue
   `strike_vs_reference` labels are excluded.

Chronological split by window close, train 50% / OOS 50%. The probability tilt
is fitted on train only. No threshold is chosen on OOS.

## Decision rules, fixed now

The cap is adopted ONLY if all three hold:

1. **OOS net at cap 2 is positive** — not merely better than cap 3. A cap that
   loses less money is not an edge.
2. **It is positive on BOTH datasets**, or positive on one and not negative on
   the other.
3. **It is not an artefact of inactivity** — the cap-2 arm must trade in at
   least 25% of eligible OOS windows. A policy that achieves $0.00 by never
   trading is already called WAIT and does not need a cap.

If any fails: **report NEGATIVE and do not change the controller.** The
engine's existing `max_resid_qty` default stays.

## What would make this test uninformative, stated in advance

With `adverse_shift` now at the measured 0.0827 and no calibrated probability
source, the controller is expected to WAIT in most or all windows. If every arm
returns $0.00 with zero trades, the test says **nothing** about the residual
cap — it says the probability gate binds first. That outcome will be reported
as inconclusive rather than as support for either cap.
