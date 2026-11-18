"""Shared account-wide risk: the rule both lanes enforce (owner 2026-11-18)."""
import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_account_risk as AR

NOW = dt.datetime(2026, 11, 18, 14, 0, 0, tzinfo=dt.timezone.utc)


def doc(**kw):
    risk = {"readable": True, "utc_day": "2026-11-18",
            "utc_day_realized_usd": "-10", "daily_halt_usd": "50",
            "lifetime_realized_usd": "-70", "cumulative_loss_ceiling_usd": "200",
            "retired_markets": ["aec-a", "aec-b"]}
    risk.update(kw.pop("risk", {}))
    d = {"as_of_utc": (NOW - dt.timedelta(seconds=5)).isoformat(),
         "account_risk": risk}
    d.update(kw)
    return d


def test_healthy_document_passes_with_room():
    v = AR.evaluate(doc(), now=NOW)
    assert v["ok"] and v["loss_room_usd"] == "40"


@pytest.mark.parametrize("d,reason", [
    ({}, "ACCOUNT_RISK_NOT_PUBLISHED"),
    ({"account_risk": {"readable": True}}, "ACCOUNT_RISK_UNDATED"),
])
def test_missing_pieces_refuse(d, reason):
    assert AR.evaluate(d, now=NOW)["reason"] == reason


def test_stale_future_unreadable_wrong_day_figures_all_refuse():
    old = doc(as_of_utc=(NOW - dt.timedelta(seconds=31)).isoformat())
    assert AR.evaluate(old, now=NOW)["reason"] == "ACCOUNT_RISK_STALE"
    fut = doc(as_of_utc=(NOW + dt.timedelta(seconds=10)).isoformat())
    assert AR.evaluate(fut, now=NOW)["reason"] == "ACCOUNT_RISK_STALE"
    bad = doc(risk={"readable": False, "reason": "X"})
    assert AR.evaluate(bad, now=NOW)["reason"] == \
        "ACCOUNT_RISK_SAYS_UNREADABLE"
    day = doc(risk={"utc_day": "2026-11-17"})
    assert AR.evaluate(day, now=NOW)["reason"] == \
        "ACCOUNT_RISK_FOR_ANOTHER_DAY"
    for k in ("utc_day_realized_usd", "daily_halt_usd",
              "lifetime_realized_usd", "cumulative_loss_ceiling_usd"):
        assert AR.evaluate(doc(risk={k: None}), now=NOW)["reason"] == \
            "ACCOUNT_RISK_FIGURES_UNREADABLE"


def test_daily_halt_and_lifetime_ceiling_bind():
    d = doc(risk={"utc_day_realized_usd": "-50"})
    assert AR.evaluate(d, now=NOW)["reason"] == "ACCOUNT_DAILY_LOSS_HALT"
    d = doc(risk={"lifetime_realized_usd": "-200"})
    assert AR.evaluate(d, now=NOW)["reason"] == \
        "ACCOUNT_CUMULATIVE_LOSS_CEILING"


def test_own_losses_bind_earlier_gains_never_later():
    # -10 published + this lane's -41 not yet booked: -51 <= -50 halts
    v = AR.evaluate(doc(), now=NOW, own_unaccounted="-41")
    assert v["reason"] == "ACCOUNT_DAILY_LOSS_HALT"
    # an own GAIN is never credited: room stays exactly the published room
    v = AR.evaluate(doc(), now=NOW, own_unaccounted="+30")
    assert v["ok"] and v["loss_room_usd"] == "40"
    assert AR.evaluate(doc(), now=NOW, own_unaccounted="junk")["reason"] == \
        "OWN_LOSSES_UNREADABLE"


def test_two_lanes_cannot_both_spend_the_same_room():
    """Lane A's unbooked loss is visible to B only once booked; each lane
    adds ITS OWN, so the published figure plus either lane's losses binds."""
    published = doc(risk={"utc_day_realized_usd": "-30"})
    a = AR.evaluate(published, now=NOW, own_unaccounted="-15")
    b = AR.evaluate(published, now=NOW, own_unaccounted="-15")
    assert a["ok"] and b["ok"]                      # -45 each, room 5
    booked = doc(risk={"utc_day_realized_usd": "-45"})
    assert AR.evaluate(booked, now=NOW, own_unaccounted="-5")["reason"] == \
        "ACCOUNT_DAILY_LOSS_HALT"


def test_fetch_errors_and_unset_url_refuse():
    assert AR.account_risk(url="", now=NOW)["reason"] == \
        "ACCOUNT_RISK_URL_NOT_SET"

    def boom(_):
        raise OSError("down")
    assert AR.account_risk(url="http://x", now=NOW, fetch=boom)["reason"] \
        == "ACCOUNT_RISK_UNREADABLE:OSError"
    assert AR.account_risk(url="http://x", now=NOW,
                           fetch=lambda _: doc())["ok"]


def test_lane_cap_split_and_typo_refusal():
    assert AR.lane_cap({}) is None                                 # unset
    assert AR.lane_cap({AR.ACCOUNT_CAP_ENV: "17"}) == D("17")
    assert AR.lane_cap({AR.ACCOUNT_CAP_ENV: "17",
                        AR.ACCOUNT_LANES_ENV: "4"}) == D("4.25")
    assert AR.lane_cap({AR.ACCOUNT_CAP_ENV: "10",
                        AR.ACCOUNT_LANES_ENV: "3"}) == D("3.33")   # floored
    for bad in ("0", "17", "two", "-1"):
        assert AR.lane_cap({AR.ACCOUNT_CAP_ENV: "10",
                            AR.ACCOUNT_LANES_ENV: bad}) is None
    assert AR.lane_cap({AR.ACCOUNT_CAP_ENV: "-5"}) is None


def test_retired_markets():
    assert AR.retired_markets(doc()) == {"aec-a", "aec-b"}
    assert AR.retired_markets({}) == set()
