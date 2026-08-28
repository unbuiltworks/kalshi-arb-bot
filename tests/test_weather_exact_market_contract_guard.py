from src.weather_exact_market_contract_guard import audit_exact_weather_market_contract


def _market(**overrides):
    market = {
        "ticker": "KXHIGHAUS-26AUG28-B102.5",
        "event_ticker": "KXHIGHAUS-26AUG28",
        "strike_type": "between",
        "floor_strike": 102.0,
        "cap_strike": 103.0,
        "rules_primary": "Primary settlement rule text.",
        "rules_secondary": "Secondary settlement rule text.",
    }
    market.update(overrides)
    return market


def _audit(**overrides):
    args = {
        "target_market_ticker": "KXHIGHAUS-26AUG28-B102.5",
        "target_event_ticker": "KXHIGHAUS-26AUG28",
        "api_request_url": "https://external-api.kalshi.com/trade-api/v2/markets/KXHIGHAUS-26AUG28-B102.5",
        "api_market": _market(),
    }
    args.update(overrides)
    return audit_exact_weather_market_contract(**args)


def test_exact_get_market_response_can_verify_contract_identity_and_bracket():
    row = _audit()
    assert row["exact_market_contract_verified"] is True
    assert row["strike_type"] == "between"
    assert row["floor_strike"] == 102.0
    assert row["cap_strike"] == 103.0
    assert row["read_only"] is True
    assert row["order_entry"] is False
    assert row["live_probe_ready"] is False


def test_sibling_market_response_cannot_certify_target_contract():
    row = _audit(api_market=_market(ticker="KXHIGHAUS-26AUG28-B103.5"))
    assert row["exact_market_contract_verified"] is False
    assert "EXACT_MARKET_API_IDENTITY_MISMATCH" in row["blockers"]


def test_sibling_request_url_cannot_certify_even_if_response_fields_are_asserted_target():
    row = _audit(
        api_request_url="https://external-api.kalshi.com/trade-api/v2/markets/KXHIGHAUS-26AUG28-B103.5"
    )
    assert row["exact_market_contract_verified"] is False
    assert "EXACT_MARKET_API_IDENTITY_MISMATCH" in row["blockers"]


def test_wrong_event_cannot_certify_target_contract():
    row = _audit(api_market=_market(event_ticker="KXHIGHAUS-26AUG29"))
    assert row["exact_market_contract_verified"] is False
    assert "EXACT_MARKET_API_IDENTITY_MISMATCH" in row["blockers"]


def test_between_market_requires_both_ordered_numeric_boundaries():
    missing = _audit(api_market=_market(cap_strike=None))
    reversed_bounds = _audit(api_market=_market(floor_strike=104, cap_strike=103))
    assert "EXACT_MARKET_RANGE_STRIKES_UNVERIFIED" in missing["blockers"]
    assert "EXACT_MARKET_RANGE_STRIKES_UNVERIFIED" in reversed_bounds["blockers"]


def test_greater_and_less_markets_require_their_authoritative_boundary():
    greater = _audit(api_market=_market(strike_type="greater", floor_strike=None, cap_strike=103))
    less = _audit(api_market=_market(strike_type="less", floor_strike=102, cap_strike=None))
    assert "EXACT_MARKET_FLOOR_STRIKE_UNVERIFIED" in greater["blockers"]
    assert "EXACT_MARKET_CAP_STRIKE_UNVERIFIED" in less["blockers"]


def test_unknown_strike_type_or_missing_rules_fail_closed():
    row = _audit(api_market=_market(strike_type="mystery", rules_secondary=""))
    assert row["exact_market_contract_verified"] is False
    assert "EXACT_MARKET_STRIKE_TYPE_UNVERIFIED" in row["blockers"]
    assert "EXACT_MARKET_SECONDARY_RULES_REQUIRED" in row["blockers"]


def test_noncanonical_api_host_or_path_cannot_bind_request_identity():
    row = _audit(
        api_request_url="https://kalshi.com/trade-api/v2/markets/KXHIGHAUS-26AUG28-B102.5"
    )
    assert row["exact_market_contract_verified"] is False
    assert row["api_request_ticker"] is None
    assert "EXACT_MARKET_API_IDENTITY_MISMATCH" in row["blockers"]
