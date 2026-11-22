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
    config.addinivalue_line(
        "markers",
        "rn1_edge_gate: run with the RN1 marginal-edge test ON, as "
        "production does (see _rn1_marginal_test_off_for_the_existing_suite)")


@pytest.fixture(autouse=True)
def _rn1_marginal_test_off_for_the_existing_suite(monkeypatch, request):
    """OWNER 2026-11-22 (issue #251): every RN1 bid must clear a marginal
    expected-value test against the international fair value, and with no
    fair value there is no bid. That is production's default
    (PMUS_RN1_EDGE_GATE, on unless "0").

    Several hundred existing sports tests build an engine with a fake venue
    and NO international price feed and then expect it to quote: they are
    about locks, caps, the registry, reconciliation -- not about pricing.
    Under the default every one of them would see "no_fair" and no orders,
    and would be measuring the gate instead of what it was written to
    check.

    So the gate is OFF for the existing suite and the tests that exercise
    it turn it on with `@pytest.mark.rn1_edge_gate`
    (tests/test_pmus_rn1_profit_first.py), where the production default is
    also pinned from a clean interpreter."""
    from src import pmus_rn1_replica as RN1
    monkeypatch.setattr(RN1, "EDGE_GATE", bool(
        request.node.get_closest_marker("rn1_edge_gate")))


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


@pytest.fixture(autouse=True)
def _migration_close_uses_ephemeral_test_state(monkeypatch, tmp_path, request):
    """The migration adapter durably persists order intent before POST.

    Production mounts /state/pmus-spring. GitHub-hosted runners deliberately
    cannot create /state, so the two adapter-body tests must exercise the same
    persistence path in an isolated writable directory instead of depending
    on a root-owned path or on state leaked from another test. This fixture is
    scoped by node id and never changes production defaults.
    """
    if "test_migration_close.py" in request.node.nodeid:
        monkeypatch.setenv("PMUS_Q1_STATE_DIR", str(tmp_path / "pmus-q1"))
