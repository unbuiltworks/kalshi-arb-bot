from decimal import Decimal as D

from src.gemini_q10_reward_frontier_shadow import Band, target


def test_target_places_complement_quotes_at_requested_distance():
    yes, no = target(D("0.49"), D("0.01"), D("0.09"))
    assert yes == D("0.40")
    assert no == D("0.42")


def test_band_summary_uses_continuous_and_minute_uptime():
    b = Band(D("0.09"))
    b.eligible_seconds = 100
    b.qualified_seconds = 70
    b.minute_total = 10
    b.minute_qualified = 7
    row = b.summary()
    assert row["continuous_qualified_pct"] == 70.0
    assert row["minute_qualified_pct"] == 70.0


def test_band_reset_clears_only_live_quote_state():
    b = Band(D("0.05"), yes_quote=D("0.44"), no_quote=D("0.46"), posts=3, pulls=1)
    b.calm_since = 123.0
    b.reset_quote()
    assert b.yes_quote is None
    assert b.no_quote is None
    assert b.calm_since is None
    assert b.posts == 3
    assert b.pulls == 1
