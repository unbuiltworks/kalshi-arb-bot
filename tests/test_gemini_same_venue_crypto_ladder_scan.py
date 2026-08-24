from decimal import Decimal as D

from src import gemini_same_venue_crypto_ladder_scan as scan


def test_safe_quote_below_market_is_rejected_for_actionable_maker():
    assert scan.maker_action(existing_bid=D("0.60"), safe_quote=D("0.55"), tick=D("0.01")) == "BEHIND_UNSAFE_TOP_BID"


def test_safe_quote_one_tick_above_market_improves_best_bid():
    assert scan.maker_action(existing_bid=D("0.60"), safe_quote=D("0.61"), tick=D("0.01")) == "IMPROVE_TO_SAFE_FRONTIER"


def test_zero_bid_can_be_seeded():
    assert scan.maker_action(existing_bid=D("0"), safe_quote=D("0.44"), tick=D("0.01")) == "SEED_FIRST_BID"


def test_safe_frontier_is_post_only_and_preserves_three_cents():
    quote = scan.safe_frontier(maker_ask=D("0.82"), hedge_ask=D("0.15"), tick=D("0.01"))
    assert quote is not None
    assert quote < D("0.82")
    assert scan.locked_profit(quote, D("0.15")) >= D("0.03")
    assert scan.locked_profit(quote + D("0.01"), D("0.15")) < D("0.03")


def _contract(symbol: str, strike: str, source: str = "IDX", expiry: str = "2026-08-24T21:00:00.000Z"):
    return {
        "instrumentSymbol": symbol,
        "strike": {"value": strike},
        "source": source,
        "expiryDate": expiry,
        "status": "active",
        "marketState": "open",
        "quantityMinimum": "0.01",
        "quantityIncrement": "0.01",
        "priceIncrement": "0.01",
    }


def test_ladder_rejects_mixed_sources_fail_closed():
    event = {
        "expiryDate": "2026-08-24T21:00:00.000Z",
        "contracts": [
            _contract("GEMI-BTC2608242100-HI79000", "79000", source="IDX_A"),
            _contract("GEMI-BTC2608242100-HI79500", "79500", source="IDX_B"),
        ],
    }
    assert scan.ladder_contracts(event) == []


def test_ladder_rejects_mixed_expiry_fail_closed():
    event = {
        "contracts": [
            _contract("GEMI-BTC2608242100-HI79000", "79000", expiry="2026-08-24T21:00:00.000Z"),
            _contract("GEMI-BTC2608242200-HI79500", "79500", expiry="2026-08-24T22:00:00.000Z"),
        ],
    }
    assert scan.ladder_contracts(event) == []
