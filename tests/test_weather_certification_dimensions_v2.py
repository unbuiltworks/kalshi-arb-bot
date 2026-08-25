from copy import deepcopy

from src.weather_certification_dimensions_v2 import audit_weather_certification_dimensions_v2
from tests.test_weather_certification_dimensions import _clean_row


def test_clean_legacy_book_remains_read_only_certified_shadow():
    out = audit_weather_certification_dimensions_v2(_clean_row())
    assert out["book_representation_verified"] is True
    assert out["market_data_verified"] is True
    assert out["executable_shadow_prereqs_verified"] is True
    assert out["order_entry"] is False
    assert out["live_probe_ready"] is False


def test_fixed_point_fields_are_authoritative_when_present():
    row = _clean_row()
    row.update({
        "yes_ask_dollars": "0.40",
        "yes_ask_qty_fp": "0.99",
    })
    out = audit_weather_certification_dimensions_v2(row)
    assert out["yes_book_representation_audit"]["fixed_point_present"] is True
    assert out["yes_book_representation_audit"]["book_representation_verified"] is False
    assert out["market_data_verified"] is True  # NO side is still independently executable.
    assert out["executable_shadow_prereqs_verified"] is True


def test_both_sides_invalid_fixed_point_cannot_fall_back_to_legacy():
    row = _clean_row()
    row.update({
        "yes_ask_dollars": "0.40",
        "yes_ask_qty_fp": "0.99",
        "no_ask_dollars": "0.61",
        "no_ask_qty_fp": "0.75",
    })
    out = audit_weather_certification_dimensions_v2(row)
    assert out["book_representation_verified"] is False
    assert out["market_data_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False
    assert "YES_FIXED_POINT_BOOK_REPRESENTATION_INVALID" in out["market_data_blockers"]
    assert "NO_FIXED_POINT_BOOK_REPRESENTATION_INVALID" in out["market_data_blockers"]


def test_conflicting_dual_price_representation_fails_that_side_closed():
    row = _clean_row()
    row.update({
        "yes_ask_dollars": "0.4050",
        "yes_ask_qty_fp": "5.00",
    })
    out = audit_weather_certification_dimensions_v2(row)
    assert out["yes_book_representation_audit"]["book_representation_verified"] is False
    assert "BOOK_PRICE_REPRESENTATION_CONFLICT" in out["yes_book_representation_audit"]["blockers"]
    assert out["market_data_verified"] is True  # valid NO side still provides executable depth evidence.


def test_partial_fixed_point_representation_cannot_use_clean_legacy_fallback():
    row = _clean_row()
    row.update({"yes_ask_dollars": "0.40"})
    out = audit_weather_certification_dimensions_v2(row)
    assert out["yes_book_representation_audit"]["book_representation_verified"] is False
    assert "FIXED_POINT_BOOK_REPRESENTATION_INVALID" in out["yes_book_representation_audit"]["blockers"]


def test_wrapper_does_not_upgrade_failed_base_certification():
    row = deepcopy(_clean_row())
    row["settlement_certified_shadow"] = False
    out = audit_weather_certification_dimensions_v2(row)
    assert out["book_representation_verified"] is True
    assert out["settlement_rules_provenance_verified"] is False
    assert out["executable_shadow_prereqs_verified"] is False
