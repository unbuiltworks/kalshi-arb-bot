from decimal import Decimal as D

from src.gemini_eth5_front_queue_census import score_book, summarize_rows


def _book():
    return {
        "bids": [
            {"price": "0.48", "amount": "250"},
            {"price": "0.47", "amount": "100"},
        ],
        "asks": [
            {"price": "0.61", "amount": "384"},
            {"price": "0.62", "amount": "75"},
        ],
    }


def test_probe_shape_reports_real_tick_priority_and_remaining_edge():
    row = score_book(
        ticker="ETH05M2608290205",
        symbol="GEMI-ETH05M2608290205-UP",
        seconds_to_expiry=287.0,
        tick=D("0.01"),
        book=_book(),
        observed_at="2026-08-29T02:00:00+00:00",
    )

    # This is the exact economics exposed by the patched no-fill probe:
    # top YES 48c with 250 ahead; top NO is 39c from the 61c YES ask.
    # V3 spends one tick on NO, so NO is already price-priority while YES joins.
    assert row["v3_yes_quote"] == "0.48"
    assert row["v3_no_quote"] == "0.40"
    assert row["v3_modeled_edge"] == "0.10"
    assert row["visible_yes_queue_ahead_at_v3"] == "250"
    assert row["visible_no_queue_ahead_at_v3"] == "0"

    # One whole tick above the visible best is the minimum front-of-queue price.
    assert row["yes_front_quote"] == "0.49"
    assert row["no_front_quote"] == "0.40"
    assert row["yes_ticks_from_v3_to_front"] == 1
    assert row["no_ticks_from_v3_to_front"] == 0
    assert row["front_post_only"] is True
    assert row["front_edge_after_maker_fees"] == "0.09"
    assert row["front_survives_2c_floor"] is True

    # Starting from the 10c V3 reserve, four symmetric extra ticks still leave
    # exactly the 2c completed-pair floor; a fifth would not.
    assert row["max_symmetric_extra_ticks_each_from_v3_preserving_2c"] == 4
    assert row["stress"][4]["yes_price"] == "0.52"
    assert row["stress"][4]["no_price"] == "0.44"
    assert row["stress"][4]["edge_after_maker_fees"] == "0.02"
    assert row["stress"][4]["survives_2c_floor"] is True


def test_front_priority_is_rejected_if_one_tick_would_cross():
    row = score_book(
        ticker="ETH05M2608290210",
        symbol="GEMI-ETH05M2608290210-UP",
        seconds_to_expiry=200.0,
        tick=D("0.01"),
        book={
            "bids": [{"price": "0.49", "amount": "10"}],
            "asks": [{"price": "0.50", "amount": "12"}],
        },
    )
    assert row["front_post_only"] is False
    assert row["front_edge_after_maker_fees"] is None
    assert row["front_survives_2c_floor"] is False


def test_summary_counts_only_raw_gate_snapshots_for_front_census():
    good = score_book(
        ticker="ETH05M2608290205",
        symbol="GEMI-ETH05M2608290205-UP",
        seconds_to_expiry=287.0,
        tick=D("0.01"),
        book=_book(),
    )
    flat = score_book(
        ticker="ETH05M2608290210",
        symbol="GEMI-ETH05M2608290210-UP",
        seconds_to_expiry=287.0,
        tick=D("0.01"),
        book={
            "bids": [{"price": "0.49", "amount": "50"}],
            "asks": [{"price": "0.51", "amount": "60"}],
        },
    )
    summary = summarize_rows([good, flat])
    assert summary["snapshots"] == 2
    assert summary["qualifier_snapshots_raw_edge_ge_5c"] == 1
    assert summary["front_feasible_2c_snapshots"] == 1
    assert summary["windows_seen"] == 2
    assert summary["windows_with_qualifier"] == 1
    assert summary["windows_with_front_feasible_2c"] == 1
    assert summary["front_edge_median"] == "0.09"
