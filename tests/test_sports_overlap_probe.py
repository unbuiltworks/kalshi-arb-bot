from src.sports_overlap_probe import find_candidates, summarize


def _poly(**overrides):
    row = {
        "slug": "astatc-mlb-phi-nym-2026-08-16-hit-bryhar-gte1",
        "sportsMarketType": "baseball_player_hits",
        "marketType": "props",
        "question": "Will Bryce Harper record 1+ hits?",
        "line": "1",
    }
    row.update(overrides)
    return row


def _kalshi_market(**overrides):
    row = {
        "ticker": "KXMLBPLAYER-26AUG16PHINYM-BHARPER-H1",
        "title": "Bryce Harper hits",
        "yes_sub_title": "1+ hits",
        "no_sub_title": "Fewer than 1 hit",
        "rules_primary": "Bryce Harper records at least 1 hit in Philadelphia vs New York.",
    }
    row.update(overrides)
    return row


def _event(market=None, **overrides):
    row = {
        "series_ticker": "KXMLBPLAYER",
        "event_ticker": "KXMLBPLAYER-26AUG16PHINYM",
        "title": "Philadelphia Phillies vs New York Mets",
        "sub_title": "MLB",
        "markets": [market or _kalshi_market()],
    }
    row.update(overrides)
    return row


def test_player_prop_candidate_requires_player_stat_threshold_and_event_anchor():
    rows = find_candidates([_poly()], [_event()])
    assert len(rows) == 1
    row = rows[0]
    assert row.poly_type == "baseball_player_hits"
    assert row.kalshi_series == "KXMLBPLAYER"
    assert "player_name" in row.evidence
    assert "stat_family" in row.evidence
    assert "threshold" in row.evidence
    assert "both_event_sides" in row.evidence


def test_player_mismatch_fails_closed():
    bad = _kalshi_market(
        title="Trea Turner hits",
        rules_primary="Trea Turner records at least 1 hit in Philadelphia vs New York.",
    )
    assert find_candidates([_poly()], [_event(bad)]) == []


def test_threshold_mismatch_fails_closed():
    bad = _kalshi_market(
        yes_sub_title="2+ hits",
        no_sub_title="Fewer than 2 hits",
        rules_primary="Bryce Harper records at least 2 hits in Philadelphia vs New York.",
    )
    assert find_candidates([_poly()], [_event(bad)]) == []


def test_summary_never_marks_candidates_execution_eligible():
    result = summarize(find_candidates([_poly()], [_event()]))
    assert result["execution_eligible_pairs"] == 0
    example = result["families"]["baseball_player_hits"]["top_examples"][0]
    assert example["execution_eligible"] is False
    assert example["status"] == "CANDIDATE_ONLY_NEEDS_RULE_ADAPTER"
