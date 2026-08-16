from src.gemini_kalshi_settlement_agreement import match_settled


def test_matches_same_asset_same_expiry_and_counts_agreement():
    gemini = [{
        "ticker": "BTC15M2608141945",
        "contracts": [{
            "ticker": "UP",
            "instrumentSymbol": "GEMI-BTC15M2608141945-UP",
            "resolutionSide": "yes",
            "strike": {"value": "100000.00"},
            "settlementValue": "100050.00",
        }],
    }]
    kalshi = [{
        "ticker": "KXBTC15M-TEST",
        "title": "Bitcoin Up or Down - 15 Minutes",
        "expected_expiration_time": "2026-08-14T19:45:00Z",
        "result": "yes",
    }]
    rows = match_settled(gemini, kalshi)
    assert len(rows) == 1
    row = rows[0]
    assert row.asset == "BTC"
    assert row.agrees is True
    assert row.gemini_margin_bps == 5.0


def test_surfaces_disagreement():
    gemini = [{
        "ticker": "ETH15M2608141945",
        "contracts": [{
            "ticker": "UP",
            "resolutionSide": "yes",
            "strike": {"value": "4000"},
            "settlementValue": "4001",
        }],
    }]
    kalshi = [{
        "ticker": "KXETH15M-TEST",
        "title": "Ethereum Up or Down - 15 Minutes",
        "expected_expiration_time": "2026-08-14T19:45:00Z",
        "result": "no",
    }]
    rows = match_settled(gemini, kalshi)
    assert len(rows) == 1
    assert rows[0].agrees is False


def test_rejects_expiry_mismatch():
    gemini = [{
        "ticker": "BTC15M2608141945",
        "contracts": [{"ticker": "UP", "resolutionSide": "yes"}],
    }]
    kalshi = [{
        "ticker": "KXBTC15M-TEST",
        "title": "Bitcoin Up or Down - 15 Minutes",
        "expected_expiration_time": "2026-08-14T19:50:00Z",
        "result": "yes",
    }]
    assert match_settled(gemini, kalshi, max_expiry_skew_s=30) == []


def test_ignores_gemini_five_minute_contracts():
    gemini = [{
        "ticker": "BTC05M2608141945",
        "contracts": [{"ticker": "UP", "resolutionSide": "yes"}],
    }]
    kalshi = [{
        "ticker": "KXBTC15M-TEST",
        "title": "Bitcoin Up or Down - 15 Minutes",
        "expected_expiration_time": "2026-08-14T19:45:00Z",
        "result": "yes",
    }]
    assert match_settled(gemini, kalshi) == []
