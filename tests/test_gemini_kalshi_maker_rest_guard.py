from decimal import Decimal

from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_rest_guard as guard

D = Decimal


def test_exact_rest_top_uses_request_start_and_binary_transform():
    top = guard.exact_rest_top(
        {
            "orderbook_fp": {
                "yes_dollars": [["0.4200", "3.00"]],
                "no_dollars": [["0.5500", "4.00"]],
            }
        },
        "KXTEST",
        123456,
    )
    assert top is not None
    assert top.ts_ms == 123456
    assert top.yes_bid == D("0.4200")
    assert top.yes_bid_size == D("3.00")
    assert top.yes_ask == D("0.4500")
    assert top.yes_ask_size == D("4.00")
    assert top.no_bid == D("0.5500")
    assert top.no_ask == D("0.5800")


def test_exact_rest_top_missing_side_invents_no_depth():
    top = guard.exact_rest_top(
        {"orderbook_fp": {"yes_dollars": [["0.3900", "2.00"]], "no_dollars": []}},
        "KXTEST",
        5,
    )
    assert top is not None
    assert top.yes_bid == D("0.3900")
    assert top.yes_ask == D("1")
    assert top.yes_ask_size == D("0")


def test_exact_rest_top_rejects_crossed_book():
    top = guard.exact_rest_top(
        {
            "orderbook_fp": {
                "yes_dollars": [["0.7000", "1"]],
                "no_dollars": [["0.4000", "1"]],
            }
        },
        "KXTEST",
        9,
    )
    assert top is None


def test_account_snapshot_detects_fill_and_status_changes():
    a = guard._order_snapshot({"fill_count_fp": "0", "status": "resting", "remaining_count_fp": "1"})
    b = guard._order_snapshot({"fill_count_fp": "1", "status": "executed", "remaining_count_fp": "0"})
    assert a != b
    evt = guard._user_order_event({
        "order_id": "o1",
        "client_order_id": "makerprobe-r-k",
        "fill_count_fp": "1",
        "status": "executed",
    })
    assert evt["type"] == "user_order"
    assert evt["msg"]["fill_count_fp"] == "1"


def test_install_replaces_only_isolated_maker_kalshi_streams(monkeypatch):
    monkeypatch.setattr(guard, "_INSTALLED", False)
    original_book = base._kalshi_stream
    original_account = base._account_stream
    try:
        guard.install()
        assert base._kalshi_stream is guard.kalshi_rest_book_stream
        assert base._account_stream is guard.kalshi_rest_account_stream
        assert base._gemini_stream is not None
    finally:
        base._kalshi_stream = original_book
        base._account_stream = original_account
        guard._INSTALLED = False


def test_poll_interval_is_fail_closed_clamped(monkeypatch):
    monkeypatch.setenv(guard.BOOK_POLL_ENV, "0.001")
    assert guard._poll_seconds(guard.BOOK_POLL_ENV, guard.DEFAULT_BOOK_POLL_S) == guard.MIN_POLL_S
    monkeypatch.setenv(guard.BOOK_POLL_ENV, "99")
    assert guard._poll_seconds(guard.BOOK_POLL_ENV, guard.DEFAULT_BOOK_POLL_S) == guard.MAX_POLL_S
