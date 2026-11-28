"""The complement-requote switch, surfaced in the heartbeat.

WHY THIS EXISTS. `maybe_completion_requote` is called on every tick --
`L5_LIVE_TICK_STAGES` shows the stage at 0.000s -- so the tick loop looks
healthy whether or not the path does anything. `completion_requote_enabled()`
reads `L5_COMPLETION_REQUOTE` and defaults to OFF, and nothing in the
heartbeat said so. The only way to tell was to notice that no requote ever
fired, which is an inference drawn from an absence.

THAT IS EXACTLY HOW THE BOOK-COMPLETION SWITCH STAYED UNEXAMINED, and it is
the same switch class: the one deciding whether a stranded leg's complement
may be repriced toward the touch at all. A switch whose state can only be
inferred from missing telemetry is a switch that will be misread, so it is
reported directly.

OBSERVED, NOT GRADED, for the same reason `book_completion_enabled` is:
grading an activation switch makes the verifier fail at the moment activation
succeeds. This row changes no gate, moves no limit and cannot refuse
anything.

NOT A PROMOTION. Enabling the path is a separate owner decision. On the
19-window replay an ORACLE requote -- instant, always successful, no queue --
improved the holdout from -2.53 to -1.67, but that is an upper bound the
available tapes cannot validate: the measured cancel round-trip is 1.02s
against a 4.4s median tape interval, so a whole cancel-and-replace cycle
fits inside one tick and is invisible.
"""
from __future__ import annotations

import pytest

from src import l5_runtime_config_verify as V
from src import l5_pmus_live_runner as R


def _observed(out):
    return {r.get("name"): r.get("effective")
            for r in (out.get("observed_not_graded") or [])}


def test_the_switch_defaults_to_off(monkeypatch):
    monkeypatch.delenv(R.COMPLETION_REQUOTE_ENV, raising=False)
    assert R.completion_requote_enabled() is False


@pytest.mark.parametrize("val", ["1", "YES", "yes", "TRUE", "true", "ON", "on"])
def test_the_accepted_truthy_spellings(monkeypatch, val):
    monkeypatch.setenv(R.COMPLETION_REQUOTE_ENV, val)
    assert R.completion_requote_enabled() is True


@pytest.mark.parametrize("val", ["", " ", "0", "no", "off", "maybe", "Y"])
def test_everything_else_is_off(monkeypatch, val):
    monkeypatch.setenv(R.COMPLETION_REQUOTE_ENV, val)
    assert R.completion_requote_enabled() is False


def test_the_row_is_reported_and_reflects_the_switch(monkeypatch):
    monkeypatch.delenv(R.COMPLETION_REQUOTE_ENV, raising=False)
    off = _observed(V.verify())
    assert "completion_requote_enabled" in off, (
        "the switch must be reported directly, not inferred from an absence")
    assert off["completion_requote_enabled"] is False

    monkeypatch.setenv(R.COMPLETION_REQUOTE_ENV, "YES")
    on = _observed(V.verify())
    assert on["completion_requote_enabled"] is True


def test_it_names_the_accessor_the_money_path_calls(monkeypatch):
    monkeypatch.delenv(R.COMPLETION_REQUOTE_ENV, raising=False)
    rows = {r.get("name"): r for r in (V.verify().get("observed_not_graded") or [])}
    assert rows["completion_requote_enabled"]["source"] == (
        "R.completion_requote_enabled()")


def test_it_is_observed_and_NOT_graded(monkeypatch):
    """Grading an activation switch would fail the verifier at the instant
    activation succeeded -- the same reason book_completion_enabled is not
    graded."""
    monkeypatch.setenv(R.COMPLETION_REQUOTE_ENV, "YES")
    out = V.verify()
    graded = {r.get("name") for r in (out.get("rows") or [])}
    assert "completion_requote_enabled" not in graded
    assert "completion_requote_enabled" not in set(out.get("failed") or [])


def test_it_sits_beside_the_other_activation_switch(monkeypatch):
    """Both switches decide whether held inventory can be completed. They are
    read together or not at all."""
    monkeypatch.delenv(R.COMPLETION_REQUOTE_ENV, raising=False)
    o = _observed(V.verify())
    assert "book_completion_enabled" in o
    assert "completion_requote_enabled" in o


def test_the_row_cannot_change_a_gate(monkeypatch):
    """Reporting only: the graded verdict is identical with the switch on and
    off, so adding this row cannot alter what the verifier permits."""
    monkeypatch.delenv(R.COMPLETION_REQUOTE_ENV, raising=False)
    a = V.verify()
    monkeypatch.setenv(R.COMPLETION_REQUOTE_ENV, "YES")
    b = V.verify()
    assert a["PASS"] == b["PASS"]
    assert sorted(a.get("failed") or []) == sorted(b.get("failed") or [])
    assert a["checked"] == b["checked"]
