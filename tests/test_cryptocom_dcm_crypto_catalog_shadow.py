from src import cryptocom_dcm_crypto_catalog_shadow as scanner


def test_classify_assets_uses_structured_crypto_identity():
    assert scanner.classify_assets({"display_name": "Bitcoin above 65000", "product_type": "EVENTS"}) == ["BTC"]
    assert scanner.classify_assets({"event_details": {"eventName": "Ethereum price"}}) == ["ETH"]
    assert scanner.classify_assets({"underlying_symbol": "SOL-USD", "event_details": {}}) == ["SOL"]
    assert scanner.classify_assets({"base_ccy": "XRP", "event_details": {}}) == ["XRP"]
    assert scanner.classify_assets({"display_name": "Philadelphia wins", "product_type": "EVENTS"}) == []


def test_compact_instrument_keeps_settlement_attributes():
    row = {
        "symbol": "NX.TEST",
        "inst_type": "OPTION",
        "display_name": "Bitcoin above",
        "base_ccy": "BTC",
        "quote_ccy": "USD",
        "underlying_symbol": "BTCUSD",
        "product_type": "EVENTS",
        "tradable": True,
        "expiry_timestamp_ms": 123,
        "event_details": {
            "eventCode": "BTC-EVENT",
            "eventName": "BTC above strike",
            "metaData": {"strike": "65000"},
            "attributes": {
                "OPEN_TIME": "20260818-18:00:00.000",
                "CLOSE_TIME": "20260818-18:20:00.000",
                "STRIKE_INDEX": "7",
                "STRIKE_OPERATOR": ">",
                "PERIOD_CODE": "I",
            },
        },
    }
    compact = scanner.compact_instrument(row)
    assert compact["symbol"] == "NX.TEST"
    assert compact["event_code"] == "BTC-EVENT"
    assert compact["close_time"] == "20260818-18:20:00.000"
    assert compact["strike_operator"] == ">"
    assert compact["event_metadata"]["strike"] == "65000"


def test_top_of_book_returns_price_quantity_order_triplets():
    top = scanner.top_of_book({
        "data": [{
            "instrument_name": "NX.TEST",
            "depth": 10,
            "bids": [["0.45", "3", "2"]],
            "asks": [["0.47", "4", "1"]],
        }]
    })
    assert top["best_bid"] == ["0.45", "3", "2"]
    assert top["best_ask"] == ["0.47", "4", "1"]
    assert top["bid_levels"] == 1
    assert top["ask_levels"] == 1
