from src.weather_shadow_settlement_reconcile import reconcile_weather_paper_fill


def _shadow(**overrides):
    row = {
        "category": "weather",
        "classification": "SHADOW",
        "order_entry": False,
        "live_probe_ready": False,
        "market_ticker": "KXHIGHAUS-26AUG21-B102.5",
        "market_date": "2026-08-21",
        "market_settlement_source_provider": "THE_WEATHER_COMPANY",
        "settlement_source_regime_verified": True,
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
        "official_source_verified": True,
        "finality_verified": True,
        "correction_window_checked": True,
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
    assert result["order_entry"] is False
    assert result["live_probe_ready"] is False


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


def test_string_true_flags_do_not_certify_settlement():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(settlement_source_regime_verified="true"),
        paper_fill=_fill(),
        settlement=_settlement(
            official_source_verified="true",
            finality_verified="true",
            correction_window_checked="true",
        ),
    )
    assert result["reconciled"] is False
    assert "QUOTE_TIME_SETTLEMENT_REGIME_UNVERIFIED" in result["blockers"]
    assert "OFFICIAL_SETTLEMENT_SOURCE_UNVERIFIED" in result["blockers"]
    assert "SETTLEMENT_FINALITY_UNVERIFIED" in result["blockers"]
    assert "CORRECTION_WINDOW_UNVERIFIED" in result["blockers"]


def test_losing_paper_fill_records_full_cost_loss():
    result = reconcile_weather_paper_fill(
        shadow_row=_shadow(),
        paper_fill=_fill(side="YES", quantity=2, price_cents=41, modeled_fee_cents=3),
        settlement=_settlement(outcome="NO"),
    )
    assert result["reconciled"] is True
    assert result["paper_cost_cents"] == "85"
    assert result["paper_payout_cents"] == "0"
    assert result["paper_realized_pnl_cents"] == "-85"
