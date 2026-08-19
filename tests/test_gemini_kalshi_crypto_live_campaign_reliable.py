from src.gemini_kalshi_crypto_live_campaign_reliable import _benign_primary_miss


def test_primary_cancel_zero_fill_without_kalshi_is_benign():
    result = {
        "outcome": "PRIMARY_NOT_FILLED",
        "hard_halt_required": False,
        "gemini": {"filled_quantity": "0", "status": "CANCELED", "reason": "FillOrKillWouldNotFill"},
        "kalshi": None,
    }
    assert _benign_primary_miss(result) is True


def test_primary_miss_is_not_benign_if_kalshi_was_submitted():
    result = {
        "outcome": "PRIMARY_NOT_FILLED",
        "hard_halt_required": False,
        "gemini": {"filled_quantity": "0", "status": "CANCELED"},
        "kalshi": {"filled_quantity": "0", "status": "CANCELED"},
    }
    assert _benign_primary_miss(result) is False


def test_any_gemini_fill_is_not_benign():
    result = {
        "outcome": "PRIMARY_NOT_FILLED",
        "hard_halt_required": False,
        "gemini": {"filled_quantity": "1", "status": "FILLED"},
        "kalshi": None,
    }
    assert _benign_primary_miss(result) is False


def test_ambiguous_primary_is_not_benign():
    result = {
        "outcome": "AMBIGUOUS_PRIMARY_SUBMISSION",
        "hard_halt_required": True,
        "gemini": {"filled_quantity": "0", "status": "UNKNOWN"},
        "kalshi": None,
    }
    assert _benign_primary_miss(result) is False


def test_one_leg_fill_is_not_benign():
    result = {
        "outcome": "ONE_LEG_FILLED",
        "hard_halt_required": True,
        "gemini": {"filled_quantity": "1", "status": "FILLED"},
        "kalshi": {"filled_quantity": "0", "status": "CANCELED"},
    }
    assert _benign_primary_miss(result) is False
