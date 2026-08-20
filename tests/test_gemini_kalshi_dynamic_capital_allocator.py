from decimal import Decimal

import pytest

from src.gemini_kalshi_dynamic_capital_allocator import (
    AtomicVenueReservationBook,
    CapitalReservationPlan,
    choose_largest_reservable,
    plan_exact_pair_reservation,
)
from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee

D = Decimal


def payloads(*, q="5", gemini_outcome="YES", gemini_price="0.20", kalshi_side="ask", kalshi_yes_price="0.75"):
    return (
        {
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "FOK",
            "quantity": q,
            "price": gemini_price,
            "eventOutcome": gemini_outcome,
        },
        {
            "side": kalshi_side,
            "time_in_force": "fill_or_kill",
            "count": f"{D(q):.2f}",
            "price": kalshi_yes_price,
        },
    )


def test_gemini_yes_kalshi_no_ask_uses_no_cost_and_exact_fees():
    g, k = payloads(q="25", gemini_outcome="YES", gemini_price="0.20", kalshi_side="ask", kalshi_yes_price="0.75")
    plan = plan_exact_pair_reservation(g, k, kalshi_fee_multiplier=D("1"))

    assert plan.quantity == D("25")
    assert plan.direction == "GEMINI_YES_KALSHI_NO"
    assert plan.kalshi_cost_price == D("0.25")
    assert plan.gemini_fee == gemini_predictions_taker_fee(D("0.20"), D("25"))
    assert plan.kalshi_fee == kalshi_quadratic_taker_fee(D("0.75"), D("25"), fee_multiplier=D("1"))
    assert plan.gemini_exact_required == D("25") * D("0.20") + plan.gemini_fee
    assert plan.kalshi_exact_required == D("25") * D("0.25") + plan.kalshi_fee
    assert plan.gemini_reserved == plan.gemini_exact_required + D("0.01")
    assert plan.kalshi_reserved == plan.kalshi_exact_required + D("0.01")


def test_kalshi_yes_gemini_no_bid_uses_yes_cost():
    g, k = payloads(q="10", gemini_outcome="NO", gemini_price="0.18", kalshi_side="bid", kalshi_yes_price="0.76")
    plan = plan_exact_pair_reservation(g, k, kalshi_fee_multiplier=D("1"))

    assert plan.direction == "KALSHI_YES_GEMINI_NO"
    assert plan.kalshi_cost_price == D("0.76")
    assert plan.kalshi_exact_required == D("10") * D("0.76") + plan.kalshi_fee


def test_quantity_above_25_is_fail_closed():
    g, k = payloads(q="26")
    with pytest.raises(ValueError, match="1 to 25"):
        plan_exact_pair_reservation(g, k, kalshi_fee_multiplier=D("1"))


def test_payloads_must_be_complementary():
    g, k = payloads(q="5", gemini_outcome="YES", kalshi_side="bid")
    with pytest.raises(ValueError, match="not complementary"):
        plan_exact_pair_reservation(g, k, kalshi_fee_multiplier=D("1"))


def test_atomic_pair_reservation_never_leaves_one_venue_reserved():
    g, k = payloads(q="10", gemini_outcome="YES", gemini_price="0.20", kalshi_side="ask", kalshi_yes_price="0.75")
    plan = plan_exact_pair_reservation(g, k, kalshi_fee_multiplier=D("1"))
    # Gemini can fund its side; Kalshi deliberately cannot.
    book = AtomicVenueReservationBook(
        gemini_available=plan.gemini_reserved + D("10"),
        kalshi_available=plan.kalshi_reserved - D("0.01"),
    )

    with pytest.raises(RuntimeError, match="insufficient pair capital"):
        book.reserve_pair("attempt-1", plan)

    assert book.reservations == {}
    assert book.reserved_for("gemini") == D("0")
    assert book.reserved_for("kalshi") == D("0")


def test_reservations_cannot_oversubscribe_shared_venue_balance():
    g, k = payloads(q="5", gemini_outcome="YES", gemini_price="0.20", kalshi_side="ask", kalshi_yes_price="0.75")
    plan = plan_exact_pair_reservation(g, k, kalshi_fee_multiplier=D("1"))
    book = AtomicVenueReservationBook(
        gemini_available=plan.gemini_reserved * D("1.5"),
        kalshi_available=plan.kalshi_reserved * D("2"),
    )
    book.reserve_pair("first", plan)
    with pytest.raises(RuntimeError):
        book.reserve_pair("second", plan)
    assert list(book.reservations) == ["first"]
    assert book.reserved_for("gemini") == plan.gemini_reserved
    assert book.reserved_for("kalshi") == plan.kalshi_reserved


def test_choose_largest_reservable_falls_back_when_one_venue_is_short():
    g25, k25 = payloads(q="25", gemini_outcome="YES", gemini_price="0.20", kalshi_side="ask", kalshi_yes_price="0.75")
    g10, k10 = payloads(q="10", gemini_outcome="YES", gemini_price="0.20", kalshi_side="ask", kalshi_yes_price="0.75")
    g5, k5 = payloads(q="5", gemini_outcome="YES", gemini_price="0.20", kalshi_side="ask", kalshi_yes_price="0.75")
    p25 = plan_exact_pair_reservation(g25, k25, kalshi_fee_multiplier=D("1"))
    p10 = plan_exact_pair_reservation(g10, k10, kalshi_fee_multiplier=D("1"))
    p5 = plan_exact_pair_reservation(g5, k5, kalshi_fee_multiplier=D("1"))

    chosen = choose_largest_reservable(
        [p5, p25, p10],
        gemini_free=p25.gemini_reserved + D("1"),
        kalshi_free=p10.kalshi_reserved,
    )
    assert chosen is not None
    assert chosen.quantity == D("10")


def test_choose_none_when_no_supported_plan_is_fundable():
    g, k = payloads(q="5")
    plan = plan_exact_pair_reservation(g, k, kalshi_fee_multiplier=D("1"))
    assert choose_largest_reservable(
        [plan], gemini_free=D("0.50"), kalshi_free=D("0.50")
    ) is None
