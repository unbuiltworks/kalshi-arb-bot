from src.strict_mlb_prop_snapshot_v2 import _resolve_exact_search_event


def _strict_event(*, start_time: str = "2026-08-27T23:05:00Z") -> dict:
    return {
        "slug": "mlb-nyy-bos-2026-08-27",
        "title": "NYY vs BOS",
        "startTime": start_time,
        "markets": [
            {
                "id": "m1",
                "slug": "astatc-mlb-nyy-bos-2026-08-27-k-gercole-gte6",
                "sportsMarketType": "baseball_player_strikeouts",
                "question": "Will Gerrit Cole record at least 6 strikeouts in NYY vs BOS?",
            }
        ],
    }


def test_search_event_requires_exact_slug_and_start_time_before_prop_discovery():
    event = _strict_event()
    out = _resolve_exact_search_event(
        {"events": [event]},
        event_slug="mlb-nyy-bos-2026-08-27",
        event_title="NYY vs BOS",
        event_start="2026-08-27T23:05:00Z",
    )
    assert out is not None
    assert out["slug"] == "mlb-nyy-bos-2026-08-27"


def test_same_slug_with_postponed_search_time_is_rejected():
    out = _resolve_exact_search_event(
        {"events": [_strict_event(start_time="2026-08-28T00:05:00Z")]},
        event_slug="mlb-nyy-bos-2026-08-27",
        event_title="NYY vs BOS",
        event_start="2026-08-27T23:05:00Z",
    )
    assert out is None


def test_same_slug_with_naive_search_time_is_rejected():
    out = _resolve_exact_search_event(
        {"events": [_strict_event(start_time="2026-08-27T23:05:00")]},
        event_slug="mlb-nyy-bos-2026-08-27",
        event_title="NYY vs BOS",
        event_start="2026-08-27T23:05:00Z",
    )
    assert out is None


def test_same_slug_without_strict_prop_supply_is_not_promoted_to_discovery():
    event = _strict_event()
    event["markets"] = [{"id": "m2", "slug": "mlb-nyy-bos-winner", "question": "Will NYY win?"}]
    out = _resolve_exact_search_event(
        {"events": [event]},
        event_slug="mlb-nyy-bos-2026-08-27",
        event_title="NYY vs BOS",
        event_start="2026-08-27T23:05:00Z",
    )
    assert out is None


def test_duplicate_exact_slug_is_rejected():
    event = _strict_event()
    out = _resolve_exact_search_event(
        {"events": [event, dict(event)]},
        event_slug="mlb-nyy-bos-2026-08-27",
        event_title="NYY vs BOS",
        event_start="2026-08-27T23:05:00Z",
    )
    assert out is None
