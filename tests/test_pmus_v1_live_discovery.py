"""Regression for issue #167 comments 5525261365/5525287122/5525293221/
5525317831 — the live-discovery gap (operator saw 34 live events while
markets_expanded=0):

Measured root cause (live probe 2026-09-03T12:31Z): the global
/v1/markets listing orders live-game rows beyond the bounded page cap
(first 5,000 sports rows were 4,998 FUTURE types with ZERO start-time
matches to live events), so the gameStartTime join can never see them.
The server-side /v1/events?live=true set returns the complete live slate
in one page with complete embedded market sets (list == by-id == by-slug
counts on probed events). Fix: admit live markets from that server-side
set; keep the legacy paths as supplemental with the join emptiness
explained by measured diagnostics, never hidden."""
from __future__ import annotations

from src import pmus_sports_collector as col
from src import pmus_ws_evidence_recorder as rec


LIVE_EVENT = {
    "id": "98111", "slug": "cs2-mibr-eye-2026-09-03", "live": True,
    "ended": False, "startTime": "2026-09-03T11:00:00Z",
    "eventState": {"score": "1-0", "period": "Map 2"},
    "markets": [
        {"slug": "aec-cs2-mibr-eye-2026-09-03", "name": "Moneyline",
         "bestBidQuote": {"value": "0.44"}, "volume24hr": 900,
         "gameStartTime": "2026-09-03T11:00:00Z"},
        {"slug": "map2-winner-mibr", "name": "Map 2 winner",
         "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_PROP"},
    ],
}
STARTED_NOT_LIVE = {  # scheduled event already past startTime: NOT live
    "slug": "kbo-old-2026-09-03", "live": False, "ended": False,
    "startTime": "2026-09-03T05:00:00Z",
    "markets": [{"slug": "aec-kbo-old-2026-09-03"}],
}
ENDED_EVENT = {
    "slug": "lol-done-2026-09-03", "live": True, "ended": True,
    "startTime": "2026-09-03T08:00:00Z",
    "markets": [{"slug": "aec-lol-done-2026-09-03"}],
}
FUTURE_ROWS = [  # the real mismatch shape: futures with non-live starts
    {"slug": f"tec-mlb-fut-{i}", "gameStartTime": "2026-09-27T00:00:00Z",
     "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_FUTURE"}
    for i in range(6)
]


def test_fetch_live_events_keeps_only_server_live_rows():
    def get_json(path):
        assert path.startswith(col.LIVE_EVENTS_PATH)
        return {"events": [LIVE_EVENT, STARTED_NOT_LIVE, ENDED_EVENT]}
    rows, diag = col.fetch_live_events(get_json)
    assert [e["slug"] for e in rows] == ["cs2-mibr-eye-2026-09-03"]
    assert diag["v1_live_events"] == 1 and diag["v1_live_error"] is None
    assert diag["v1_live_hit_cap"] is False


def test_fetch_live_events_surfaces_failure_visibly():
    def boom(path):
        raise OSError("gateway down")
    rows, diag = col.fetch_live_events(boom)
    assert rows == []
    assert diag["v1_live_error"].startswith("OSError")


def test_live_event_targets_admit_props_and_never_leak_futures():
    t = col.live_event_targets([LIVE_EVENT, STARTED_NOT_LIVE, ENDED_EVENT])
    slugs = [x["slug"] for x in t]
    assert slugs == ["aec-cs2-mibr-eye-2026-09-03", "map2-winner-mibr"]
    by = {x["slug"]: x for x in t}
    assert by["map2-winner-mibr"]["prop"] is True         # prop admitted
    assert by["aec-cs2-mibr-eye-2026-09-03"]["prop"] is False
    assert by["aec-cs2-mibr-eye-2026-09-03"]["why"] == "v1_live_event_market"
    assert by["aec-cs2-mibr-eye-2026-09-03"]["event_state"] == \
        {"live": True, "score": "1-0", "period": "Map 2"}
    assert by["aec-cs2-mibr-eye-2026-09-03"]["league"] == "cs2"
    # cheap-screen metadata still flows for pre-WS seeding
    assert by["aec-cs2-mibr-eye-2026-09-03"]["market_meta"][
        "bestBidQuote"] == {"value": "0.44"}
    # a merely-started scheduled event and an ended event admit nothing
    assert "aec-kbo-old-2026-09-03" not in slugs
    assert "aec-lol-done-2026-09-03" not in slugs


def test_enumeration_join_diagnostics_explain_zero_expansion():
    events_by_league = {"cs2": [LIVE_EVENT], "kbo": [STARTED_NOT_LIVE]}
    d = col.enumeration_join_diagnostics(events_by_league, FUTURE_ROWS,
                                         hit_cap=True)
    assert d["enumerated_open_rows"] == 6
    assert d["enumeration_hit_cap"] is True
    assert d["enum_rows_with_gameStartTime"] == 6
    assert d["enum_rows_matching_any_event_start"] == 0
    assert d["enum_rows_matching_live_event_start"] == 0
    assert d["enum_sample"]["first"]["slug"] == "tec-mlb-fut-0"


def test_discover_universe_admits_live_slate_despite_broken_join(
        monkeypatch):
    """End-to-end mismatch regression: the /v2 league feed shows the
    event WITHOUT usable embedded markets, global enumeration serves only
    unjoinable futures — yet the v1 live surface admits the full slate,
    and the diagnostics carry both the v1 counts and the measured
    explanation for markets_expanded=0."""
    def fake_get(path):
        if path.startswith("/v2/leagues/"):
            return {"events": [{**LIVE_EVENT, "markets": []}]}
        if path == "/v2/leagues":
            return {"leagues": [{"slug": "cs2"}]}
        if path == "/v1/incentives":
            return {}
        if path.startswith(col.LIVE_EVENTS_PATH):
            return {"events": [LIVE_EVENT, STARTED_NOT_LIVE, ENDED_EVENT]}
        if path.startswith("/v1/markets?active=true"):
            return {"markets": FUTURE_ROWS}
        raise AssertionError(f"unexpected path {path}")
    monkeypatch.setattr(col, "http_get_json", fake_get)
    monkeypatch.setenv(rec.LEAGUES_ENV, "cs2")
    monkeypatch.setenv(rec.EXPAND_PAGES_ENV, "1")
    monkeypatch.delenv(rec.MARKETS_ENV, raising=False)
    monkeypatch.delenv(rec.PREFIXES_ENV, raising=False)
    targets = rec.discover_universe()
    slugs = {t["slug"] for t in targets}
    assert {"aec-cs2-mibr-eye-2026-09-03", "map2-winner-mibr"} <= slugs
    assert "tec-mlb-fut-0" not in slugs                  # no futures leak
    assert "aec-kbo-old-2026-09-03" not in slugs         # started != live
    d = dict(rec.DISCOVERY_DIAG)
    assert d["v1_live_markets_added"] == 2
    assert d["v1_live_events"] == 1 and d["v1_live_error"] is None
    assert d["markets_expanded"] == 0                    # honest, explained:
    assert d["enum_rows_matching_live_event_start"] == 0
    assert d["enumerated_open_rows"] == 6


def test_v1_failure_degrades_to_legacy_path_visibly(monkeypatch):
    def fake_get(path):
        if path.startswith("/v2/leagues/"):
            return {"events": [LIVE_EVENT]}          # legacy embedded works
        if path == "/v1/incentives":
            return {}
        if path.startswith(col.LIVE_EVENTS_PATH):
            raise OSError("v1 surface down")
        if path.startswith("/v1/markets?active=true"):
            return {"markets": []}
        raise AssertionError(f"unexpected path {path}")
    monkeypatch.setattr(col, "http_get_json", fake_get)
    monkeypatch.setenv(rec.LEAGUES_ENV, "cs2")
    monkeypatch.setenv(rec.EXPAND_PAGES_ENV, "0")
    monkeypatch.delenv(rec.MARKETS_ENV, raising=False)
    monkeypatch.delenv(rec.PREFIXES_ENV, raising=False)
    targets = rec.discover_universe()
    assert {t["slug"] for t in targets} >= \
        {"aec-cs2-mibr-eye-2026-09-03", "map2-winner-mibr"}
    d = dict(rec.DISCOVERY_DIAG)
    assert d["v1_live_error"].startswith("OSError")      # visible, not silent
    assert d["v1_live_markets_added"] == 0
