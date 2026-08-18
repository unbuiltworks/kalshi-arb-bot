from src import cryptocom_prediction_discovery_shadow as cdc


def test_asset_alias_matching_is_case_insensitive():
    assert cdc._text_matches_asset("BTC", "Will BITCOIN finish above 70000?")
    assert cdc._text_matches_asset("ETH", "ETH higher or lower")
    assert cdc._text_matches_asset("SOL", "Solana strike")
    assert cdc._text_matches_asset("XRP", "Ripple / XRP")
    assert not cdc._text_matches_asset("BTC", "Philadelphia vs New York")


def test_build_record_is_explicitly_read_only():
    record = cdc._build_record(
        asset="SOL",
        event={
            "id": "event-1",
            "title": "Solana higher or lower",
            "event_date": "2026-08-18T18:00:00Z",
            "kind": "CRYPTO",
            "type": "recurring",
            "market_format": "head_to_head",
        },
        contract={
            "id": "contract-1",
            "symbol": "SOL-TEST",
            "title": "SOL up",
            "status": "active",
            "yes": "0.48",
            "no": "0.52",
            "chance": "0.48",
        },
        price={
            "bid": "0.47",
            "ask": "0.49",
            "mid": "0.48",
            "spread": "0.02",
            "probability": "0.48",
            "status": "active",
            "updated_at": "2026-08-18T17:59:59Z",
        },
        observed_at_ms=123,
    )
    assert record["asset"] == "SOL"
    assert record["symbol"] == "SOL-TEST"
    assert record["bid"] == "0.47"
    assert record["ask"] == "0.49"
    assert record["trade_authorized"] is False
    assert record["read_only"] is True
    assert record["order_entry"] is False


def test_rate_snapshot_parses_missing_and_present_headers():
    import httpx

    headers = httpx.Headers({
        "X-RateLimit-Limit": "100",
        "X-RateLimit-Remaining": "77",
        "X-RateLimit-Day-Limit": "50000",
        "X-RateLimit-Day-Remaining": "49900",
    })
    assert cdc._rate_snapshot(headers) == {
        "minute_limit": 100,
        "minute_remaining": 77,
        "day_limit": 50000,
        "day_remaining": 49900,
    }
