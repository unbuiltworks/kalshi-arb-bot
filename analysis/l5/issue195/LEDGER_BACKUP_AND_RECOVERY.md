# Shadow-ledger backup and recovery — required before any retention change

**Nothing in this document has been executed.** No backup has been taken, no
ledger has been modified, no file on `/data` has been touched. This exists
because the owner required a verified backup and recovery procedure *before*
the live ledger is modified or compacted, and because the retention change is
the first change in this workstream that would delete durable data.

## What the ledger is, and what losing it would cost

`ShadowLedger` at `/data`, mounted from Railway volume
`3c08c916-dc42-4eb4-9592-3b25dc07958f` (`l5-pmus-live-ledger`, 50 GB).

Measured on the running service, deployment `84909719`, 2026-11-06T17:54z:

```
L5_SHADOW_LEDGER {"finalized":2173,"pending":0,"max_pending":16}
```

**2,173 scored windows**, each ~156 KB (`cells_reduced.full_bytes` 155,943 on
the live line), so roughly **339 MB**. At 96 windows a day that is about 22
days of continuous scoring.

This is **shadow** scoring, not settlement of record. It does not hold cash,
fills, positions or realized P&L — those live in the inventory store, the
campaign journal and `realized_today`, which this change does not touch. But
it is the only record of modelled per-cell economics across 2,173 windows, it
cannot be reconstructed from the venue, and it is what any claim about which
threshold/envelope cell performs best rests on. Losing it would not lose money;
it would lose the evidence base.

## The procedure, in order

### 1. Back up before touching anything

The container has no shell available to this session, so the backup is taken by
the service itself, on a disarmed deploy, before any retention code is active:

* copy `finalized` to `shadow_ledger.backup.<ISO8601>.json` **on the same
  volume**, written via the same tmp-then-`os.replace` pattern `save` uses, so
  a crash mid-write cannot leave a partial file at the real path;
* emit `L5_SHADOW_LEDGER_BACKUP` with the path, the byte count and the
  **record count**, plus a SHA-256 of the file;
* refuse to proceed if free space on the volume is under 3x the file size.
  339 MB against 50 GB is ample, but the check is the point: the
  2026-09-26 observer failure was a full volume.

The backup lives on the same volume, which protects against a bad retention
change but **not** against volume loss. That is the honest limit of it. A
second copy off-volume would need a destination the owner nominates; none is
assumed here.

### 2. Verify the backup before trusting it

A backup nobody has read is not a backup. Verification is a separate step on a
later disarmed deploy, before retention is enabled:

* re-read the backup file from the volume;
* assert the record count equals the count written;
* assert the SHA-256 matches the one emitted;
* assert a sampled record round-trips field-for-field against the live
  `finalized` entry of the same slug;
* emit `L5_SHADOW_LEDGER_BACKUP_VERIFIED` with the three results.

**Retention stays off until that line appears.** If any check fails, stop and
report; do not compact.

### 3. Only then, retention — and it should not delete first

The safer shape, and the one proposed:

* keep the most recent **N** scored reports whole (N configurable, default
  generous — 192, two days);
* reduce older ones to the digest `bounded_for_log` already produces, which
  keeps `matched_shares`, `residual_shares`, `residual_cost`, the three P&L
  figures and `state`, and drops only per-cell detail;
* **reduce, do not delete.** The slug, the state and the economics survive for
  every window ever scored. Nothing is removed from the record, so a mistake
  costs detail rather than history.

This is a deliberate change from "prune oldest", which is what `pending`
already does and what the first sketch of this proposed. `pending` can prune
because a parked window that never settled is an error to be reported, not
evidence. `finalized` is the evidence.

### 4. Recovery, if a retention change goes wrong

| symptom | action |
|---|---|
| ledger file unreadable or truncated | `ShadowLedger.__init__` already falls back to `{"pending":{},"finalized":{}}` on a bad read rather than crashing, so the service keeps trading; restore from the verified backup |
| records reduced that should not have been | restore from the verified backup; the digest is lossy and cannot be un-reduced |
| retention deleted records | restore from the verified backup; this is why step 3 reduces instead of deleting |
| volume lost entirely | the on-volume backup is gone too. Shadow scoring history is unrecoverable. Settlement, cash and realized P&L are NOT in this file and are unaffected |
| rollback of code | deployments `84909719` (`0d2449fbbe`), `7e3d1e1b` (`a31fe8fd4`) and `a342fccc` (`e131437c8`) all `canRollback: true`. None of them writes retention, so rolling back stops further reduction but does not restore already-reduced records |

Code rollback does not undo data changes. That asymmetry is the reason the
backup comes first and is verified before retention is enabled.

## Relationship to the two-window live test

This procedure gates the **retention change**, not the live test. The live test
does not modify the ledger's retention and does not need the file compacted —
`park` and `finalize` work identically at 2,173 records as at 200. If the
rollover cost turns out to be this file, the two are independent decisions and
the test need not wait on the compaction.
