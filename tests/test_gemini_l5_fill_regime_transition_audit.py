from src.gemini_l5_fill_regime_transition_audit import audit


def _raw(ts, *, fav, prob, ticker="BTC05M1", expiry="120"):
    return {
        "ticker": ticker,
        "observed_at": ts,
        "favored_side": fav,
        "favored_probability": prob,
        "seconds_to_expiry": expiry,
    }


def _score(fill_side="YES"):
    return {
        "venue": "gemini",
        "settled_detail": [{
            "ticker": "BTC05M1",
            "resolution_side": "NO",
            "settled_shadow_trading_pnl": "-0.61",
            "fill_chronology": [{
                "side": fill_side,
                "price": "0.60",
                "quantity": "1",
                "observed_at": "2026-08-28T08:10:02+00:00",
            }],
        }],
    }


def test_detects_flip_against_fill_side():
    result = audit([
        _raw("2026-08-28T08:10:00+00:00", fav="YES", prob="0.70"),
        _raw("2026-08-28T08:10:02+00:00", fav="NO", prob="0.78"),
    ], _score())
    assert result["strict_fills"] == 1
    assert result["flipped_against_fill_side"] == 1
    assert result["adverse_regime_transition_fills"] == 1
    assert result["adverse_transition_rate"] == "1"


def test_detects_favored_regime_disappearing():
    result = audit([
        _raw("2026-08-28T08:10:00+00:00", fav="YES", prob="0.70"),
        _raw("2026-08-28T08:10:02+00:00", fav=None, prob="0.56"),
    ], _score())
    assert result["adverse_regime_transition_fills"] == 1
    assert result["detail"][0]["transition"] == "FAVORED_REGIME_DISAPPEARED"


def test_persistent_fill_side_is_not_marked_adverse_transition():
    result = audit([
        _raw("2026-08-28T08:10:00+00:00", fav="YES", prob="0.70"),
        _raw("2026-08-28T08:10:02+00:00", fav="YES", prob="0.68"),
    ], _score())
    assert result["fill_side_still_favored"] == 1
    assert result["adverse_regime_transition_fills"] == 0
