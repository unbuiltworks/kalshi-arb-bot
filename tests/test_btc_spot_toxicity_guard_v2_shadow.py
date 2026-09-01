from decimal import Decimal as D

from src.btc_spot_toxicity_guard_v2_shadow import BtcSpotGuardV2Shadow


def test_heartbeat_keeps_quiet_ticker_live():
    g = BtcSpotGuardV2Shadow(window_ms=2000, stale_ms=1500, pull_move_bps=D("5"))
    g.record(D("78000"), now_ns=0)
    g.record_liveness(now_ns=2_000_000_000)
    d = g.decision(now_ns=2_100_000_000)
    assert d.ready
    assert not d.stale
    assert not d.dangerous
    assert d.reason == "CALM"


def test_missing_heartbeat_still_fails_closed():
    g = BtcSpotGuardV2Shadow(window_ms=2000, stale_ms=1500, pull_move_bps=D("5"))
    g.record(D("78000"), now_ns=0)
    d = g.decision(now_ns=2_000_000_000)
    assert d.stale
    assert d.dangerous


def test_fast_move_remains_dangerous_with_fresh_liveness():
    g = BtcSpotGuardV2Shadow(window_ms=2000, stale_ms=1500, pull_move_bps=D("5"))
    g.record(D("78000"), now_ns=0)
    g.record(D("78050"), now_ns=1_000_000_000)
    g.record_liveness(now_ns=1_100_000_000)
    d = g.decision(now_ns=1_100_000_000)
    assert d.ready
    assert d.dangerous
    assert d.reason == "FAST_SPOT_MOVE"
