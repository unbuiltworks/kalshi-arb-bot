from src.weather_shadow_settlement_reconcile import reconcile_weather_paper_fill


def _exact_audit(*, provider="THE_WEATHER_COMPANY", ticker="KXHIGHAUS-26AUG21-B102.5", market_date="2026-08-21", verified=True, source_url=None):
    if source_url is None:
        source_url = "https://weather.com/kalshi" if provider == "THE_WEATHER_COMPANY" else "https://www.weather.gov/product.php?site=NWS&product=CLI&issuedby=AUS"
    return {
        "verified": verified,
        "market_ticker": ticker,
        "market_date": market_date,
        "settlement_source_provider": provider,
        "settlement_source_url": source_url,
    }


def _shadow(**overrides):
    row = {
        "category": "weather",
        "classification": "SHADOW",
        "order_entry": False,
        "live_probe_ready": False,
        "market_ticker": "KXHIGHAUS-26AUG21-B102.5",
        "market_date": "2026-08-21",
        "market_settlement_source_provider": "THE_WEATHER_COMPANY",
        "settlement_source_regime_verified": False,
        "exact_market_source_verified": True,
        "exact_market_source_audit": _exact_audit(),
        "settlement_certified_shadow": True,
        "strike_type": "between",
        "floor_strike": 102,
        "cap_strike": 103,
    }
    row.update(overrides)
    return row


def _fill(**overrides):
    row = {
        "market_ticker": "KXHIGHAUS-26AUG21-B102.5",
        "market_date": "2026-08-21",
        "side": "NO",
        "quantity": 1,
        "price_cents": 36,
        "modeled_fee_cents": 2,
    }
    row.update(overrides)
    return row


def _settlement(**overrides):
    row = {
        "market_ticker": "KXHIGHAUS-26AUG21-B102.5",
        "market_date": "2026-08-21",
        "settlement_source_provider": "THE_WEATHER_COMPANY",
        "settlement_source_url": "https://weather.com/kalshi",
        "official_source_verified": True,
        "finality_verified": True,
        "correction_window_checked": True,
        "settlement_value": 104,
        "outcome": "NO",
    }
    row.update(overrides)
    return row


def test_reconciles_exact_official_weather_paper_win():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(), paper_fill=_fill(), settlement=_settlement()
    )
    assert result["reconciled"] is True
    assert result["paper_cost_cents"] == "38"
    assert result["paper_payout_cents"] == "100"
    assert result["paper_realized_pnl_cents"] == "62"
    assert result["settlement_outcome"] == "NO"
    assert result["settlement_value"] == "104"
    assert result["settlement_source_url"] == "https://weather.com/kalshi"
    assert result["order_entry"] is False
    assert result["live_probe_ready"] is False


def test_historical_exact_audit_can_override_legacy_series_regime_for_reconciliation():
    nws_source = "https://www.weather.gov/product.php?site=NWS&product=CLI&issuedby=AUS"
    shadow = _shadow(
        market_ticker="KXHIGHAUS-26AUG12-B102.5",
        market_date="2026-08-12",
        market_settlement_source_provider="NWS",
        settlement_source_regime_verified=False,
        exact_market_source_verified=True,
        exact_market_source_audit=_exact_audit(
            provider="NWS", ticker="KXHIGHAUS-26AUG12-B102.5", market_date="2026-08-12", source_url=nws_source
        ),
    )
    fill = _fill(market_ticker="KXHIGHAUS-26AUG12-B102.5", market_date="2026-08-12")
    settlement = _settlement(
        market_ticker="KXHIGHAUS-26AUG12-B102.5",
        market_date="2026-08-12",
        settlement_source_provider="NWS",
        settlement_source_url=nws_source,
    )
    result = reconcile_weather_paper_fill(shadow_row=shadow, paper_fill=fill, settlement=settlement)
    assert result["reconciled"] is True
    assert result["settlement_source_provider"] == "NWS"


def test_exact_market_or_date_mismatch_fails_closed():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(),
        paper_fill=_fill(market_date="2026-08-20"),
        settlement=_settlement(market_ticker="KXHIGHAUS-26AUG21-B100.5"),
    )
    assert result["reconciled"] is False
    assert "EXACT_MARKET_MISMATCH" in result["blockers"]
    assert "EXACT_MARKET_DATE_MISMATCH" in result["blockers"]
    assert result["paper_realized_pnl_cents"] is None


def test_provider_mismatch_or_unverified_finality_fails_closed():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(),
        paper_fill=_fill(),
        settlement=_settlement(
            settlement_source_provider="NWS",
            finality_verified=False,
            correction_window_checked=False,
        ),
    )
    assert result["reconciled"] is False
    assert "SETTLEMENT_SOURCE_MISMATCH" in result["blockers"]
    assert "SETTLEMENT_FINALITY_UNVERIFIED" in result["blockers"]
    assert "CORRECTION_WINDOW_UNVERIFIED" in result["blockers"]


def test_missing_or_string_exact_market_verification_fails_closed():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(exact_market_source_verified="true"),
        paper_fill=_fill(),
        settlement=_settlement(),
    )
    assert result["reconciled"] is False
    assert "QUOTE_TIME_EXACT_MARKET_SOURCE_UNVERIFIED" in result["blockers"]


def test_audit_ticker_or_date_mismatch_fails_closed():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(
            exact_market_source_audit=_exact_audit(
                ticker="KXHIGHAUS-26AUG20-B102.5", market_date="2026-08-20"
            )
        ),
        paper_fill=_fill(),
        settlement=_settlement(),
    )
    assert result["reconciled"] is False
    assert "QUOTE_TIME_EXACT_MARKET_AUDIT_MISMATCH" in result["blockers"]


def test_string_true_settlement_flags_do_not_certify_settlement():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(),
        paper_fill=_fill(),
        settlement=_settlement(
            official_source_verified="true",
            finality_verified="true",
            correction_window_checked="true",
        ),
    )
    assert result["reconciled"] is False
    assert "OFFICIAL_SETTLEMENT_SOURCE_UNVERIFIED" in result["blockers"]
    assert "SETTLEMENT_FINALITY_UNVERIFIED" in result["blockers"]
    assert "CORRECTION_WINDOW_UNVERIFIED" in result["blockers"]


def test_missing_or_different_terminal_settlement_source_url_fails_closed():
    missing = reconcile_weather_paper_fill(
        shadow_row=_shadow(), paper_fill=_fill(), settlement=_settlement(settlement_source_url=None)
    )
    assert missing["reconciled"] is False
    assert "SETTLEMENT_SOURCE_URL_MISMATCH" in missing["blockers"]

    different = reconcile_weather_paper_fill(
        shadow_row=_shadow(),
        paper_fill=_fill(),
        settlement=_settlement(settlement_source_url="https://weather.com/weather/today"),
    )
    assert different["reconciled"] is False
    assert "SETTLEMENT_SOURCE_URL_MISMATCH" in different["blockers"]


def test_losing_paper_fill_records_full_cost_loss():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(),
        paper_fill=_fill(side="YES", quantity=2, price_cents=41, modeled_fee_cents=3),
        settlement=_settlement(outcome="NO", settlement_value=104),
    )
    assert result["reconciled"] is True
    assert result["paper_cost_cents"] == "85"
    assert result["paper_payout_cents"] == "0"
    assert result["paper_realized_pnl_cents"] == "-85"


def test_free_form_outcome_cannot_disagree_with_terminal_value():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(),
        paper_fill=_fill(),
        settlement=_settlement(outcome="YES", settlement_value=104),
    )
    assert result["reconciled"] is False
    assert "SETTLEMENT_OUTCOME_VALUE_MISMATCH" in result["blockers"]


def test_missing_terminal_value_fails_closed():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(), paper_fill=_fill(), settlement=_settlement(settlement_value=None)
    )
    assert result["reconciled"] is False
    assert "TERMINAL_SETTLEMENT_VALUE_UNVERIFIED" in result["blockers"]


def test_unsettlement_certified_quote_cannot_realize_paper_pnl():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(settlement_certified_shadow=False),
        paper_fill=_fill(),
        settlement=_settlement(),
    )
    assert result["reconciled"] is False
    assert "QUOTE_TIME_SETTLEMENT_CERTIFICATION_UNVERIFIED" in result["blockers"]


def test_tail_strike_fails_closed_until_exact_operator_semantics_are_encoded():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(strike_type="greater", floor_strike=103, cap_strike=None),
        paper_fill=_fill(),
        settlement=_settlement(settlement_value=105, outcome="YES"),
    )
    assert result["reconciled"] is False
    assert "TERMINAL_STRIKE_SEMANTICS_UNSUPPORTED" in result["blockers"]