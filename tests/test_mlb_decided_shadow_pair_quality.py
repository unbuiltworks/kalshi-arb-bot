from decimal import Decimal

from src.mlb_decided_shadow_pair_quality import (
    analyze,
    pair_synchronization_status,
    parse_signal_lines,
)


def _pair_row(**extra):
    row = {
        "family": "baseball_player_hits",
        "poly_slug": "astatc-mlb-phi-nyy-2026-08-19-hits-example-gte1",
        "kalshi_ticker": "KXMLBHIT-26AUG19PHINYY-EXAMPLE-1",
        "known_winner_edges": [{"venue": "kalshi"}],
        "cross_venue_q1_pair_lock": {"profit": "0.04", "direction": "POLY_YES_KALSHI_NO"},
        "read_only": True,
        "order_entry": False,
    }
    row.update(extra)
    return row


def test_legacy_pair_lock_without_receive_timing_fails_closed():
    ok, reason = pair_synchronization_status(_pair_row())
    assert ok is False
    assert reason == "MISSING_SYNCHRONIZATION_EVIDENCE"


def test_pair_lock_accepts_consistent_sub_500ms_receive_skew():
    row = _pair_row(
        poly_book_received_ms=1000,
        kalshi_book_received_ms=1230,
        quote_receive_skew_ms=230,
    )
    assert pair_synchronization_status(row) == (True, "SYNCHRONIZED_PAIR_EVIDENCE")


def test_pair_lock_rejects_over_500ms_receive_skew():
    row = _pair_row(
        poly_book_received_ms=1000,
        kalshi_book_received_ms=1501,
        quote_receive_skew_ms=501,
    )
    assert pair_synchronization_status(row) == (False, "PAIR_RECEIVE_SKEW_EXCEEDED")


def test_pair_lock_rejects_inconsistent_reported_skew():
    row = _pair_row(
        poly_book_received_ms=1000,
        kalshi_book_received_ms=1200,
        quote_receive_skew_ms=20,
    )
    assert pair_synchronization_status(row) == (False, "INCONSISTENT_SYNCHRONIZATION_EVIDENCE")


def test_analyze_keeps_single_venue_edges_but_excludes_unsynchronized_pair_capacity():
    result = analyze([_pair_row()])
    assert result["single_venue_known_winner_edges"] == 1
    assert result["cross_venue_pair_locks_seen"] == 1
    assert result["synchronized_cross_venue_pair_locks"] == 0
    assert result["synchronized_pair_profit_dollars"] == "0"
    assert result["pair_lock_reasons"] == {"MISSING_SYNCHRONIZATION_EVIDENCE": 1}


def test_analyze_counts_only_synchronized_pair_profit():
    rows = [
        _pair_row(
            poly_book_received_ms=1000,
            kalshi_book_received_ms=1100,
            quote_receive_skew_ms=100,
        ),
        _pair_row(),
    ]
    result = analyze(rows)
    assert result["synchronized_cross_venue_pair_lock_samples"] == 1
    assert result["synchronized_cross_venue_pair_locks"] == 1
    assert Decimal(result["synchronized_pair_profit_dollars"]) == Decimal("0.04")


def test_repeated_resting_pair_samples_are_not_additive_capacity():
    rows = [
        _pair_row(
            poly_book_received_ms=1000,
            kalshi_book_received_ms=1050,
            quote_receive_skew_ms=50,
            cross_venue_q1_pair_lock={"profit": "0.04", "direction": "POLY_YES_KALSHI_NO"},
        ),
        _pair_row(
            poly_book_received_ms=3000,
            kalshi_book_received_ms=3040,
            quote_receive_skew_ms=40,
            cross_venue_q1_pair_lock={"profit": "0.05", "direction": "POLY_YES_KALSHI_NO"},
        ),
        _pair_row(
            poly_book_received_ms=5000,
            kalshi_book_received_ms=5030,
            quote_receive_skew_ms=30,
            cross_venue_q1_pair_lock={"profit": "0.03", "direction": "POLY_YES_KALSHI_NO"},
        ),
    ]
    result = analyze(rows)
    assert result["synchronized_cross_venue_pair_lock_samples"] == 3
    assert result["unique_synchronized_pair_identities"] == 1
    assert result["duplicate_synchronized_pair_samples_excluded"] == 2
    assert Decimal(result["synchronized_pair_profit_dollars"]) == Decimal("0.05")


def test_opposite_directions_remain_distinct_consumable_identities():
    rows = [
        _pair_row(
            poly_book_received_ms=1000,
            kalshi_book_received_ms=1050,
            quote_receive_skew_ms=50,
            cross_venue_q1_pair_lock={"profit": "0.04", "direction": "POLY_YES_KALSHI_NO"},
        ),
        _pair_row(
            poly_book_received_ms=3000,
            kalshi_book_received_ms=3040,
            quote_receive_skew_ms=40,
            cross_venue_q1_pair_lock={"profit": "0.03", "direction": "KALSHI_YES_POLY_NO"},
        ),
    ]
    result = analyze(rows)
    assert result["unique_synchronized_pair_identities"] == 2
    assert Decimal(result["synchronized_pair_profit_dollars"]) == Decimal("0.07")


def test_synchronized_pair_without_strict_identity_is_excluded_from_capacity():
    row = _pair_row(
        poly_book_received_ms=1000,
        kalshi_book_received_ms=1050,
        quote_receive_skew_ms=50,
    )
    row.pop("poly_slug")
    result = analyze([row])
    assert result["synchronized_cross_venue_pair_lock_samples"] == 1
    assert result["unique_synchronized_pair_identities"] == 0
    assert result["synchronized_pair_samples_missing_strict_identity"] == 1
    assert result["pair_lock_reasons"]["MISSING_STRICT_PAIR_IDENTITY"] == 1
    assert result["synchronized_pair_profit_dollars"] == "0"


def test_parse_signal_lines_ignores_other_log_lines_and_bad_json():
    rows = parse_signal_lines(
        [
            "INFO other message",
            '2026-08-19 INFO DECIDED_MLB_PROP_SIGNAL {"known_winner_edges":[]}',
            "DECIDED_MLB_PROP_SIGNAL not-json",
        ]
    )
    assert rows == [{"known_winner_edges": []}]
