from src import three_venue_mlb_gk_exec_shadow_sharded as sharded


def _payload(**overrides):
    payload = {
        "recomputed_payload_locked_profit": "0.03",
        "quote_skew_ms": 50,
        "gemini_age_ms": 100,
        "kalshi_age_ms": 100,
        "blockers": ["FULL_RULE_FINGERPRINT_REQUIRED", "LIVE_NOT_ARMED"],
    }
    payload.update(overrides)
    return payload


def test_hot_payload_accepts_price_freshness_candidate_with_non_economic_blockers():
    assert sharded._is_hot_payload(_payload()) is True


def test_hot_payload_rejects_payload_economics_mismatch_even_when_thresholds_pass():
    assert sharded._is_hot_payload(_payload(blockers=[
        "FULL_RULE_FINGERPRINT_REQUIRED",
        "PAYLOAD_ECONOMICS_MISMATCH",
        "LIVE_NOT_ARMED",
    ])) is False


def test_hot_payload_requires_explicit_blocker_list():
    payload = _payload()
    payload.pop("blockers")
    assert sharded._is_hot_payload(payload) is False


def test_hot_payload_fails_closed_on_stale_or_low_profit_quotes():
    assert sharded._is_hot_payload(_payload(recomputed_payload_locked_profit="0.0299")) is False
    assert sharded._is_hot_payload(_payload(quote_skew_ms=51)) is False
    assert sharded._is_hot_payload(_payload(gemini_age_ms=101)) is False
    assert sharded._is_hot_payload(_payload(kalshi_age_ms=101)) is False
