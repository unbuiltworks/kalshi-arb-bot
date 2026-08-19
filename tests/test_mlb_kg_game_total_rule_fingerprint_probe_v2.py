from src.mlb_kg_game_total_rule_fingerprint_probe_v2 import _event_candidates_from_symbol


def test_event_candidates_try_documented_event_ticker_without_gemi_prefix():
    assert _event_candidates_from_symbol("GEMI-MLB-2608182340-ATH-KC-T-O6") == [
        "GEMI-MLB-2608182340-ATH-KC-T",
        "MLB-2608182340-ATH-KC-T",
    ]


def test_event_candidates_fail_closed_on_bad_symbol():
    assert _event_candidates_from_symbol("") == []
    assert _event_candidates_from_symbol("NOHYPHEN") == []
