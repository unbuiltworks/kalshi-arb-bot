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

#: THE ACTIVATION INVARIANT (OWNER AUTHORIZATION 2026-09-16, "final CP1
#: activation"). Expressed as a function so the real repository state and the
#: adversarial cases below are checked by the SAME rule -- a rule that only
#: the live tree exercises is a rule nobody has tested.
#:
#: ABSENCE OF HALT IS NEVER AUTHORIZATION. That is the whole point. Halted is
#: always safe; un-halted is safe only when a committed, parseable, unexpired
#: mandate for exactly this service is there to authorize it.
ACTIVATION_SERVICE = "l5-r1-probe"


def activation_authorized(auth_dir: Path, now=None) -> tuple[bool, str]:
    """(is_this_tree_allowed_to_be_un_halted, reason)."""
    if (auth_dir / "HALT").exists():
        return True, "halted"                    # always a safe state
    f = auth_dir / "live_mandate.json"
    if not f.exists():
        return False, "no_halt_and_no_mandate"
    try:
        raw = f.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"mandate_unreadable:{type(exc).__name__}"
    m, why = M.parse_mandate(raw)
    if m is None:
        return False, f"mandate_unusable:{why}"
    if m.service != ACTIVATION_SERVICE:
        return False, f"mandate_wrong_service:{m.service}"
    if m.expired(now or datetime.now(timezone.utc)):
        return False, f"mandate_expired:{m.expiry}"
    return True, "activated_under_a_valid_mandate"


@pytest.mark.real_halt_latch
def test_the_halt_and_the_authorization_are_consistent():
    """RESTATED 2026-09-16 under explicit owner authorization. The guard is
    KEPT and made stronger -- not deleted, skipped, xfailed or weakened.

    It asserted `HALT.exists()` unconditionally: "if someone removes the HALT
    file, this test is where they are told that they just re-enabled a service
    that traded eleven unauthorized markets." That was right while nothing was
    authorized, and it firing the moment HALT was removed is exactly what it
    was for -- it did fire, and the removal stopped until the owner authorized
    this change specifically.

    What replaces it is a CONSISTENCY invariant, which is strictly more
    protection than the original. The original only said the file exists; it
    would have passed again for anyone who re-added HALT regardless of whether
    any authorization existed, and said nothing at all about the mandate. This
    one ties the two together in the dangerous direction: un-halting REQUIRES
    a committed, parseable, unexpired mandate naming exactly this service.

    Removing HALT with no mandate, a malformed mandate, an expired mandate, or
    a mandate for another service all still fail right here.
    """
    ok, why = activation_authorized(AUTH_DIR)
    assert ok, f"HALT removed without a valid authorization: {why}"
    # and the runtime must agree with the file state, in both directions
    halted = (AUTH_DIR / "HALT").exists()
    assert cs.shutdown_requested() is halted


@pytest.mark.parametrize("halt,mandate_body,expect_ok,expect_reason", [
    # HALT present -- always halted, whatever the mandate says
    (True, None, True, "halted"),
    (True, "valid", True, "halted"),
    # HALT absent -- the mandate is the only thing that can authorize it
    (False, None, False, "no_halt_and_no_mandate"),
    (False, "malformed", False, "mandate_unusable"),
    (False, "expired", False, "mandate_expired"),
    (False, "wrong_service", False, "mandate_wrong_service"),
    (False, "valid", True, "activated_under_a_valid_mandate"),
])
def test_the_activation_invariant_adversarially(
        tmp_path, halt, mandate_body, expect_ok, expect_reason):
    """The seven cases the owner enumerated, against the same rule the live
    tree is checked with. Run in a temp dir so they test the RULE rather than
    whatever the repository happens to carry today."""
    now = datetime(2026, 9, 16, 21, 0, tzinfo=timezone.utc)
    base = {
        "authorization_id": "OWNER-2026-09-16-CP1-CONTINUOUS-PROBE",
        "authorized_at": "2026-09-16T20:36:35Z",
        "service": ACTIVATION_SERVICE, "strategy": "pmus-l5-continuous-v1",
        "strategy_version": "CP1", "max_markets": 1, "max_total_markets": 3,
        "max_spend_per_market": "5.00", "max_total_spend": "15.00",
        "max_residual": "3.00", "max_daily_realized_loss": "6.00",
        "expiry": "2026-09-17T20:36:35Z",
    }
    if halt:
        (tmp_path / "HALT").write_text("stop")
    if mandate_body == "valid":
        (tmp_path / "live_mandate.json").write_text(json.dumps(base))
    elif mandate_body == "malformed":
        (tmp_path / "live_mandate.json").write_text("{not json at all")
    elif mandate_body == "expired":
        (tmp_path / "live_mandate.json").write_text(
            json.dumps(dict(base, expiry="2026-09-16T00:00:00Z")))
    elif mandate_body == "wrong_service":
        (tmp_path / "live_mandate.json").write_text(
            json.dumps(dict(base, service="some-other-service")))

    ok, why = activation_authorized(tmp_path, now=now)
    assert ok is expect_ok, f"{halt=} {mandate_body=} -> {ok} ({why})"
    assert why.startswith(expect_reason), why


def test_absence_of_halt_is_never_authorization_on_its_own():
    """Stated as its own test because it is the sentence the owner wrote, and
    because it is the one way this invariant could be quietly inverted."""
    import inspect
    src = inspect.getsource(activation_authorized)
    assert "no_halt_and_no_mandate" in src
    # an empty directory -- no HALT, no mandate -- must never be authorized
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ok, why = activation_authorized(Path(d))
        assert ok is False and why == "no_halt_and_no_mandate"


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

    THE AUTHORIZED ENVELOPE, from the owner's 2026-09-17 message (breadth
    widened from the 2026-09-16 three-market probe; DOLLARS UNCHANGED):
      max_spend_per_market $5.00 · max_total_spend $15.00 · max_residual $3.00
      max_markets 1 · max_total_markets 10 · max_daily_realized_loss $6.00
      expiry 24h · service l5-r1-probe

    Note what did NOT move: max_total_spend stays $15.00 while breadth goes to
    ten, so the mandate is now strictly tighter than the rung's own
    cap x markets worst case ($50.00) instead of equal to it. The
    "not roomier than the rung" checks below therefore still bind.
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
    assert m.max_total_markets == 10
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
    outlives the decision behind it.

    HALT SPLITS THIS INTO TWO DIFFERENT CLAIMS, AND ONLY ONE OF THEM IS ABOUT
    DANGER. How the mandate was WRITTEN is always checkable and always
    checked: a 24h-from-authorization ceiling, below, holds in every state.
    Whether it is still CURRENT only matters while the service could act on
    it. With HALT committed the service is halted whatever the mandate says --
    the parametrised cases above pin that -- and an expired mandate sitting
    beside HALT is not a live authorization, it is the spent residue of one,
    kept because this file is the authorization of record and deleting
    evidence to make a test pass is the wrong direction entirely.

    So the currency assertions apply UN-HALTED, which is the only state in
    which a stale expiry could authorize anything. Un-halted with an expired
    mandate additionally fails
    `test_the_halt_and_the_authorization_are_consistent`, so nothing that
    could enable trading is relaxed here: it is checked twice.
    """
    f = AUTH_DIR / "live_mandate.json"
    if not f.exists():
        return
    m, why = M.parse_mandate(f.read_text(encoding="utf-8"))
    assert m is not None, why
    now = datetime.now(timezone.utc)
    exp = M._parse_ts(m.expiry)
    auth = M._parse_ts(m.authorized_at)
    # ALWAYS: the shape of the authorization, whatever state we are in
    assert exp - auth <= timedelta(hours=24), (
        f"expiry is {exp - auth} after authorization; the owner authorized 24h")
    if (AUTH_DIR / "HALT").exists():
        return
    # UN-HALTED ONLY: the service could act on this, so it must be current
    assert not m.expired(now), f"the shipped mandate is already expired: {m.expiry}"
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
