from src.gemini_prediction_maker_shadow import snapshot_score


def test_snapshot_score_rewards_tighter_two_sided_quote():
    tight, tight_ok = snapshot_score(max_spread_cents=10.0, bid=0.49, ask=0.51, size=50)
    wide, wide_ok = snapshot_score(max_spread_cents=10.0, bid=0.45, ask=0.55, size=50)
    assert tight_ok is True
    assert wide_ok is True
    assert tight > wide > 0


def test_snapshot_score_caps_size():
    capped, ok1 = snapshot_score(max_spread_cents=10.0, bid=0.49, ask=0.51, size=250)
    larger, ok2 = snapshot_score(max_spread_cents=10.0, bid=0.49, ask=0.51, size=1000)
    assert ok1 is True and ok2 is True
    assert capped == larger


def test_snapshot_score_rejects_too_wide_or_small():
    too_wide, ok1 = snapshot_score(max_spread_cents=2.0, bid=0.45, ask=0.55, size=50)
    too_small, ok2 = snapshot_score(max_spread_cents=10.0, bid=0.49, ask=0.51, size=9)
    assert too_wide == 0 and ok1 is False
    assert too_small == 0 and ok2 is False
