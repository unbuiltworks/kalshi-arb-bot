from copy import deepcopy

from tests.test_weather_certification_dimensions import _clean_row
from src.weather_certification_dimensions import audit_weather_certification_dimensions


def _semantics_row():
    row = deepcopy(_clean_row())
    row.update({
        "rounding_tie_semantics_verified": True,
        "correction_revision_semantics_verified": True,
        "settlement_lifecycle_verified": True,
        "terminal_value_transform": "AS_PUBLISHED",
        "settlement_lifecycle_evidence_url": "https://kalshi.com/markets/kxhighmia-26aug24/miami-high-temperature/kxhighmia-26aug24-b92.5",
    })
    return row


def test_canonical_weather_dimensions_require_settlement_rule_semantics():
    row = _semantics_row()
    out = audit_weather_certification_dimensions(row)
    assert out["settlement_rule_semantics_verified"] is True
    assert out["settlement_rules_provenance_verified"] is True
    assert out["executable_shadow_prereqs_verified"] is True


def test_missing_rounding_tie_audit_blocks_canonical_settlement_certification():
    row = _semantics_row()
    row.pop("rounding_tie_semantics_verified")
    out = audit_weather_certification_dimensions(row)
    assert out["settlement_rule_semantics_verified"] is False
    assert out["settlement_rules_provenance_verified"] is False
    assert "ROUNDING_TIE_SEMANTICS_VERIFIED_REQUIRED" in out["settlement_blockers"]
    assert out["executable_shadow_prereqs_verified"] is False


def test_unaudited_terminal_transform_blocks_canonical_settlement_certification():
    row = _semantics_row()
    row["terminal_value_transform"] = "ROUND_NEAREST_INTEGER"
    out = audit_weather_certification_dimensions(row)
    assert out["settlement_rule_semantics_verified"] is False
    assert "TERMINAL_VALUE_TRANSFORM_UNAUDITED" in out["settlement_blockers"]
    assert out["live_probe_ready"] is False
    assert out["order_entry"] is False
