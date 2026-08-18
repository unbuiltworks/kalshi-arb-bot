from src.mlb_prop_paper_microprobe import paper_probe_events
from src.strict_mlb_prop_snapshot_v5 import _book_pair_is_synchronized


def _row(*, synchronized: bool) -> dict:
    return {
        "family": "hits",
        "game_date": "2026-08-18",
        "player": "Test Player",
        "threshold": "1",
        "poly_slug": "poly-test-market",
        "kalshi_ticker": "KALSHI-TEST-MARKET",
        "book_comparable": True,
        "synchronized_book_pair": synchronized,
        "quote_receive_skew_ms": 10 if synchronized else 750,
        "participation_gate_satisfied": True,
        "read_only": True,
        "order_entry": False,
        "execution_eligible": False,
        "live_probe_ready": False,
        "fetch_error": None,
        "probe_1usd": {
            "direction": "POLY_YES_KALSHI_NO",
            "quantity": 1,
            "poly_price": "0.40",
            "kalshi_price": "0.50",
            "poly_fee": "0.01",
            "kalshi_fee_upper_bound": "0.01",
            "capital_cap": "1",
            "conservative_locked_profit": "0.08",
        },
    }


def test_receive_skew_boundary_is_fail_closed() -> None:
    assert _book_pair_is_synchronized(1_000, 1_500) is True
    assert _book_pair_is_synchronized(1_000, 1_501) is False


def test_paper_probe_rejects_unsynchronized_books() -> None:
    assert paper_probe_events(_row(synchronized=False), probe_field="probe_1usd", ts_ms=123) is None


def test_paper_probe_accepts_synchronized_read_only_pair() -> None:
    built = paper_probe_events(_row(synchronized=True), probe_field="probe_1usd", ts_ms=123)
    assert built is not None
    events, audit = built
    assert len(events) == 4
    assert audit["synchronized_book_pair"] is True
    assert audit["paper_probe_only"] is True
    assert audit["live_probe_ready"] is False
