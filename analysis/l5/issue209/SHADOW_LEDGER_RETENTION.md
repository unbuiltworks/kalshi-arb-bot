# Shadow-ledger persistence: the 293 MB rewrite, measured and removed

Issue **#209**. Branch `fix/l5-shadow-ledger-retention-209`.
Production SHA at the time of measurement: `a2f6537517bdbd04e991015a86fa4c727dfe3dff`,
deployment `18deb02d-d3fd-4888-a062-93f77b4f0942`, service `l5-pmus-live`, **disarmed**.

Nothing here was deployed, merged, armed or re-configured. No Railway variable was
changed, no risk limit was touched, and no record was deleted. Every number in this
ledger is **modelled**: this document is about latency and durability, not profit.

---

## 1. The defect, as the running service reported it

The v1 `ShadowLedger.save` docstring named itself as the remaining candidate for the
residual rollover cost and refused to act on the belief until the service produced its
own number. It did. From the executor's own `L5_SHADOW_LEDGER` line:

```
2026-11-08T00:06:24.357Z  L5_SHADOW_LEDGER
  {"finalized": 2294, "pending": 0, "bytes": 292732040, "last_save_s": 6.7605,
   "saves_this_process": 9, "max_pending": 16, ...}
```

* **2,294** scored windows, kept whole.
* **292,732,040 bytes** — 292.7 MB — mean record **127,608 B**.
* **6.7605 s** to write, on a network-attached volume.
* `save()` is called from `park` **and** from `finalize`: **twice a window**, so
  ≈ **13.5 s of writing per 15-minute window** on the same thread that rolls the window.
* Nothing on the money path ever reads a single one of those records. `two_sided_ledger`
  is reached from exactly three places in the runner — `__init__`, `two_sided_close`
  and `two_sided_settle` — all shadow paths. A regression test now pins that.

### Reproduced on local disk at the same record size

`analysis/l5/issue209/shadow_ledger_benchmark.py`, records padded to the production
mean of 127,608 B. `LegacyLedger` in that file is the v1 `load`/`save` verbatim and is
verified byte-identical to the deployed writer.

|     N | file MB | v1 save s | per window (2×) | v1 load s |
|------:|--------:|----------:|----------------:|----------:|
|    96 |    12.2 |     0.058 |           0.117 |     0.035 |
|   480 |    61.2 |     0.297 |           0.593 |     0.219 |
|   960 |   122.5 |     0.623 |           1.245 |     0.386 |
|  1440 |   183.7 |     0.943 |           1.886 |     0.568 |
|  2294 |   292.7 |     1.532 |           3.063 |     1.166 |

Local disk is ~4.4× faster than the volume (1.53 s here against 6.76 s there), which
is what closes the gap between this table and the production line. The **shape** is the
finding: cost is linear in the whole history and the history only grows.

Two further costs the latency line did not show:

* **Startup.** `load()` parsed all 292.7 MB on every container start: **1.10 s** and
  **+295 MB of peak RSS** measured locally. Production memory metrics over the last six
  hours show `MEMORY_USAGE_GB` average 3.04 with a **max of 7.10** against a 32 GB
  limit — consistent with that document being parsed into Python objects.
* **Write amplification.** The v1 writer wrote a 292.7 MB `.tmp` and renamed it, so each
  save touched ~585 MB of volume I/O and briefly doubled the file's footprint.

---

## 2. What replaced it

`finalized` is append-only **by its own contract** — a window is scored exactly once and
never revised — so it does not belong in a document that gets rewritten. It now lives in
a log beside a small manifest:

| file | contents | written |
|---|---|---|
| `two_sided_shadow.json` | manifest: `format`, `pending`, `finalized_count`, log name | rewritten atomically each save; `pending` is bounded by `max_pending` (16), so it cannot grow |
| `two_sided_shadow.json.finalized.jsonl` | one `{"market", "record"}` JSON line per scored window | **appended once, never rewritten**, `fsync`ed |
| `two_sided_shadow.json.finalized.idx` | `slug → (offset, length)` | a **cache**: always validated against the log and rebuilt from it on any mismatch |

`state["finalized"]` is still a mapping — `FinalizedRecords` — so the ledger's own
methods, the existing tests and the analysis tooling read it unchanged. `__contains__`
and `__len__` answer from the offset index without touching a record; `__getitem__`
reads one record's bytes by offset and caches the last `RECORD_CACHE` (8) of them.

**Idempotency became structural rather than incidental.** A slug already in the log is
dropped rather than appended, so a window cannot be scored twice however many times
`finalize` is called, whatever the caller's `if` says, and across any restart.

### Migration, and why the ordering is the safety argument

1. Records are written into the log and `fsync`ed.
2. Every migrated slug is **read back out of the log and parsed**.
3. The manifest is replaced **last**.

A crash at any point leaves the v1 document untouched, so the migration simply runs
again; a log that was half written is resumed, because records already in the index are
not appended twice. If step 2 does not verify, nothing is swapped. The v1 document is the only copy that
is provably complete, so it is **left exactly as it is**: rewriting it from records we
have just failed to read back would be the one way to actually lose one. `migration_state`
becomes `MIGRATION_NOT_VERIFIED`, every record stays reachable, scoring carries on
against the log, and manifest writes are **suspended** — reported as
`manifest_writes: SUSPENDED_MIGRATION_NOT_VERIFIED` and `pending_durable: false`, so
the cost is one window's modelled `pending` rather than the scoring history. A later
start resumes the half-written log without duplicating it and swaps once it verifies.

An unparseable manifest is **moved aside** to `…json.unparseable-<epoch>` instead of
being silently overwritten by the next save, and the log survives it, so a corrupt
manifest no longer costs the scoring history.

### It cannot block a trading decision

`save()` cannot raise and cannot refuse. A write failure is counted and **named** in
`size_report()` (`save_error`) and never propagates into the settle pass or the
rollover. Latency is measured against `SAVE_BUDGET_S = 0.25 s` and **reported**
(`over_budget`, `slow_saves`) — reported, not enforced: this is telemetry, and
telemetry may not become a limit. An index-write failure cannot even be reported as a
save failure, because the index is a cache whose loss costs a scan.

---

## 3. After

Same seeded history, same records, same disk:

|     N | file MB | v1 save s | **v2 save s** | v1 load s | **v2 load s** | v2 bytes appended | one-time migration s |
|------:|--------:|----------:|--------------:|----------:|--------------:|------------------:|---------------------:|
|    96 |    12.2 |     0.058 |    **0.0018** |     0.035 |    **0.0002** |           127,927 |                 0.11 |
|   480 |    61.2 |     0.297 |    **0.0028** |     0.219 |    **0.0007** |           127,928 |                 0.63 |
|   960 |   122.5 |     0.623 |    **0.0029** |     0.386 |    **0.0009** |           127,928 |                 1.07 |
|  1440 |   183.7 |     0.943 |    **0.0025** |     0.568 |    **0.0012** |           127,929 |                 1.65 |
|  2294 |   292.7 |     1.532 |    **0.0043** |     1.166 |    **0.0038** |           127,929 |                 2.72 |

At the production record count:

* **save: 1.532 s → 0.0043 s**, a 356× reduction on local disk. The volume is ~4.4×
  slower, so the expected production figure is **≈0.019 s a save, ≈0.038 s a window**
  against the 13.5 s it costs today. That is an estimate by ratio; the real number will
  come from the service's own `L5_SHADOW_LEDGER` line, exactly as this one did.
* **bytes written per save is constant in N** — one record plus the manifest — which is
  the property that matters. It is asserted directly in the regression suite rather than
  inferred from a timing.
* **startup: 1.10 s and +295 MB → 0.001 s and +0.0 MB** (peak RSS measured in a
  subprocess: 625.2 MB for v1, 330.3 MB baseline for v2, i.e. no growth at all).
* **write amplification: ~585 MB a save → ~128 KB a save.**

One-time cost, on the first start after the change: the migration parses the v1
document once — 2.72 s and the same ~295 MB peak locally, so on the order of **10–15 s
of startup on the volume**, once, before the loop begins. Every later start is the
0.001 s path. The volume is 50 GB with 2.67 GB in use, so the transient double
footprint (log written while the v1 document still exists, ≈586 MB) has ample headroom.

Growth rate is unchanged: the same records, ~128 KB a window, ~12 MB a day. This is a
**format** change, not a retention change — a test pins that a full report still goes to
disk whole.

---

## 4. Tests

`tests/test_l5_shadow_ledger_retention.py`, 25 tests, all passing:

* **migration** — record-for-record equality, pending preserved, no rescoring of a
  migrated window, idempotent across restarts, log-before-manifest ordering proven by
  making the manifest write fail, and the unverified-migration fallback keeping both the
  v1 document and the v1 write path;
* **duplicate prevention** — a second record for a scored slug is dropped, `finalize`
  appends once however often it is called, a duplicate line hand-appended to the log
  resolves to the first;
* **restart recovery** — the index is read and **no record is parsed** (asserted on the
  cache), a lost index is rebuilt by scan, an index ahead of the log is discarded, and
  records appended without an index update are caught up and still count as scored;
* **corruption** — a partial final line is not a record and is overwritten by the next
  append, a corrupt manifest is quarantined with its bytes intact while the log
  survives, an unreadable record still counts as scored (`in` stays True, the read
  raises) so it cannot be scored twice, a failing write cannot raise into the settle
  pass, and an unwritable index costs a scan and never a record;
* **rollover latency** — the bytes written are the same at 50 and at 500 records of
  history, the manifest does not grow with the history, two saves a window at the
  production count of 2,294 stay inside the budget with a wide margin, and a slow save
  is reported and never enforced;
* **the runner's contract** — every field the emitted line carried is still there, the
  report is JSON-serialisable, the ledger is reachable only from the shadow paths, the
  durable writers still do not bound a report, and the whole report is still stored.

Two tests in `tests/test_l5_roll_stall_issue195.py` pinned the **v1 on-disk format** and
were rewritten to pin the new one. They are named in the PR, and the section comment
that framed the hypothesis now records that the service measured it:

* `test_the_size_report_names_the_unbounded_half` → `test_the_size_report_names_the_append_only_log`
* `test_the_instrumentation_changes_nothing_that_is_written` → `test_the_manifest_no_longer_carries_the_history`

Every other test in that section — including the log-line bounding from #195 — still
holds unchanged.

---

## 5. What this does not do

* It does not touch scoring, settlement, fills, risk, accounting or the watchdog.
* It does not delete a record, bound the history, or roll anything up.
* It does not change what a window is worth, and it does not make a modelled fill any
  closer to a real one.
* It is **not deployed**. The migration runs on the first start after a deployment that
  someone else authorises.
