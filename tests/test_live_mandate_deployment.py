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
from decimal import Decimal as D
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

def test_the_shipped_mandate_is_exactly_the_authorized_envelope():
    """RESTATED 2026-09-16, and deliberately STRONGER than what it replaced.

    It asserted `not live_mandate.json.exists()` -- "no mandate is shipped
    right now, which is the safe state". That was correct while no
    authorization existed, and it is the assertion that fired the moment one
    was written, which is exactly what it was for.

    The owner then authorized a bounded three-market continuous live probe and
    step 8 of their activation sequence is "create fresh 24h mandate while
    HALTED". So absence is no longer the only safe state -- but "a mandate
    exists" must never become "any mandate is fine". Deleting this test would
    have done that. Instead it now pins every field of the shipped mandate to
    the authorized envelope, so a mandate that is absent, unparseable, expired,
    aimed at another service, or roomier than authorized ALL still fail here.

    THE AUTHORIZED ENVELOPE, from the owner's 2026-09-16 message:
      max_spend_per_market $5.00 · max_total_spend $15.00 · max_residual $3.00
      max_markets 1 · max_total_markets 3 · max_daily_realized_loss $6.00
      expiry 24h · service l5-r1-probe
    """
    f = AUTH_DIR / "live_mandate.json"
    if not f.exists():
        # absence is still a safe state; nothing else here can be checked
        m, why = M.load_mandate(env={})
        assert m is None and why == "no_mandate"
        return
    m, why = M.parse_mandate(f.read_text(encoding="utf-8"))
    assert m is not None, why
    assert m.service == "l5-r1-probe", (
        "a mandate naming another service would be refused at the wire, but it "
        "should never have been written")
    assert str(m.max_spend_per_market) == "5.00"
    assert str(m.max_total_spend) == "15.00"
    assert str(m.max_residual) == "3.00"
    assert str(m.max_daily_realized_loss) == "6.00"
    assert m.max_markets == 1
    assert m.max_total_markets == 3
    # NOT ROOMIER THAN THE RUNG EITHER. min(rung, mandate) means a roomier
    # mandate cannot raise the envelope, but it would still misstate what was
    # authorized, and this file is the authorization of record.
    from src import pmus_l5_ramp_ladder as _ld
    cp1 = _ld.rung("CP1")
    assert m.max_spend_per_market <= D(cp1["per_market_cap_usd"])
    assert m.max_total_markets <= int(cp1["markets_per_campaign"])
    assert m.max_markets <= int(cp1["concurrency"])
    assert m.max_daily_realized_loss <= abs(_ld.daily_loss_stop(cp1))
    # residual: 3 contracts, priced at $1/share by the wire guard
    assert m.max_residual <= D(str(cp1["residual_units"]))


def test_the_shipped_mandate_is_current_and_expires_within_a_day():
    """A mandate that has expired, or that was written with a runaway expiry,
    is the 2026-09-16 failure in a different costume: an authorization that
    outlives the decision behind it."""
    f = AUTH_DIR / "live_mandate.json"
    if not f.exists():
        return
    m, why = M.parse_mandate(f.read_text(encoding="utf-8"))
    assert m is not None, why
    now = datetime.now(timezone.utc)
    assert not m.expired(now), f"the shipped mandate is already expired: {m.expiry}"
    exp = M._parse_ts(m.expiry)
    auth = M._parse_ts(m.authorized_at)
    assert exp - auth <= timedelta(hours=24), (
        f"expiry is {exp - auth} after authorization; the owner authorized 24h")
    assert exp <= now + timedelta(hours=24)


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
