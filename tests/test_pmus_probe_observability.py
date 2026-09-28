"""3d: monitoring failure must ABORT the probe, and staleness must be visible."""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pmus_probe_observability import (                      # noqa: E402
    KINDS, MAX_HEARTBEAT_AGE_S, REQUIRED_BEFORE_CLOSE, MonitorUnavailable,
    ProbeMonitor)

MKT = "cpc-btc-updown-15m-2026-09-28-0130z"


def _mon(tmp_path, clock=None):
    return ProbeMonitor(str(tmp_path / "j" / "probe.jsonl"), market=MKT,
                        clock=clock or (lambda: 1000.0))


def test_the_owners_whole_3d_list_is_a_required_kind():
    for k in ("DECISION", "SUBMISSION", "ACK", "FILL", "INVENTORY", "LOCK",
              "CANCEL", "COMPLETION", "RECONCILIATION", "TERMINAL"):
        assert k in KINDS, k


def test_an_unknown_kind_aborts_rather_than_being_journalled(tmp_path):
    m = _mon(tmp_path)
    with pytest.raises(MonitorUnavailable):
        m.record("DECISIN", {})            # a typo must not become a new class


def test_records_are_appended_with_monotonic_sequence_and_utc(tmp_path):
    m = _mon(tmp_path)
    for i, k in enumerate(("DECISION", "INVENTORY", "LOCK"), start=1):
        row = m.record(k, {"i": i})
        assert row["seq"] == i and row["market"] == MKT
        assert row["t"].endswith("+00:00")
    lines = open(m.path, encoding="utf-8").read().splitlines()
    assert [json.loads(l)["kind"] for l in lines] == ["DECISION", "INVENTORY",
                                                      "LOCK"]


def test_an_unwritable_journal_aborts_at_construction(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    with pytest.raises(MonitorUnavailable) as e:
        ProbeMonitor(str(blocker / "probe.jsonl"), market=MKT)
    assert "not writable" in str(e.value)


def test_an_unserialisable_payload_aborts_instead_of_being_dropped(tmp_path):
    """The recorder's handoff swallows this; here it MUST abort."""
    class Bad:
        def __repr__(self):
            raise RuntimeError("boom")
    m = _mon(tmp_path)
    with pytest.raises(MonitorUnavailable):
        m.record("DECISION", {"bad": Bad()})


def test_a_write_failure_aborts(tmp_path, monkeypatch):
    m = _mon(tmp_path)
    m.record("DECISION", {})

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr("builtins.open", boom)
    with pytest.raises(MonitorUnavailable) as e:
        m.record("INVENTORY", {})
    assert "journal write failed" in str(e.value)


def test_silence_is_not_health(tmp_path):
    m = _mon(tmp_path)
    with pytest.raises(MonitorUnavailable) as e:
        m.require_healthy()
    assert "no event has ever been recorded" in str(e.value)


def test_a_stale_heartbeat_aborts(tmp_path):
    now = {"t": 1000.0}
    m = ProbeMonitor(str(tmp_path / "p.jsonl"), market=MKT,
                     clock=lambda: now["t"])
    m.record("DECISION", {})
    now["t"] += MAX_HEARTBEAT_AGE_S - 1
    m.require_healthy()                                  # still inside the bound
    now["t"] += 2
    with pytest.raises(MonitorUnavailable) as e:
        m.require_healthy()
    assert "heartbeat" in str(e.value)


def test_a_clean_close_requires_every_kind_that_a_clean_close_implies(tmp_path):
    m = _mon(tmp_path)
    for k in REQUIRED_BEFORE_CLOSE[:-1]:
        m.record(k, {})
    with pytest.raises(MonitorUnavailable) as e:
        m.require_observed_end_to_end()
    assert REQUIRED_BEFORE_CLOSE[-1] in str(e.value)
    m.record(REQUIRED_BEFORE_CLOSE[-1], {})
    m.require_observed_end_to_end()


def test_a_probe_that_never_filled_can_still_close_cleanly(tmp_path):
    """No fill is a legitimate outcome; it must not look like blindness."""
    m = _mon(tmp_path)
    for k in REQUIRED_BEFORE_CLOSE:
        m.record(k, {})
    m.require_observed_end_to_end()
    assert m.seen["FILL"] == 0 and m.seen["ACK"] == 0


def test_the_summary_exposes_counts_and_staleness(tmp_path):
    m = _mon(tmp_path)
    m.record("DECISION", {})
    s = m.summary()
    assert s["events"] == 1 and s["by_kind"]["DECISION"] == 1
    assert s["heartbeat_age_s"] is not None and s["market"] == MKT
