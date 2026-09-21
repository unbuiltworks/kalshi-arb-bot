# INADEQUATE COHORT — preserved as evidence, 2026-09-20

**STATUS: INADEQUATE — INFRASTRUCTURE / DATA CONTINUITY. This is NOT a selector
PASS/FAIL.** The frozen habitat selector was never given a valid prospective cohort
to be judged on. Nothing here may be quoted as evidence for or against it.

## What happened

The cohort opened 2026-09-20T19:52:46Z against the **local scratchpad tape**. The
research container recycled **eleven times** that day. Coverage segments since the
freeze were 18.3 min, 31.1 min, 2.8 min and 4.6 min against the **40 minutes**
(600 s lookback + 1,800 s forward) a single observation requires. No segment ever
reached it.

## The defect this exposed

The extractor walked a time grid over whatever rows were in the file. The tape is
**append-mode across collector restarts**, so a window spanning an outage was built
from the data on either side with the missing minutes invisible — two fragments with
a hole, presented as one continuous observation.

At the first prospective read, **28 of 33** rows bridged an outage, one across
**18.3 minutes** of missing book. Scoring them would have produced a confident
separation manufactured from our own downtime.

Fixed by `habitat_selector.continuous()`, which requires unbroken coverage at both
ends of `[t − LOOKBACK_S, t + HORIZON_S]` with no observation gap above
`MAX_OBS_GAP_S = 120 s` (sweep cadence is ~8 s). Five regression tests.

## Contents

| file | what it is |
|---|---|
| `habitat_rows_as_extracted.json` | the 260 historical + 33 prospective rows **as originally extracted**, bridged rows included, preserved unaltered |
| `split.json` | the chronological cut and the straddling markets excluded from the frozen run |
| `AUDIT.txt` | the bridged-window audit and the frozen selector's before/after metrics |

The frozen artefact `f9dcd886549a28656c315cd2249075f6aae7c6fcf48bd77e5a2fa0abfc861959`
is untouched: not refitted, cutoff not moved, no feature added or dropped.
