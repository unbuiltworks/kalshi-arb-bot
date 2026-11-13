"""The account-wide portfolio drawdown limit (owner 2026-11-13)."""
from decimal import Decimal as D

import pytest

from src import l5_portfolio_drawdown as P
from src import l5_live_validation_gates as G

ON = {P.ANCHOR_ENV: "336.68", P.MAX_DRAWDOWN_ENV: "15"}


def test_unset_is_off_and_allows_even_an_unknown_value():
    assert P.config({})["state"] == P.OFF
    assert P.refusal(None, P.config({})) is None


@pytest.mark.parametrize("env,why", [
    ({P.ANCHOR_ENV: "336.68"}, "HALF_CONFIGURED"),
    ({P.MAX_DRAWDOWN_ENV: "15"}, "HALF_CONFIGURED"),
    ({P.ANCHOR_ENV: "x", P.MAX_DRAWDOWN_ENV: "15"}, "UNREADABLE"),
    ({P.ANCHOR_ENV: "336.68", P.MAX_DRAWDOWN_ENV: "nan"}, "UNREADABLE"),
    ({P.ANCHOR_ENV: "336.68", P.MAX_DRAWDOWN_ENV: "0"}, "NOT_POSITIVE"),
    ({P.ANCHOR_ENV: "-1", P.MAX_DRAWDOWN_ENV: "15"}, "NOT_POSITIVE"),
])
def test_a_limit_someone_tried_to_set_never_silently_turns_off(env, why):
    c = P.config(env)
    assert c == {"state": "INVALID", "why": why}
    assert P.refusal(D("999"), c) == "PORTFOLIO_DRAWDOWN_CONFIG_" + why


def test_unknown_value_refuses_when_on():
    assert P.refusal(None, P.config(ON)) == "PORTFOLIO_VALUE_UNAVAILABLE"


def test_trips_at_the_stop_and_not_before():
    c = P.config(ON)
    assert P.refusal(D("321.69"), c) is None                 # 14.99 down
    assert P.refusal(D("321.68"), c).startswith("PORTFOLIO_DRAWDOWN_LIMIT:15")
    assert P.refusal(D("300"), c).startswith("PORTFOLIO_DRAWDOWN_LIMIT")
    assert P.refusal(D("400"), c) is None                    # above anchor


def test_value_counts_only_guaranteed_pair_redemption():
    pos = {"m1": {"UP": D("3.51"), "DOWN": D("2.51")},       # 2.51 pairs
           "m2": {"UP": D("1"), "DOWN": D("0")}}             # lone leg = 0
    assert P.worst_case_value(D("330"), pos) == D("332.51")


def test_value_is_unknown_without_buying_power_or_ledger():
    assert P.worst_case_value(None, {}) is None
    assert P.worst_case_value(D("330"), None) is None
    assert P.worst_case_value(D("330"), {"m": {"UP": "x", "DOWN": "1"}}) is None


def test_report_shows_room():
    r = P.report(D("330"), P.config(ON))
    assert r["state"] == "ON" and r["drawdown_usd"] == "6.68"
    assert r["room_usd"] == "8.32" and r["refusal"] is None


def _gate_refuse(value):
    g = G.ValidationGates()
    return g.refuse(order_qty=D("1"), order_price=D("0.40"),
                    order_intent="ORDER_INTENT_BUY_LONG",
                    portfolio_value_usd=value)


def test_gate_is_additive_and_refuses_on_breach(monkeypatch):
    monkeypatch.delenv(P.ANCHOR_ENV, raising=False)
    monkeypatch.delenv(P.MAX_DRAWDOWN_ENV, raising=False)
    off = _gate_refuse(None)
    assert not (off or "").startswith("PORTFOLIO_")          # OFF: no new refusal
    monkeypatch.setenv(P.ANCHOR_ENV, "336.68")
    monkeypatch.setenv(P.MAX_DRAWDOWN_ENV, "15")
    assert _gate_refuse(D("320")).startswith("PORTFOLIO_DRAWDOWN_LIMIT")
    assert _gate_refuse(None) == "PORTFOLIO_VALUE_UNAVAILABLE"
    ok = _gate_refuse(D("335"))
    assert not (ok or "").startswith("PORTFOLIO_")           # other gates unchanged
    assert ok == off


def test_book_uncertain_still_wins_first(monkeypatch):
    monkeypatch.setenv(P.ANCHOR_ENV, "336.68")
    monkeypatch.setenv(P.MAX_DRAWDOWN_ENV, "15")
    g = G.ValidationGates()
    r = g.refuse(order_qty=D("1"), order_price=D("0.4"),
                 order_intent="ORDER_INTENT_BUY_LONG", book_uncertain="x",
                 portfolio_value_usd=D("1"))
    assert r.startswith("BOOK_UNCERTAIN")
