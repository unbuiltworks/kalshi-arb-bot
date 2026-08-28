from src import strict_mlb_prop_snapshot as base
from src import strict_mlb_prop_snapshot_v4_safe as safe


def _payload(yes_px, no_px):
    return {
        "orderbook_fp": {
            "yes_dollars": [[yes_px, "5"]],
            "no_dollars": [[no_px, "5"]],
        }
    }


def test_guarded_entrypoint_rejects_crossed_kalshi_book():
    assert safe._guarded_kalshi_top_book(_payload("0.61", "0.40")) is None


def test_guarded_entrypoint_preserves_valid_kalshi_book():
    book = safe._guarded_kalshi_top_book(_payload("0.40", "0.50"))
    assert isinstance(book, base.TopBook)
    assert str(book.yes_ask) == "0.50"
    assert str(book.no_ask) == "0.60"


def test_guarded_entrypoint_is_read_only_by_construction():
    assert safe.run_once is not None
    assert not hasattr(safe, "place_order")
    assert not hasattr(safe, "cancel_order")
