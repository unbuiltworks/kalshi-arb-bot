from decimal import Decimal

from src import gemini_kalshi_crypto_live_campaign as campaign


def test_first_xrp_live_fill_actual_cost_math():
    record = {
        "result": {
            "gemini": {
                "filled_quantity": "1.00",
                "average_fill_price": "0.2600",
                "fee_paid": "0.0100",
            },
            "kalshi": {
                "filled_quantity": "1.00",
                # Kalshi executor reports the fill in YES-price space.  The live
                # XRP leg was NO, so purchased-side cost is 1 - 0.33 = 0.67.
                "average_fill_price": "0.3300",
                "fee_paid": "0.015500",
            },
        },
        "kalshi_payload": {"side": "ask"},
    }
    costs = campaign._costs(record)
    assert costs["gemini_amount"] == Decimal("0.270000")
    assert costs["kalshi_cost_price"] == Decimal("0.6700")
    assert costs["kalshi_amount"] == Decimal("0.685500")
    assert costs["raw_cost"] == Decimal("0.930000")
    assert costs["fees"] == Decimal("0.025500")
    assert costs["capital"] == Decimal("0.955500")
    assert Decimal("1") - costs["capital"] == Decimal("0.044500")


def test_kalshi_bid_and_ask_map_to_purchased_outcomes():
    assert campaign._purchased_kalshi_side({"side": "bid"}) == "yes"
    assert campaign._purchased_kalshi_side({"side": "ask"}) == "no"


def test_gemini_event_ticker_derives_from_instrument_symbol():
    assert (
        campaign._event_ticker_from_symbol("GEMI-XRP15M2608191515-UP")
        == "XRP15M2608191515"
    )
    assert campaign._asset_from_event_ticker("XRP15M2608191515") == "XRP"


def test_campaign_run_ids_remain_within_v2_persistent_id_limit():
    campaign_id = campaign._clean_campaign_id("gk5-260819-1225")
    assert campaign._run_id(campaign_id, 5) == "gk5-260819-1225-05"
    assert len(campaign._run_id(campaign_id, 5)) <= 32
