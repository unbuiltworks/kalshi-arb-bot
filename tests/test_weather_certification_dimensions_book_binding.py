from src.weather_certification_dimensions import audit_weather_certification_dimensions


def _market_row():
    return {
        "blockers": [],
        "quote_age_ms": 100,
        "max_quote_age_ms": 2000,
        "quote_observed_at": "2026-08-25T02:55:00Z",
        "quote_age_evaluated_at": "2026-08-25T02:55:00.100Z",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }


def test_partial_fixed_point_cannot_fall_back_to_clean_legacy_book():
    row = _market_row()
    row.update({
        "yes_ask_dollars": "0.40",
        # Missing yes_ask_qty_fp is intentional. Legacy fields must not rescue it.
        "yes_ask_cents": 40,
        "yes_available_contracts": 5,
    })
    out = audit_weather_certification_dimensions(row)
    assert out["market_data_verified"] is False
    assert out["book_representation_verified"] is False
    assert "YES_FIXED_POINT_BOOK_REPRESENTATION_INVALID" in out["market_data_blockers"]
    assert out["yes_book_representation_audit"]["book_representation_verified"] is False


def test_conflicting_fixed_and_legacy_prices_fail_closed():
    row = _market_row()
    row.update({
        "yes_ask_dollars": "0.385",
        "yes_ask_qty_fp": "5.00",
        "yes_ask_cents": 40,
        "yes_available_contracts": 5,
    })
    out = audit_weather_certification_dimensions(row)
    assert out["market_data_verified"] is False
    assert "YES_BOOK_PRICE_REPRESENTATION_CONFLICT" in out["market_data_blockers"]


def test_valid_fixed_point_side_is_sufficient_without_legacy_fallback():
    row = _market_row()
    row.update({
        "yes_ask_dollars": "0.385",
        "yes_ask_qty_fp": "1.25",
    })
    out = audit_weather_certification_dimensions(row)
    assert out["quote_freshness_binding_verified"] is True
    assert out["book_representation_verified"] is True
    assert out["market_data_verified"] is True
    assert out["yes_book_representation_audit"]["book_representation_source"] == "fixed_point"
    assert out["no_book_representation_audit"]["book_representation_verified"] is False
