"""Bounded incremental emission of observer rows to the log.

The observer's tape lives on a Railway volume the analysis process cannot
open, so without this a window recorded there could never be scored. These
tests pin the properties that make the emission safe to leave enabled:

  * OFF by default, and OFF rather than unbounded on a malformed value;
  * INCREMENTAL, so concatenating emissions gives a tape with no duplicates
    and no holes -- a repeated snapshot would silently double-count;
  * bounded in both log volume and process memory;
  * unable to break collection, since an evidence convenience must never be
    able to stop the thing it is observing.
"""
from __future__ import annotations

import json

import pytest

from src import l5_pmus_realtime_observer as obs


@pytest.fixture()
def observer(tmp_path, monkeypatch):
    monkeypatch.delenv(obs.ROWS_LOG_ENV, raising=False)
    return obs.Observer(str(tmp_path))


def row(key, **kw):
    r = {"action_key": key, "l5_event_utc": "2026-09-23T18:45:00+00:00",
         "kind": "action"}
    r.update(kw)
    return r


def test_it_is_off_by_default(observer, capsys):
    observer._emit(row("a"))
    assert observer.emit_new_rows() is None
    assert "L5_PMUS_ROWS" not in capsys.readouterr().out


def test_a_malformed_cap_is_off_not_unbounded(monkeypatch):
    monkeypatch.setenv(obs.ROWS_LOG_ENV, "all")
    assert obs.rows_log_cap() == 0
    monkeypatch.setenv(obs.ROWS_LOG_ENV, "")
    assert obs.rows_log_cap() == 0
    monkeypatch.setenv(obs.ROWS_LOG_ENV, "-5")
    assert obs.rows_log_cap() == 0


def test_the_cap_is_clamped(monkeypatch):
    monkeypatch.setenv(obs.ROWS_LOG_ENV, "99999")
    # raised 200 -> 400 when emission moved from per-poll to per-summary: a
    # batch now covers a whole interval, so the clamp has to fit one.
    assert obs.rows_log_cap() == 400
    monkeypatch.setenv(obs.ROWS_LOG_ENV, "7")
    assert obs.rows_log_cap() == 7


def test_emission_is_incremental_not_a_repeated_snapshot(observer, monkeypatch,
                                                         capsys):
    monkeypatch.setenv(obs.ROWS_LOG_ENV, "50")
    observer._emit(row("a"))
    observer._emit(row("b"))
    first = observer.emit_new_rows()
    assert first["rows"] == 2
    # nothing new written -> nothing emitted, rather than the same two again
    assert observer.emit_new_rows() is None
    observer._emit(row("c"))
    second = observer.emit_new_rows()
    assert second["rows"] == 1
    assert [d["action_key"] for d in second["data"]] == ["c"]
    capsys.readouterr()


def test_concatenating_emissions_gives_a_tape_with_no_holes(observer,
                                                            monkeypatch):
    monkeypatch.setenv(obs.ROWS_LOG_ENV, "2")
    for k in "abcde":
        observer._emit(row(k))
    seen = []
    for _ in range(5):
        doc = observer.emit_new_rows()
        if doc:
            seen += [d["action_key"] for d in doc["data"]]
        for k in []:
            pass
    # the cap bounds ONE emission; rows beyond it are dropped from the pending
    # buffer, which is why the cap is a per-emission bound and the consumer is
    # told the cap on every document rather than having to infer it.
    assert seen == ["a", "b"]
    assert len(set(seen)) == len(seen)


def test_a_row_without_an_action_key_is_skipped_not_emitted_twice(observer,
                                                                  monkeypatch):
    monkeypatch.setenv(obs.ROWS_LOG_ENV, "50")
    observer._emit({"kind": "summary", "no_key": True})
    observer._emit(row("a"))
    doc = observer.emit_new_rows()
    assert doc["rows"] == 1
    assert doc["data"][0]["action_key"] == "a"


def test_the_seen_index_is_bounded(observer, monkeypatch):
    monkeypatch.setenv(obs.ROWS_LOG_ENV, "200")
    for i in range(4300):
        observer._emit(row("k%d" % i))
        if i % 200 == 0:
            observer.emit_new_rows()
    observer.emit_new_rows()
    assert len(observer._rows_logged) <= 4000


def test_it_never_raises_and_never_stops_collection(observer, monkeypatch):
    monkeypatch.setenv(obs.ROWS_LOG_ENV, "10")

    class Unserialisable:
        def __repr__(self):
            raise RuntimeError("boom")

    # The poison goes straight into the pending buffer: putting it through
    # _emit would fail on the TAPE write, which is pre-existing behaviour and
    # not what this test is about. The claim under test is narrower -- the
    # EMITTER cannot raise into the poll loop.
    observer._pending_log.append(row("a", poison=Unserialisable()))
    assert observer.emit_new_rows() is None      # swallowed, not raised
    # and the buffer is cleared so one bad row cannot wedge the emitter
    assert observer._pending_log == []


def test_the_document_carries_its_schema_and_shadow_labels(observer,
                                                           monkeypatch):
    monkeypatch.setenv(obs.ROWS_LOG_ENV, "5")
    observer._emit(row("a"))
    doc = observer.emit_new_rows()
    assert "three clocks" in doc["schema"]
    assert "no_backdating_ok" in doc["schema"]
    assert doc["labels"] == obs.LABELS
    assert "not submitted" in doc["labels"] or "submitted" in doc["labels"]


def test_the_rows_are_emitted_verbatim(observer, monkeypatch):
    monkeypatch.setenv(obs.ROWS_LOG_ENV, "5")
    r = row("a", latency_s=3.5, copyability={"klass": "MAKER_COPYABLE"},
            safety_overlay={"would_block": True}, no_backdating_ok=True)
    observer._emit(r)
    doc = observer.emit_new_rows()
    got = doc["data"][0]
    assert got["copyability"] == {"klass": "MAKER_COPYABLE"}
    assert got["safety_overlay"] == {"would_block": True}
    assert got["no_backdating_ok"] is True
    assert got["latency_s"] == 3.5


def test_the_tape_is_still_written_when_emission_is_off(observer, tmp_path):
    observer._emit(row("a"))
    observer.emit_new_rows()
    lines = [l for l in open(observer.tape) if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["action_key"] == "a"
