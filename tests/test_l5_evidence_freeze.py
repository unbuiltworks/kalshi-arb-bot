"""Freezing Probe 08's two artifacts, and proving they stayed frozen.

The window left exactly two files on the volume and its report died before
it printed, so those files are the only record of what it did. Recovery read
and wrote the same journal and reconciled the same inventory in place. These
tests hold the line that reading evidence must not change it -- and that the
report must MEASURE that rather than assert it.
"""
from __future__ import annotations

import json

import pytest

from src import l5_evidence_freeze as FZ
import scripts.l5_pmus_live_oneshot as ONE

MARKET = "cpc-btc-updown-15m-2026-09-26-1130z"
ACTION = ('{"kind": "action", "market": "%s", "action_key": "a1",'
          ' "L5_NATIVE": {"side": "DOWN"}, "PMUS_TRANSLATION": {},'
          ' "INVENTORY_CLASS": {"inventory_class": "REDUCE_RESIDUAL",'
          ' "residual_before": "2.25"}}\n' % MARKET)


# -------------------------------------------------------------- hashing

def test_a_missing_file_is_recorded_as_absent_not_raised(tmp_path):
    rec = FZ.sha256_file(str(tmp_path / "nope.json"))
    assert rec["present"] is False and "sha256" not in rec


def test_a_digest_is_stable_and_size_is_carried(tmp_path):
    f = tmp_path / "a.jsonl"
    f.write_text(ACTION)
    a, b = FZ.sha256_file(str(f)), FZ.sha256_file(str(f))
    assert a["sha256"] == b["sha256"]
    assert a["bytes"] == len(ACTION.encode())


def test_freeze_records_every_named_path_including_absent_ones(tmp_path):
    f = tmp_path / "a.jsonl"
    f.write_text(ACTION)
    m = FZ.freeze_originals([str(f), str(tmp_path / "gone.json"), ""])
    assert [r["present"] for r in m] == [True, False]


# ---------------------------------------------------------- staged copy

def test_a_copy_is_byte_identical_and_the_source_is_untouched(tmp_path):
    src, dst = tmp_path / "o.json", tmp_path / "w.json"
    src.write_text('{"positions": {}}')
    before = FZ.sha256_file(str(src))["sha256"]
    out = FZ.stage_copy(str(src), str(dst))
    assert out["copied"] and out["identical"]
    assert dst.read_bytes() == src.read_bytes()
    assert FZ.sha256_file(str(src))["sha256"] == before


def test_an_existing_destination_is_refused_not_overwritten(tmp_path):
    src, dst = tmp_path / "o.json", tmp_path / "w.json"
    src.write_text("A")
    dst.write_text("B")
    out = FZ.stage_copy(str(src), str(dst))
    assert out["copied"] is False and out["reason"] == "DESTINATION_EXISTS"
    assert dst.read_text() == "B", "a rerun silently reused a mutated copy"


def test_an_absent_source_is_reported_not_raised(tmp_path):
    out = FZ.stage_copy(str(tmp_path / "no.json"), str(tmp_path / "w.json"))
    assert out["copied"] is False and out["reason"] == "SOURCE_ABSENT"


# ------------------------------------------------------ the actual point

def test_verify_passes_when_nothing_moved(tmp_path):
    f = tmp_path / "a.jsonl"
    f.write_text(ACTION)
    m = FZ.freeze_originals([str(f)])
    assert FZ.verify_unchanged(m)["all_unchanged"] is True


def test_verify_CATCHES_a_single_appended_byte(tmp_path):
    """Appending one row is exactly what recovery used to do."""
    f = tmp_path / "a.jsonl"
    f.write_text(ACTION)
    m = FZ.freeze_originals([str(f)])
    with open(f, "a") as fh:
        fh.write('{"kind": "recovery"}\n')
    v = FZ.verify_unchanged(m)
    assert v["all_unchanged"] is False
    assert v["drift"][0]["before"] != v["drift"][0]["after"]


def test_verify_catches_a_deletion(tmp_path):
    f = tmp_path / "a.jsonl"
    f.write_text(ACTION)
    m = FZ.freeze_originals([str(f)])
    f.unlink()
    assert FZ.verify_unchanged(m)["all_unchanged"] is False


# ------------------------------------------- end to end through recover()

def test_recovery_leaves_both_originals_byte_identical(tmp_path):
    journal = tmp_path / "l5_probe08b_journal.jsonl"
    inventory = tmp_path / "l5_probe08b_inventory.json"
    journal.write_text(ACTION)
    inventory.write_text(json.dumps(
        {"positions": {"%s|DOWN" % MARKET: "2.25"}, "live_orders": {}}))
    j0, i0 = journal.read_bytes(), inventory.read_bytes()

    out = ONE.recover(http=object(),
                      state_path=str(tmp_path / "recovery_state.json"),
                      journal_path=str(tmp_path / "recovery.jsonl"),
                      read_path=str(journal),
                      state_source=str(inventory),
                      market=MARKET)

    assert journal.read_bytes() == j0, "the journal was modified"
    assert inventory.read_bytes() == i0, "the inventory was modified"
    assert out["evidence"]["verified"]["all_unchanged"] is True
    assert out["evidence"]["staged_state"]["copied"] is True


def test_the_working_copy_carries_the_original_state(tmp_path):
    journal = tmp_path / "j.jsonl"
    inventory = tmp_path / "inv.json"
    journal.write_text(ACTION)
    inventory.write_text(json.dumps(
        {"positions": {"%s|DOWN" % MARKET: "2.25"}, "live_orders": {}}))
    work = tmp_path / "work.json"
    ONE.recover(http=object(), state_path=str(work),
                journal_path=str(tmp_path / "r.jsonl"),
                read_path=str(journal), state_source=str(inventory),
                market=MARKET)
    assert json.loads(work.read_text())["positions"] == {
        "%s|DOWN" % MARKET: "2.25"}


def test_the_report_names_both_paths_so_a_silent_in_place_run_is_visible(
        tmp_path):
    journal = tmp_path / "j.jsonl"
    journal.write_text(ACTION)
    out = ONE.recover(http=object(), state_path=str(tmp_path / "s.json"),
                      journal_path=str(tmp_path / "r.jsonl"),
                      read_path=str(journal), market=MARKET)
    assert out["journal"]["read_only_source"] is True
    assert out["journal"]["written_to"].endswith("r.jsonl")


def test_the_action_class_survives_extraction(tmp_path):
    journal = tmp_path / "j.jsonl"
    journal.write_text(ACTION)
    out = ONE.recover(http=object(), state_path=str(tmp_path / "s.json"),
                      journal_path=str(tmp_path / "r.jsonl"),
                      read_path=str(journal), market=MARKET)
    assert out["action_table"][0]["inventory_class"] == "REDUCE_RESIDUAL"
