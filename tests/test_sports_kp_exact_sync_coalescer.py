from src.sports_kp_exact_sync_coalescer import ExactSyncCoalescer


def test_coalescer_preserves_global_four_rps_floor():
    c = ExactSyncCoalescer(min_interval_seconds=0.25)
    c.offer("a")
    assert c.pop_due(10.0) == "a"
    c.offer("b")
    assert c.pop_due(10.249) is None
    assert c.pop_due(10.25) == "b"


def test_rate_limited_triggers_are_coalesced_not_dropped():
    c = ExactSyncCoalescer(min_interval_seconds=0.25)
    c.offer("first")
    assert c.pop_due(1.0) == "first"
    c.offer("a")
    c.offer("b")
    c.offer("c")
    assert c.pending_count == 3
    assert c.pop_due(1.1) is None
    assert c.pending_count == 3
    assert c.pop_due(1.25) == "c"
    assert c.pending_count == 2


def test_repeated_key_is_deduplicated_and_refreshed():
    c = ExactSyncCoalescer()
    c.offer("a")
    c.offer("b")
    c.offer("a")
    assert c.pending_count == 2
    assert c.pop_due(5.0) == "a"
