from src.weather_certification_dimensions import _exact_market_source_audit


def _v2_row():
    return {
        "series_ticker": "KXHIGHMIA",
        "event_ticker": "KXHIGHMIA-26AUG24",
        "market_ticker": "KXHIGHMIA-26AUG24-B92.5",
        "market_date": "2026-08-24",
        # Shape emitted by weather_settlement_certified_v2: authoritative normalized
        # source evidence lives in the nested audit, including the evidence-side exact
        # market identity that downstream trust boundaries must replay rather than
        # synthesizing from the target row.
        "exact_market_source_audit": {
            "verified": True,
            "settlement_source_provider": "THE_WEATHER_COMPANY",
            "settlement_source_url": "https://weather.com/kalshi",
            "evidence_url": "https://kalshi.com/markets/kxhighmia-26aug24/miami-high-temperature/kxhighmia-26aug24-b92.5",
            "evidence_market_ticker": "KXHIGHMIA-26AUG24-B92.5",
            "evidence_market_date": "2026-08-24",
        },
    }


def test_v2_nested_exact_source_evidence_is_replayed_not_false_negative():
    out = _exact_market_source_audit(_v2_row())
    assert out["verified"] is True
    assert out["settlement_source_provider"] == "THE_WEATHER_COMPANY"


def test_v2_nested_verified_boolean_cannot_override_bad_evidence():
    row = _v2_row()
    row["exact_market_source_audit"]["evidence_url"] = (
        "https://kalshi.com/markets/kxhighny-26aug24/new-york-high-temperature/kxhighny-26aug24-b80.5"
    )
    out = _exact_market_source_audit(row)
    assert out["verified"] is False
    assert "EXACT_MARKET_EVIDENCE_NOT_BOUND_TO_EVENT" in out["blockers"]


def test_v2_nested_verified_boolean_cannot_replace_missing_evidence_identity():
    row = _v2_row()
    row["exact_market_source_audit"].pop("evidence_market_ticker")
    row["exact_market_source_audit"].pop("evidence_market_date")
    out = _exact_market_source_audit(row)
    assert out["verified"] is False
    assert "EXACT_MARKET_EVIDENCE_MISMATCH" in out["blockers"]
