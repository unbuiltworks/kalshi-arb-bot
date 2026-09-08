"""Shared fixtures. The ACTIVE ramp rung is process state set by the
campaign (OWNER 5560851070); every test starts and ends on the default
(env) rung so a rung set in one test can never leak into another."""
from __future__ import annotations

import pytest

from src import pmus_l5_ramp_ladder as ladder


@pytest.fixture(autouse=True)
def _reset_active_rung():
    ladder.set_active(None)
    yield
    ladder.set_active(None)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_event_progress: exercise src.pmus_event_progress itself "
        "(still offline -- the test injects its own get_json)")


@pytest.fixture(autouse=True)
def _no_live_event_progress(monkeypatch, request):
    """The ranker reads the venue's live-event feed for game position.
    Tests must never touch the network: stub it to the same EMPTY result a
    failed read produces, so every existing test exercises the unchanged
    fallback path. A test that wants the progress path patches it itself.
    """
    from src import pmus_event_progress as prog
    prog._CACHE.clear()
    if request.node.get_closest_marker("real_event_progress"):
        return
    monkeypatch.setattr(prog, "table",
                        lambda *a, **k: ({}, {"stubbed_in_tests": True}))


@pytest.fixture(autouse=True)
def _no_completion_extension_by_default(monkeypatch):
    """OWNER GO 5589583477 lets COMPLETE work a residual PAST the opening
    budget. 21 existing tests pin PMUS_SPRING_MAX_ACTIONS to bound a
    session to an exact number of orders, and every one of them that holds
    a residual would silently get 12 more attempts -- their assertions
    would be measuring the new budget instead of what they were written to
    check.

    So the extension is OFF for the existing suite and the tests that
    exercise it turn it on explicitly (tests/test_complete_working_loop.py).
    A default of 0 here means production's default of 12 is never reached
    by accident, and never exercised by accident either."""
    monkeypatch.setenv("PMUS_SPRING_MAX_COMPLETE_ACTIONS", "0")
