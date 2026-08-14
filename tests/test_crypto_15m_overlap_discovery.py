from src.crypto_15m_overlap_discovery import discover_candidates


def test_same_asset_close_aligned_pair_is_candidate():
    kalshi = [{
        "ticker": "KXBTC15M-TEST",
        "event_ticker": "KXBTC15M-TEST",
        "title": "Bitcoin Up or Down - 15 Minutes",
        "rules_primary": "Resolves based on Bitcoin over the 15-minute window.",
        "rules_secondary": "",
        "close_time": "2026-08-14T18:15:00Z",
    }]
    poly = [{
        "slug": "btc-up-or-down-15-minute-test",
        "question": "Bitcoin up or down in 15 minutes?",
        "description": "Bitcoin 15-minute market",
        "category": "crypto",
        "endDate": "2026-08-14T18:15:30Z",
    }]
    k, p, pairs = discover_candidates(kalshi, poly, max_close_skew_s=120)
    assert len(k) == 1
    assert len(p) == 1
    assert len(pairs) == 1
    assert pairs[0].asset == "BTC"
    assert pairs[0].close_skew_s == 30


def test_different_assets_do_not_match():
    kalshi = [{
        "ticker": "KXBTC15M-TEST",
        "title": "BTC Up or Down - 15 min",
        "close_time": "2026-08-14T18:15:00Z",
    }]
    poly = [{
        "slug": "eth-15m",
        "question": "Ethereum higher or lower in 15 minutes?",
        "description": "ETH 15m",
        "category": "crypto",
        "endDate": "2026-08-14T18:15:00Z",
    }]
    _, _, pairs = discover_candidates(kalshi, poly, max_close_skew_s=120)
    assert pairs == []


def test_close_skew_must_be_small():
    kalshi = [{
        "ticker": "KXBTC15M-TEST",
        "title": "Bitcoin Up or Down - 15 Minutes",
        "close_time": "2026-08-14T18:15:00Z",
    }]
    poly = [{
        "slug": "btc-15m",
        "question": "Bitcoin higher or lower in 15 minutes?",
        "description": "Bitcoin 15-minute market",
        "category": "crypto",
        "endDate": "2026-08-14T18:20:00Z",
    }]
    _, _, pairs = discover_candidates(kalshi, poly, max_close_skew_s=120)
    assert pairs == []


def test_alignment_never_upgrades_settlement_status():
    kalshi = [{
        "ticker": "KXBTC15M-TEST",
        "title": "Bitcoin Up or Down - 15 Minutes",
        "rules_primary": "One index definition",
        "close_time": "2026-08-14T18:15:00Z",
    }]
    poly = [{
        "slug": "btc-15m",
        "question": "Bitcoin up or down in 15 minutes?",
        "description": "Potentially different index definition",
        "category": "crypto",
        "endDate": "2026-08-14T18:15:00Z",
    }]
    _, _, pairs = discover_candidates(kalshi, poly, max_close_skew_s=120)
    assert pairs[0].as_dict()["settlement_status"] == "NEEDS_RULE_AUDIT"
