# Chained-D analyzer: loader-only correction, and what it revealed

## 1. The superseded fingerprint, preserved

| | |
|---|---|
| **superseded** | `b2f6d50a035b56c0edc4efea49d4ade8fc5eec292e2dada2e58e405a142f9bc6` |
| **corrected** | `0b746c20fba27dd1891c9dab7a8f922dbfc2ed1bb6721363db19c1545ef553bd` |

The original file is archived verbatim at
`frozen_archive/gemini_btc5m_chained_study_b2f6d50a.py`, and the diff at
`frozen_archive/chained_loader_only.diff`.

**It was superseded solely because of the `kind == "window"` loader defect.**
The venue sets `strike.value` AT `availableAt`; the recorder's metadata poll
re-reads the contract every cycle, emitting the first read as `window` and
every re-read as `window_poll`. The first read therefore carries
`strike: null` by construction. The original loader read only `window`, found
a strike for **2 of 18** tickers on a tape whose `window_poll` rows carry one
for 18, and ran on ~1-2 windows while reporting 0 trades across 32
candidates. That was a measurement failure, not a test.

## 2. Proof the change is loader-only

AST comparison of every top-level definition and constant:

* **exactly one definition differs: `windows`.**
* **constants changed: NONE.** `CHECKPOINTS`, `SIZE_LEVELS`, `D_BINS_BPS`,
  `ASK_BINS`, `TAKER_RATE`, `FEE_QUANTUM`, `MAX_REFERENCE_AGE_S`,
  `OOS_FRACTION`, `EXCLUDED_TICKERS`, `MIN_EV_PER_WINDOW_USD` all verified
  identical by value.
* **`CANDIDATES` identical**, 32 of them.
* `observations`, `cell_table`, `evaluate_candidate`, `capacity`,
  `incorporation`, `reference_at`, `fee_for`, `breakeven`, `_bin`, `wilson`
  all byte-identical.

Inside `windows()` the strike is the **first non-null venue reading**, with
`strike_type`, the row kind, the row's own `t_ns` and `availableAt` kept as
provenance and `strike_inferred: false` -- the corrected census rule. A
window with no authoritative strike **fails closed**. One further loader
correction was needed: the original assigned `t0_epoch = None` on every
window row, which with 9,200 poll rows would erase the `t0_epoch` a
checkpoint had already supplied; it is now `setdefault`, leaving the
checkpoint branch as the only authority on it.

## 3. Window agreement with the corrected census

Strikes found: **18** (was 2). Chaining validation: **16 of 16 agree to the
cent**. The census's 16 complete windows are **all present**. The analyzer
additionally admits `BTC05M2610051750`, which the census excluded on its
`>= 6 early checkpoints` gate -- that window has **zero** early checkpoints
and so contributes zero observations either way. The two gates differ by
design and neither was changed to make them match.

## 4. OOS: SEAL UNCHANGED, and a discrepancy named rather than papered over

`OOS_FRACTION = 0.40` untouched. The analyzer seals **the last 6 of its 17
settled windows** and did not read them. The frozen early-window analyzer
sealed **7 of 16**. The two numbers differ because the two analyzers have
different completeness gates and therefore different settled lists, and
harmonizing them would mean changing a split -- which this authorization
forbids. Discovery used **11** windows, **80** observations at size 1 (was
8).

## 5. All 32 preregistered candidates, discovery only, failures included

| candidate | trades | opp rate | EV/trade | EV/all-windows | max DD | NO_TRADE |
|---|---|---|---|---|---|---|
| c120_DOWN_ge5p0 | 1 | 0.0909 | -0.1300 | -0.0118 | -0.13 | 10 |
| **all other 31** | **0** | 0.0 | - | 0.0000 | 0 | 11 |

**The preregistered selection rule, applied unchanged** ("among candidates
whose DISCOVERY ev_per_all_windows exceeds $0.01 at size 1, carry the
highest opportunity_rate to OOS"): **0 candidates clear the bar. No candidate
is named. The OOS partition stays sealed.**

## 6. Why 31 of 32 fired nothing -- and it is NOT the hypothesis failing

All 80 discovery observations pass **both** structural gates: economic
two-sidedness at q1 **80/80**, book integrity OK **80/80**. The binding
constraint is the D threshold, and the reason is scale:

| `\|D_bps\|` | value |
|---|---|
| min | 0.00 |
| **median** | **1.48** |
| p90 | 5.53 |
| **max** | **7.00** |
| observations >= 5 bps | **8 of 80** |
| observations >= 25 bps | **0 of 80** |

The candidate grid was predeclared at **+/-5 and +/-25 bps**. At an 85,000
strike those are **$42.50 and $212.50** -- and `reference - strike` inside a
five-minute BTC window has a median magnitude of 1.48 bps (~$12.60) and never
once reached 25 bps. **The preregistered thresholds do not intersect the
distribution the data actually occupies.** Only 8 of 80 observations are
eligible for the loosest variant at all, and only one of those falls on a
candidate checkpoint.

So the correct reading is: the candidates are **untested at the scale the
data occupies**, not refuted. The selection rule naming nothing is the rule
working, not a verdict on whether D carries information.

**I did not change the thresholds.** They are preregistered and this
authorization is loader-only. Choosing a grid that matches the measured D
distribution would be a new preregistration and is the owner's call.

## Scope

No candidate rule, checkpoint, D definition, bin, threshold, fee model,
capacity calculation, selection criterion, discovery/OOS split or OOS seal
was changed. The frozen early-window analyzer `f2800e3e...960e90b` was not
touched, and its +UP result is **not** treated as validation: 7 of 9 UP
settlements across nine non-independent discovery windows is a base-rate
observation. No new strategy implementation. No live authorization.
