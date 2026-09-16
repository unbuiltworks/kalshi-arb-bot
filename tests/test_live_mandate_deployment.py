"""The halt and the authorization are durable artefacts, not runtime overrides.

The 2026-09-16 halt worked, but only because a Railway `startCommand` happened
to be settable when `set-variables` was not and `redeploy` had no snapshot. That
is the same class of runtime override that condemned the legacy probe, and it is
not a mechanism anyone should have to rely on twice. These tests pin the
replacement: a committed HALT file and a committed mandate, both shipped in the
image, both fail-closed by absence or presence rather than by configuration.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

#: These files test the mandate itself, so the suite's permissive stand-in
#: mandate must never be what makes them pass.
pytestmark = pytest.mark.real_mandate_env

from src import pmus_live_mandate as M
from src import pmus_l5_continuous_session as cs

DOCKERFILE = Path("deploy/l5_r1_probe/Dockerfile")
AUTH_DIR = Path("analysis/l5/authorization")


# --------------------------------------------------------------------------
# the halt

@pytest.mark.real_halt_latch
def test_the_repo_is_currently_halted():
    """This is a state assertion, not a style one. If someone removes the HALT
    file, this test is where they are told that they just re-enabled a service
    that traded eleven unauthorized markets."""
    assert (AUTH_DIR / "HALT").exists()
    assert cs.shutdown_requested() is True


def test_the_halt_file_alone_halts_with_no_environment_variable(monkeypatch, tmp_path):
    monkeypatch.delenv(cs.SHUTDOWN_ENV, raising=False)
    monkeypatch.setattr(cs, "HALT_FILE", tmp_path / "HALT")
    assert cs.shutdown_requested() is False
    (tmp_path / "HALT").write_text("stop")
    assert cs.shutdown_requested() is True


def test_the_env_latch_still_works_so_neither_mechanism_is_load_bearing_alone(
        monkeypatch, tmp_path):
    """Two independent sources, OR'd: no single unavailable mechanism can
    leave the system unable to stop."""
    monkeypatch.setattr(cs, "HALT_FILE", tmp_path / "absent")
    monkeypatch.setenv(cs.SHUTDOWN_ENV, "1")
    assert cs.shutdown_requested() is True


def test_an_unreadable_halt_path_is_treated_as_halted(monkeypatch):
    class Boom:
        def exists(self):
            raise OSError("permission denied")
    monkeypatch.delenv(cs.SHUTDOWN_ENV, raising=False)
    monkeypatch.setattr(cs, "HALT_FILE", Boom())
    assert cs.shutdown_requested() is True


def test_shutdown_is_decided_before_arming_is_even_considered():
    """Source-level: `shutdown_requested()` must be tested before `armed()` in
    run_campaign, or a halted-but-armed process could still trade."""
    src = Path("src/pmus_l5_continuous_session.py").read_text()
    body = src.split("def run_campaign")[1]
    assert body.index("shutdown_requested()") < body.index("base.armed()")


# --------------------------------------------------------------------------
# the mandate as a deployment artefact

def test_no_mandate_is_shipped_right_now_which_is_the_safe_state():
    assert not (AUTH_DIR / "live_mandate.json").exists()
    m, why = M.load_mandate(env={})
    assert m is None and why == "no_mandate"


def test_the_default_mandate_path_is_the_committed_one_not_a_variable():
    assert M.DEFAULT_MANDATE_FILE == "analysis/l5/authorization/live_mandate.json"


def test_a_committed_mandate_file_is_read_without_any_env_var(tmp_path, monkeypatch):
    f = tmp_path / "live_mandate.json"
    f.write_text(json.dumps({
        "authorization_id": "A1", "authorized_at": "2026-09-17T00:00:00Z",
        "service": "l5-r1-probe", "strategy": "S", "strategy_version": "V0",
        "max_markets": 1, "max_total_markets": 1,
        "max_spend_per_market": "0.05", "max_total_spend": "0.05",
        "max_residual": "0.06", "max_daily_realized_loss": "1.00",
        "expiry": "2099-01-01T00:00:00Z"}))
    monkeypatch.setattr(M, "DEFAULT_MANDATE_FILE", str(f))
    m, why = M.load_mandate(env={})
    assert m is not None, why
    assert m.authorization_id == "A1"


def test_the_image_ships_the_authorization_directory_and_the_guard():
    df = DOCKERFILE.read_text()
    assert "COPY analysis/l5/authorization/ analysis/l5/authorization/" in df
    assert "COPY src/pmus_live_mandate.py" in df


def test_no_start_command_smuggles_configuration_into_the_process():
    """The repo requires a startCommand that MIRRORS the image CMD -- that is a
    fall-through guard, not an override, and another test enforces it.

    What must never come back is a start command that CONFIGURES the process:
    the legacy probe's rewrote source with `sed -i` at boot, and the emergency
    halt I used on 2026-09-16 exported PMUS_SPRING_SHUTDOWN / PMUS_SPRING_LIVE.
    Both make the running system something other than the reviewed artifact.
    Configuration belongs in the committed authorization directory, which is
    why that directory now exists.
    """
    p = Path("deploy/l5_r1_probe/railway.json")
    if not p.exists():
        pytest.skip("no manifest")
    cmd = json.dumps(json.loads(p.read_text()).get("deploy") or {})
    for smuggled in ("PMUS_SPRING_SHUTDOWN", "PMUS_SPRING_LIVE",
                     "PMUS_SPRING_ARM", "PMUS_LIVE_MANDATE", "export ",
                     "sed -i", "&&", ";rm", "curl"):
        assert smuggled not in cmd, f"startCommand smuggles {smuggled!r}"


# --------------------------------------------------------------------------
# expiry is enforced by the process, not by a person

def test_an_expired_mandate_stops_the_campaign_without_anyone_intervening():
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    m, why = M.parse_mandate({
        "authorization_id": "A1", "authorized_at": "2026-09-16T00:00:00Z",
        "service": "l5-r1-probe", "strategy": "S", "strategy_version": "V0",
        "max_markets": 1, "max_total_markets": 1,
        "max_spend_per_market": "0.05", "max_total_spend": "0.05",
        "max_residual": "0.06", "max_daily_realized_loss": "1.00",
        "expiry": past})
    assert m is not None, why
    assert m.expired() is True


def test_expiry_is_re_read_every_pass_not_once_at_boot():
    """Source-level, because the whole failure mode is 'it was valid when we
    started'. The loop must consult the mandate again, inside the loop."""
    src = Path("src/pmus_l5_continuous_session.py").read_text()
    loop = src.split("while clock() < stop_at")[1][:2000]
    assert "_mandate_still_valid()" in loop
    assert "shutdown_requested()" in loop


def test_risk_enforcement_lives_in_the_executor_not_in_a_monitor():
    """Monitoring detects; it must never be what prevents. The guard that
    refuses an order is in src/, reachable with no network and no scheduler."""
    import inspect
    src = inspect.getsource(M.GuardedTransport.request)
    assert "admit(" in src and "MandateRefusal" in src
    # and it does not phone anything to make its decision
    for forbidden in ("requests", "urllib", "socket", "sleep", "notify"):
        assert forbidden not in src
