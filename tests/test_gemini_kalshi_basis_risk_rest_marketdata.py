from decimal import Decimal

from src import gemini_kalshi_basis_risk_marketdata as m


def test_exact_rest_top_uses_best_bids_and_request_start_timestamp():
    payload = {
        "orderbook_fp": {
            "yes_dollars": [["0.40", "2"], ["0.45", "3"]],
            "no_dollars": [["0.50", "4"], ["0.52", "5"]],
        }
    }
    top = m.exact_rest_top(payload, "KXBTC15M-TEST", 123456)
    assert top is not None
    assert top.market_id == "KXBTC15M-TEST"
    assert top.ts_ms == 123456
    assert top.yes_bid == Decimal("0.45")
    assert top.yes_bid_size == Decimal("3")
    assert top.yes_ask == Decimal("0.48")
    assert top.yes_ask_size == Decimal("5")


def test_exact_rest_top_preserves_yes_only_book_without_inventing_ask_depth():
    top = m.exact_rest_top(
        {"orderbook_fp": {"yes_dollars": [["0.45", "3"]], "no_dollars": []}},
        "KXBTC15M-TEST",
        123456,
    )
    assert top is not None
    assert top.yes_bid == Decimal("0.45")
    assert top.yes_bid_size == Decimal("3")
    assert top.yes_ask == Decimal("1")
    assert top.yes_ask_size == Decimal("0")


def test_exact_rest_top_preserves_no_only_book_without_inventing_bid_depth():
    top = m.exact_rest_top(
        {"orderbook_fp": {"yes_dollars": [], "no_dollars": [["0.52", "5"]]}},
        "KXBTC15M-TEST",
        123456,
    )
    assert top is not None
    assert top.yes_bid == Decimal("0")
    assert top.yes_bid_size == Decimal("0")
    assert top.yes_ask == Decimal("0.48")
    assert top.yes_ask_size == Decimal("5")


def test_exact_rest_top_fails_closed_when_both_sides_missing():
    assert m.exact_rest_top(
        {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}},
        "KXBTC15M-TEST",
        123456,
    ) is None


def test_poll_interval_cannot_exceed_20hz_per_ticker(monkeypatch):
    monkeypatch.setenv("CRYPTO_BASIS_KALSHI_REST_POLL_INTERVAL_S", "0.001")
    assert m._poll_interval_s() == m.MIN_POLL_INTERVAL_S == 0.05


def test_poll_interval_has_conservative_upper_bound(monkeypatch):
    monkeypatch.setenv("CRYPTO_BASIS_KALSHI_REST_POLL_INTERVAL_S", "9")
    assert m._poll_interval_s() == m.MAX_POLL_INTERVAL_S == 0.25
