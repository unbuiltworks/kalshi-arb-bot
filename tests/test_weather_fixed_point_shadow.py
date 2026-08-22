from decimal import Decimal

from src.weather_fixed_point_shadow import (
    FIXED_POINT_FEE_BLOCKER,
    build_fixed_point_research_economics,
    normalize_fixed_point_book,
)


def test_accepts_current_fractional_price_and_quantity_without_coercion():
    book = normalize_fixed_point_book({
        "yes_ask_dollars": "0.0550",
        "yes_ask_qty_fp": "0.30",
        "no_ask_dollars": "0.9460",
        "no_ask_qty_fp": "12.75",
    })
    assert book["blockers"] == []
    assert book["sides"]["YES"]["ask_dollars"] == "0.0550"
    assert book["sides"]["YES"]["available_contracts_fp"] == "0.30"
    assert book["sides"]["NO"]["available_contracts_fp"] == "12.75"
    assert book["sides"]["YES"]["book_representation"] == "fixed_point"


def test_does_not_invent_no_depth_from_yes_side():
    book = normalize_fixed_point_book({
        "yes_ask_dollars": "0.4000",
        "yes_ask_qty_fp": "25.50",
    })
    assert set(book["sides"]) == {"YES"}


def test_legacy_whole_cent_book_is_losslessly_retained_as_research():
    book = normalize_fixed_point_book({
        "yes_ask_cents": 40,
        "yes_ask_qty": 5,
    })
    assert book["blockers"] == []
    assert book["sides"]["YES"]["ask_dollars"] == "0.4"
    assert book["sides"]["YES"]["available_contracts_fp"] == "5"
    assert book["sides"]["YES"]["book_representation"] == "legacy_integer_exact"


def test_public_base_fee_is_not_promoted_to_exact_net_fee():
    result = build_fixed_point_research_economics(
        quote={"yes_ask_dollars": "0.5000", "yes_ask_qty_fp": "10.00"},
        fee_type="quadratic",
        fee_multiplier=1,
        yes_probability="0.70",
    )
    assert result["fixed_point_book_verified"] is True
    assert result["exact_fee_context_verified"] is False
    assert result["exact_after_fee_economics_verified"] is False
    assert FIXED_POINT_FEE_BLOCKER in result["blockers"]
    yes = result["book_sides"][0]
    assert Decimal(yes["base_quadratic_fee_dollars"]) == Decimal("0.1750")
    assert yes["base_fee_is_exact_net_fee"] is False
    assert yes["exact_after_fee_model_ev_dollars"] is None
    assert result["live_probe_ready"] is False


def test_verified_exact_fee_context_can_emit_exact_shadow_ev_only():
    result = build_fixed_point_research_economics(
        quote={
            "yes_ask_dollars": "0.4000",
            "yes_ask_qty_fp": "2.50",
            "no_ask_dollars": "0.6100",
            "no_ask_qty_fp": "3.25",
        },
        fee_type="quadratic",
        fee_multiplier=1,
        yes_probability="0.55",
        exact_net_fee_dollars={"YES": "0.0300", "NO": "0.0400"},
        exact_fee_context_verified=True,
    )
    assert result["exact_after_fee_economics_verified"] is True
    assert result["economics_status"] == "EXACT_AFTER_FEE_SHADOW_ECONOMICS"
    assert result["live_probe_ready"] is False
    rows = {row["side"]: row for row in result["book_sides"]}
    assert Decimal(rows["YES"]["exact_after_fee_model_ev_dollars"]) == Decimal("0.34500")
    assert Decimal(rows["NO"]["exact_after_fee_model_ev_dollars"]) == Decimal("-0.56000")


def test_string_true_cannot_verify_exact_fee_context():
    result = build_fixed_point_research_economics(
        quote={"yes_ask_dollars": "0.5000", "yes_ask_qty_fp": "1.00"},
        fee_type="quadratic",
        fee_multiplier=1,
        exact_net_fee_dollars={"YES": "0.0100"},
        exact_fee_context_verified="true",
    )
    assert result["exact_fee_context_verified"] is False
    assert result["exact_after_fee_economics_verified"] is False
    assert FIXED_POINT_FEE_BLOCKER in result["blockers"]


def test_malformed_fixed_point_precision_fails_closed():
    book = normalize_fixed_point_book({
        "yes_ask_dollars": "0.12345",
        "yes_ask_qty_fp": "1.001",
    })
    assert book["sides"] == {}
    assert book["blockers"] == ["FIXED_POINT_BOOK_UNVERIFIED"]
