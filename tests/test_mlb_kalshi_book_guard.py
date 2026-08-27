from src.mlb_kalshi_book_guard import audit_kalshi_binary_book


def _book(yes_px="0.40", yes_qty="5", no_px="0.50", no_qty="4"):
    return {
        "orderbook_fp": {
            "yes_dollars": [[yes_px, yes_qty]],
            "no_dollars": [[no_px, no_qty]],
        }
    }


def test_accepts_coherent_two_sided_book():
    result = audit_kalshi_binary_book(_book())
    assert result["verified"] is True
    assert result["derived_yes_ask"] == "0.50"
    assert result["derived_no_ask"] == "0.60"
    assert result["order_entry"] is False


def test_rejects_crossed_binary_book():
    result = audit_kalshi_binary_book(_book(yes_px="0.60", no_px="0.50"))
    assert result == {"verified": False, "blocker": "KALSHI_BINARY_BOOK_CROSSED"}


def test_rejects_out_of_range_price():
    result = audit_kalshi_binary_book(_book(yes_px="1.01"))
    assert result == {"verified": False, "blocker": "KALSHI_BOOK_PRICE_OUT_OF_RANGE"}


def test_rejects_nonfinite_price():
    result = audit_kalshi_binary_book(_book(no_px="NaN"))
    assert result == {"verified": False, "blocker": "KALSHI_BOOK_NONFINITE_OR_MALFORMED"}


def test_rejects_missing_or_nonpositive_depth():
    assert audit_kalshi_binary_book(_book(yes_qty="0")) == {
        "verified": False,
        "blocker": "KALSHI_TWO_SIDED_DEPTH_MISSING",
    }
    assert audit_kalshi_binary_book({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}) == {
        "verified": False,
        "blocker": "KALSHI_TWO_SIDED_DEPTH_MISSING",
    }
