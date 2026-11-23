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


def test_published_drawdown_under_the_limit_passes_and_is_reported():
    v = AR.evaluate(dd(), now=NOW)
    assert v["ok"]
    assert v["drawdown"]["committed_and_realized_usd"] == "1.6744"
    assert v["drawdown"]["capacity_usd"] == "1.3256"


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


def test_the_three_figures_are_reported_separately():
    # the ledger's 2.50 includes 1.90 of this bot's own resting orders
    v = AR.evaluate(dd("2.50"), now=NOW, own_at_risk="1.90")
    d = v["drawdown"]
    assert v["ok"]
    assert d["committed_and_realized_usd"] == "2.50"       # the ledger's own
    assert d["own_worst_case_exposure_usd"] == "1.90"
    assert d["realized_drawdown_upper_bound_usd"] == "0.60"
    assert d["capacity_usd"] == "0.50"                     # 3.00 - 2.50


def test_own_committed_money_is_never_taken_out_of_the_limit():
    # however much of the figure is this bot's own resting money, the
    # limit is reached when the ledger's figure reaches it
    for own in ("0", "2.00", "5.00", None):
        v = AR.evaluate(dd("3.00"), now=NOW, own_at_risk=own)
        assert v["reason"] == "ACCOUNT_DRAWDOWN_LIMIT", own


def test_exposure_added_since_the_snapshot_is_counted_once():
    # snapshot 1.67; the bot has since put 0.94 at risk the ledger has not
    # seen: capacity is 3.00 - 1.67 - 0.94
    v = AR.evaluate(dd("1.67"), now=NOW, own_at_risk="0.94",
                    own_added_since="0.94")
    assert v["ok"] and v["drawdown"]["capacity_usd"] == "0.39"
    # once the ledger's snapshot includes it, it is not added again
    v = AR.evaluate(dd("2.61"), now=NOW, own_at_risk="0.94",
                    own_added_since="0")
    assert v["drawdown"]["capacity_usd"] == "0.39"
    # and enough of it reaches the limit before the ledger has seen any
    v = AR.evaluate(dd("1.67"), now=NOW, own_at_risk="1.40",
                    own_added_since="1.40")
    assert v["reason"] == "ACCOUNT_DRAWDOWN_LIMIT"


def test_unknown_own_additions_refuse():
    for bad in ("x", None, "NaN"):
        v = AR.evaluate(dd("1.00"), now=NOW, own_added_since=bad)
        assert v["reason"] == "ACCOUNT_DRAWDOWN_OWN_UNREADABLE", bad
    v = AR.evaluate(dd("1.00"), now=NOW, own_added_since="Infinity")
    assert not v["ok"]


def test_one_order_must_fit_the_capacity_left():
    v = AR.evaluate(dd("1.67"), now=NOW)
    assert AR.capacity_allows(v, "1.33") and not AR.capacity_allows(v, "1.34")
    assert not AR.capacity_allows(v, "x")
    # nothing published and nothing required: nothing to measure against
    assert AR.capacity_allows(AR.evaluate(doc(), now=NOW), "999")


# ---- the document is read off the trading loop
def test_the_reader_keeps_the_last_good_document_and_its_age():
    t = [100.0]
    box = {"doc": {"n": 1}, "fail": False}

    def fetch(url):
        if box["fail"]:
            raise OSError("down")
        return box["doc"]
    r = AR.RiskReader(lambda: "http://x", fetch=fetch, clock=lambda: t[0])
    s = r.snapshot()
    assert s["doc"] is None and s["age_s"] is None and s["tries"] == 0
    r.refresh_once()
    assert r.snapshot()["doc"] == {"n": 1} and r.snapshot()["age_s"] == 0
    t[0] += 40
    box["fail"] = True
    r.refresh_once()
    s = r.snapshot()
    assert s["doc"] == {"n": 1} and s["age_s"] == 40      # ageing, not fresh
    assert s["err"] == "ACCOUNT_RISK_UNREADABLE:OSError" and s["tries"] == 2
    box["fail"] = False
    r.refresh_once()
    assert r.snapshot()["age_s"] == 0 and r.snapshot()["err"] is None


def test_the_reader_without_an_address_reads_nothing():
    r = AR.RiskReader(lambda: "", fetch=lambda u: {"never": 1})
    r.refresh_once()
    assert r.snapshot()["doc"] is None
    assert r.snapshot()["err"] == "ACCOUNT_RISK_URL_NOT_SET"


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
    assert v["ok"] and v["drawdown"]["capacity_usd"] == "1.3256"
    # a value the ledger could not establish: no figure, its own refusal
    blk = {"state": "ON", "worst_case_value_usd": None,
           "anchor_usd": "321.4782", "max_drawdown_usd": "3.00",
           "refusal": "PORTFOLIO_VALUE_UNAVAILABLE"}
    v = AR.evaluate(doc(risk={"portfolio_drawdown": blk}), now=NOW)
    assert not v["ok"] and v["reason"] == "ACCOUNT_DRAWDOWN_UNREADABLE"
