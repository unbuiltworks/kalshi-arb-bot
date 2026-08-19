from decimal import Decimal

from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_mlb_prop_hot_shadow import orient_tob

D = Decimal


def _book() -> TopOfBook:
    return TopOfBook(
        venue="gemini",
        market_id="TEST",
        ts_ms=123,
        yes_bid=D("0.31"),
        yes_bid_size=D("7"),
        yes_ask=D("0.36"),
        yes_ask_size=D("11"),
    )


def test_contract_side_preserves_yes_space():
    raw = _book()
    assert orient_tob(raw, "CONTRACT") == raw
    assert orient_tob(raw, "YES") == raw


def test_no_side_complements_prices_and_crossing_depth():
    raw = _book()
    oriented = orient_tob(raw, "NO")
    assert oriented.yes_bid == D("0.64")
    assert oriented.yes_bid_size == D("11")
    assert oriented.yes_ask == D("0.69")
    assert oriented.yes_ask_size == D("7")
    assert oriented.ts_ms == raw.ts_ms


def test_no_side_round_trip_is_identity():
    raw = _book()
    assert orient_tob(orient_tob(raw, "NO"), "NO") == raw
