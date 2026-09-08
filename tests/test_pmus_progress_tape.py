"""The observation channel mapping B is scored through.

The forward A/B failed on coverage, not on the translation: 147 of 165
decisions had no event-progress row at all because a held market stops the
selector, which was the only witness. These tests pin the properties that
make an independent witness trustworthy -- it records transitions, it does
not re-record them after a restart, it stays bounded, it never dies for a
venue hiccup, and it can never reach the live order path."""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from src import pmus_progress_tape as ptape


@pytest.fixture(autouse=True)
def _tape_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PMUS_PROGRESS_TAPE_DIR", str(tmp_path / "progress"))
    yield tmp_path / "progress"


NOW = 1788870000.0            # a real epoch: hourly tape parts key off it


def _event(slug, period, ended=False):
    """The venue only counts rows carrying live: true, so a fixture without
    it silently observes nothing -- the exact failure this tape exists to
    make visible."""
    return {"slug": f"ev-{slug}", "eventState": {"type": "tennis"},
            "period": period, "ended": ended, "live": True,
            "markets": [{"slug": slug, "feeCoefficient": 0.01}]}


def _getter(*events):
    def get_json(_path, timeout=15.0):
        return {"events": [dict(e) for e in events]}
    return get_json


# --------------------------------------------------------------------------

def test_transitions_are_recorded_and_stable_states_are_not_re_recorded():
    """A step function is reconstructible from its steps; re-writing an
    unchanged clock every tick would be tape with no information in it."""
    last = {}
    tbl = {"m1": {"period": "S1", "game_proximity": 0.2, "ended": False,
                  "proximity_basis": "logged", "event_slug": "ev-1"}}
    first = ptape.changed_rows(tbl, last, now=100.0)
    assert [r["slug"] for r in first] == ["m1"]
    assert first[0]["reason"] == "moved"

    same = ptape.changed_rows(tbl, last, now=130.0)
    assert same == []

    tbl["m1"]["period"] = "S2"
    tbl["m1"]["game_proximity"] = 0.45
    moved = ptape.changed_rows(tbl, last, now=160.0)
    assert len(moved) == 1 and moved[0]["game_proximity"] == 0.45


def test_a_long_unchanged_run_still_proves_the_observer_was_looking():
    """Absence of rows is not evidence of a quiet board -- it is exactly the
    shape the 4h10m probe outage took. The keepalive separates the two."""
    last = {}
    tbl = {"m1": {"period": "S1", "game_proximity": 0.2, "ended": False}}
    ptape.changed_rows(tbl, last, now=0.0)
    assert ptape.changed_rows(tbl, last, now=800.0, keepalive_s=900.0) == []
    beat = ptape.changed_rows(tbl, last, now=901.0, keepalive_s=900.0)
    assert len(beat) == 1 and beat[0]["reason"] == "keepalive"


def test_restart_does_not_re_append_transitions_the_tape_already_holds():
    last = {}
    tbl = {"m1": {"period": "S1", "game_proximity": 0.2, "ended": False}}
    ptape.append(ptape.changed_rows(tbl, last, now=NOW), now=NOW)

    reseeded = ptape.seed_last_from_tape()
    assert ptape.changed_rows(tbl, reseeded, now=NOW + 60.0) == []


def test_seeding_survives_a_corrupt_line():
    last = {}
    tbl = {"m1": {"period": "S1", "game_proximity": 0.2, "ended": False}}
    ptape.append(ptape.changed_rows(tbl, last, now=NOW), now=NOW)
    with open(ptape.tape_path(NOW), "at", encoding="utf-8") as fh:
        fh.write('{"kind": "progress", truncated\n')
    assert "m1" in ptape.seed_last_from_tape()


def test_reader_returns_the_selection_progress_shape_and_merges_with_it():
    """merge_progress is a union: the selector's rows are real observations
    and are not discarded when the tape starts covering the same market."""
    last = {}
    tbl = {"m1": {"period": "S1", "game_proximity": 0.2, "ended": False,
                  "proximity_basis": "logged_game_proximity"}}
    ptape.append(ptape.changed_rows(tbl, last, now=NOW), now=NOW)
    series = ptape.load_progress()
    assert list(series) == ["m1"]
    t, entry = series["m1"][0]
    assert t == NOW
    assert set(entry) >= {"period", "proximity", "basis", "clock"}
    assert entry["source"] == "progress_tape"

    from_selector = {"m1": [(NOW - 50.0, {"period": "S0", "proximity": 0.0,
                                          "basis": "logged", "clock": None})]}
    merged = ptape.merge_progress(from_selector, series)
    assert [r[0] for r in merged["m1"]] == [NOW - 50.0, NOW]


def test_retention_is_bounded_and_never_deletes_the_part_being_written():
    d = ptape.tape_dir()
    d.mkdir(parents=True, exist_ok=True)
    current = ptape.tape_path(NOW)
    old = d / "progress-19700101T00.jsonl"
    old.write_text("x" * 4096, encoding="utf-8")
    current.write_text("y" * 4096, encoding="utf-8")

    out = ptape.sweep_tape(budget=1024, now=NOW)
    assert old.name in out["deleted"]
    assert current.exists() and current.name not in out["deleted"]


def test_a_write_failure_alarms_instead_of_ending_the_observer(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise OSError("ENOSPC")
    monkeypatch.setattr(ptape, "open", boom, raising=False)
    monkeypatch.setattr("builtins.open", boom)
    out = ptape.append([{"kind": "progress", "slug": "m1", "t": NOW}], now=NOW)
    assert out["written"] == 0 and "error" in out
    assert "tape_write_failed" in capsys.readouterr().out


def test_a_venue_hiccup_is_a_visible_empty_outcome_not_an_exception(monkeypatch):
    def boom(_path, timeout=15.0):
        raise OSError("connection reset")
    row = ptape.tick({}, now=NOW, get_json=boom)
    assert row["markets"] == 0 and row["rows"] == 0
    assert row["diag_error"]


def test_observer_keeps_its_own_cadence_regardless_of_how_often_it_is_called():
    clock = itertools.count(NOW, 5.0)
    calls = []

    def get_json(_path, timeout=15.0):
        calls.append(1)
        return {"events": []}

    obs = ptape.Observer(tick_s=60.0, clock=lambda: next(clock),
                         get_json=get_json)
    for _ in range(13):
        obs.maybe_tick()
    # 13 calls spanning 60 s of clock -> exactly two observations
    assert len(calls) == 2


def test_an_observer_seeing_nothing_says_so(capsys):
    clock = itertools.count(NOW, 100.0)
    obs = ptape.Observer(tick_s=1.0, clock=lambda: next(clock),
                         get_json=_getter(_event("m1", "S1")))
    obs.maybe_tick()
    capsys.readouterr()
    obs.get_json = lambda *_a, **_k: {"events": []}
    for _ in range(40):
        obs.maybe_tick()
    assert "no_live_events" in capsys.readouterr().out


def test_the_module_has_no_credential_and_no_order_path():
    """Read-only as a property of the source, not a promise in a docstring.
    This tape must never be able to reach the live money path."""
    src = Path(ptape.__file__).read_text(encoding="utf-8")
    banned = ("Authorization", "api_key", "private_key", "POST", "DELETE",
              "/portfolio/", "place_order", "cancel_order")
    hits = [w for w in banned if w in src]
    assert hits == [], hits


def test_shadow_loop_hosts_the_observer_and_never_lets_it_raise_into_l5():
    """It rides the shadow loop's spare time rather than the probe: a gateway
    read per decision would put network latency on the live order path."""
    src = Path("src/l5_shadow_loop.py").read_text(encoding="utf-8")
    assert "_progress_tick" in src
    body = src.split("def _progress_tick")[1].split("\n    def ")[0]
    assert "if self.dry_run" in body          # --dry-run makes no network call
    assert body.count("except Exception") >= 2
    assert "pmus_progress_tape" in body


def test_the_ab_merges_the_tape_without_changing_how_mapping_b_is_scored():
    src = Path("src/l5_state_translation.py").read_text(encoding="utf-8")
    assert "pmus_progress_tape" in src and "merge_progress" in src
    # the pre-registered gate is untouched by widening coverage
    from src import l5_state_translation as st
    assert (st.PROMOTION_GATE["sample_minimums"]
            ["forward_decisions_with_event_progress"] == 400)
    assert st.PROMOTION_GATE["sha256_16"] == "6a474b81b397c2bb"


def test_only_usable_transitions_go_on_the_log_stream():
    """The tape lives on a volume the analysis host cannot read, so the
    transitions mapping B can use travel by log. Keepalives and unrecognised
    proximity carry nothing to reconstruct and are left off."""
    rows = [
        {"reason": "moved", "t": NOW, "slug": "m1", "game_proximity": 0.2,
         "period": "S1"},
        {"reason": "keepalive", "t": NOW, "slug": "m2", "game_proximity": 0.45,
         "period": "S2"},
        {"reason": "moved", "t": NOW, "slug": "m3", "game_proximity": None,
         "period": "MAP 2"},
    ]
    emit = ptape.emit_rows(rows)
    assert emit == [[round(NOW, 1), "m1", 0.2, "S1"]]


def test_the_log_emission_is_capped():
    rows = [{"reason": "moved", "t": NOW, "slug": f"m{i}",
             "game_proximity": 0.2, "period": "S1"} for i in range(100)]
    assert len(ptape.emit_rows(rows, cap=40)) == 40


def test_harvested_log_rows_rebuild_the_same_series_shape(tmp_path):
    """Raw stdout and the JSON-escaped form a harvested Railway log file
    carries must both reconstruct -- the harvest is the only path from the
    shadow host to the analysis."""
    raw = tmp_path / "raw.log"
    raw.write_text(
        "noise\n"
        "PMUS_PROGRESS " + json.dumps(
            {"kind": "progress_tick", "rows": 1,
             "emit": [[NOW, "m1", 0.2, "S1"], [NOW + 60, "m1", 0.45, "S2"]]})
        + "\nmore noise\n", encoding="utf-8")

    escaped = tmp_path / "harvested.txt"
    escaped.write_text(json.dumps({"deploy": [{"message":
        "PMUS_PROGRESS " + json.dumps(
            {"emit": [[NOW, "m2", 0.65, "S3"]]})}]}), encoding="utf-8")

    series = ptape.progress_from_logs([raw, escaped])
    assert [t for t, _ in series["m1"]] == [NOW, NOW + 60]
    assert series["m1"][1][1]["proximity"] == 0.45
    assert series["m2"][0][1]["proximity"] == 0.65
    assert series["m1"][0][1]["source"] == "progress_tape_log"


def test_a_missing_or_unreadable_log_is_skipped_not_fatal(tmp_path):
    assert ptape.progress_from_logs([tmp_path / "nope.log"]) == {}
    bad = tmp_path / "bad.log"
    bad.write_text("PMUS_PROGRESS {truncated", encoding="utf-8")
    assert ptape.progress_from_logs([bad]) == {}


def test_the_ab_reads_the_harvested_rows_when_the_volume_is_unreachable():
    src = Path("src/l5_state_translation.py").read_text(encoding="utf-8")
    assert "_progress_rows" in src and "progress_from_logs" in src
