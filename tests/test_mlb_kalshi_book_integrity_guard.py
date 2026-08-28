from src.mlb_kalshi_book_integrity_guard import audit_kalshi_top_book_payload


def _book(yes_px="0.40", yes_q="5", no_px="0.50", no_q="6"):
    return {
        "orderbook_fp": {
            "yes_dollars": [[yes_px, yes_q]],
            "no_dollars": [[no_px, no_q]],
        }
    }


def test_valid_two_sided_book_passes():
    row = audit_kalshi_top_book_payload(_book())
    assert row["verified"] is True
    assert row["blockers"] == []
    assert row["order_entry"] is False
    assert row["live_probe_ready"] is False


def test_crossed_yes_no_bids_fail_closed():
    row = audit_kalshi_top_book_payload(_book(yes_px="0.61", no_px="0.40"))
    assert row["verified"] is False
    assert "KALSHI_BOOK_CROSSED" in row["blockers"]


def test_out_of_range_price_fails_closed():
    row = audit_kalshi_top_book_payload(_book(yes_px="1.01"))
    assert row["verified"] is False
    assert "KALSHI_YES_BID_OUT_OF_RANGE" in row["blockers"]


def test_nonfinite_price_fails_closed():
    row = audit_kalshi_top_book_payload(_book(no_px="NaN"))
    assert row["verified"] is False
    assert "KALSHI_BOOK_PRICE_INVALID" in row["blockers"]


def test_nonpositive_depth_fails_closed():
    row = audit_kalshi_top_book_payload(_book(yes_q="0"))
    assert row["verified"] is False
    assert "KALSHI_YES_DEPTH_NONPOSITIVE" in row["blockers"]


def test_one_sided_book_fails_closed():
    row = audit_kalshi_top_book_payload({"orderbook_fp": {"yes_dollars": [["0.40", "5"]]}})
    assert row["verified"] is False
    assert "KALSHI_BOOK_TWO_SIDED_DEPTH_REQUIRED" in row["blockers"]
