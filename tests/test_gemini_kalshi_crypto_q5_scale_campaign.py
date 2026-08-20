from src.gemini_kalshi_crypto_q5_scale_campaign import graduation_from_q1_state


def _clean_state(completed=5):
    return {
        "halted": False,
        "completed_total": completed,
        "unsettled_deployed_usd": "0",
        "reserved_capital_usd": "0",
        "venue_unsettled_usd": {"gemini": "0", "kalshi": "0"},
        "venue_reserved_usd": {"gemini": "0", "kalshi": "0"},
        "lanes": {
            asset: {"active_run_id": None, "status": "IDLE"}
            for asset in ("BTC", "ETH", "SOL", "XRP")
        },
    }


def test_q5_graduation_accepts_five_clean_fully_reconciled_q1_trades():
    ok, reason = graduation_from_q1_state(_clean_state(), min_clean_trades=5)
    assert ok is True
    assert reason == "clean_q1_graduation_evidence"


def test_q5_graduation_rejects_halted_q1_campaign():
    state = _clean_state(); state["halted"] = True
    ok, reason = graduation_from_q1_state(state, min_clean_trades=5)
    assert ok is False
    assert reason == "q1_campaign_halted"


def test_q5_graduation_rejects_insufficient_clean_settlements():
    ok, reason = graduation_from_q1_state(_clean_state(completed=4), min_clean_trades=5)
    assert ok is False
    assert reason == "q1_clean_trades_4_lt_5"


def test_q5_graduation_rejects_any_global_reservation_or_unsettled_capital():
    state = _clean_state(); state["reserved_capital_usd"] = "1"
    ok, reason = graduation_from_q1_state(state, min_clean_trades=5)
    assert ok is False and reason == "q1_reserved_capital_nonzero"
    state = _clean_state(); state["unsettled_deployed_usd"] = "0.75"
    ok, reason = graduation_from_q1_state(state, min_clean_trades=5)
    assert ok is False and reason == "q1_unsettled_deployed_nonzero"


def test_q5_graduation_rejects_any_venue_reservation_or_unsettled_capital():
    state = _clean_state(); state["venue_reserved_usd"]["gemini"] = "1"
    ok, reason = graduation_from_q1_state(state, min_clean_trades=5)
    assert ok is False and reason == "q1_gemini_reserved_nonzero"
    state = _clean_state(); state["venue_unsettled_usd"]["kalshi"] = "0.5"
    ok, reason = graduation_from_q1_state(state, min_clean_trades=5)
    assert ok is False and reason == "q1_kalshi_unsettled_nonzero"


def test_q5_graduation_rejects_any_active_asset_lane():
    state = _clean_state(); state["lanes"]["ETH"]["active_run_id"] = "still-open"
    ok, reason = graduation_from_q1_state(state, min_clean_trades=5)
    assert ok is False
    assert reason == "q1_active_lane_ETH"
