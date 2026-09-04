from __future__ import annotations

from src import pmus_sports_live_collector as live
from src import pmus_sports_collector as col


def _event(i: int, markets: int = 3) -> dict:
    return {
        "id": str(i),
        "slug": f"sport-{i}",
        "live": True,
        "ended": False,
        "eventState": {"period": "live", "score": "0-0"},
        "markets": [
            {
                "slug": f"m-{i}-{j}",
                "volume24hr": 1000 - j,
                "liquidity": 100 + j,
                "bestBidQuote": {"value": "0.49"},
                "bestAskQuote": {"value": "0.51"},
            }
            for j in range(markets)
        ],
    }


def test_cap_64_covers_all_50_live_events_before_extra_markets():
    universe = live.live_targets([_event(i) for i in range(50)])
    picked, diag = live.select_for_poll(universe, 64, cycle=0)
    assert len(picked) == 64
    assert diag["discovered_events"] == 50
    assert diag["discovered_markets"] == 150
    assert diag["selected_events"] == 50
    assert len({r["event_slug"] for r in picked}) == 50


def test_surplus_rotates_without_losing_event_floor():
    universe = live.live_targets([_event(i) for i in range(50)])
    first, d0 = live.select_for_poll(universe, 64, cycle=0)
    second, d1 = live.select_for_poll(universe, 64, cycle=1)
    assert len({r["event_slug"] for r in first}) == 50
    assert len({r["event_slug"] for r in second}) == 50
    extra0 = {r["slug"] for r in first if not r["slug"].endswith("-0")}
    extra1 = {r["slug"] for r in second if not r["slug"].endswith("-0")}
    assert extra0 != extra1
    assert d1["rotation_cycle"] == 1


def test_healthy_live_surface_never_admits_incentive_only_slug(monkeypatch):
    events = [_event(1, markets=2)]
    incentives = {
        "programs": [{
            "marketSlug": "stale-incentive-only",
            "timePeriods": [{"status": "active"}],
        }]
    }

    def fake_get(path):
        if path.startswith(col.LIVE_EVENTS_PATH):
            return {"events": events}
        raise AssertionError(path)

    monkeypatch.setattr(col, "http_get_json", fake_get)
    monkeypatch.delenv(col.PREFIXES_ENV, raising=False)
    picked, diag = live.discover_poll_targets(incentives, True, 64, cycle=0)
    assert {r["slug"] for r in picked} == {"m-1-0", "m-1-1"}
    assert "stale-incentive-only" not in {r["slug"] for r in picked}
    assert diag["source_mode"] == "v1_live_sports_authoritative"
    assert diag["incentive_only_admitted"] == 0


def test_explicit_prefix_filter_is_respected_but_default_is_full_slate(monkeypatch):
    ev = _event(2, markets=2)
    ev["markets"][0]["slug"] = "aec-keep"
    ev["markets"][1]["slug"] = "prop-keep-by-default"

    def fake_get(path):
        if path.startswith(col.LIVE_EVENTS_PATH):
            return {"events": [ev]}
        raise AssertionError(path)

    monkeypatch.setattr(col, "http_get_json", fake_get)
    monkeypatch.delenv(col.PREFIXES_ENV, raising=False)
    full, _ = live.discover_poll_targets({}, False, 64, cycle=0)
    assert {r["slug"] for r in full} == {"aec-keep", "prop-keep-by-default"}

    monkeypatch.setenv(col.PREFIXES_ENV, "aec-")
    filtered, _ = live.discover_poll_targets({}, False, 64, cycle=0)
    assert {r["slug"] for r in filtered} == {"aec-keep"}
