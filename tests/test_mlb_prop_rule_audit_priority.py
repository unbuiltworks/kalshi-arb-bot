from src.mlb_prop_rule_audit_priority import build_rule_audit_priority_queue


def _row(*, profit1="0.03", profit5="0.15", synchronized=True, ticker="KXMLBHIT-X-P1-1"):
    return {
        "family": "baseball_player_hits",
        "poly_slug": "poly-hit-p1-1",
        "kalshi_ticker": ticker,
        "book_comparable": True,
        "synchronized_book_pair": synchronized,
        "quote_receive_skew_ms": 42,
        "probe_1usd": {
            "direction": "POLY_YES_KALSHI_NO",
            "quantity": 1,
            "conservative_locked_profit": profit1,
            "total_cost_upper_bound": "0.97",
        },
        "probe_5usd": {
            "direction": "POLY_YES_KALSHI_NO",
            "quantity": 5,
            "conservative_locked_profit": profit5,
            "total_cost_upper_bound": "4.85",
        },
    }


def _evidence(*, complete=True, ticker="KXMLBHIT-X-P1-1"):
    return {
        "family": "baseball_player_hits",
        "polymarket_instrument_id": "pm-instrument-1",
        "kalshi_ticker": ticker,
        "evidence_complete_for_independent_compare": complete,
        "missing_evidence": [] if complete else ["kalshi_settlement_sources"],
    }


def test_collapses_one_and_five_dollar_views_without_double_counting():
    result = build_rule_audit_priority_queue([_row()], [_evidence()])
    assert result["queue_size"] == 1
    item = result["audit_queue"][0]
    assert item["quantity"] == 5
    assert item["conservative_locked_profit"] == "0.15"
    assert item["exact_rule_evidence_complete"] is True
    assert item["live_probe_ready"] is False
    assert result["live_probe_ready_pairs"] == 0


def test_unsynchronized_books_fail_closed():
    result = build_rule_audit_priority_queue([_row(synchronized=False)], [_evidence()])
    assert result["queue_size"] == 0
    assert result["skipped"]["unsafe_or_unsynchronized"] == 1


def test_incomplete_exact_evidence_stays_in_queue_but_never_promotes():
    result = build_rule_audit_priority_queue([_row()], [_evidence(complete=False)])
    assert result["queue_size"] == 1
    assert result["evidence_complete_queue_size"] == 0
    item = result["audit_queue"][0]
    assert item["exact_rule_evidence_complete"] is False
    assert item["missing_evidence"] == ["kalshi_settlement_sources"]
    assert item["full_rules_audited_shadow"] is False
    assert item["live_probe_ready"] is False


def test_evidence_complete_positive_pair_ranks_ahead_of_incomplete_pair():
    complete = _row(profit5="0.06", ticker="KXMLBHIT-X-P1-1")
    incomplete = _row(profit5="0.30", ticker="KXMLBHIT-X-P2-1")
    incomplete["poly_slug"] = "poly-hit-p2-1"
    result = build_rule_audit_priority_queue(
        [incomplete, complete],
        [_evidence(ticker="KXMLBHIT-X-P1-1"), _evidence(complete=False, ticker="KXMLBHIT-X-P2-1")],
    )
    assert result["queue_size"] == 2
    assert result["audit_queue"][0]["kalshi_ticker"] == "KXMLBHIT-X-P1-1"
    assert result["audit_queue"][1]["kalshi_ticker"] == "KXMLBHIT-X-P2-1"
