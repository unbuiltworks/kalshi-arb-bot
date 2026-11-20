"""This lane's settled loss is not added to an account figure that already
contains it.

Live 2026-11-20 17:08Z: the account service had adopted, retired and
restated the 14:20Z window (-0.0114), and this lane added the same -0.0114
again -- the day read -0.0228 and the lifetime -39.4908 against a true
-0.0114 and -39.4794. It erred toward refusing, and it was still wrong.
"""
import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_btc5m_runner as RUN
from tests.test_l5_btc5m_runner import NOW, risk_doc

M1 = "cpc-btc-updown-5m-2026-11-20-1420z"
M2 = "cpc-btc-updown-5m-2026-11-20-1705z"


def read(own, **over):
    doc = risk_doc(utc_day_realized_usd="-0.0114",
                   lifetime_realized_usd="-39.4794", daily_halt_usd="50",
                   cumulative_loss_ceiling_usd="75", **over)
    return RUN.account_risk(url="http://x", now=NOW, own_unaccounted=own,
                            fetch=lambda u: doc)


def test_the_live_double_count_as_it_was():
    r = read(D("-0.0114"))                       # the total, no breakdown
    assert r["utc_day_realized_usd"] == "-0.0228"
    assert r["lifetime_realized_usd"] == "-39.4908"


def test_a_market_the_account_service_retired_is_not_added_again():
    r = read(RUN.OwnSettled.of({M1: D("-0.0114")}), retired_markets=[M1])
    assert r["ok"] is True
    assert r["utc_day_realized_usd"] == "-0.0114"
    assert r["lifetime_realized_usd"] == "-39.4794"
    assert D(r["btc5m_losses_not_yet_in_account_ledger_usd"]) == 0
    assert r["btc5m_markets_already_in_account_ledger"] == [M1]
    assert r["btc5m_markets_added"] == []


def test_a_market_it_has_not_retired_is_still_added():
    own = RUN.OwnSettled.of({M1: D("-0.0114"), M2: D("-0.0151")})
    r = read(own, retired_markets=[M1])
    assert D(r["utc_day_realized_usd"]) == D("-0.0114") + D("-0.0151")
    assert r["btc5m_markets_added"] == [M2]
    assert r["btc5m_markets_already_in_account_ledger"] == [M1]


@pytest.mark.parametrize("listed", [None, "x", {M1: 1}, 7])
def test_without_a_readable_list_everything_is_added_as_before(listed):
    over = {} if listed is None else {"retired_markets": listed}
    r = read(RUN.OwnSettled.of({M1: D("-0.0114")}), **over)
    assert r["utc_day_realized_usd"] == "-0.0228"
    assert r["btc5m_markets_added"] == []        # no per-market claim made


def test_a_total_without_a_breakdown_is_added_whatever_is_listed():
    r = read(D("-0.0114"), retired_markets=[M1])
    assert r["utc_day_realized_usd"] == "-0.0228"


def test_a_gain_is_never_added_and_never_offsets_the_account_figure():
    r = read(RUN.OwnSettled.of({M2: D("0.50")}), retired_markets=[])
    assert r["utc_day_realized_usd"] == "-0.0114"


def test_a_retired_gain_cannot_hide_an_unretired_loss():
    own = RUN.OwnSettled.of({M1: D("0.50"), M2: D("-0.0151")})
    r = read(own, retired_markets=[M1])
    assert D(r["utc_day_realized_usd"]) == D("-0.0114") + D("-0.0151")


def test_leaving_a_market_out_can_only_raise_the_room_to_the_true_value():
    with_list = read(RUN.OwnSettled.of({M1: D("-0.0114")}),
                     retired_markets=[M1])
    without = read(D("-0.0114"))
    assert D(with_list["loss_room_usd"]) - D(without["loss_room_usd"]) \
        == D("0.0114")


def test_own_settled_is_still_the_decimal_total():
    own = RUN.OwnSettled.of({M1: D("-0.0114"), M2: D("-0.0151")})
    assert own == D("-0.0265") and isinstance(own, D)
    assert str(own) == "-0.0265"


def test_the_executor_breaks_the_day_down_by_market(tmp_path):
    from src import l5_btc5m_execution as X
    ex = X.Btc5mExecutor(transport=None,
                         ledger_path=str(tmp_path / "ledger.json"))
    at = dt.datetime(2026, 11, 20, 17, 8, tzinfo=dt.timezone.utc)
    ex._ledger["settled"] = {
        M1: {"settled_at": "2026-11-20T14:26:14+00:00",
             "settlement_pnl": "-0.0114"},
        "old": {"settled_at": "2026-11-19T10:00:00+00:00",
                "settlement_pnl": "-9"}}
    assert ex.day_settled_by_market(at) == {M1: D("-0.0114")}
    assert sum(ex.day_settled_by_market(at).values()) \
        == ex.day_settled_pnl(at)
    ex._ledger["settled"]["bad"] = {"settled_at": "?", "settlement_pnl": "1"}
    assert ex.day_settled_by_market(at) is None
