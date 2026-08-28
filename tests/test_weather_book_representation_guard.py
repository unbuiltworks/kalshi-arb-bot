from src.weather_book_representation_guard import audit_weather_book_representation


def test_fixed_point_representation_is_accepted_when_executable():
    row = {"yes_ask_dollars": "0.40", "yes_ask_qty_fp": "1.25"}
    out = audit_weather_book_representation(row, "yes")
    assert out["book_representation_verified"] is True
    assert out["book_representation_source"] == "fixed_point"
    assert out["order_entry"] is False and out["live_probe_ready"] is False


def test_malformed_fixed_point_does_not_fall_back_to_clean_legacy_fields():
    row = {
        "yes_ask_dollars": "NaN",
        "yes_ask_qty_fp": "5",
        "yes_ask_cents": 40,
        "yes_available_contracts": 5,
    }
    out = audit_weather_book_representation(row, "yes")
    assert out["book_representation_verified"] is False
    assert "FIXED_POINT_BOOK_REPRESENTATION_INVALID" in out["blockers"]


def test_partial_fixed_point_does_not_fall_back_to_legacy_fields():
    row = {
        "yes_ask_dollars": "0.40",
        "yes_ask_cents": 40,
        "yes_available_contracts": 5,
    }
    out = audit_weather_book_representation(row, "yes")
    assert out["book_representation_verified"] is False
    assert "FIXED_POINT_BOOK_REPRESENTATION_INVALID" in out["blockers"]


def test_conflicting_fixed_and_legacy_prices_fail_closed():
    row = {
        "yes_ask_dollars": "0.4050",
        "yes_ask_qty_fp": "5",
        "yes_ask_cents": 40,
        "yes_available_contracts": 5,
    }
    out = audit_weather_book_representation(row, "yes")
    assert out["book_representation_verified"] is False
    assert "BOOK_PRICE_REPRESENTATION_CONFLICT" in out["blockers"]


def test_matching_dual_representation_is_accepted():
    row = {
        "yes_ask_dollars": "0.40",
        "yes_ask_qty_fp": "5.25",
        "yes_ask_cents": 40,
        "yes_available_contracts": 5,
    }
    out = audit_weather_book_representation(row, "yes")
    assert out["book_representation_verified"] is True


def test_sub_q1_fixed_point_depth_is_not_executable_even_if_legacy_claims_q1():
    row = {
        "no_ask_dollars": "0.61",
        "no_ask_qty_fp": "0.99",
        "no_ask_cents": 61,
        "no_available_contracts": 5,
    }
    out = audit_weather_book_representation(row, "no")
    assert out["book_representation_verified"] is False
    assert "FIXED_POINT_BOOK_REPRESENTATION_INVALID" in out["blockers"]
