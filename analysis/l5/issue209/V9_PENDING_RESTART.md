# V9 is still pending, and the live record says exactly why

PR #208, PRIORITY 3. Status of the one post-deployment verification from
the #209 manifest that the migrating start could not answer.

**No Railway service was restarted, redeployed or varied to produce this
document.** Every figure below is from read-only deployment and log queries.

---

## 1. What V9 is for

V9 exists because of a correction to the manifest: **V3 asserted an
integrity agreement that the migrating start cannot demonstrate.** The
first start after #209 has no witness to check itself against — it is the
start that *writes* the first manifest. So the integrity machinery
(offset index, manifest count, slug digest) can only be proven on a start
that **reads** a manifest someone else wrote, i.e. the next restart.

V9's expectations, from `deploy/l5_pmus_live/MANIFEST_shadow_ledger_209.md`:

| field | V9 expects |
|---|---|
| `index_state` | `READ_FROM_INDEX` |
| `integrity` | `LOG_AGREES_WITH_MANIFEST` |
| `digest_state` | `SLUG_SET_AGREES_WITH_MANIFEST` |
| `expected_finalized` | the count, no longer `null` |
| `records_missing`, `orphan_slugs`, `index_refusals` | `0` |
| `load_s` | milliseconds, **not** the migration's seconds |
| `migration_state` | `FORMAT_2_NATIVE` |

---

## 2. Current state: the migrating process is still the running process

Read from the Railway deployment list and the live log, 2026-11-08:

- executor service `87f1834e`, deployment `4cec3f66`, status **SUCCESS**
- commit **6116675ed** on `deploy/l5-disarmed-shadow209-6116675ed-20261108`
- started **2026-11-08T01:51:51Z**; heartbeats observed through
  **12:45:59Z** — about **10.9 hours** with no restart
- `saves_this_process: 88` — two saves a window over 44 windows, which is
  the same process throughout

The `L5_SHADOW_LEDGER` heartbeat at 12:45:59Z:

| field | observed | V9 expects | verdict |
|---|---|---|---|
| `migration_state` | `MIGRATED_FROM_V1` | `FORMAT_2_NATIVE` | **pending** |
| `load_s` | `13.7232` | milliseconds | **pending** (this is the one-time migration) |
| `index_state` | `EMPTY` | `READ_FROM_INDEX` | **pending** |
| `integrity` | `NO_MANIFEST_COUNT_TO_CHECK_AGAINST` | `LOG_AGREES_WITH_MANIFEST` | **pending** |
| `digest_state` | `NO_DIGEST_IN_MANIFEST` | `SLUG_SET_AGREES_WITH_MANIFEST` | **pending** |
| `expected_finalized` | `null` | the count | **pending** |

**Every pending field is pending for the same reason**, and it is the
designed reason: this process migrated, so it has no predecessor's manifest
to agree with. None of these is a fault, and none is evidence of a problem.

---

## 3. What the migrating start DID establish

The parts of #209 that do not need a second start are already measured in
production:

| claim | observed |
|---|---|
| save latency | `last_save_s` **0.0116 – 0.0256 s** (was 6.7605 s) |
| manifest size | `bytes` **409** when flushed (was a 292.7 MB rewrite) |
| history preserved | `finalized` **2345**, `readable_records` **2345** |
| migration scope | `migrated_records` **2301**, and 44 appended since |
| nothing lost | `records_missing` 0, `records_unreadable` 0, `orphan_slugs` 0 |
| nothing doubled | `duplicates_dropped` 0, `truncated_tail_bytes` 0 |
| index never lied | `index_refusals` 0, `index_spot_mismatches` 0, `slug_mismatches` 0 |
| no save error, no slow save | `save_error: null`, `slow_saves: 0`, `over_budget: false` |
| rollback target intact | `v1_backup: /data/l5-live-20261012z/two_sided_shadow.json.v1-pre209` |
| the witness is being written | `manifest_writes: ACTIVE` |

`manifest_writes: ACTIVE` is the load-bearing one for V9: the manifest the
*next* start will check itself against is being maintained now. V9's
premise is satisfied; only the restart that exercises it has not happened.

---

## 4. One honest gap in V9 as written

`index_state: READ_FROM_INDEX` requires an offset index **file** to exist
on the volume at the next start. The running record reports
`index_state: EMPTY` and has no field for "the index file has been
written", so **whether that file exists on `/data` cannot be established
from the heartbeat alone.** If it does not, the next start will report
`index_state` as rebuilt-by-scan rather than `READ_FROM_INDEX`, with
`integrity` and `digest_state` still passing on the manifest.

That outcome would be a **cache miss, not an integrity failure**, and V9
should be read that way: `integrity`, `digest_state`, `expected_finalized`,
`records_missing`, `orphan_slugs` and `index_refusals` are the verification;
`index_state` is a performance observation. The index is a cache and the
log is the history — that was the design, and three tests pin it.

---

## 5. What should happen, and what must not

**Must not:** restart, redeploy or vary the service to produce V9. The
service is a read-only collector running continuously and nothing about
this verification is urgent enough to interrupt it.

**Should:** let the next restart happen on its own — a deployment, a
platform event, or the owner's choice — and read the first
`L5_SHADOW_LEDGER` heartbeat of the new process against the table in
section 1. It is a single log read and needs no authorization.

Until then, **V9 is correctly recorded as PENDING, not as passed and not as
failed.** The disarmed deployment's other verifications stand on their own,
and nothing in #208's work depends on V9 clearing.

## 6. Provenance

- `mcp__Railway__list-deployments`, project `50fb7357`, env `f3642d27`,
  service `87f1834e` — deployment `4cec3f66`, commit `6116675ed`.
- `mcp__Railway__get-logs` on that deployment, filter `L5_SHADOW_LEDGER`,
  records 12:42:35Z – 12:45:59Z on 2026-11-08.
- V9's text: `deploy/l5_pmus_live/MANIFEST_shadow_ledger_209.md` at commit
  `762b543f9` (`base/l5-shadow-ledger-209`).
