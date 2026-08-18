from src import cryptocom_dcm_book_probe_shadow as dcm


def test_choose_contract_prefers_near_fifty_percent_and_strict_asset_symbol():
    contracts = [
        {"status": "active", "symbol": "BCHUSD_X", "chance": "49", "title": "BCH"},
        {"status": "active", "symbol": "BTCUSD_FAR", "chance": "12", "title": "far"},
        {"status": "active", "symbol": "BTCUSD_NEAR", "chance": "52", "title": "near"},
    ]
    selected = dcm.choose_contract(contracts, "BTC")
    assert selected is not None
    assert selected["symbol"] == "BTCUSD_NEAR"


def test_top_of_book_extracts_non_crossed_levels():
    top = dcm.top_of_book({
        "data": [{
            "instrument_name": "SOLUSD_TEST",
            "depth": 10,
            "bids": [["0.44", "3", "2"]],
            "asks": [["0.46", "5", "1"]],
        }]
    })
    assert top["best_bid_price"] == "0.44"
    assert top["best_ask_price"] == "0.46"
    assert top["bid_levels"] == 1
    assert top["ask_levels"] == 1
    assert top["crossed"] is False


def test_top_of_book_flags_crossed_book():
    top = dcm.top_of_book({
        "data": [{"bids": [["0.55", "1", "1"]], "asks": [["0.45", "1", "1"]]}]
    })
    assert top["crossed"] is True
