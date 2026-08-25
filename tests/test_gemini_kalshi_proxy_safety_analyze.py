from src.gemini_kalshi_proxy_safety_analyze import parse_settlement_lines, summarize, wilson_upper_95


def _payload(*, g="yes", k="yes", price="110", checkpoint=3):
    return {
        "classification": "INDEX_MISMATCH_CALIBRATION_ONLY",
        "window_end_utc": "2026-08-18T12:15:00+00:00",
        "gemini_strike": "100",
        "kalshi_target": "101",
        "gemini_outcome": g,
        "kalshi_result": k,
        "checkpoints": [{"checkpoint_s": checkpoint, "composite_price": price}],
    }


def test_parse_railway_prefixed_line():
    rows = parse_settlement_lines([
        '2026 INFO PROXY_SETTLEMENT {"classification":"INDEX_MISMATCH_CALIBRATION_ONLY","gemini_outcome":"yes"}'
    ])
    assert len(rows) == 1


def test_same_side_far_margin_is_eligible():
    result = summarize([_payload()])
    c = result["checkpoints"]["3"]["cohorts"]["20"]
    assert c["eligible_windows"] == 1
    assert c["venue_outcome_disagreements"] == 0
    assert c["proxy_wrong_for_either_venue"] == 0
    assert c["research_gate_passes"] is False


def test_between_boundaries_is_rejected():
    result = summarize([_payload(price="100.5", g="yes", k="no")])
    assert result["checkpoints"]["3"]["cohorts"]["0"]["eligible_windows"] == 0


def test_disagreement_counted_even_when_proxy_same_side():
    result = summarize([_payload(g="yes", k="no")])
    c = result["checkpoints"]["3"]["cohorts"]["20"]
    assert c["eligible_windows"] == 1
    assert c["venue_outcome_disagreements"] == 1
    assert c["proxy_wrong_for_either_venue"] == 1


def test_zero_errors_small_sample_not_zero_risk():
    upper = wilson_upper_95(0, 100)
    assert upper is not None
    assert 0.03 < upper < 0.04
