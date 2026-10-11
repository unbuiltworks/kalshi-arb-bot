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
        "real_halt_latch: read the REAL committed HALT file rather than the "
        "suite's non-existent stand-in (for tests that assert the halt state)")
    config.addinivalue_line(
        "markers",
        "real_mandate_env: do NOT inject the permissive suite mandate; the "
        "test supplies its own (for tests of the mandate itself)")


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


@pytest.fixture(autouse=True)
def _halt_latch_is_not_the_repo_one(monkeypatch, request):
    """The committed HALT file stops the service -- including in tests.

    `analysis/l5/authorization/HALT` exists right now because the R1 probe is
    halted, and `shutdown_requested()` reads it. That is the point in
    production: `run_campaign` enters `safe_shutdown` and then `shutdown_idle`,
    which DELIBERATELY NEVER RETURNS (a halted process that exits reads as a
    crash, and the platform's repair for a crash is to roll back to the
    previous, possibly ARMED, build).

    In the suite that same behaviour hangs every campaign test forever. So the
    latch is pointed at a path that does not exist, and the tests that exist to
    check the real halt opt back in with @pytest.mark.real_halt_latch.

    This is a harness fixture, not a bypass: it cannot affect production, and
    `test_live_mandate_deployment.py` still asserts the real file is present.
    """
    from src import pmus_l5_continuous_session as cs
    if request.node.get_closest_marker("real_halt_latch"):
        return
    monkeypatch.setattr(cs, "HALT_FILE", tmp_halt_path(request))
    monkeypatch.delenv(cs.SHUTDOWN_ENV, raising=False)


def tmp_halt_path(request):
    from pathlib import Path
    return Path(str(request.node.name)) / "no-such-halt-file"


@pytest.fixture(autouse=True)
def _suite_runs_under_a_test_mandate(monkeypatch, request):
    """Live execution now requires a mandate; the pre-existing suite predates it.

    `run_campaign` refuses to arm without one, so every armed-campaign test
    would otherwise stop at NO_MANDATE and assert nothing about the campaign it
    was written to exercise. Those tests are about selection, resume, recovery
    and rung accounting -- not about authorization -- so they get a permissive
    test mandate and go on testing what they were written to test.

    THE MANDATE'S OWN BEHAVIOUR IS NOT TESTED THROUGH THIS. It is deliberately
    generous, so it can never be what makes a mandate test pass:
    tests/test_live_mandate_*.py build their own mandates and opt out with
    @pytest.mark.real_mandate_env. If this fixture ever starts being the thing
    that keeps a spending test green, that test is measuring the fixture.
    """
    if request.node.get_closest_marker("real_mandate_env"):
        return
    import json as _json
    monkeypatch.setenv("PMUS_LIVE_MANDATE", _json.dumps({
        "authorization_id": "TEST-SUITE-MANDATE",
        "authorized_at": "2026-01-01T00:00:00Z",
        # a real value: an EMPTY service is correctly not a valid mandate, and
        # PMUS_SERVICE_ROLE is unset in the suite so this matches everything
        "service": "test-suite", "strategy": "test", "strategy_version": "test",
        "max_markets": 1000, "max_total_markets": 1000,
        "max_spend_per_market": "1000000", "max_total_spend": "1000000",
        "max_residual": "1000000", "max_daily_realized_loss": "1000000",
        "expiry": "2999-01-01T00:00:00Z"}))
