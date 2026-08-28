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


def _sync_pair_row(*, observed_ms=1000, event="OPEN", opened_ms=None, profit="0.04", **extra):
    row = _pair_row(
        poly_book_received_ms=observed_ms,
        kalshi_book_received_ms=observed_ms + 40,
        quote_receive_skew_ms=40,
        cross_venue_q1_pair_lock={"profit": profit, "direction": "POLY_YES_KALSHI_NO"},
        pair_episode={
            "identity": "example",
            "event": event,
            "opened_ms": observed_ms if opened_ms is None else opened_ms,
            "observed_ms": observed_ms,
        },
    )
    row.update(extra)
    return row


def test_legacy_pair_lock_without_receive_timing_fails_closed():
    ok, reason = pair_synchronization_status(_pair_row())
    assert ok is False
    assert reason == "MISSING_SYNCHRONIZATION_EVIDENCE"


def test_pair_lock_accepts_consistent_sub_500ms_receive_skew():
    row = _pair_row(poly_book_received_ms=1000, kalshi_book_received_ms=1230, quote_receive_skew_ms=230)
    assert pair_synchronization_status(row) == (True, "SYNCHRONIZED_PAIR_EVIDENCE")


def test_pair_lock_rejects_over_500ms_receive_skew():
    row = _pair_row(poly_book_received_ms=1000, kalshi_book_received_ms=1501, quote_receive_skew_ms=501)
    assert pair_synchronization_status(row) == (False, "PAIR_RECEIVE_SKEW_EXCEEDED")


def test_pair_lock_rejects_inconsistent_reported_skew():
    row = _pair_row(poly_book_received_ms=1000, kalshi_book_received_ms=1200, quote_receive_skew_ms=20)
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
        _pair_row(poly_book_received_ms=1000, kalshi_book_received_ms=1100, quote_receive_skew_ms=100),
        _pair_row(),
    ]
    result = analyze(rows)
    assert result["synchronized_cross_venue_pair_lock_samples"] == 1
    assert result["synchronized_cross_venue_pair_locks"] == 1
    assert Decimal(result["synchronized_pair_profit_dollars"]) == Decimal("0.04")
    assert Decimal(result["executable_shadow_pair_profit_dollars"]) == Decimal("0")


def test_repeated_resting_legacy_pair_samples_are_not_additive_capacity():
    rows = [
        _pair_row(poly_book_received_ms=1000, kalshi_book_received_ms=1050, quote_receive_skew_ms=50,
                  cross_venue_q1_pair_lock={"profit": "0.04", "direction": "POLY_YES_KALSHI_NO"}),
        _pair_row(poly_book_received_ms=3000, kalshi_book_received_ms=3040, quote_receive_skew_ms=40,
                  cross_venue_q1_pair_lock={"profit": "0.05", "direction": "POLY_YES_KALSHI_NO"}),
        _pair_row(poly_book_received_ms=5000, kalshi_book_received_ms=5030, quote_receive_skew_ms=30,
                  cross_venue_q1_pair_lock={"profit": "0.03", "direction": "POLY_YES_KALSHI_NO"}),
    ]
    result = analyze(rows)
    assert result["synchronized_cross_venue_pair_lock_samples"] == 3
    assert result["unique_synchronized_pair_identities"] == 1
    assert result["legacy_identity_collapsed_units"] == 1
    assert result["duplicate_synchronized_pair_samples_excluded"] == 2
    assert Decimal(result["synchronized_pair_profit_dollars"]) == Decimal("0.05")
    assert Decimal(result["executable_shadow_pair_profit_dollars"]) == Decimal("0")


def test_episode_open_move_resting_counts_one_consumable_unit():
    rows = [
        _sync_pair_row(observed_ms=1000, event="OPEN", opened_ms=1000, profit="0.04"),
        _sync_pair_row(observed_ms=2000, event="MOVE", opened_ms=1000, profit="0.06"),
        _sync_pair_row(observed_ms=3000, event="RESTING", opened_ms=1000, profit="0.06"),
    ]
    result = analyze(rows)
    assert result["episode_aware_consumable_units"] == 1
    assert result["episode_open_executable_shadow_units"] == 1
    assert result["duplicate_synchronized_pair_samples_excluded"] == 2
    assert Decimal(result["episode_aware_profit_dollars"]) == Decimal("0.06")
    assert Decimal(result["episode_first_seen_executable_shadow_profit_dollars"]) == Decimal("0.04")
    assert Decimal(result["executable_shadow_pair_profit_dollars"]) == Decimal("0.04")
    assert result["pair_episode_events"] == {"MOVE": 1, "OPEN": 1, "RESTING": 1}


def test_new_open_after_close_counts_renewed_liquidity_once():
    rows = [
        _sync_pair_row(observed_ms=1000, event="OPEN", opened_ms=1000, profit="0.04"),
        _sync_pair_row(observed_ms=2000, event="RESTING", opened_ms=1000, profit="0.04"),
        {"pair_episode": {"identity": "example", "event": "CLOSE", "opened_ms": 1000,
                          "last_seen_ms": 2000, "closed_observed_ms": 3000}, "read_only": True},
        _sync_pair_row(observed_ms=5000, event="OPEN", opened_ms=5000, profit="0.03"),
        _sync_pair_row(observed_ms=6000, event="MOVE", opened_ms=5000, profit="0.05"),
    ]
    result = analyze(rows)
    assert result["unique_synchronized_pair_identities"] == 1
    assert result["episode_aware_consumable_units"] == 2
    assert result["episode_open_executable_shadow_units"] == 2
    assert result["consumable_episode_units"] == 2
    assert Decimal(result["synchronized_pair_profit_dollars"]) == Decimal("0.09")
    assert Decimal(result["executable_shadow_pair_profit_dollars"]) == Decimal("0.07")
    assert result["pair_episode_events"] == {"CLOSE": 1, "MOVE": 1, "OPEN": 2, "RESTING": 1}


def test_midstream_episode_without_open_is_not_backfilled_as_executable_shadow():
    rows = [
        _sync_pair_row(observed_ms=2000, event="MOVE", opened_ms=1000, profit="0.06"),
        _sync_pair_row(observed_ms=3000, event="RESTING", opened_ms=1000, profit="0.08"),
    ]
    result = analyze(rows)
    assert result["episode_aware_consumable_units"] == 1
    assert result["episode_open_executable_shadow_units"] == 0
    assert result["episode_units_without_observed_open"] == 1
    assert Decimal(result["episode_peak_theoretical_profit_dollars"]) == Decimal("0.08")
    assert Decimal(result["executable_shadow_pair_profit_dollars"]) == Decimal("0")


def test_malformed_episode_evidence_fails_closed_instead_of_falling_back_to_legacy():
    row = _sync_pair_row(observed_ms=1000, event="OPEN", opened_ms=0, profit="0.09")
    result = analyze([row])
    assert result["malformed_episode_rows_excluded"] == 1
    assert result["synchronized_cross_venue_pair_locks"] == 0
    assert result["synchronized_pair_profit_dollars"] == "0"
    assert result["pair_lock_reasons"]["MALFORMED_EPISODE_EVIDENCE"] == 1


def test_opposite_directions_remain_distinct_consumable_identities():
    rows = [
        _pair_row(poly_book_received_ms=1000, kalshi_book_received_ms=1050, quote_receive_skew_ms=50,
                  cross_venue_q1_pair_lock={"profit": "0.04", "direction": "POLY_YES_KALSHI_NO"}),
        _pair_row(poly_book_received_ms=3000, kalshi_book_received_ms=3040, quote_receive_skew_ms=40,
                  cross_venue_q1_pair_lock={"profit": "0.03", "direction": "KALSHI_YES_POLY_NO"}),
    ]
    result = analyze(rows)
    assert result["unique_synchronized_pair_identities"] == 2
    assert Decimal(result["synchronized_pair_profit_dollars"]) == Decimal("0.07")
    assert Decimal(result["executable_shadow_pair_profit_dollars"]) == Decimal("0")


def test_synchronized_pair_without_strict_identity_is_excluded_from_capacity():
    row = _pair_row(poly_book_received_ms=1000, kalshi_book_received_ms=1050, quote_receive_skew_ms=50)
    row.pop("poly_slug")
    result = analyze([row])
    assert result["synchronized_cross_venue_pair_lock_samples"] == 1
    assert result["unique_synchronized_pair_identities"] == 0
    assert result["synchronized_pair_samples_missing_strict_identity"] == 1
    assert result["pair_lock_reasons"]["MISSING_STRICT_PAIR_IDENTITY"] == 1
    assert result["synchronized_pair_profit_dollars"] == "0"


def test_parse_signal_lines_accepts_legacy_and_sync_markers():
    rows = parse_signal_lines(
        [
            "INFO other message",
            '2026-08-19 INFO DECIDED_MLB_PROP_SIGNAL {"known_winner_edges":[]}',
            '2026-08-19 INFO DECIDED_MLB_PROP_SYNC_PAIR {"pair_episode":{"event":"OPEN"}}',
            "DECIDED_MLB_PROP_SIGNAL not-json",
        ]
    )
    assert rows == [
        {"known_winner_edges": []},
        {"pair_episode": {"event": "OPEN"}},
    ]
