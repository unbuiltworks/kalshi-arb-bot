# ACTIVATION — LIVE SHORT-HORIZON SPORTS cohort

OWNER AUTHORIZATION 2026-09-17 "LIVE SHORT-HORIZON SPORTS", reviewed through
commit 2366aa3b, EXECUTION-class delta authorized by name in
`src/pmus_event_progress.py` and `src/pmus_l5_continuous_session.py`.

## Lineage re-pin

`REVIEWED` advanced `6f382b37` → `2366aa3b`. **`FORBIDDEN_CLASSES` UNCHANGED**
and still `("EXECUTION","DECISION","SIZING","SUBMISSION")`. `PREVIOUS_REVIEWED`
left at the original `6adcc4c4` so the mechanism's own self-tests keep a real
delta to catch. Nothing relabelled, removed or bypassed.

```
ACCEPTANCE   : True          reviewed: 2366aa3b
copy_sources : 38   identical: 37
differing    : ['analysis/l5/authorization/']   (pinned, class DOCUMENTATION)
undisclosed  : []   forbidden_class: []   content_drift: []
```

The authorization directory's pin was refreshed `32284962…` → `7fa63fbe…`
with a new reason, which is the pin working rather than a rubber stamp.

## Pre-activation verification

| check | result |
|---|---|
| reviewed lineage green | **ACCEPTANCE True**, 41 lineage tests pass |
| no unintended execution diff | `git diff 2366aa3b HEAD -- src/` **empty** |
| fresh mandate loaded | `OWNER-2026-09-17-CP1-LIVE-SHORT-HORIZON-COHORT` |
| envelope | 1 / 10 / $5.00 / $15.00 / 3.00 / −$6.00 — **exact match** |
| expiry | 2026-09-17T10:46:53Z → **22:46:53Z** (12 h) |
| sizing | target **0.10**, max leg **0.125** |
| k | **0.00619** |
| freshness gate active | `AUTH_TRIM_MAX_AGE_S` present, gate wired |
| cohort `*` active | `PMUS_L5_COHORT=*` pinned in the IMAGE |
| zero starting exposure | see below |
| HALT | absent (armed) |

### Zero starting exposure

From the probe's own venue-reconciled records:

- `campaign_end` 02:28:12Z — settled 4, **pending 0**, realized −$0.1836,
  `realized_confirmed_by_venue: true`
- settlement 02:26:39Z — `beforeNet −0.4800 → afterNet 0.0000`,
  `position_net 0`
- final `session_end` 02:28:12Z — inventory `up 0 / dn 0 / matched 0 /
  residual 0 / spent 0`, submitted 0, orders `[]`

I hold no venue credentials and must not, so I cannot issue an independent
authenticated position query. The probe's settlement audit is the authoritative
source, and it is venue-confirmed rather than self-reported.

### Full regression

**5052 passed, 27 skipped, 1 xfailed, ZERO failures** (137.59s). The four
lineage failures disappeared for the reviewed reason -- the baseline now
contains the authorized delta -- and nothing else changed state.

## Cohort semantics in force

Fail-closed, unchanged from the reviewed layer except the league equality:
live-feed presence required, not ended, recognised progress token required;
unknown progress refused, feed unavailable refused; **no endDate inference, no
invented per-sport duration**. Cricket / esports / NPB / KBO are excluded
because their progress evidence is insufficient — never by name — and become
eligible automatically the day the existing rules can read their tokens.

Refusals are **L5_ADAPTED** execution selection, counted via `cohort_refused` /
`cohort_refusal_reasons`, and are **never scored as model WAIT**.

## Standing instructions for this run

Do not tune during the cohort. The previous tennis lifecycle stands at
REALIZED **−$0.1836**, entirely directional residual, and a single result is
not a reason to move the controller.
