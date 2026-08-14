from decimal import Decimal as D

from src.gemini_kalshi_execution_shadow import (
    ConsumableLiquidity,
    TopOfBook,
    evaluate_pair,
    survives,
)


def book(venue, market_id, ts_ms, bid, bid_size, ask, ask_size):
    return TopOfBook(
        venue=venue,
        market_id=market_id,
        ts_ms=ts_ms,
        yes_bid=D(bid),
        yes_bid_size=D(bid_size),
        yes_ask=D(ask),
        yes_ask_size=D(ask_size),
    )


def test_finds_gemini_yes_kalshi_no_lock_at_executable_top_of_book():
    gemini = book("gemini", "g", 1000, "0.44", "20", "0.45", "12")
    kalshi = book("kalshi", "k", 1020, "0.60", "8", "0.61", "30")
    rows = evaluate_pair(gemini, kalshi)
    assert len(rows) == 1
    row = rows[0]
    assert row.direction == "GEMINI_YES_KALSHI_NO"
    assert row.quantity == D("8")
    assert row.raw_cost == D("6.80")
    assert row.locked_profit == D("1.20")


def test_fee_can_remove_false_positive():
    gemini = book("gemini", "g", 1000, "0.49", "10", "0.50", "10")
    kalshi = book("kalshi", "k", 1000, "0.51", "10", "0.52", "10")

    def fee(price, qty):
        return D("0.01") * qty

    # Gemini YES 0.50 + Kalshi NO 0.49 = 0.99 before fees, 1.01 after fees.
    assert evaluate_pair(gemini, kalshi, gemini_fee=fee, kalshi_fee=fee) == []


def test_rejects_stale_cross_venue_quotes():
    gemini = book("gemini", "g", 1000, "0.40", "100", "0.41", "100")
    kalshi = book("kalshi", "k", 1400, "0.70", "100", "0.71", "100")
    assert evaluate_pair(gemini, kalshi, max_quote_skew_ms=250) == []


def test_max_quantity_caps_shadow_size():
    gemini = book("gemini", "g", 1000, "0.44", "100", "0.45", "100")
    kalshi = book("kalshi", "k", 1000, "0.60", "100", "0.61", "100")
    rows = evaluate_pair(gemini, kalshi, max_quantity=D("5"))
    assert rows[0].quantity == D("5")


def test_consumable_liquidity_does_not_double_count_same_resting_depth():
    c = ConsumableLiquidity()
    assert c.observe(D("0.45"), D("10")) == D("10")
    assert c.observe(D("0.45"), D("10")) == D("0")
    assert c.observe(D("0.45"), D("14")) == D("4")
    assert c.observe(D("0.45"), D("11")) == D("0")
    assert c.observe(D("0.46"), D("7")) == D("7")


def test_survival_requires_cost_below_one_after_fees():
    assert survives(D("0.94"), D("0.97"), D("0.01"))
    assert not survives(D("0.94"), D("0.99"), D("0.01"))
