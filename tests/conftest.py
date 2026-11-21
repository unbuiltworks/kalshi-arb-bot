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


@pytest.fixture(autouse=True)
def _shared_reservation_file(tmp_path, monkeypatch):
    """Every test gets its OWN account-wide reservation file.

    `l5_shared_reservation` refuses every submission when its path is
    unset -- deliberately, because a lane that cannot reach the shared
    budget must not trade, and the per-lane caps alone cannot bound the
    account. That makes an unset path a hard stop for any test that
    expects an order to reach a venue.

    Each test gets a fresh file, so no budget leaks between tests and a
    hold taken in one cannot refuse an order in another. The fail-closed
    behaviour itself is pinned in tests/test_l5_shared_reservation.py,
    which sets the path explicitly and also covers it being unset.
    """
    monkeypatch.setenv("L5_SHARED_RESERVATION",
                       str(tmp_path / "l5_shared_reservation.json"))


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
    config.addinivalue_line(
        "markers",
        "real_gemini_socket: start the REAL Gemini websocket thread (the "
        "suite replaces its body; see _no_real_gemini_socket)")
    config.addinivalue_line(
        "markers",
        "multi_submission: the test drives SEVERAL armed submissions, each "
        "standing for a separate authorization, so the one-submission "
        "claim's experiment id rotates per call")


@pytest.fixture(autouse=True)
def _submission_claim_scope(monkeypatch, tmp_path, request):
    """Give every test its OWN experiment id and authorization ledger.

    The one-submission claim is now the last gate before the wire, so any
    test that drives an ARMED submit has to hold a claim. Two things would go
    wrong without this: the claim would be written to the production
    `DEFAULT_LEDGER` path, and a shared experiment id would make the SECOND
    armed submit anywhere in the suite refuse -- tests would start depending
    on each other's order.

    A unique id per test keeps each one's claim its own, which is exactly the
    production semantics (one authorization, one submission) applied per
    test. A test about the claim itself overrides both values.
    """
    monkeypatch.setenv("L5_LIVE_AUTH_LEDGER",
                       str(tmp_path / "auth_ledger.jsonl"))
    monkeypatch.setenv("L5_EXPERIMENT_ID",
                       "SUITE-%s" % abs(hash(request.node.nodeid)))
    if request.node.get_closest_marker("multi_submission"):
        # THESE TESTS PREDATE THE ONE-SUBMISSION CLAIM and exercise the
        # engine's MULTI-ORDER behaviour -- duplicate-id protection, the
        # absence of behavioural suppression, envelope counting across
        # partials. Each of their submissions stands for a SEPARATE
        # authorization, so the experiment id rotates per call rather than
        # the claim gate being switched off. The production gate is
        # untouched; only the identity it is keyed to varies here.
        from src import l5_live_authorization as _AUTH
        seq = {"n": 0}

        def _rotating() -> str:
            seq["n"] += 1
            return "SUITE-%s-%d" % (abs(hash(request.node.nodeid)), seq["n"])

        monkeypatch.setattr(_AUTH, "experiment_id", _rotating)


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


# --------------------------------------------------------------- the segfault
# THE CRASH THIS FIXTURE PINS. Two full-suite runs died with
# `Fatal Python error: Segmentation fault`, the faulting frame being
# `ssl.load_default_certs` inside `src/l5_shadow/gemini_ws.py:_ctx`, on one of
# SIX leaked daemon socket threads, concurrent with `_pytest/capture.py:resume`
# on the main thread. `GeminiFeed.watch` starts a real websocket thread whose
# reconnect loop builds a fresh SSL context every two seconds and runs until
# the process exits; nothing in the suite stops them. Several of those building
# a default context at once is what faults.
#
# The thread body is replaced with a no-op, so `watch` still returns a book and
# every reader sees the same "socket has delivered nothing" state it sees
# before the first frame arrives in production -- the path the fallback exists
# for. No test asserts live Gemini socket content; a test that wants the real
# socket opts in with @pytest.mark.real_gemini_socket.
@pytest.fixture(autouse=True)
def _no_real_gemini_socket(monkeypatch, request):
    from src.l5_shadow import gemini_ws as _gws
    if request.node.get_closest_marker("real_gemini_socket"):
        return
    seen = []

    def _inert(self, symbol):               # the thread target, doing nothing
        seen.append(symbol)
    monkeypatch.setattr(_gws.GeminiFeed, "_run", _inert)
    yield
    if seen:
        import os
        p = os.environ.get("L5_TEST_WS_TRIPWIRE")
        if p:
            with open(p, "a") as fh:
                fh.write("%s %s\n" % (request.node.nodeid, ",".join(seen)))
