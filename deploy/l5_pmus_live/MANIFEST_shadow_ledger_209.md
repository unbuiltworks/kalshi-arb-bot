# Deployment manifest — shadow-ledger persistence, disarmed (#209)

**This document is not an authorization.** It is what a disarmed rollout should be
judged by, written so that someone else can run it and check it. No Railway variable,
risk limit or budget changes. Nothing is armed. Every number the ledger holds is
**modelled**.

---

## 1. Source

| | |
|---|---|
| branch | `fix/l5-shadow-ledger-retention-209` |
| code SHA | `6116675ed` — the last commit that changes code or tests |
| base | `a2f6537517bdbd04e991015a86fa4c727dfe3dff` (`a2f653751`), **what production runs now** |
| review PR | #210, against the inert branch `base/l5-shadow-ledger-209` at that same base |
| issue | #209 item 1 |

The branch is rebased directly onto the deployed SHA, so it carries **no** part of the
#208 controller stack. The manifest commit that adds this file changes no code; deploy
the branch tip and verify `commitHash` against it.

Commits, in order:

| SHA | what |
|---|---|
| `99cb66f2a` | append-only log replaces the whole-file rewrite |
| `1475df2cf` | an unverified migration must not rewrite the v1 document |
| `9105377e5` | a damaged offset index cannot hide a scored window |
| `3c7bdb94f` | the manifest witnesses the slug set (the rename case) |
| `6116675ed` | keep the v1 document so a rollback is a rename |

## 2. Production diff

```
 analysis/l5/issue209/SHADOW_LEDGER_RETENTION.md | 327 +++++++++
 analysis/l5/issue209/shadow_ledger_benchmark.py | 170 +++++
 src/l5_two_sided_shadow.py                      | 894 +++++++++++++++++++++++-
 tests/test_l5_roll_stall_issue195.py            |  42 +-
 tests/test_l5_shadow_ledger_retention.py        | 853 ++++++++++++++++++++++
 5 files changed, 2247 insertions(+), 39 deletions(-)
```

One runtime module changes: `src/l5_two_sided_shadow.py`, class `ShadowLedger`. Nothing
else in `src/` is touched, so no gate, limit, accounting, order, fill, risk or watchdog
path is in this diff.

### Image closure

`src/l5_two_sided_shadow.py` is already one of the Dockerfile's 75 `COPY`
directives (line 145), no new module is added, and the three new imports —
`hashlib`, `re`, `collections.abc` — are **stdlib**. The build surface does not change.
This establishes that the COPY set and the import graph are unchanged; it does **not**
prove the container builds, starts or behaves correctly. V1–V6 below cover that.

## 3. Required configuration

**None.** No variable is added, changed or removed. `L5_SHADOW_ONLY`, `L5_LIVE_HALT`,
`L5_MAX_WORST_CASE_DEFICIT_USD`, `L5_MAX_CASH_DEPLOYED_USD`, every campaign and
authorization variable, and the watchdog's own settings are all untouched. The ledger
reads no new environment variable and introduces none.

Two files appear beside the manifest on the volume at runtime, at whatever
`L5_LIVE_ROOT` resolves to:

| path | what |
|---|---|
| `two_sided_shadow.json` | the manifest: `format`, `pending`, `finalized_count`, `finalized_digest` |
| `two_sided_shadow.json.finalized.jsonl` | the history, one JSON line a scored window |
| `two_sided_shadow.json.finalized.idx` | the offset cache |
| `two_sided_shadow.json.v1-pre209` | the pre-migration v1 document, kept by rename |

Disk after migration is ~586 MB (292.7 MB of history plus the 292.7 MB backup) against a
50 GB volume holding 2.67 GB. Growth is unchanged at ~128 KB a window, ~12 MB a day. The
292.7 MB `.tmp` that v1 wrote on **every** save is gone.

## 4. Rollback target

`a2f6537517bdbd04e991015a86fa4c727dfe3dff`, which is what is running now.

**The rollback is a rename, and it must be done.** Reverting the code alone leaves the
pre-#209 ledger reading a v2 manifest: it finds no `finalized` key, starts with an
**empty scoring history**, and re-scores windows into a fresh v1 document. No record is
destroyed — the log is untouched — but scoring would duplicate until someone noticed. So:

```bash
# on the volume, after reverting the service to a2f653751
mv two_sided_shadow.json.v1-pre209 two_sided_shadow.json
# the .finalized.jsonl and .finalized.idx can be left in place; v1 ignores them,
# and rolling FORWARD again re-migrates from the restored document without
# duplicating a single line, because every slug is already in the log.
```

Verified at the production record count: without the rename v1 sees `finalized=0`; with
it, `finalized=2294`; rolling forward again gives 2,294 records with the log still at
2,294 lines.

## 5. Post-deployment verification

### V0 — capture the before-state FIRST

This cannot be recovered afterwards. Before the new container starts, capture the live
`L5_SHADOW_LEDGER` line and the three P&L measures from one heartbeat. The baseline for
comparison is:

```
L5_SHADOW_LEDGER {"finalized": 2294, "pending": 0, "bytes": 292732040,
                  "last_save_s": 6.7605, "saves_this_process": 9, "max_pending": 16}
```

(2026-11-08T00:06:24Z, deployment `18deb02d`. #209's own 23:56Z reading was 2,293 /
292,624,771 / 6.7077, one window earlier.)

### V1 — the container is running this SHA

Railway: `list-deployments` for service `87f1834e-530e-4fc4-a310-91c8d3add196`; expect
`SUCCESS` and `meta.commitHash` equal to the branch tip.

### V2 — the migration ran once and verified

`get-logs`, filter `L5_SHADOW_LEDGER`. Expect, on the **first** start only:

| field | expect |
|---|---|
| `migration_state` | `MIGRATED_FROM_V1` |
| `migrated_records` | the v1 document's count |
| `v1_backup` | the `…json.v1-pre209` path |
| `load_s` | one slow value, order 10–15 s on the volume, once |

**Measured** on `4cec3f66`: `MIGRATED_FROM_V1`, `migrated_records: 2301` (the document
had grown by 7 windows since the 00:06Z baseline), `v1_backup:
/data/l5-live-20261012z/two_sided_shadow.json.v1-pre209`, `load_s: 13.7232` — inside the
predicted 10–15 s and inside the watchdog's 120 s. `finalized` then read **2302**, one
window scored after the migration, with `readable_records: 2302` and every integrity
counter zero.

`MIGRATION_NOT_VERIFIED` means the v1 document was deliberately left intact and nothing
is lost — but it must not be left in that state; `manifest_writes` will read
`SUSPENDED_MIGRATION_NOT_VERIFIED` and `pending_durable` `false`.

### V3 — the history is all there, and the ledger says so

Same line. **The first start is a special case and this table was wrong about it** in the
first version of this manifest; the correction is below the table.

| field | first start (migrating) | every later start |
|---|---|---|
| `finalized` | the v1 document's count, **not fewer** | unchanged or higher |
| `readable_records` | equal to `finalized` | equal to `finalized` |
| `records_missing` | `0` | `0` |
| `orphan_slugs` | `0` | `0` |
| `index_refusals`, `slug_mismatches`, `index_spot_mismatches`, `records_unreadable`, `duplicates_dropped` | `0` | `0` |
| `save_error` | `null` | `null` |
| `integrity` | **`NO_MANIFEST_COUNT_TO_CHECK_AGAINST`** | `LOG_AGREES_WITH_MANIFEST` |
| `digest_state` | **`NO_DIGEST_IN_MANIFEST`** | `SLUG_SET_AGREES_WITH_MANIFEST` |
| `index_state` | **`EMPTY`** | `READ_FROM_INDEX` |

**Why those three differ on the first start, and why it is not a fault.** Both witnesses
live in the manifest, and the manifest being read on that start is the **v1** document,
which carries neither a `finalized_count` nor a `finalized_digest`. So there is nothing
to check against, and the ledger says exactly that rather than claiming agreement it
cannot demonstrate. `index_state` reads `EMPTY` for the same kind of reason: the index is
loaded *before* the migration writes the log, so at load time there genuinely was no log
to index. All three are set once at load and are not recomputed, so they keep those
values for the life of that process — **not** merely until the next save.

The first start therefore cannot verify the cross-checks. **V9 does**, after a restart.
Reading `NO_MANIFEST_COUNT_TO_CHECK_AGAINST` as an integrity failure is a misreading;
reading it on a *later* start would be a real finding, because by then the manifest the
ledger wrote itself does carry both witnesses.

Any non-zero value in the integrity row is the thing this deployment exists to make
visible. It is evidence to read, not a reason to panic: nothing is deleted, and the
rescan paths recover from the log.

### V4 — the latency actually moved, on the volume — **MEASURED**

This was the one claim in the manifest that could not be settled offline. It is settled.
Deployment `4cec3f66` at `6116675ed`, its own line at 2026-11-08T02:10:14Z:

| | before (`18deb02d`) | after (`4cec3f66`) |
|---|---|---|
| `last_save_s` | **6.7605 s** | **0.0188 s** |
| per window (two saves) | ~13.5 s | **~0.038 s** |
| `bytes` (what is rewritten) | 292,732,040 | **409** |
| `bytes_written` a save | ~585 MB of I/O | **82,975** |
| `log_bytes` (the history) | — | 294,326,738 |
| `over_budget` / `slow_saves` | — | `false` / `0` |

**360× on the volume, and the prediction was right for the right reason.** The manifest
expected "order 0.02 s", scaled from local disk by the observed 4.4× ratio; the volume
returned 0.0188 s. The estimate is now superseded by the measurement — quote 0.0188 s,
not the scaling.

### V5 — it still cannot submit

Same heartbeat as before: `armed false`, submission gate blocked, `shadow_lock` held,
zero live orders, nothing unresolved. The ledger change touches no authorization path,
and a test pins that it is reachable only from `__init__`, `two_sided_close` and
`two_sided_settle`.

### V6 — the watchdog survived the slow first start

`describe-service` for `fb038b8c-c7c1-4599-b06a-54f72bad544f`: still deployed, still
healthy, and no `EXECUTOR_REPORT_STALE` latch. The watchdog's threshold is
`DEFAULT_REPORT_MAX_AGE_S = 120 s` and the one-time migration is order 10–15 s, so this
should hold — but it is the one interaction worth watching rather than assuming.

### V7 — every P&L measure unchanged, each against its OWN baseline

Settled realized is **−$15.6109** account-wide, **−$13.4809** rebate-adjusted with the
confirmed **+$2.13** of credited maker rebates. The day-scoped measure resets at 00:00
UTC, so compare it against its own pre-deployment value and not the all-time figure.
This deployment produces no fill, no pair and no dollar; any change in a P&L measure is
unrelated to it and should be investigated as such.

### V8 — regression parity at the deployed SHA

```bash
python3 -m pytest tests/ -q -p no:randomly
```

Three runs on the same machine, the base in a worktree:

| tree | failed | passed | skipped | xfailed | errors |
|---|---|---|---|---|---|
| `a2f653751` — what production runs | 20 | 18,080 | 27 | 1 | 14 |
| `3c7bdb94f` — this branch before the rollback backup | 20 | 18,115 | 27 | 1 | 14 |
| `6116675ed` — the tip | 20 | **18,117** | 27 | 1 | 14 |

The base figure matches what `MANIFEST_a2f65375.md` recorded for the deployed SHA, which
is an independent check that the baseline is the one that was reviewed. For the first two
rows the failing and erroring **ID sets are identical** — 34 IDs, `diff` clean — and the
passed count rises by exactly the tests added (`tests/test_l5_shadow_ledger_retention.py`
collects 37), and that holds for the tip too: 18,080 + 37 = 18,117, with the same 34 IDs
and `diff` clean. All three rows are measured — the tip's row carried an arithmetic
prediction of 18,118 before its run, which was wrong by one because two test functions
were added and a third change modified an existing test rather than adding one. The
measured figure is also recorded on PR #210.

### V9 — the cross-checks, after one restart

The first start has no witness to check against (V3). The manifest it writes does, so a
restart is what proves the integrity machinery works on real data. Restart the service —
or simply wait for the next one — and expect:

| field | expect |
|---|---|
| `index_state` | `READ_FROM_INDEX` |
| `integrity` | `LOG_AGREES_WITH_MANIFEST` |
| `digest_state` | `SLUG_SET_AGREES_WITH_MANIFEST` |
| `expected_finalized` | the count, no longer `null` |
| `records_missing`, `orphan_slugs`, `index_refusals` | `0` |
| `load_s` | milliseconds, **not** the one-time migration's seconds |
| `migration_state` | `FORMAT_2_NATIVE` — nothing left to migrate |

`load_s` dropping from seconds to milliseconds is the startup half of this change, and
it is only observable on a start that does not migrate.

## 6. What this deployment does NOT do

- It does not arm trading, change a limit, a budget or a variable, or touch the watchdog.
- It does not delete a record, bound the history, or roll anything up — all 2,294 scored
  windows are kept, one JSON line apiece, and the pre-migration document is kept too.
- It does not change scoring, settlement, fills, risk or accounting.
- It does not make a modelled fill any closer to a real one, and it moves settled P&L by
  nothing.

## 7. What it is for

It removes ~13.5 s of per-window write latency from the thread that rolls the window —
two 292.7 MB rewrites a window, for records nothing on the money path reads. That makes
the execution questions testable without that confound. It answers none of them.

## 8. Still open, and not blockers for a disarmed rollout

- #209 item 2: read-only authenticated bridge reconciliation — the −$1.27 delta and the
  eight `UNPROVEN_LEGACY_ATTRIBUTION` markets of 2026-10-14/15.
- #209 item 3: queue-position-aware maker fill replay with real TTL, cancellation,
  adverse selection and settled fees.
- #209 item 5: the one-window maker-only experiment proposal.
- $1.48 of accrued-not-credited rewards, and the $0.0521 defensive-budget discrepancy,
  both to stay open and unreset.
- `tests/test_l5_shadow_controller_wiring.py::test_the_heartbeat_carries_the_shadow_report`
  fails at both SHAs: the heartbeat reduces the `shadow_controller` block under the #195
  line bound. Pre-existing, unrelated to persistence, unaddressed here.
