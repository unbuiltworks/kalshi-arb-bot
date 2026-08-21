from src.weather_exact_market_source_audit import audit_exact_market_source


def _base(**overrides):
    data = {
        "series_ticker": "KXHIGHAUS",
        "event_ticker": "KXHIGHAUS-26AUG12",
        "market_ticker": "KXHIGHAUS-26AUG12-B102.5",
        "market_date": "2026-08-12",
        "provider": "NWS",
        "source_url": "https://www.weather.gov/wrh/Climate?wfo=ewx",
        "evidence_url": "https://kalshi.com/markets/kxhighaus/highest-temperature-in-austin/kxhighaus-26aug12",
        "evidence_market_ticker": "KXHIGHAUS-26AUG12-B102.5",
        "evidence_market_date": "2026-08-12",
    }
    data.update(overrides)
    return data


def test_pre_transition_exact_nws_market_can_verify_without_current_series_provider():
    row = audit_exact_market_source(**_base())
    assert row["verified"] is True
    assert row["settlement_source_provider"] == "NWS"
    assert row["read_only"] is True
    assert row["order_entry"] is False
    assert row["live_probe_ready"] is False


def test_post_transition_exact_twc_market_can_verify():
    row = audit_exact_market_source(**_base(
        event_ticker="KXHIGHAUS-26AUG21",
        market_ticker="KXHIGHAUS-26AUG21-B102.5",
        market_date="2026-08-21",
        provider="THE_WEATHER_COMPANY",
        source_url="https://weather.com/kalshi",
        evidence_url="https://kalshi.com/markets/kxhighaus/highest-temperature-in-austin/kxhighaus-26aug21",
        evidence_market_ticker="KXHIGHAUS-26AUG21-B102.5",
        evidence_market_date="2026-08-21",
    ))
    assert row["verified"] is True
    assert row["settlement_source_provider"] == "THE_WEATHER_COMPANY"


def test_series_prefix_or_evidence_mismatch_fails_closed():
    row = audit_exact_market_source(**_base(
        market_ticker="KXHIGHMIA-26AUG12-B102.5",
        evidence_market_date="2026-08-13",
    ))
    assert row["verified"] is False
    assert "EXACT_MARKET_IDENTITY_UNVERIFIED" in row["blockers"]
    assert "EXACT_MARKET_EVIDENCE_MISMATCH" in row["blockers"]


def test_unofficial_or_wrong_source_endpoint_fails_closed():
    row = audit_exact_market_source(**_base(
        provider="THE_WEATHER_COMPANY",
        source_url="https://weather.com/weather/today",
        evidence_url="https://example.com/kalshi-market-copy",
    ))
    assert row["verified"] is False
    assert "EXACT_MARKET_SETTLEMENT_SOURCE_UNVERIFIED" in row["blockers"]
    assert "EXACT_MARKET_EVIDENCE_UNOFFICIAL" in row["blockers"]


def test_stringly_or_missing_dates_do_not_pass():
    row = audit_exact_market_source(**_base(
        market_date="2026-8-12",
        evidence_market_date="2026-8-12",
    ))
    assert row["verified"] is False
    assert "EXACT_MARKET_EVIDENCE_MISMATCH" in row["blockers"]
