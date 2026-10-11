"""F -- the execution simulator, and the contradiction it was blind to.

THE DEFECT CI15M-1 PAID FOR. The frozen policy selects an IMMEDIATELY
EXECUTABLE acquisition price: it buys UP at the ASK. The authorized
transport sent `makerOrCancel: true`. A post-only order at a crossing price
is cancelled by definition, so zero fills were STRUCTURALLY GUARANTEED --
twelve orders, twelve non-fills, the venue cancelling several in the same
millisecond it created them.

The dry-run stub never caught it because it filled anything at the limit.
A simulator that fills an order the venue would refuse is worse than no
simulator: it manufactures confidence. These tests pin the real semantics.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src.gemini_execution import payload, spec
from src.gemini_execution.ci15m_sim import (CANCELLED, FILLED, RESTING,
                                            simulate)


# =====================================================================
# the exact CI15M-1 case
# =====================================================================
def test_the_ci15m_1_case_a_crossing_moc_buy_is_cancelled_never_filled():
    """ask = 0.70, BUY @ 0.70, makerOrCancel = true -> CANCELLED.

    Verbatim from the venue, order 145828836710181412:
        request price 0.70, makerOrCancel true
        body    status "cancelled", timeInForce "maker-or-cancel"
                createdAt 17:04:37.509Z  cancelledAt 17:04:37.503Z
    """
    r = simulate(side="UP", price=D("0.70"), qty=D("1"),
                 maker_or_cancel=True, bid=D("0.60"), ask=D("0.70"))
    assert r["outcome"] == CANCELLED
    assert r["filled_qty"] == D("0")
    assert r["would_cross"] is True
    assert "post-only" in r["why"]


@pytest.mark.parametrize("ask,price", [("0.70", "0.70"), ("0.70", "0.71"),
                                       ("0.86", "0.86"), ("0.86", "0.90"),
                                       ("0.30", "0.55")])
def test_any_moc_buy_at_or_above_the_ask_is_cancelled(ask, price):
    r = simulate(side="UP", price=D(price), qty=D("1"), maker_or_cancel=True,
                 bid=D("0.10"), ask=D(ask))
    assert r["outcome"] == CANCELLED, (ask, price, r)


def test_the_simulator_never_fills_a_crossing_post_only_order():
    """Stated as the invariant rather than as a case: across the whole
    1-cent grid, no post-only buy at or above the ask may fill."""
    for a in range(2, 100):
        ask = D(a) / 100
        for p in range(a, 101):
            r = simulate(side="UP", price=D(p) / 100, qty=D("1"),
                         maker_or_cancel=True, bid=(ask - D("0.01")),
                         ask=ask)
            assert r["outcome"] != FILLED, (ask, p, r)


# =====================================================================
# a genuinely resting maker order, tested separately
# =====================================================================
def test_a_non_crossing_moc_buy_rests_and_does_not_fill_on_arrival():
    """RESTING is not FILLED. CI15M-1 left four orders in exactly this
    state (slots 2-5, status `open`) and they are the reason exposure could
    not be called $0 from our own records alone."""
    r = simulate(side="UP", price=D("0.60"), qty=D("1"), maker_or_cancel=True,
                 bid=D("0.60"), ask=D("0.70"))
    assert r["outcome"] == RESTING
    assert r["filled_qty"] == D("0")
    assert r["would_cross"] is False


def test_a_resting_order_fills_only_when_a_print_comes_through_it():
    r = simulate(side="UP", price=D("0.60"), qty=D("1"), maker_or_cancel=True,
                 bid=D("0.55"), ask=D("0.70"), print_through=D("0.59"))
    assert r["outcome"] == FILLED and r["filled_qty"] == D("1")
    assert r["fill_basis"] == "THROUGH"
    # a print AT our price is not defensible as a fill: queue position is
    # unknown, and the Gemini methodology counts THROUGH only.
    r2 = simulate(side="UP", price=D("0.60"), qty=D("1"),
                  maker_or_cancel=True, bid=D("0.55"), ask=D("0.70"),
                  print_through=D("0.60"))
    assert r2["outcome"] == RESTING


# =====================================================================
# the marketable-limit (taker) path CI15M-2 would use
# =====================================================================
def test_a_marketable_limit_without_post_only_fills_at_the_ask():
    """SEMANTICS-1 proved this live: visible ask 0.50, marketable limit buy,
    filled 1 @ 0.50 in 376 ms -- AT the limit, not above it."""
    r = simulate(side="UP", price=D("0.70"), qty=D("1"),
                 maker_or_cancel=False, bid=D("0.60"), ask=D("0.70"))
    assert r["outcome"] == FILLED
    assert r["fill_price"] == D("0.70")
    assert r["filled_qty"] == D("1")


def test_a_marketable_limit_never_pays_more_than_its_limit():
    r = simulate(side="UP", price=D("0.70"), qty=D("1"),
                 maker_or_cancel=False, bid=D("0.60"), ask=D("0.80"))
    assert r["outcome"] == CANCELLED, "the ask is above our limit"
    assert r["filled_qty"] == D("0")


def test_a_taker_fill_is_capped_by_displayed_size():
    r = simulate(side="UP", price=D("0.70"), qty=D("5"),
                 maker_or_cancel=False, bid=D("0.60"), ask=D("0.70"),
                 ask_size=D("2"))
    assert r["filled_qty"] == D("2") and r["outcome"] == "PARTIAL"


# =====================================================================
# the payload difference between CI15M-1 and the proposed CI15M-2
# =====================================================================
def test_the_only_payload_field_that_differs_is_makerOrCancel():
    """D -- shown rather than asserted in prose. Everything else is
    identical, so the change is exactly one boolean and nothing else."""
    kw = dict(symbol="GEMI-BTC15M2610031730-UP", price="0.70", quantity="1",
              outcome=spec.OUTCOME_YES)
    one = payload.build_order(maker_or_cancel=True, **kw)
    two = payload.build_order(maker_or_cancel=False, **kw)
    assert set(one) == set(two)
    diff = {k for k in one if one[k] != two[k]}
    assert diff == {"makerOrCancel"}, diff
    assert one["makerOrCancel"] is True and two["makerOrCancel"] is False
    assert two["orderType"] == spec.ORDER_TYPE_LIMIT, "still a LIMIT order"
    assert two["timeInForce"] == spec.TIME_IN_FORCE_GTC
    assert two["price"] == "0.70", "a limit, never an unrestricted market"


def test_the_taker_payload_is_refused_by_the_validator_without_optin():
    """It is not reachable by accident: assert_types refuses
    makerOrCancel False unless the caller explicitly opts in, and
    transport additionally requires the matching authorization."""
    two = payload.build_order(symbol="S", price="0.70", quantity="1",
                              maker_or_cancel=False)
    with pytest.raises(payload.PayloadViolation):
        payload.assert_types(two)                     # no opt-in
    assert payload.assert_types(two, allow_taker=True)


# =====================================================================
# the BOOK ADAPTER: price comes from the EXECUTABLE ladder, and the
# contract price is telemetry that prices nothing.
# =====================================================================
def test_the_adapter_prices_from_the_ladder_not_the_contract(monkeypatch):
    """THE CI15M-1 ROOT CAUSE, as a regression. The contract said the ask
    was 0.70; the executable ladder said 0.82. A limit at 0.70 rests."""
    from src.gemini_execution import ci15m_probe as P
    from src.gemini_execution import metadata

    monkeypatch.setattr(metadata, "contract_depth", lambda sym, **k: {
        "readable": True,
        "bids": {"best": "0.63", "best_qty": "500"},
        "asks": {"best": "0.82", "best_qty": "400"},
        "snapshot_age_s": 0.2, "venue_ts": "1"})
    monkeypatch.setattr(metadata, "outcome_book", lambda sym: {
        "readable": True, "identity_consistent": True,
        "yes": {"bid": "0.60", "ask": "0.70"},
        "no": {"bid": "0.30", "ask": "0.40"}})
    bk = P.book_from_venue("GEMI-BTC15M2610031715-UP")
    assert bk["readable"] is True
    assert bk["ask"] == D("0.82"), "the price must come from the ladder"
    assert bk["bid"] == D("0.63")
    assert "EXECUTABLE ladder" in bk["source"]
    tel = bk["contract_price_telemetry"]
    assert tel["read"] is True
    assert tel["contract_ask"] == "0.70" and tel["ladder_ask"] == "0.82"
    assert tel["ask_gap_contract_minus_ladder"] == "-0.12"
    assert tel["contract_ask_would_have"] == "RESTED"


def test_the_adapter_still_works_when_the_contract_price_is_unreadable(
        monkeypatch):
    """Telemetry must never be able to block a decision."""
    from src.gemini_execution import ci15m_probe as P
    from src.gemini_execution import metadata
    monkeypatch.setattr(metadata, "contract_depth", lambda sym, **k: {
        "readable": True,
        "bids": {"best": "0.63", "best_qty": "500"},
        "asks": {"best": "0.82", "best_qty": "400"}})
    monkeypatch.setattr(metadata, "outcome_book",
                        lambda sym: {"readable": False, "why": "no prices"})
    bk = P.book_from_venue("S")
    assert bk["readable"] is True and bk["ask"] == D("0.82")
    assert bk["contract_price_telemetry"]["read"] is False


def test_an_unreadable_or_one_sided_ladder_fails_closed(monkeypatch):
    from src.gemini_execution import ci15m_probe as P
    from src.gemini_execution import metadata
    for dep in ({"readable": False, "why": "503"},
                {"readable": True, "bids": {"best": None},
                 "asks": {"best": "0.82"}},
                {"readable": True, "bids": {"best": "0.90"},
                 "asks": {"best": "0.80"}}):
        monkeypatch.setattr(metadata, "contract_depth", lambda s, _d=dep,
                            **k: _d)
        bk = P.book_from_venue("S")
        assert bk["readable"] is False, dep


# =====================================================================
# the two TREATMENTS
# =====================================================================
def test_the_maker_treatment_never_crosses():
    from src.gemini_execution.ci15m_treatments import maker_price
    for b in range(1, 99):
        for a in range(b + 1, 100):
            bid, ask = D(b) / 100, D(a) / 100
            r = maker_price("UP", bid, ask, ceiling=D("0.90"))
            if r["ok"]:
                assert r["price"] < ask, (bid, ask, r)
                assert r["crosses"] is False


def test_the_taker_treatment_always_crosses_and_is_capped():
    from src.gemini_execution.ci15m_treatments import taker_price
    r = taker_price("UP", D("0.60"), D("0.70"), ceiling=D("0.90"))
    assert r["ok"] and r["price"] == D("0.70") and r["crosses"] is True
    assert r["maker_or_cancel"] is False
    over = taker_price("UP", D("0.60"), D("0.95"), ceiling=D("0.90"))
    assert over["ok"] is False and "ceiling" in over["why"]


def test_the_maker_treatment_is_labelled_as_an_experiment():
    """It must never be described as an immediately executable
    acquisition -- that mislabel is what CI15M-1 acted on."""
    from src.gemini_execution.ci15m_treatments import maker_price
    r = maker_price("UP", D("0.60"), D("0.70"), ceiling=D("0.90"))
    assert "MAKER EXPERIMENT" in r["labels"]
    assert "NOT an immediately" in r["labels"]


def test_both_treatments_price_the_same_decision():
    from src.gemini_execution.ci15m_treatments import both
    r = both("UP", D("0.60"), D("0.70"), ceiling=D("0.90"))
    assert r["side"] == "UP"
    assert r["M"]["price"] == D("0.61") and r["T"]["price"] == D("0.70")
    assert r["differ"] is True and r["improvement"] == "0.09"


def test_the_treatments_cannot_read_or_change_a_policy_parameter():
    import src.gemini_execution.ci15m_treatments as T
    src = open(T.__file__, encoding="utf-8").read()
    for banned in ("EXTEND_MIN_AVG_IMPROVEMENT", "MAX_PAIR_COST",
                   "OPEN_SIDE_RULE", "decide_once", "want_extend",
                   "want_complement", "Inventory"):
        assert banned not in src, banned
