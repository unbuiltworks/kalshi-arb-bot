"""Coverage-audit regressions (issue #167 comment 5520693117): a live
event whose embedded page holds only 15 markets while the venue serves
150+ must be fully expanded via the paginated global open-market
enumeration, joined on the exact gameStartTime key — a 100+ market event
can never silently collapse to its first embedded page again."""
from __future__ import annotations

from src import pmus_sports_collector as col
from src import pmus_ws_evidence_recorder as rec

START = "2026-09-03T18:10:00Z"


def _open_market(i, gst=START, prefix="vmc"):
    return {"slug": f"{prefix}-mlb-cws-hou-2026-09-03-{i}",
            "gameStartTime": gst, "active": True, "closed": False,
            "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_PROP",
            "question": f"prop {i}"}


def _pages(rows, page_size=500):
    return [rows[i:i + page_size] for i in range(0, len(rows), page_size)]


def test_enumeration_pages_until_short_page():
    rows = [_open_market(i) for i in range(1200)]
    pages = _pages(rows)
    calls = []

    def get_json(path):
        calls.append(path)
        assert "active=true" in path and "closed=false" in path
        offset = int(path.split("offset=")[1])
        idx = offset // 500
        return {"markets": pages[idx] if idx < len(pages) else []}

    got, n_pages = col.enumerate_open_markets(get_json)
    assert len(got) == 1200 and n_pages == 3       # 500+500+200(short)
    assert len(calls) == 3                          # stopped on short page


def test_enumeration_failure_degrades_not_dies():
    def get_json(path):
        if "offset=500" in path:
            raise RuntimeError("boom")
        return {"markets": [_open_market(i) for i in range(500)]}
    got, n_pages = col.enumerate_open_markets(get_json)
    assert len(got) == 500 and n_pages == 1


def test_join_admits_only_live_event_matches():
    events = {"mlb": [
        {"live": True, "slug": "mlb-cws-hou-2026-09-03",
         "startTime": START, "eventState": {"score": "3-2"},
         "markets": []},
        {"live": False, "slug": "mlb-later", "startTime": "2026-09-04T18:10:00Z",
         "markets": []},
    ]}
    rows = ([_open_market(i) for i in range(150)]                # live match
            + [_open_market(i, gst="2026-09-04T18:10:00Z") for i in range(9)]
            + [{"slug": "tec-mlb-future", "gameStartTime": ""},   # future
               {"slug": "", "gameStartTime": START}])            # no slug
    out = col.expand_live_event_markets(events, rows)
    assert len(out) == 150                          # only the LIVE event's
    assert all(r["why"] == "expanded_enumeration" for r in out)
    assert all(r["league"] == "mlb" for r in out)
    assert all(r["prop"] is True for r in out)      # vmc- prefix labeled
    assert out[0]["event_state"]["score"] == "3-2"
    assert out[0]["market_meta"]["sportsMarketTypeV2"] == \
        "SPORTS_MARKET_TYPE_PROP"


def test_discover_universe_expands_and_reports_diagnostics(monkeypatch):
    live_event = {"live": True, "slug": "mlb-cws-hou-2026-09-03",
                  "startTime": START, "eventState": {},
                  "markets": [{"slug": f"aec-mlb-cws-hou-2026-09-03-{i}"}
                              for i in range(15)]}     # embedded cap shape
    big = [_open_market(i) for i in range(150)]

    def fake_get(path, timeout=15.0):
        if path == "/v2/leagues":
            return [{"slug": "mlb"}]
        if path.startswith("/v2/leagues/mlb/events"):
            assert f"limit={col.EVENTS_PAGE_LIMIT}" in path   # 10-cap fixed
            return {"events": [live_event]}
        if path == "/v1/incentives":
            return {}
        if path.startswith("/v1/markets?"):
            offset = int(path.split("offset=")[1])
            return {"markets": big[offset:offset + 500]}
        raise AssertionError(path)

    monkeypatch.setattr(col, "http_get_json", fake_get)
    monkeypatch.setenv(rec.LEAGUES_ENV, "all")
    monkeypatch.delenv(rec.MARKETS_ENV, raising=False)
    monkeypatch.delenv(rec.PREFIXES_ENV, raising=False)
    targets = rec.discover_universe()
    slugs = {t["slug"] for t in targets}
    assert len(slugs) == 165                        # 15 embedded + 150 props
    d = rec.DISCOVERY_DIAG
    assert d["events_discovered"] == 1
    assert d["markets_embedded"] == 15
    assert d["markets_expanded"] == 150
    assert d["enumeration_pages_fetched"] == 1
