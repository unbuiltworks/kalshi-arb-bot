"""Opportunities lost because a sibling lane held the shared room.

The four lanes are separate processes drawing on ONE reservation, so
the room goes to whichever lane asks first. That is neither equal
allocation nor expected-value allocation. Before replacing it we need
to know whether arrival order ever actually cost us an order, so the
window report has to carry the refusals rather than swallow them.
"""
from decimal import Decimal as D

from src.l5_btc5m_runner import BUDGET_REFUSAL, _budget_exhausted


def _refusal(adding="1.50", ev=None, **kw):
    order = {"side": "YES", "price": D("0.97"), "qty": D("2"),
             "strategy": "LATE_FAVORITE"}
    order.update(kw)
    return {"reason": BUDGET_REFUSAL, "order": order,
            "detail": {"adding": D(adding), "aggregate_usd": D("9.10"),
                       "cap_usd": D("10.00")}}


def test_a_window_that_refused_nothing_reports_a_plain_zero():
    out = _budget_exhausted([{"refused": []}, {"refused": []}])
    assert out["count"] == 0
    assert out["notional_usd"] == "0"
    assert out["orders"] == []


def test_no_events_at_all_is_not_an_error():
    assert _budget_exhausted(None)["count"] == 0
    assert _budget_exhausted(())["count"] == 0


def test_a_budget_refusal_is_counted_with_its_notional():
    out = _budget_exhausted([{"refused": [_refusal("1.50")],
                              "t_in_window_s": 41}])
    assert out["count"] == 1
    assert out["notional_usd"] == "1.50"
    o = out["orders"][0]
    assert o["side"] == "YES"
    assert o["price"] == "0.97"
    assert o["notional_usd"] == "1.50"
    assert o["aggregate_at_the_time_usd"] == D("9.10")
    assert o["cap_usd"] == D("10.00")
    assert o["t_in_window_s"] == 41


def test_notional_accumulates_across_steps():
    out = _budget_exhausted([{"refused": [_refusal("1.50")]},
                             {"refused": [_refusal("0.75")]},
                             {"refused": [_refusal("2.00")]}])
    assert out["count"] == 3
    assert out["notional_usd"] == "4.25"


def test_other_refusals_are_not_mistaken_for_budget_exhaustion():
    #: a market that simply was not tradable is not a lost
    #: opportunity -- counting it would invent a case for
    #: cross-lane allocation that the tape does not support
    other = {"reason": "PERSISTENT_MODE:NOT_ARMED", "order": {},
             "detail": {"adding": D("1.50")}}
    out = _budget_exhausted([{"refused": [other, _refusal("1.50")]}])
    assert out["count"] == 1
    assert out["notional_usd"] == "1.50"


def test_a_refusal_missing_its_notional_is_still_counted():
    #: fail loud, not silent: the opportunity was lost whether or not
    #: the detail carried a number, so the count must not drop it
    bad = {"reason": BUDGET_REFUSAL, "order": {"side": "NO"},
           "detail": {}}
    out = _budget_exhausted([{"refused": [bad]}])
    assert out["count"] == 1
    assert out["orders"][0]["notional_usd"] is None
    assert out["notional_usd"] == "0"


def test_malformed_records_are_skipped_rather_than_raising():
    out = _budget_exhausted(["nonsense", 7, None,
                             {"refused": ["nonsense", None]},
                             {"refused": [_refusal("1.00")]}])
    assert out["count"] == 1


def test_the_block_says_what_a_high_count_would_mean():
    #: the number is useless to the operator without the reading
    note = _budget_exhausted([])["note"]
    assert "expected value" in note
    assert "sibling" in note


def test_the_real_refusal_shape_the_reservation_emits_is_read():
    #: l5_shared_reservation returns these as STRINGS, not Decimals,
    #: and the intent dict carries `reason` rather than `strategy`.
    #: A reader that only handled Decimals would silently report $0
    #: of lost opportunity in production while counting the orders.
    real = {"reason": BUDGET_REFUSAL,
            "order": {"side": "NO", "price": D("0.98"), "qty": D("3"),
                      "reason": "LATE_FAVORITE_OPEN"},
            "detail": {"aggregate_usd": "9.10", "adding": "2.94",
                       "cap_usd": "10.00"}}
    out = _budget_exhausted([{"refused": [real], "t_in_window_s": 275}])
    assert out["count"] == 1
    assert out["notional_usd"] == "2.94"
    o = out["orders"][0]
    assert o["strategy"] == "LATE_FAVORITE_OPEN"
    assert o["notional_usd"] == "2.94"
    assert o["aggregate_at_the_time_usd"] == "9.10"
