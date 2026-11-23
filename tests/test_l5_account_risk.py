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


# ---- account-wide drawdown (owner 2026-11-23: "The $3 limit needs to apply
# ---- consistently across every money-capable service.")
def dd(drawdown="1.6744", mx="3.00", state="ON", **kw):
    blk = {"state": state, "anchor_usd": "321.4782",
           "max_drawdown_usd": mx, "drawdown_usd": drawdown}
    blk.update(kw)
    return doc(risk={"portfolio_drawdown": blk})


@pytest.fixture(autouse=True)
def _no_drawdown_env(monkeypatch):
    monkeypatch.delenv(AR.DRAWDOWN_REQUIRED_ENV, raising=False)
    monkeypatch.delenv(AR.DRAWDOWN_BUDGET_ENV, raising=False)


def test_published_drawdown_under_the_limit_passes_and_is_reported():
    v = AR.evaluate(dd(), now=NOW)
    assert v["ok"] and v["drawdown"]["drawdown_usd"] == "1.6744"
    assert v["drawdown"]["room_usd"] == "1.3256"


def test_published_drawdown_at_the_limit_refuses():
    for used in ("3.00", "3.01", "47"):
        v = AR.evaluate(dd(used), now=NOW)
        assert not v["ok"] and v["reason"] == "ACCOUNT_DRAWDOWN_LIMIT"


@pytest.mark.parametrize("blk, reason", [
    ({"state": "INVALID"}, "ACCOUNT_DRAWDOWN_INVALID"),
    ({"state": "UNREADABLE"}, "ACCOUNT_DRAWDOWN_UNREADABLE"),
    ({"state": "ON", "max_drawdown_usd": "3"}, "ACCOUNT_DRAWDOWN_UNREADABLE"),
    ({"state": "ON", "drawdown_usd": "x", "max_drawdown_usd": "3"},
     "ACCOUNT_DRAWDOWN_UNREADABLE"),
    ({"state": "ON", "drawdown_usd": "1", "max_drawdown_usd": "0"},
     "ACCOUNT_DRAWDOWN_UNREADABLE"),
])
def test_a_misconfigured_or_unreadable_limit_refuses(blk, reason):
    v = AR.evaluate(doc(risk={"portfolio_drawdown": blk}), now=NOW)
    assert not v["ok"] and v["reason"] == reason


def test_a_publisher_without_the_figure_is_refused_only_when_required(
        monkeypatch):
    assert AR.evaluate(doc(), now=NOW)["ok"]             # earlier behaviour
    assert AR.evaluate(dd(state="OFF"), now=NOW)["ok"]
    monkeypatch.setenv(AR.DRAWDOWN_REQUIRED_ENV, "1")
    assert AR.evaluate(doc(), now=NOW)["reason"] == \
        "ACCOUNT_DRAWDOWN_NOT_PUBLISHED"
    assert AR.evaluate(dd(state="OFF"), now=NOW)["reason"] == \
        "ACCOUNT_DRAWDOWN_OFF_AT_PUBLISHER"
    assert AR.evaluate(dd(), now=NOW)["ok"]


def test_strict_by_default_own_resting_money_counts_as_drawdown():
    # the ledger's 5.00 includes 4.00 of this bot's own resting orders
    v = AR.evaluate(dd("5.00"), now=NOW, own_at_risk="4.00")
    assert v["reason"] == "ACCOUNT_DRAWDOWN_LIMIT"


def test_with_an_owner_budget_own_at_risk_money_is_not_counted_up_to_it(
        monkeypatch):
    monkeypatch.setenv(AR.DRAWDOWN_BUDGET_ENV, "50")
    v = AR.evaluate(dd("5.00"), now=NOW, own_at_risk="4.00")
    assert v["ok"] and v["drawdown"]["drawdown_usd"] == "1.00"
    assert v["drawdown"]["own_at_risk_not_counted_usd"] == "4.00"
    # only up to the budget: 60 at risk, 50 excused, 10 + 1 counted
    v = AR.evaluate(dd("61.00"), now=NOW, own_at_risk="60")
    assert v["reason"] == "ACCOUNT_DRAWDOWN_LIMIT"
    assert v["drawdown"]["drawdown_usd"] == "11.00"
    # a settled loss is never excused: nothing is at risk any more
    v = AR.evaluate(dd("3.50"), now=NOW, own_at_risk="0")
    assert v["reason"] == "ACCOUNT_DRAWDOWN_LIMIT"


def test_with_a_budget_marked_losses_on_held_inventory_count(monkeypatch):
    monkeypatch.setenv(AR.DRAWDOWN_BUDGET_ENV, "50")
    # 2.00 published + 8 at risk, of which 1.20 is already marked down
    v = AR.evaluate(dd("10.00"), now=NOW, own_at_risk="8",
                    own_unaccounted="-1.20")
    assert v["drawdown"]["own_marked_loss_counted_usd"] == "1.20"
    assert v["drawdown"]["drawdown_usd"] == "3.20"
    assert v["reason"] == "ACCOUNT_DRAWDOWN_LIMIT"


def test_unknown_own_exposure_excuses_nothing(monkeypatch):
    monkeypatch.setenv(AR.DRAWDOWN_BUDGET_ENV, "50")
    for unknown in (None, "Infinity", "x", "-1"):
        v = AR.evaluate(dd("5.00"), now=NOW, own_at_risk=unknown)
        assert v["reason"] == "ACCOUNT_DRAWDOWN_LIMIT", unknown


def test_an_unreadable_budget_refuses(monkeypatch):
    monkeypatch.setenv(AR.DRAWDOWN_BUDGET_ENV, "lots")
    v = AR.evaluate(dd(), now=NOW, own_at_risk="1")
    assert v["reason"] == "ACCOUNT_DRAWDOWN_BUDGET_UNREADABLE"


def test_a_publisher_that_refuses_for_another_reason_is_followed():
    v = AR.evaluate(dd(refusal="PORTFOLIO_VALUE_UNAVAILABLE"), now=NOW)
    assert v["reason"] == "ACCOUNT_DRAWDOWN_PUBLISHER_REFUSES"
    # its own limit refusal is not double-counted when the figure is under
    # the reader's (budget-adjusted) limit: the reader's arithmetic decides
    v = AR.evaluate(dd("1.00", refusal="PORTFOLIO_DRAWDOWN_LIMIT:9>=3"),
                    now=NOW)
    assert v["ok"]


def test_the_ledgers_own_published_block_shape_is_read(monkeypatch):
    # exactly what l5_portfolio_drawdown.report() publishes today
    blk = {"state": "ON", "worst_case_value_usd": "319.8038",
           "anchor_usd": "321.4782", "max_drawdown_usd": "3.00",
           "drawdown_usd": "1.6744", "room_usd": "1.3256", "refusal": None}
    v = AR.evaluate(doc(risk={"portfolio_drawdown": blk}), now=NOW)
    assert v["ok"] and v["drawdown"]["room_usd"] == "1.3256"
    # a value the ledger could not establish: no figure, its own refusal
    blk = {"state": "ON", "worst_case_value_usd": None,
           "anchor_usd": "321.4782", "max_drawdown_usd": "3.00",
           "refusal": "PORTFOLIO_VALUE_UNAVAILABLE"}
    v = AR.evaluate(doc(risk={"portfolio_drawdown": blk}), now=NOW)
    assert not v["ok"] and v["reason"] == "ACCOUNT_DRAWDOWN_UNREADABLE"
