from src import pmus_ws_evidence_recorder_fast as fast


def _state(*, hot: bool):
    if hot:
        return {
            "bid": "0.50", "ask": "0.51", "bid_qty": "100",
            "ask_qty": "100", "both_sides_served": True,
            "book_updates": 500,
            "trades": [{"t": 1_000.0, "px": "0.50", "dir": "buy"}
                       for _ in range(40)],
        }
    return {
        "bid": "0.48", "ask": "0.53", "bid_qty": "1",
        "ask_qty": "1", "both_sides_served": False,
        "book_updates": 1, "trades": [],
    }


def test_fast_rerank_moves_l5_seat_without_discovery():
    now = 1_000.0
    state = {"a": _state(hot=False), "b": _state(hot=True)}
    alloc, diag = fast.fast_l5_allocation(
        state, ["a", "b"], {"a"}, now=now)
    assert alloc == {"b"}
    assert diag["changed_from_cycle_plan"] is True
    assert diag["network_get_calls_added"] == 0
    assert diag["source"] == "authenticated_ws_in_memory"

    # Same subscribed websocket universe, no discovery/reconnect: changing
    # only the in-memory authenticated evidence changes the next allocation.
    state["a"] = _state(hot=True)
    state["b"] = _state(hot=False)
    alloc2, diag2 = fast.fast_l5_allocation(
        state, ["a", "b"], alloc, now=now)
    assert alloc2 == {"a"}
    assert diag2["changed_from_cycle_plan"] is True


def test_fast_rerank_never_allocates_outside_current_ws_universe():
    state = {
        "current": _state(hot=False),
        "stale-but-hot": _state(hot=True),
    }
    alloc, _ = fast.fast_l5_allocation(
        state, ["current"], {"current"}, now=1_000.0)
    assert alloc == {"current"}
    assert "stale-but-hot" not in alloc


def test_rotation_planner_semantics_stay_disabled():
    alloc, diag = fast.fast_l5_allocation(
        {"a": _state(hot=True)}, ["a"], None, now=1_000.0)
    assert alloc == set()
    assert diag["mode"] == "disabled_non_priority_planner"
    assert diag["network_get_calls_added"] == 0


def test_subscription_capture_delegates_without_adding_frames(monkeypatch):
    expected = [{"subscribe": {"market_slugs": ["a", "b"]}}]
    monkeypatch.setattr(fast, "_BASE_SUBSCRIBE_FRAMES",
                        lambda slugs: expected)
    got = fast.capture_subscribe_frames(["a", "b"])
    assert got is expected
    assert fast._CURRENT_SUBSCRIBED == ("a", "b")
