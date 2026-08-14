from src.gemini_kalshi_fast_overlap import discover_overlaps


def test_finds_same_asset_expiry_15m_overlap_and_keeps_research_only_status():
    gemini = [{
        "ticker": "BTC15M2608141945",
        "category": "crypto",
        "sourceDetails": {"agency": "Kaiko", "index": "GRR-KAIKO_BTCUSD_60S"},
        "contracts": [{"instrumentSymbol": "GEMI-BTC15M2608141945-UP"}],
    }]
    kalshi = [{
        "ticker": "KXBTC15M-TEST",
        "event_ticker": "KXBTC15M-TEST-EVENT",
        "title": "Bitcoin Up or Down - 15 Minutes",
        "rules_primary": "Uses CF Benchmarks BRTI.",
        "expected_expiration_time": "2026-08-14T19:45:00Z",
    }]
    g, k, overlaps = discover_overlaps(gemini, kalshi)
    assert len(g) == 1
    assert len(k) == 1
    assert len(overlaps) == 1
    row = overlaps[0].as_dict()
    assert row["asset"] == "BTC"
    assert row["duration_minutes"] == 15
    assert row["expiry_skew_s"] == 0
    assert row["settlement_status"] == "INDEX_MISMATCH_RESEARCH_ONLY"
    assert row["gemini_source_index"] == "GRR-KAIKO_BTCUSD_60S"


def test_five_minute_gemini_is_ignored_entirely():
    gemini = [{
        "ticker": "BTC05M2608141945",
        "contracts": [{"instrumentSymbol": "GEMI-BTC05M2608141945-UP"}],
    }]
    kalshi = [{
        "ticker": "KXBTC15M-TEST",
        "title": "Bitcoin Up or Down - 15 Minutes",
        "expected_expiration_time": "2026-08-14T19:45:00Z",
    }]
    g, k, overlaps = discover_overlaps(gemini, kalshi)
    assert g == []
    assert len(k) == 1
    assert overlaps == []


def test_non_15_minute_kalshi_market_is_ignored():
    gemini = [{
        "ticker": "BTC15M2608141945",
        "contracts": [{"instrumentSymbol": "GEMI-BTC15M2608141945-UP"}],
    }]
    kalshi = [{
        "ticker": "KXBTC-HOURLY",
        "title": "Bitcoin Up or Down - 60 Minutes",
        "expected_expiration_time": "2026-08-14T19:45:00Z",
    }]
    g, k, overlaps = discover_overlaps(gemini, kalshi)
    assert len(g) == 1
    assert k == []
    assert overlaps == []


def test_does_not_pair_different_assets_even_with_same_expiry():
    gemini = [{
        "ticker": "ETH15M2608141945",
        "contracts": [{"instrumentSymbol": "GEMI-ETH15M2608141945-UP"}],
    }]
    kalshi = [{
        "ticker": "KXBTC15M-TEST",
        "title": "Bitcoin Up or Down - 15 Minutes",
        "expected_expiration_time": "2026-08-14T19:45:00Z",
    }]
    _, _, overlaps = discover_overlaps(gemini, kalshi)
    assert overlaps == []


def test_rejects_expiry_skew_beyond_30_seconds_by_default():
    gemini = [{
        "ticker": "BTC15M2608141945",
        "contracts": [{"instrumentSymbol": "GEMI-BTC15M2608141945-UP"}],
    }]
    kalshi = [{
        "ticker": "KXBTC15M-TEST",
        "title": "Bitcoin Up or Down - 15 Minutes",
        "expected_expiration_time": "2026-08-14T19:45:31Z",
    }]
    _, _, overlaps = discover_overlaps(gemini, kalshi)
    assert overlaps == []
