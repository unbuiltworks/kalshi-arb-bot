"""THE SHADOW LEDGER'S PERSISTENCE, ISSUE #209.

WHAT WAS WRONG, MEASURED ON THE RUNNING SERVICE. Deployment 18deb02d,
executor, its own `L5_SHADOW_LEDGER` line at 2026-11-08T00:06:24Z:

    {"finalized": 2294, "pending": 0, "bytes": 292732040,
     "last_save_s": 6.7605, "saves_this_process": 9, "max_pending": 16}

`save` rewrote all 292.7 MB on every park and every finalize -- twice a
window -- for a file nothing on the money path ever reads. The fix is an
append-only log beside a small manifest.

WHAT THESE TESTS PIN. Migration off the v1 document, duplicate prevention,
restart recovery, corruption handling, and that a save costs the records it
adds rather than the history. Nothing here is a trading result: every
number in this ledger is modelled.
"""
from __future__ import annotations

import inspect
import json
import os
import time

import pytest

from src import l5_two_sided_shadow as TS
from src import l5_pmus_live_runner as RUN


# ---------------------------------------------------------------- helpers
def record(slug: str, *, pad: int = 0) -> dict:
    """A scored-report-shaped record. `pad` inflates it to a real size."""
    return {"LABEL": "L5_TWO_SIDED_SHADOW", "market": slug,
            "state": TS.FINALIZED,
            "cells": [{"policy": "SKEWED", "net_pnl_modelled": "-0.11",
                       "detail": "x" * pad}]}


def v1_document(path: str, finalized: dict, pending: dict = None) -> None:
    """The v1 on-disk format, exactly: one JSON document, both halves in it.

    `test_l5_roll_stall_issue195` pinned this shape against the deployed v1
    writer, and `analysis/l5/issue209/shadow_ledger_benchmark.py` carries the
    writer itself; this is what migration has to read.
    """
    with open(path, "w") as fh:
        json.dump({"pending": pending or {}, "finalized": finalized}, fh)


def log_lines(path: str) -> list:
    with open(path + ".finalized.jsonl") as fh:
        return [ln for ln in fh.read().split("\n") if ln]


def window(slug: str) -> dict:
    """A report `park` will accept."""
    return {"LABEL": "L5_TWO_SIDED_SHADOW", "market": slug, "cells": [
        {"policy": TS.COMPLEMENT_ONLY, "envelope": "100",
         "matched_shares": "0", "residual_shares": "10",
         "residual_cost": "5.90", "matched_cost": "0",
         "matched_pnl_modelled": "0", "residual_pnl_modelled": None,
         "net_pnl_modelled": None}]}


# =====================================================================
# 1. MIGRATION OFF THE V1 DOCUMENT.
# =====================================================================

def test_a_v1_document_is_migrated_record_for_record(tmp_path):
    p = str(tmp_path / "s.json")
    recs = {"w%03d" % i: record("w%03d" % i, pad=200) for i in range(25)}
    v1_document(p, recs, pending={"p1": {"market": "p1", "parked_at": 1.0}})

    led = TS.ShadowLedger(p)

    assert led.migration_state == TS.MIGRATED
    assert led.migrated_records == 25
    assert len(led.state["finalized"]) == 25
    for slug, rec in recs.items():
        assert slug in led.state["finalized"]
        assert led.state["finalized"][slug] == rec        # byte-for-byte
    assert led.pending_slugs() == ["p1"]                  # pending preserved
    doc = json.loads(open(p).read())
    assert doc["format"] == TS.LEDGER_FORMAT
    assert "finalized" not in doc
    assert doc["finalized_count"] == 25
    assert len(log_lines(p)) == 25
    # THE V1 DOCUMENT IS KEPT, byte for byte, by rename rather than copy --
    # so rolling the code back to pre-#209 is a rename back, not a
    # re-migration. v1 reading a v2 manifest finds no `finalized` key and
    # would start with an empty scoring history.
    backup = p + TS.V1_BACKUP_SUFFIX
    assert led.v1_backup == backup
    assert json.loads(open(backup).read())["finalized"].keys() == recs.keys()
    assert led.size_report()["v1_backup"] == backup


def test_migration_does_not_rescore_a_migrated_window(tmp_path):
    p = str(tmp_path / "s.json")
    v1_document(p, {"w1": record("w1")})
    led = TS.ShadowLedger(p)
    assert led.finalize("w1", settle_up=True) is None
    assert led.park(window("w1"), now=5.0)["state"] == TS.FINALIZED


def test_migration_is_idempotent_across_restarts(tmp_path):
    p = str(tmp_path / "s.json")
    v1_document(p, {"w%d" % i: record("w%d" % i) for i in range(5)})
    first = TS.ShadowLedger(p)
    assert first.migration_state == TS.MIGRATED
    again = TS.ShadowLedger(p)
    assert again.migration_state == TS.NATIVE      # nothing left to migrate
    assert again.migrated_records == 0
    assert len(log_lines(p)) == 5                  # not 10
    assert len(again.state["finalized"]) == 5


def test_the_records_reach_the_log_before_the_manifest_is_replaced(tmp_path, monkeypatch):
    """The ordering IS the safety argument. A crash between the two leaves
    the v1 document in place, so the migration simply runs again."""
    p = str(tmp_path / "s.json")
    recs = {"w%d" % i: record("w%d" % i) for i in range(4)}
    v1_document(p, recs)
    before = open(p).read()

    boom = {"n": 0}

    def explode(self, backup=None):
        boom["n"] += 1
        raise OSError("volume went away")

    monkeypatch.setattr(TS.ShadowLedger, "_write_manifest", explode)
    led = TS.ShadowLedger(p)                       # must not raise
    assert boom["n"] == 1
    assert open(p).read() == before                # v1 document untouched
    assert len(log_lines(p)) == 4                  # records already durable
    monkeypatch.undo()

    reopened = TS.ShadowLedger(p)                  # the retry
    assert len(reopened.state["finalized"]) == 4
    assert len(log_lines(p)) == 4                  # still 4, not 8
    assert json.loads(open(p).read())["format"] == TS.LEDGER_FORMAT


def test_an_unverified_migration_leaves_the_v1_document_alone(tmp_path, monkeypatch):
    """If the records cannot be read back out of the log, nothing is swapped.

    The v1 document is the only copy that is provably complete, so it is not
    rewritten from records we have just failed to read -- that would be the
    one way to actually lose one. Manifest writes are suspended and said to
    be, and a later start migrates cleanly and swaps.
    """
    p = str(tmp_path / "s.json")
    recs = {"w%d" % i: record("w%d" % i) for i in range(3)}
    v1_document(p, recs)
    before = open(p).read()
    monkeypatch.setattr(TS.FinalizedRecords, "flush", lambda self: 0)

    led = TS.ShadowLedger(p)

    assert led.migration_state == TS.NOT_VERIFIED
    assert len(led.state["finalized"]) == 3        # kept, in memory
    assert led.state["finalized"]["w1"] == recs["w1"]
    r = led.size_report()
    assert r["manifest_writes"] == "SUSPENDED_MIGRATION_NOT_VERIFIED"
    assert r["pending_durable"] is False
    monkeypatch.undo()
    led.save()
    assert open(p).read() == before                # untouched, on purpose
    assert len(log_lines(p)) == 3                  # the log did get them

    recovered = TS.ShadowLedger(p)                 # a later start
    assert recovered.migration_state == TS.MIGRATED
    assert len(recovered.state["finalized"]) == 3
    assert len(log_lines(p)) == 3                  # resumed, not duplicated
    assert recovered.state["finalized"]["w2"] == recs["w2"]
    assert recovered.size_report()["pending_durable"] is True
    assert json.loads(open(p).read())["format"] == TS.LEDGER_FORMAT


# =====================================================================
# 2. DUPLICATE PREVENTION. Scoring once is now a property of the STORAGE.
# =====================================================================

def test_the_log_refuses_a_second_record_for_a_scored_window(tmp_path):
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    led.state["finalized"]["w1"] = record("w1")
    led.save()
    led.state["finalized"]["w1"] = record("w1", pad=99)      # a revision
    led.save()
    assert len(log_lines(p)) == 1
    assert led.state["finalized"]["w1"] == record("w1")      # first wins
    assert led.records.duplicates_dropped == 1


def test_finalize_appends_once_however_often_it_is_called(tmp_path):
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    led.park(window("w1"), now=1.0)
    assert led.finalize("w1", settle_up=False) is not None
    for _ in range(5):
        assert led.finalize("w1", settle_up=True) is None
    assert len(log_lines(p)) == 1
    reopened = TS.ShadowLedger(p)
    assert reopened.finalize("w1", settle_up=True) is None
    assert len(log_lines(p)) == 1


def test_a_duplicate_line_in_the_log_resolves_to_the_first(tmp_path):
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    led.state["finalized"]["w1"] = record("w1")
    led.save()
    with open(p + ".finalized.jsonl", "a") as fh:
        fh.write(json.dumps({"market": "w1",
                             "record": record("w1", pad=7)}) + "\n")
    os.unlink(p + ".finalized.idx")                # force a scan
    reopened = TS.ShadowLedger(p)
    assert len(reopened.state["finalized"]) == 1
    assert reopened.state["finalized"]["w1"] == record("w1")
    assert reopened.records.duplicates_dropped == 1


# =====================================================================
# 3. RESTART RECOVERY.
# =====================================================================

def test_a_restart_reads_the_index_and_parses_no_records(tmp_path):
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    for i in range(30):
        led.state["finalized"]["w%02d" % i] = record("w%02d" % i, pad=500)
    led.save()

    reopened = TS.ShadowLedger(p)
    assert reopened.records.index_state == "READ_FROM_INDEX"
    assert len(reopened.state["finalized"]) == 30
    assert reopened.records._cache == {}           # nothing parsed yet
    assert "w17" in reopened.state["finalized"]    # answered from the index
    assert reopened.records._cache == {}
    assert reopened.state["finalized"]["w17"] == record("w17", pad=500)
    assert list(reopened.records._cache) == ["w17"]


def test_a_lost_index_is_rebuilt_from_the_log(tmp_path):
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    for i in range(6):
        led.state["finalized"]["w%d" % i] = record("w%d" % i)
    led.save()
    os.unlink(p + ".finalized.idx")
    reopened = TS.ShadowLedger(p)
    assert reopened.records.index_state == "REBUILT_BY_SCAN"
    assert len(reopened.state["finalized"]) == 6
    assert reopened.state["finalized"]["w3"] == record("w3")
    reopened.state["finalized"]["w9"] = record("w9")
    reopened.save()                                 # the index is rewritten
    assert TS.ShadowLedger(p).records.index_state == "READ_FROM_INDEX"


def test_an_index_ahead_of_the_log_is_discarded_and_the_loss_is_named(tmp_path):
    """The LOG lost two records. The index is refused for pointing past the
    end of it, the geometry is rebuilt from what survives -- and the two
    slugs stay present, unscorable and reported, because the index knowing
    they were scored is evidence even when their bytes are gone."""
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    for i in range(6):
        led.state["finalized"]["w%d" % i] = record("w%d" % i)
    led.save()
    lines = log_lines(p)
    with open(p + ".finalized.jsonl", "w") as fh:   # lose the last two
        fh.write("\n".join(lines[:4]) + "\n")

    reopened = TS.ShadowLedger(p)

    assert reopened.records.index_state == "REBUILT_BY_SCAN"
    assert reopened.records.index_refusals == 1
    assert reopened.state["finalized"]["w0"] == record("w0")
    assert reopened.records.readable_count() == 4
    assert reopened.records.orphan_slugs() == ["w4", "w5"]
    assert len(reopened.state["finalized"]) == 6     # still six were scored
    for lost in ("w4", "w5"):
        assert lost in reopened.state["finalized"]   # cannot be scored again
        with pytest.raises(KeyError):
            reopened.state["finalized"][lost]
        assert reopened.finalize(lost, settle_up=True) is None
    r = reopened.size_report()
    assert r["integrity"] == TS.INTEGRITY_SHORT      # NEVER silent
    assert r["records_missing"] == 2
    assert r["expected_finalized"] == 6
    assert r["readable_records"] == 4
    assert r["orphan_slugs"] == 2


def test_records_appended_without_an_index_update_are_caught_up(tmp_path):
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    led.state["finalized"]["w1"] = record("w1")
    led.save()
    with open(p + ".finalized.jsonl", "a") as fh:   # index write was lost
        fh.write(json.dumps({"market": "w2", "record": record("w2")}) + "\n")
    reopened = TS.ShadowLedger(p)
    # the index is caught up off the log, and then the manifest's digest --
    # written when only w1 existed -- disagrees, which buys one verification
    # pass against the log and is then NAMED rather than chased.
    assert reopened.records.index_state == TS.DIGEST_REBUILT
    assert reopened.digest_state == TS.DIGEST_STALE
    assert len(reopened.state["finalized"]) == 2
    assert reopened.state["finalized"]["w2"] == record("w2")
    assert reopened.finalize("w2", settle_up=True) is None
    reopened.save()                                 # manifest catches up
    assert TS.ShadowLedger(p).digest_state == TS.DIGEST_AGREES


# =====================================================================
# 4. CORRUPTION HANDLING. None of it may stop the executor, and none of it
#    may lose a record that is still on disk.
# =====================================================================

def test_a_partial_final_line_is_not_a_record_and_is_overwritten(tmp_path):
    """A crash mid-append. The half-written record was never acknowledged;
    the complete ones before it are intact and the next append replaces it."""
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    for i in range(3):
        led.state["finalized"]["w%d" % i] = record("w%d" % i)
    led.save()
    good = os.path.getsize(p + ".finalized.jsonl")
    with open(p + ".finalized.jsonl", "a") as fh:
        fh.write('{"market": "w9", "reco')               # died here
    os.unlink(p + ".finalized.idx")

    reopened = TS.ShadowLedger(p)
    assert len(reopened.state["finalized"]) == 3
    assert "w9" not in reopened.state["finalized"]
    assert reopened.records.truncated_tail_bytes > 0
    reopened.state["finalized"]["w4"] = record("w4")
    reopened.save()
    assert reopened.last_save_error is None
    assert os.path.getsize(p + ".finalized.jsonl") > good
    third = TS.ShadowLedger(p)
    assert len(third.state["finalized"]) == 4
    assert third.state["finalized"]["w4"] == record("w4")
    assert third.records.truncated_tail_bytes == 0


def test_a_corrupt_manifest_is_kept_aside_and_the_log_survives_it(tmp_path):
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    led.state["finalized"]["w1"] = record("w1")
    led.save()
    with open(p, "w") as fh:
        fh.write("{not json")

    reopened = TS.ShadowLedger(p)

    assert reopened.quarantined and os.path.exists(reopened.quarantined)
    assert open(reopened.quarantined).read() == "{not json"   # not deleted
    assert reopened.pending_slugs() == []
    assert len(reopened.state["finalized"]) == 1               # log survived
    assert reopened.state["finalized"]["w1"] == record("w1")
    assert reopened.finalize("w1", settle_up=True) is None


def test_a_corrupt_sidecar_with_no_log_starts_empty_rather_than_raising(tmp_path):
    p = str(tmp_path / "s.json")
    with open(p, "w") as fh:
        fh.write("{not json")
    led = TS.ShadowLedger(p)
    assert led.pending_slugs() == [] and led.state["finalized"] == {}


def test_an_unreadable_record_still_counts_as_scored(tmp_path):
    """The slug WAS scored. Refusing to score it twice is the half of this
    ledger that matters, so `in` stays True and the read raises."""
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    for s in ("w1", "w2"):
        led.state["finalized"][s] = record(s)
    led.save()
    off, n = led.records._index["w1"]
    with open(p + ".finalized.jsonl", "r+b") as fh:
        fh.seek(off)
        fh.write(b"@" * (n - 1))                   # same length, unparseable

    reopened = TS.ShadowLedger(p)
    assert "w1" in reopened.state["finalized"]
    with pytest.raises(KeyError):
        reopened.state["finalized"]["w1"]
    assert reopened.records.records_unreadable >= 1
    assert reopened.records.orphan_slugs() == ["w1"]
    assert reopened.finalize("w1", settle_up=True) is None     # not rescored
    assert reopened.park(window("w1"), now=9.0)["state"] == TS.FINALIZED
    assert reopened.state["finalized"]["w2"] == record("w2")   # unaffected
    r = reopened.size_report()
    assert r["integrity"] == TS.INTEGRITY_SHORT
    assert r["records_missing"] == 1


def test_a_failed_write_cannot_raise_into_the_settle_pass(tmp_path, monkeypatch):
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)

    def explode(self):
        raise OSError("volume went away")

    monkeypatch.setattr(TS.FinalizedRecords, "flush", explode)
    led.park(window("w1"), now=1.0)                # must not raise
    assert led.last_save_error == "OSError"
    scored = led.finalize("w1", settle_up=False)   # must not raise
    assert scored is not None
    assert led.size_report()["save_error"] == "OSError"
    monkeypatch.undo()
    led.save()
    assert led.last_save_error is None
    assert len(TS.ShadowLedger(p).state["finalized"]) == 1


def test_an_unwritable_index_costs_a_scan_and_never_a_record(tmp_path, monkeypatch):
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    monkeypatch.setattr(TS.FinalizedRecords, "_write_idx",
                        lambda self: (_ for _ in ()).throw(OSError("nope")))
    led.state["finalized"]["w1"] = record("w1")
    led.save()
    monkeypatch.undo()
    assert led.last_save_error is None             # the index is a cache
    assert len(log_lines(p)) == 1
    reopened = TS.ShadowLedger(p)
    assert reopened.records.index_state == "REBUILT_BY_SCAN"
    assert reopened.state["finalized"]["w1"] == record("w1")


# =====================================================================
# 5. ROLLOVER LATENCY. A save must cost the records it ADDS.
# =====================================================================

def test_a_save_writes_only_the_new_record_whatever_the_history(tmp_path):
    """The whole point. 50 records of history and 500 cost the same write."""
    sizes = {}
    for n in (50, 500):
        p = str(tmp_path / ("h%d.json" % n))
        led = TS.ShadowLedger(p)
        for i in range(n):
            led.state["finalized"]["w%04d" % i] = record("w%04d" % i, pad=1000)
        led.save()
        history = os.path.getsize(p + ".finalized.jsonl")
        led.state["finalized"]["new"] = record("new", pad=1000)
        led.save()
        sizes[n] = (led.last_save_bytes_written, history,
                    os.path.getsize(p))
    w50, h50, m50 = sizes[50]
    w500, h500, m500 = sizes[500]
    assert h500 > 9 * h50                       # the history really grew
    assert w500 - w50 < 200                     # the write did not
    assert w50 < 3000                           # one record plus a manifest
    assert m500 - m50 < 50                      # nor did the manifest


def test_two_saves_a_window_stay_well_inside_the_budget_at_production_scale(tmp_path):
    """2,294 scored windows is what production carries. v1 took 6.76 s a
    save there and 1.77 s on local disk; this asserts a margin so wide that
    it cannot be a timing flake."""
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    for i in range(2294):
        led.state["finalized"]["w%05d" % i] = record("w%05d" % i)
    led.save()
    led.park(window("fresh"), now=1.0)
    park_s = led.last_save_s
    scored = led.finalize("fresh", settle_up=False)
    assert scored is not None
    window_cost = park_s + led.last_save_s
    assert window_cost < TS.SAVE_BUDGET_S, window_cost
    r = led.size_report()
    assert r["over_budget"] is False and r["slow_saves"] == 0
    assert r["finalized"] == 2295

    t0 = time.monotonic()
    reopened = TS.ShadowLedger(p)
    assert time.monotonic() - t0 < 1.0             # the index, not 293 MB
    assert len(reopened.state["finalized"]) == 2295


def test_a_slow_save_is_reported_and_never_enforced(tmp_path, monkeypatch):
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    monkeypatch.setattr(TS, "SAVE_BUDGET_S", -1.0)
    led.state["finalized"]["w1"] = record("w1")
    led.save()
    assert led.last_save_error is None             # it still wrote
    assert led.slow_saves == 1
    assert led.size_report()["over_budget"] is True
    monkeypatch.undo()
    assert len(TS.ShadowLedger(p).state["finalized"]) == 1


# =====================================================================
# 6. THE CONTRACT THE RUNNER DEPENDS ON.
# =====================================================================

def test_the_size_report_keeps_every_field_the_runner_emitted(tmp_path):
    led = TS.ShadowLedger(str(tmp_path / "s.json"))
    r = led.size_report()
    for k in ("finalized", "pending", "bytes", "last_save_s",
              "saves_this_process", "max_pending", "labels"):
        assert k in r, k
    for k in ("log_bytes", "bytes_written", "save_error", "slow_saves",
              "over_budget", "migration_state", "index_state",
              "records_unreadable", "duplicates_dropped", "format"):
        assert k in r, k
    json.dumps(r)                                  # the emit must not raise


def test_the_ledger_is_reached_only_from_the_shadow_paths():
    """It holds modelled numbers. If a gate, a limit or an accounting path
    ever reads it, that is the defect -- not the latency."""
    import re
    src = inspect.getsource(RUN).split("\n")
    cur = None
    sites = {}
    for line in src:
        m = re.match(r"    def (\w+)", line)
        if m:
            cur = m.group(1)
        if "two_sided_ledger" in line:
            sites.setdefault(cur, 0)
            sites[cur] += 1
    assert set(sites) == {"__init__", "two_sided_close", "two_sided_settle"}


def test_the_ledgers_own_writers_still_do_not_reduce():
    """park and finalize are the durable copy; neither may bound a report."""
    for fn in (TS.ShadowLedger.park, TS.ShadowLedger.finalize):
        assert "bounded_for_log" not in inspect.getsource(fn)


def test_the_whole_report_is_still_what_gets_stored(tmp_path):
    """The log is a format change, not a retention change. Nothing is
    dropped, summarised or rolled up on the way to disk."""
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    big = window("w1")
    big["cells"][0]["noise"] = "x" * 20000
    led.park(big, now=1.0)
    assert len(led.state["pending"]["w1"]["cells"][0]["noise"]) == 20000
    led.finalize("w1", settle_up=False)
    stored = TS.ShadowLedger(p).state["finalized"]["w1"]
    assert len(stored["cells"][0]["noise"]) == 20000
    assert stored["cells"][0]["residual_pnl_modelled"] == "-5.90"


# =====================================================================
# 7. THE INDEX IS A CACHE, AND A DAMAGED CACHE MUST NOT HIDE A RECORD.
#
# Raised in review of PR #210: "the offset index is a cache, but its
# recovery behavior needs particular scrutiny if the index exists and
# points to incorrect data. We should verify that a damaged index cannot
# silently hide historical records."
#
# It could. Two holes, both reproduced on the first implementation:
#
#   A  drop ONE entry from the middle and the index still ENDS at the right
#      byte, so it was accepted -- `len` reported 5 of 6 and the dropped
#      slug read as absent, which would let an already-scored window be
#      parked and scored a SECOND time.
#   B  swap two entries' slugs and the index still tiles the log and still
#      has the right count -- so `records["w1"]` returned w4's record.
#
# Both are closed by four layers, and every one of them is exercised below:
#   1. the index must TILE the log, so a gap or an overlap refuses it;
#   2. a bounded sample of entries is verified against the log's own bytes;
#   3. every read checks the record's own slug and heals the index if it
#      disagrees, which catches what the sample misses;
#   4. the manifest's count is an independent witness, and a shortfall is
#      NAMED rather than passed off as a smaller history.
#
# The invariant: the log decides WHERE a record is, the slug set decides
# WHICH windows were scored, and no damaged index can make a scored window
# look unscored.
# =====================================================================

def idx_rows(path: str) -> list:
    with open(path + ".finalized.idx") as fh:
        return [ln.split("\t") for ln in fh.read().split("\n") if ln]


def write_idx(path: str, rows: list) -> None:
    with open(path + ".finalized.idx", "w") as fh:
        fh.write("".join("\t".join(r) + "\n" for r in rows))


def seeded(path: str, n: int, pad: int = 0) -> TS.ShadowLedger:
    led = TS.ShadowLedger(path)
    for i in range(n):
        led.state["finalized"]["w%03d" % i] = record("w%03d" % i, pad=pad)
    led.save()
    return led


def test_an_index_missing_a_middle_entry_cannot_hide_the_record(tmp_path):
    """Hole A. The index still ends at the right byte, and before the fix
    that was enough for it to be trusted."""
    p = str(tmp_path / "s.json")
    seeded(p, 6)
    rows = idx_rows(p)
    write_idx(p, rows[:2] + rows[3:])              # w002's entry, gone

    led = TS.ShadowLedger(p)

    assert led.records.index_state == "REBUILT_BY_SCAN"
    assert led.records.index_refusals == 1
    assert led.records.readable_count() == 6       # not 5
    assert "w002" in led.state["finalized"]
    assert led.state["finalized"]["w002"] == record("w002")
    # and the window it would have hidden still cannot be scored twice
    assert led.park(window("w002"), now=1.0)["state"] == TS.FINALIZED
    assert led.finalize("w002", settle_up=True) is None
    assert len(log_lines(p)) == 6                  # nothing re-appended
    r = led.size_report()
    assert r["integrity"] == TS.INTEGRITY_OK
    assert r["records_missing"] == 0 and r["orphan_slugs"] == 0


def test_an_index_that_names_the_wrong_bytes_never_returns_another_window(tmp_path):
    """Hole B. Two slugs swapped: the index tiles the log and the count is
    right, so only the bytes themselves can give it away."""
    p = str(tmp_path / "s.json")
    seeded(p, 6, pad=40)
    rows = {r[0]: r for r in idx_rows(p)}
    a, b = rows["w001"], rows["w004"]
    a[1], b[1] = b[1], a[1]
    a[2], b[2] = b[2], a[2]
    write_idx(p, list(rows.values()))

    led = TS.ShadowLedger(p)

    assert led.records.index_spot_mismatches > 0   # caught by the sample
    assert led.records.index_state == "REBUILT_AFTER_SPOT_CHECK_FAILED"
    for s in ("w001", "w004"):
        got = led.state["finalized"][s]
        assert got["market"] == s                  # never another window
        assert got == record(s, pad=40)
    # the damage was written back out, so it does not recur
    again = TS.ShadowLedger(p)
    assert again.records.index_state == "READ_FROM_INDEX"
    assert again.records.index_spot_mismatches == 0
    assert again.state["finalized"]["w001"]["market"] == "w001"


def test_a_swap_the_sample_misses_is_caught_on_the_read_and_heals(tmp_path):
    """Layer 3. 200 records: the spot check samples 16 of them, so a swap
    between two unsampled neighbours reaches a read. The read is where it
    stops -- and healing fixes the whole index, not just this pair."""
    p = str(tmp_path / "s.json")
    seeded(p, 200)
    rows = {r[0]: r for r in idx_rows(p)}
    a, b = rows["w005"], rows["w006"]
    a[1], b[1] = b[1], a[1]
    a[2], b[2] = b[2], a[2]
    write_idx(p, list(rows.values()))

    led = TS.ShadowLedger(p)
    assert led.records.index_state == "READ_FROM_INDEX"   # it got through
    assert led.records.index_spot_mismatches == 0

    got = led.state["finalized"]["w005"]                 # the read catches it

    assert got["market"] == "w005" and got == record("w005")
    assert led.records.slug_mismatches == 1
    assert led.records.index_state == "REBUILT_AFTER_SLUG_MISMATCH"
    assert led.state["finalized"]["w006"] == record("w006")
    assert led.records.readable_count() == 200
    assert TS.ShadowLedger(p).records.index_state == "READ_FROM_INDEX"


def test_an_overlapping_index_entry_is_refused(tmp_path):
    p = str(tmp_path / "s.json")
    seeded(p, 5)
    rows = idx_rows(p)
    rows[2][1] = str(int(rows[2][1]) - 1)          # overlaps its neighbour
    rows[2][2] = str(int(rows[2][2]) + 1)
    write_idx(p, rows)
    led = TS.ShadowLedger(p)
    assert led.records.index_state == "REBUILT_BY_SCAN"
    assert led.records.readable_count() == 5
    assert led.state["finalized"]["w002"] == record("w002")


def test_an_index_naming_a_window_the_log_does_not_hold_is_reported(tmp_path):
    """The mirror image: the cache claims a window the log cannot show.

    It is not believed -- no record is invented -- and it is not discarded
    either, because a slug recorded as scored is the evidence that stops a
    second scoring. It becomes an orphan, and the discrepancy is NAMED.
    """
    p = str(tmp_path / "s.json")
    seeded(p, 4)
    rows = idx_rows(p)
    last_end = int(rows[-1][1]) + int(rows[-1][2])
    write_idx(p, rows + [["phantom", str(last_end), "120"]])

    led = TS.ShadowLedger(p)

    assert led.records.index_state == TS.DIGEST_REBUILT
    assert led.digest_state == TS.DIGEST_STALE   # the witness disagrees too
    assert led.records.readable_count() == 4
    assert led.records.orphan_slugs() == ["phantom"]
    assert "phantom" in led.state["finalized"]
    with pytest.raises(KeyError):
        led.state["finalized"]["phantom"]
    r = led.size_report()
    assert r["integrity"] == TS.INTEGRITY_SHORT    # never silent
    assert r["records_missing"] == 1
    assert r["readable_records"] == 4


def test_deleting_the_whole_index_loses_nothing_and_says_so(tmp_path):
    p = str(tmp_path / "s.json")
    seeded(p, 8)
    os.unlink(p + ".finalized.idx")
    led = TS.ShadowLedger(p)
    assert led.records.index_state == "REBUILT_BY_SCAN"
    assert led.records.readable_count() == 8
    assert led.records.orphan_slugs() == []
    assert led.size_report()["integrity"] == TS.INTEGRITY_OK
    for i in range(8):
        assert led.state["finalized"]["w%03d" % i] == record("w%03d" % i)


def test_a_truncated_index_file_is_refused_not_believed(tmp_path):
    """Half an index file ends mid-line and does not tile the log."""
    p = str(tmp_path / "s.json")
    seeded(p, 6)
    raw = open(p + ".finalized.idx").read()
    with open(p + ".finalized.idx", "w") as fh:
        fh.write(raw[:len(raw) // 2])
    led = TS.ShadowLedger(p)
    assert led.records.index_state == "REBUILT_BY_SCAN"
    assert led.records.readable_count() == 6
    assert led.size_report()["integrity"] == TS.INTEGRITY_OK


def test_the_fast_slug_reader_agrees_with_a_full_parse(tmp_path):
    """Indexing reads the slug off the front of the line instead of parsing
    a 128 KB body. If the two ever disagreed, the index would mis-name a
    record -- so they are pinned against each other, escapes included."""
    p = str(tmp_path / "s.json")
    led = TS.ShadowLedger(p)
    odd = ['plain-slug', 'has "quotes"', 'back\\slash', 'uniçode',
           'tab\there', 'new\nline', '{"market": "spoof"}']
    for s in odd:
        led.state["finalized"][s] = record(s)
    led.save()
    with open(p + ".finalized.jsonl", "rb") as fh:
        for raw in fh:
            assert led.records._slug_of(raw) == json.loads(raw)["market"]
    reopened = TS.ShadowLedger(p)
    assert reopened.records.readable_count() == len(odd)
    for s in odd:
        assert reopened.state["finalized"][s]["market"] == s


def test_an_entry_that_renames_a_slug_is_caught_by_the_manifest_digest(tmp_path):
    """THE ONE CASE TILING AND THE SAMPLE CANNOT SEE between them.

    Rename a single unsampled entry and the geometry is untouched, the count
    is untouched, and the 16-entry sample does not look at it -- so without
    an outside witness the window it renamed would stay hidden until
    something happened to read that slug, and until then it could be parked
    and scored a second time. The manifest is a different file written in the
    same save, so its digest of the slug set is that witness.
    """
    p = str(tmp_path / "s.json")
    seeded(p, 200)
    rows = idx_rows(p)
    hidden = rows[5][0]
    assert hidden == "w005"
    rows[5][0] = "renamed"                         # geometry untouched
    write_idx(p, rows)

    led = TS.ShadowLedger(p)

    assert led.records.index_state == TS.DIGEST_REBUILT
    assert led.records.index_refusals == 1
    assert "w005" in led.state["finalized"]        # NOT hidden
    assert led.state["finalized"]["w005"] == record("w005")
    assert led.records.readable_count() == 200
    # and the window it would have hidden still cannot be scored twice
    assert led.park(window("w005"), now=1.0)["state"] == TS.FINALIZED
    assert led.finalize("w005", settle_up=True) is None
    # the invented name is kept as an orphan and reported, never believed
    assert led.records.orphan_slugs() == ["renamed"]
    with pytest.raises(KeyError):
        led.state["finalized"]["renamed"]
    r = led.size_report()
    assert r["integrity"] == TS.INTEGRITY_SHORT
    assert r["records_missing"] == 1
    assert r["digest_state"] == TS.DIGEST_STALE


def test_the_digest_witnesses_the_slug_set_and_not_the_geometry(tmp_path):
    """It must not fire on a healthy ledger, or it would rescan every start."""
    p = str(tmp_path / "s.json")
    led = seeded(p, 30)
    assert led.records.slug_digest() == json.loads(open(p).read())["finalized_digest"]
    for _ in range(3):
        again = TS.ShadowLedger(p)
        assert again.digest_state == TS.DIGEST_AGREES
        assert again.records.index_state == "READ_FROM_INDEX"
        assert again.records.index_refusals == 0
        assert again.size_report()["integrity"] == TS.INTEGRITY_OK
    # rewriting the index in a different ORDER is not a change of slug set
    rows = idx_rows(p)
    write_idx(p, list(reversed(rows)))
    quiet = TS.ShadowLedger(p)
    assert quiet.digest_state == TS.DIGEST_AGREES
    assert quiet.records.index_state == "READ_FROM_INDEX"


def test_the_v1_backup_is_byte_exact_and_never_clobbered(tmp_path):
    """It is the rollback target, so it must be what was there -- and a
    later migration must not overwrite it with something thinner."""
    p = str(tmp_path / "s.json")
    recs = {"w%d" % i: record("w%d" % i, pad=120) for i in range(6)}
    v1_document(p, recs, pending={"p1": {"market": "p1", "parked_at": 2.0}})
    original = open(p).read()

    led = TS.ShadowLedger(p)

    backup = p + TS.V1_BACKUP_SUFFIX
    assert open(backup).read() == original         # byte for byte
    assert led.pending_slugs() == ["p1"]
    # a second v1 document appearing at `path` must not take the backup's place
    v1_document(p, {"later": record("later")})
    second = TS.ShadowLedger(p)
    assert second.migration_state == TS.MIGRATED
    assert open(backup).read() == original         # still the FIRST one
    assert len(second.state["finalized"]) == 7


def test_the_backup_is_a_convenience_and_not_load_bearing(tmp_path):
    p = str(tmp_path / "s.json")
    v1_document(p, {"w%d" % i: record("w%d" % i) for i in range(5)})
    TS.ShadowLedger(p)
    os.unlink(p + TS.V1_BACKUP_SUFFIX)
    led = TS.ShadowLedger(p)
    assert len(led.state["finalized"]) == 5
    assert led.size_report()["integrity"] == TS.INTEGRITY_OK
    assert led.v1_backup is None                   # nothing to report
