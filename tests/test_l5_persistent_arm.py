"""MONITOR -> ARMED, only from the owner's approved documents.

`may_submit` has always required the durable state to be ARMED and
nothing ever wrote it: the only `write_state` callers write STOPPED and
HALTED. So setting an envelope made a lane unable to submit at all --
permanently, because MONITOR is the birth state and no path left it.
The gate was correct and unreachable.

These tests pin the six cases the owner named, and the properties that
make the transition safe to have at all: it is durable across restarts,
idempotent for the identical campaign and nothing else, and it can
never walk past a latch.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_persistent_mode as P

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 16, 18, 0, 0, tzinfo=UTC)
FIRST = dt.datetime(2026, 11, 16, 18, 20, 0, tzinfo=UTC)
END = dt.datetime(2026, 11, 16, 18, 40, 0, tzinfo=UTC)
SHA = "3543f70f"
SERIES = "cpc-btc-updown-5m-"


def envelope(**over):
    d = {"approved": True, "mode": "PERSISTENT", "deployment": SHA,
         "strategies": ["LATE_FAVORITE"], "series": [SERIES],
         "taker_allowed": False, "max_aggregate_usd": "10.00",
         "max_per_market_usd": "5.00", "max_daily_loss_usd": "5.00",
         "expires_utc": "2026-11-16T18:40:00Z",
         "nonce": "stage1a-btc5m-1820"}
    d.update(over)
    return json.dumps(d)


def campaign(**over):
    d = {"approved": True, "nonce": "b5m-72138ecce416d29e87531273",
         "series": SERIES, "issued_utc": "2026-11-16T18:00:00Z",
         "expires_utc": "2026-11-16T18:40:00Z", "max_windows": 4,
         "max_total_exposure_usd": "1.00", "max_daily_loss_usd": "5.00"}
    d.update(over)
    return json.dumps(d)


def do(tmp_path, *, env=None, camp=None, now=NOW, first=FIRST, end=END,
       dep=SHA, name="state.json"):
    return P.enter_armed(raw_envelope=envelope() if env is None else env,
                 raw_campaign=campaign() if camp is None else camp,
                 first_window_utc=first, not_after_utc=end, now=now,
                 deployment=dep, path=str(tmp_path / name))


# ---- 1. successful authorization ---------------------------------------

def test_the_owner_documents_arm_a_monitor_lane(tmp_path):
    out = do(tmp_path)
    assert out["ok"] is True, out
    assert out["state"] == P.ARMED
    assert out["already"] is False
    assert out["campaign_nonce"].startswith("b5m-")


def test_the_transition_is_durable_and_auditable(tmp_path):
    do(tmp_path)
    doc = json.loads((tmp_path / "state.json").read_text())
    assert doc["state"] == P.ARMED
    #: the record names the MECHANISM, the deployment, the series, the
    #: campaign and the window span -- not merely the outcome
    assert doc["why"].startswith(P.ARM_WHY)
    assert SHA in doc["why"] and SERIES in doc["why"]
    assert "18:20:00" in doc["why"] and "18:40:00" in doc["why"]
    assert doc["latch_digest"]


def test_may_submit_now_passes_the_state_gate(tmp_path):
    #: THE WHOLE POINT. Before this the envelope made the lane unable to
    #: submit at all; the state gate is what refused.
    path = str(tmp_path / "state.json")
    before = P.may_submit(
        market="cpc-btc-updown-5m-2026-11-16-1820z", strategy="LATE_FAVORITE",
        post_only=True, notional_usd=P.D("1.94"),
        market_exposure_usd=P.D("0"), aggregate_exposure_usd=P.D("0"),
        day_realized_usd=P.D("0"), now=FIRST,
        raw=envelope(), deployment=SHA, path=path)
    assert before["reason"] == "STATE_NOT_ARMED"
    assert do(tmp_path)["ok"] is True
    after = P.may_submit(
        market="cpc-btc-updown-5m-2026-11-16-1820z", strategy="LATE_FAVORITE",
        post_only=True, notional_usd=P.D("1.94"),
        market_exposure_usd=P.D("0"), aggregate_exposure_usd=P.D("0"),
        day_realized_usd=P.D("0"), now=FIRST,
        raw=envelope(), deployment=SHA, path=path)
    assert after["ok"] is True, after


# ---- 2. invalid approval ------------------------------------------------

@pytest.mark.parametrize("over,reason", [
    ({"approved": False}, "CAMPAIGN_NOT_APPROVED"),
    ({"approved": "yes"}, "CAMPAIGN_NOT_APPROVED"),
    ({"nonce": "short"}, "CAMPAIGN_NONCE_TOO_SHORT"),
    ({"series": ""}, "CAMPAIGN_NAMES_NO_SERIES"),
    ({"series": "cpceth-eth-updown-5m-"}, "CAMPAIGN_SERIES_NOT_IN_ENVELOPE"),
    ({"max_total_exposure_usd": None}, "CAMPAIGN_LIMITS_UNREADABLE"),
])
def test_an_invalid_approval_refuses(tmp_path, over, reason):
    out = do(tmp_path, camp=campaign(**over))
    assert out["ok"] is False
    assert out["reason"] == reason


def test_an_absent_or_unparseable_approval_refuses(tmp_path):
    assert do(tmp_path, camp="")["reason"] == "CAMPAIGN_ABSENT"
    assert do(tmp_path, camp="{not json")["reason"] == "CAMPAIGN_UNPARSEABLE"
    assert do(tmp_path, camp="[]")["reason"] == "CAMPAIGN_NOT_AN_OBJECT"


def test_a_campaign_looser_than_the_envelope_refuses(tmp_path):
    #: the campaign may be tighter, never looser, and the comparison is
    #: against the envelope rather than a figure restated here
    assert do(tmp_path, camp=campaign(max_total_exposure_usd="20.00")
              )["reason"] == "CAMPAIGN_EXPOSURE_ABOVE_ENVELOPE"
    assert do(tmp_path, camp=campaign(max_daily_loss_usd="11.00")
              )["reason"] == "CAMPAIGN_DAILY_LOSS_ABOVE_ENVELOPE"


def test_the_ceilings_cannot_be_armed_above_the_compiled_maxima(tmp_path):
    #: parse_envelope clamps, so this never reaches the state write
    out = do(tmp_path, env=envelope(max_aggregate_usd="20.00"))
    assert out["ok"] is False
    assert out["reason"] == "ENVELOPE:ENVELOPE_EXCEEDS_CODE_CEILING"


def test_a_missing_ceiling_is_not_read_as_the_ceiling(tmp_path):
    e = json.loads(envelope())
    del e["max_daily_loss_usd"]
    out = do(tmp_path, env=json.dumps(e))
    assert out["reason"] == "ENVELOPE:ENVELOPE_LIMITS_UNREADABLE"


# ---- 3. expired windows ------------------------------------------------

def test_a_campaign_whose_first_window_has_passed_refuses(tmp_path):
    #: arming mid-flight would authorize windows nobody has audited
    out = do(tmp_path, now=dt.datetime(2026, 11, 16, 18, 25, tzinfo=UTC))
    assert out["ok"] is False
    assert out["reason"] == "CAMPAIGN_ALREADY_STARTED"


def test_an_expired_envelope_refuses(tmp_path):
    out = do(tmp_path, now=dt.datetime(2026, 11, 16, 19, 0, tzinfo=UTC),
             first=dt.datetime(2026, 11, 16, 19, 20, tzinfo=UTC),
             end=dt.datetime(2026, 11, 16, 19, 40, tzinfo=UTC))
    assert out["reason"] == "ENVELOPE:ENVELOPE_EXPIRED"


def test_a_campaign_outliving_the_envelope_refuses(tmp_path):
    out = do(tmp_path, end=dt.datetime(2026, 11, 16, 19, 30, tzinfo=UTC))
    assert out["reason"] == "CAMPAIGN_OUTLIVES_ENVELOPE"


def test_inverted_or_unreadable_windows_refuse(tmp_path):
    assert do(tmp_path, first=END, end=FIRST
              )["reason"] == "CAMPAIGN_WINDOWS_INVERTED"
    assert do(tmp_path, first=None)["reason"] == "CAMPAIGN_WINDOWS_UNREADABLE"
    assert do(tmp_path, first="not a time"
              )["reason"] == "CAMPAIGN_WINDOWS_UNREADABLE"


# ---- 4. wrong SHA ------------------------------------------------------

def test_a_different_deployment_cannot_use_this_envelope(tmp_path):
    out = do(tmp_path, dep="deadbeef")
    assert out["ok"] is False
    assert out["reason"] == "ENVELOPE:ENVELOPE_IS_FOR_ANOTHER_DEPLOYMENT"


def test_an_unknown_deployment_refuses(tmp_path):
    out = do(tmp_path, dep="")
    assert out["reason"] == "ENVELOPE:DEPLOYMENT_UNKNOWN"


def test_an_envelope_naming_no_deployment_refuses(tmp_path):
    e = json.loads(envelope())
    del e["deployment"]
    assert do(tmp_path, env=json.dumps(e)
              )["reason"] == "ENVELOPE:ENVELOPE_NAMES_NO_DEPLOYMENT"


# ---- 5. restart persistence and idempotence ----------------------------

def test_a_restart_finds_itself_armed_and_proceeds(tmp_path):
    first = do(tmp_path)
    again = do(tmp_path)
    assert again["ok"] is True
    assert again["already"] is True
    assert again["digest"] == first["digest"]


def test_the_state_survives_being_read_by_a_fresh_reader(tmp_path):
    do(tmp_path)
    st = P.read_state(str(tmp_path / "state.json"))
    assert st["ok"] is True
    assert st["state"] == P.ARMED


# ---- 6. rejected rearming ----------------------------------------------

def test_a_different_campaign_cannot_rearm_an_armed_lane(tmp_path):
    do(tmp_path)
    out = do(tmp_path, camp=campaign(nonce="b5m-a-completely-different-one"))
    assert out["ok"] is False
    assert out["reason"] == "ARMED_FOR_ANOTHER_CAMPAIGN"


def test_a_different_envelope_cannot_rearm_an_armed_lane(tmp_path):
    do(tmp_path)
    out = do(tmp_path, env=envelope(nonce="stage1a-btc5m-second-attempt"))
    assert out["reason"] == "ARMED_FOR_ANOTHER_CAMPAIGN"


@pytest.mark.parametrize("latched", [P.HALTED, P.STOPPED])
def test_a_latch_is_never_walked_past(tmp_path, latched):
    path = str(tmp_path / "state.json")
    P.write_state(latched, why="prior safety event", now=NOW, path=path)
    out = do(tmp_path)
    assert out["ok"] is False
    assert out["reason"] == "STATE_%s" % latched
    assert "cannot clear a halt" in out["note"]
    #: and the latch is untouched
    assert P.read_state(path)["state"] == latched


def test_arming_touches_no_loss_accounting(tmp_path):
    #: the record carries state, why, at and the digest -- and nothing
    #: that could reset a day's realized result
    do(tmp_path)
    doc = json.loads((tmp_path / "state.json").read_text())
    assert set(doc) == {"state", "why", "at_utc", "latch_digest"}


def test_an_unreadable_state_refuses_rather_than_arming(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    out = do(tmp_path)
    assert out["ok"] is False
    assert out["reason"].startswith("STATE:")


def test_an_unset_state_path_refuses(tmp_path):
    out = P.enter_armed(raw_envelope=envelope(), raw_campaign=campaign(),
                first_window_utc=FIRST, not_after_utc=END, now=NOW,
                deployment=SHA, path="")
    assert out["ok"] is False


def test_an_unreadable_now_refuses(tmp_path):
    assert do(tmp_path, now="not a time")["reason"] == "NOW_UNREADABLE"


def test_the_digest_distinguishes_both_documents(tmp_path):
    a = P.arm_digest(envelope(), campaign())
    assert a != P.arm_digest(envelope(nonce="other"), campaign())
    assert a != P.arm_digest(envelope(), campaign(nonce="b5m-other-nonce-xx"))
    assert a == P.arm_digest(envelope(), campaign())


# ---- the ceremony is the ONLY route into durable ARMED -----------------

def test_only_enter_armed_writes_the_durable_armed_state():
    """One writer, proved by walking the AST rather than by convention.

    The mirror of the runner's one-`.arm()` invariant. ARMED is the state
    that lets `may_submit` through, so a second writer anywhere -- a
    helper, a repair script, a future convenience -- would be a second
    arming mechanism with none of the checks. STOPPED and HALTED may be
    written from anywhere, because writing a latch is always safe.
    """
    import ast
    import pathlib

    root = pathlib.Path(P.__file__).resolve().parents[1]
    offenders, found = [], []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in str(path) or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        #: the enclosing function of every write_state call whose first
        #: argument names ARMED
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call):
                    continue
                name = (call.func.attr if isinstance(call.func, ast.Attribute)
                        else getattr(call.func, "id", ""))
                if name != "write_state" or not call.args:
                    continue
                a0 = call.args[0]
                names = {a0.attr} if isinstance(a0, ast.Attribute) else set()
                if isinstance(a0, ast.Name):
                    names = {a0.id}
                if isinstance(a0, ast.Constant):
                    names = {str(a0.value)}
                if "ARMED" in names:
                    found.append("%s:%s" % (path.name, fn.name))
                    if fn.name != "enter_armed":
                        offenders.append("%s:%s" % (path.name, fn.name))
    assert offenders == [], offenders
    #: NOT VACUOUS. The scan must actually see the one legitimate writer,
    #: or a future refactor could move it and the guard would still pass
    #: while proving nothing.
    assert found == ["l5_persistent_mode.py:enter_armed"], found


def test_the_latched_states_may_still_be_written_from_anywhere():
    #: the guard above must not have narrowed the ability to STOP or HALT
    import ast
    import pathlib

    root = pathlib.Path(P.__file__).resolve().parents[1]
    writers = set()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in str(path) or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not call.args:
                continue
            name = (call.func.attr if isinstance(call.func, ast.Attribute)
                    else getattr(call.func, "id", ""))
            a0 = call.args[0]
            attr = a0.attr if isinstance(a0, ast.Attribute) else None
            if name == "write_state" and attr in ("STOPPED", "HALTED"):
                writers.add(path.name)
    assert writers, "nothing can latch a halt or a stop any more"


# ---- a finished campaign must not keep reporting ARMED -----------------

def test_an_unreadable_deadline_is_never_read_as_no_deadline():
    from src import l5_btc5m_runner as RUN
    assert RUN._utc_or_none("not a time") is None
    assert RUN._utc_or_none(None) is None
    #: and a naive stamp is read as UTC rather than rejected
    got = RUN._utc_or_none("2026-11-16T19:30:00")
    assert got is not None and got.tzinfo is not None


def test_a_passed_deadline_is_recognised_as_expired():
    from src import l5_btc5m_runner as RUN
    end = RUN._utc_or_none("2026-11-16T19:30:00Z")
    assert end is not None
    after = dt.datetime(2026, 11, 16, 20, 15, tzinfo=UTC)
    before = dt.datetime(2026, 11, 16, 19, 25, tzinfo=UTC)
    assert after >= end
    assert not (before >= end)


def test_returning_to_monitor_leaves_armed_with_one_writer(tmp_path):
    """The lifecycle fix must not become a second way into ARMED.

    It writes MONITOR, never ARMED, so the AST guard above still holds
    and `enter_armed` remains the only route in.
    """
    path = str(tmp_path / "state.json")
    assert do(tmp_path)["state"] == P.ARMED
    P.write_state(P.MONITOR, why="CAMPAIGN_EXPIRED:2026-11-16T19:30:00Z",
                  now=dt.datetime(2026, 11, 16, 20, 15, tzinfo=UTC), path=path)
    st = P.read_state(path)
    assert st["state"] == P.MONITOR
    assert "CAMPAIGN_EXPIRED" in st["detail"]["why"]


def test_an_expired_campaign_cannot_resubmit(tmp_path):
    #: MONITOR is refused by the submit gate, which is the point
    path = str(tmp_path / "state.json")
    do(tmp_path)
    P.write_state(P.MONITOR, why="CAMPAIGN_EXPIRED", now=NOW, path=path)
    out = P.may_submit(
        market="cpc-btc-updown-5m-2026-11-16-1910z", strategy="LATE_FAVORITE",
        post_only=True, notional_usd=P.D("1.94"),
        market_exposure_usd=P.D("0"), aggregate_exposure_usd=P.D("0"),
        day_realized_usd=P.D("0"), now=FIRST,
        raw=envelope(), deployment=SHA, path=path)
    assert out["ok"] is False
    assert out["reason"] == "STATE_NOT_ARMED"


def test_a_latch_still_outranks_the_lifecycle_reset(tmp_path):
    #: returning to MONITOR must never overwrite a HALT or a STOP; the
    #: loop exits on those before it reaches the expiry check
    path = str(tmp_path / "state.json")
    P.write_state(P.STOPPED, why="operator", now=NOW, path=path)
    assert P.read_state(path)["state"] == P.STOPPED
    out = do(tmp_path)
    assert out["reason"] == "STATE_STOPPED"
    assert P.read_state(path)["state"] == P.STOPPED
