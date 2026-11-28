"""A lane whose own ledger has lost a settled market must not trade.

The ledger that is UNREADABLE already refuses. One that is ABSENT loads as
empty and reads as "nothing ever lost". The account service's reconciled
basis lists what settled on the account; a market of this lane's series it
lists that this lane's history lacks is a hole, and is refused.
"""
from decimal import Decimal as D

from tests.test_l5_btc5m_lifetime_own_losses import (   # noqa: F401
    PRIOR, TODAY, basis, hist, own, read)

OTHER = "cpc-btc-updown-15m-2026-11-20-1200z"       # another lane's series
LOST = "cpc-btc-updown-5m-2026-11-20-1420z"         # ours, absent from ours


def test_a_whole_ledger_is_admitted():
    h = hist(**{PRIOR: "-0.02", TODAY: "-0.01"})
    r = read(own(h), lifetime_basis=basis({PRIOR: "-0.02", TODAY: "-0.01"}))
    assert r["ok"], r["reason"]
    assert r["btc5m_lifetime_basis"][
        "own_series_markets_not_in_own_history"] == []


def test_a_market_of_ours_the_account_lists_and_we_lack_is_refused():
    h = hist(**{TODAY: "-0.01"})
    r = read(own(h), lifetime_basis=basis({LOST: "-0.0114",
                                           TODAY: "-0.01"}))
    assert r["ok"] is False
    assert r["reason"] == "OWN_LEDGER_MISSING_SETTLED_MARKETS"
    assert r["btc5m_lifetime_basis"][
        "own_series_markets_not_in_own_history"] == [LOST]


def test_an_empty_ledger_after_a_lost_volume_is_refused():
    r = read(own({}), lifetime_basis=basis({LOST: "-0.0114"}))
    assert r["ok"] is False
    assert r["reason"] == "OWN_LEDGER_MISSING_SETTLED_MARKETS"


def test_another_lanes_market_is_not_ours_to_hold():
    h = hist(**{TODAY: "-0.01"})
    r = read(own(h), lifetime_basis=basis({OTHER: "-3.00", TODAY: "-0.01"}))
    assert r["ok"], r["reason"]


def test_even_a_winning_lost_market_refuses():
    #: the hole is the defect, whatever the sign of what fell through it
    r = read(own({}), lifetime_basis=basis({LOST: "0.05"}))
    assert r["reason"] == "OWN_LEDGER_MISSING_SETTLED_MARKETS"


def test_an_unreconciled_basis_cannot_accuse_the_ledger():
    #: it is not trusted to say what settled; every own loss is then added
    #: in full instead, which is the existing conservative path
    h = hist(**{TODAY: "-0.01"})
    r = read(own(h), lifetime_basis=basis({LOST: "-0.0114"},
                                          reconciled=False))
    assert r["ok"], r["reason"]
    assert r["btc5m_lifetime_basis"]["basis_used"] is False
    assert r["btc5m_lifetime_basis"][
        "own_series_markets_not_in_own_history"] == []


def test_no_basis_published_changes_nothing():
    h = hist(**{TODAY: "-0.01"})
    assert read(own(h))["ok"]


# ------------------------------------------- markets something else traded

from src import l5_btc5m_runner as RUN                    # noqa: E402

STRANGER = "cpc-btc-updown-5m-2026-11-18-1540z"


def test_a_market_the_owner_attests_is_reported_not_refused(monkeypatch):
    monkeypatch.setenv(RUN.NOT_THIS_LANE_ENV, STRANGER)
    h = hist(**{TODAY: "-0.01"})
    r = read(own(h), lifetime_basis=basis({STRANGER: "-1.8398",
                                           TODAY: "-0.01"}))
    assert r["ok"], r["reason"]
    b = r["btc5m_lifetime_basis"]
    assert b["own_series_markets_not_in_own_history"] == []
    assert b["owner_attested_not_this_lane"] == [STRANGER]


def test_attesting_one_market_does_not_excuse_another(monkeypatch):
    monkeypatch.setenv(RUN.NOT_THIS_LANE_ENV, STRANGER)
    r = read(own({}), lifetime_basis=basis({STRANGER: "-1.8398",
                                            LOST: "-0.0114"}))
    assert r["reason"] == "OWN_LEDGER_MISSING_SETTLED_MARKETS"
    assert r["btc5m_lifetime_basis"][
        "own_series_markets_not_in_own_history"] == [LOST]


def test_an_attested_market_adds_nothing_and_removes_nothing(monkeypatch):
    h = hist(**{TODAY: "-0.01"})
    doc = basis({STRANGER: "-1.8398", TODAY: "-0.01"})
    monkeypatch.setenv(RUN.NOT_THIS_LANE_ENV, STRANGER)
    with_it = read(own(h), lifetime_basis=doc)
    assert with_it["lifetime_realized_usd"] == \
        read(own(h), lifetime_basis=basis({TODAY: "-0.01"}))[
            "lifetime_realized_usd"]


def test_only_whole_exact_slugs_are_ever_exempted(monkeypatch):
    for bad in ("cpc-btc-updown-5m-", "*", "cpc-btc-updown-5m-2026-11-18-*",
                "CPC-BTC-UPDOWN-5M-2026-11-18-1540Z", "short", ""):
        monkeypatch.setenv(RUN.NOT_THIS_LANE_ENV, bad)
        assert STRANGER not in RUN.not_this_lane_markets()
        r = read(own({}), lifetime_basis=basis({STRANGER: "-1.8398"}))
        assert r["reason"] == "OWN_LEDGER_MISSING_SETTLED_MARKETS", bad
    monkeypatch.setenv(RUN.NOT_THIS_LANE_ENV,
                       " %s , cpc-btc-updown-5m-2026-11-17-0150z " % STRANGER)
    assert RUN.not_this_lane_markets() == (
        "cpc-btc-updown-5m-2026-11-17-0150z", STRANGER)


def test_unset_changes_nothing(monkeypatch):
    monkeypatch.delenv(RUN.NOT_THIS_LANE_ENV, raising=False)
    r = read(own({}), lifetime_basis=basis({STRANGER: "-1.8398"}))
    assert r["reason"] == "OWN_LEDGER_MISSING_SETTLED_MARKETS"
