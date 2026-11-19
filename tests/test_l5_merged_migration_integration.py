"""The merged tree against the state production's volume is actually in.

PR #211 merged the shadow-ledger retention fix (issue #209) into the #208
branch. The volume it will start against is NOT a clean v1 document and
NOT a clean v2 one. It is the state the ROLLBACK left:

  * `two_sided_shadow.json.finalized.jsonl` -- the append-only v2 log,
    302 MB, 2345 records, untouched by the rolled-back code
  * `two_sided_shadow.json` -- OVERWRITTEN by the pre-#209 code with a v1
    document holding windows it re-scored from an empty history
  * `two_sided_shadow.json.v1-pre209` -- the backup the FIRST migration
    made, which must survive the second one

So the merged code's first start performs a SECOND migration, over a log
that already holds most of what the v1 document contains. The four things
the owner asked about are exactly the four risks in that path:

  migration failure      -> does it complete, and how long does it take?
  duplicate settlement   -> are overlapping windows appended twice?
  lost accounting state  -> is any of the 2345 still readable afterwards?
  startup delay          -> is `load_s` inside the watchdog's 120 s?

These are tests rather than a one-off script because this path will run
again on every rollback, and a one-off script proves nothing the next
time.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from src import l5_two_sided_shadow as SH

BACKUP_SUFFIX = ".v1-pre209"


def v2_history(d, n=5):
    """A v2 log with `n` scored windows, as the volume has."""
    canon = os.path.join(d, "two_sided_shadow.json")
    L = SH.ShadowLedger(canon)
    L.load()
    for i in range(n):
        slug = "w-%04dz" % (100 + i * 15)
        L.state["finalized"][slug] = {"market": slug, "n": i,
                                      "note": "original"}
    L.save()
    return canon, L


def rolled_back_write(canon, records):
    """What the pre-#209 code does: finds no `finalized` key in the V2
    manifest, starts from an EMPTY history, and overwrites the canonical
    path with a v1 document of windows it re-scored."""
    open(canon, "w").write(json.dumps({"finalized": records,
                                       "pending": {}}))


def test_the_second_migration_completes_and_loses_nothing(tmp_path):
    d = str(tmp_path)
    canon, _ = v2_history(d, n=5)
    # two windows the log already has, re-scored; one genuinely new
    rolled_back_write(canon, {
        "w-0115z": {"market": "w-0115z", "n": 1, "note": "RESCORED"},
        "w-0130z": {"market": "w-0130z", "n": 2, "note": "RESCORED"},
        "w-0175z": {"market": "w-0175z", "n": 9, "note": "new-while-back"}})

    t0 = time.time()
    L2 = SH.ShadowLedger(canon)
    L2.load()
    elapsed = time.time() - t0
    r = L2.size_report()

    # migration completed
    assert r["migration_state"] == "MIGRATED_FROM_V1", r
    # NOTHING LOST: all five originals plus the one genuinely new window
    assert r["records_missing"] == 0, r
    assert r["records_unreadable"] == 0, r
    assert r["orphan_slugs"] == 0, r
    fin = L2.state["finalized"]
    assert len(fin) == 6, sorted(fin)
    for i in range(5):
        slug = "w-%04dz" % (100 + i * 15)
        assert slug in fin, (slug, sorted(fin))
    assert "w-0175z" in fin

    # NO DUPLICATE SETTLEMENT RECORDS: the log wins for windows it already
    # holds, so the re-scores are DISCARDED rather than double-counted.
    assert fin["w-0115z"]["note"] == "original", fin["w-0115z"]
    assert fin["w-0130z"]["note"] == "original", fin["w-0130z"]
    assert fin["w-0175z"]["note"] == "new-while-back"
    assert r["duplicates_dropped"] == 0, r
    assert r["truncated_tail_bytes"] == 0, r

    # the integrity machinery agrees with itself afterwards
    assert r["integrity"] == "LOG_AGREES_WITH_MANIFEST", r
    assert r["digest_state"] == "SLUG_SET_AGREES_WITH_MANIFEST", r
    assert r["index_refusals"] == 0 and r["slug_mismatches"] == 0, r

    # STARTUP DELAY: a second migration over a small v1 document is fast.
    # The watchdog's staleness bound is 120 s; this must be nowhere near.
    assert elapsed < 10.0, elapsed


def test_the_log_grows_by_the_NEW_record_only(tmp_path):
    """The arithmetic behind "no duplicate settlement records": one new
    window means one appended line, not four."""
    d = str(tmp_path)
    canon, _ = v2_history(d, n=5)
    log = canon + ".finalized.jsonl"
    before = len(open(log).read().splitlines())
    rolled_back_write(canon, {
        "w-0100z": {"market": "w-0100z", "n": 0, "note": "RESCORED"},
        "w-0115z": {"market": "w-0115z", "n": 1, "note": "RESCORED"},
        "w-0175z": {"market": "w-0175z", "n": 9, "note": "new"}})
    SH.ShadowLedger(canon).load()
    after = len(open(log).read().splitlines())
    assert before == 5, before
    assert after == 6, (before, after)


def test_an_existing_v1_backup_is_not_clobbered_by_the_second_migration(
        tmp_path):
    """THE ONE THAT WOULD ACTUALLY DESTROY SOMETHING.

    `.v1-pre209` on the production volume is the ORIGINAL pre-#209
    document, 292.7 MB. The second migration must not overwrite it with
    the small re-scored document the rollback wrote, or the only
    standalone copy of the original history is gone.
    """
    d = str(tmp_path)
    canon, _ = v2_history(d, n=3)
    backup = canon + BACKUP_SUFFIX
    open(backup, "w").write(json.dumps(
        {"finalized": {"w-ORIGINAL-%d" % i: {"market": "w-ORIGINAL-%d" % i}
                       for i in range(50)}, "pending": {}}))
    original = open(backup).read()

    rolled_back_write(canon, {"w-0199z": {"market": "w-0199z", "n": 9}})
    SH.ShadowLedger(canon).load()

    assert os.path.exists(backup), "the backup vanished"
    assert open(backup).read() == original, \
        "the second migration clobbered the original v1 backup"


def test_a_start_with_nothing_to_migrate_is_FORMAT_2_NATIVE_and_fast(
        tmp_path):
    """The steady state after the probe deploy settles: no v1 document,
    so no migration, and the startup cost is the index read. This is also
    V9's own expectation."""
    d = str(tmp_path)
    canon, _ = v2_history(d, n=5)
    t0 = time.time()
    L = SH.ShadowLedger(canon)
    L.load()
    elapsed = time.time() - t0
    r = L.size_report()
    assert r["migration_state"] == "FORMAT_2_NATIVE", r
    assert r["index_state"] == "READ_FROM_INDEX", r
    assert r["integrity"] == "LOG_AGREES_WITH_MANIFEST", r
    assert r["digest_state"] == "SLUG_SET_AGREES_WITH_MANIFEST", r
    assert r["expected_finalized"] is not None, r
    assert r["records_missing"] == 0 and r["orphan_slugs"] == 0, r
    assert elapsed < 5.0, elapsed


def test_accounting_state_is_not_touched_by_the_ledger_at_all(tmp_path):
    """"Lost accounting state" has to mean the INVENTORY store, not the
    shadow ledger -- they are different files, and the shadow ledger
    cannot reach the one the money path uses. Pinned so the two are not
    conflated when reading a migration report."""
    import inspect
    src = inspect.getsource(SH)
    # NOT a bare "leg_cost": the shadow controller models its own
    # `first_leg_cost`, which is a different thing from the inventory
    # store's `leg_cost` ledger. The property that matters is that this
    # module cannot WRITE the money path's state, so check for the write
    # methods and for the store itself -- the third time a crude substring
    # ban has caught me on a legitimate name.
    for banned in ("record_submit(", "apply_order_progress(", "apply_fill(",
                   "InventoryStore", "leg_cost[", 'state["leg_cost"]',
                   'state["positions"]'):
        assert banned not in src, banned
    # and it reaches no inventory file by name
    assert "inventory.json" not in src
    assert "inv.json" not in src


def test_a_save_after_the_second_migration_appends_rather_than_rewrites(
        tmp_path):
    """The whole point of #209, still true after the merge: the save is an
    append, so the 6.76 s whole-file rewrite does not come back."""
    d = str(tmp_path)
    canon, _ = v2_history(d, n=5)
    rolled_back_write(canon, {"w-0199z": {"market": "w-0199z", "n": 9}})
    L = SH.ShadowLedger(canon)
    L.load()
    log = canon + ".finalized.jsonl"
    size_before = os.path.getsize(log)
    L.state["finalized"]["w-0200z"] = {"market": "w-0200z", "n": 10}
    t0 = time.time()
    L.save()
    save_s = time.time() - t0
    size_after = os.path.getsize(log)
    assert size_after > size_before
    r = L.size_report()
    assert int(r["format"]) == 2, r
    assert save_s < 1.0, save_s
    # the manifest stays small -- it is an index, not the history
    assert int(r["bytes"]) < 100_000, r
