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
