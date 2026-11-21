"""Production-tape coverage proof: measured from rows, never from config."""
from __future__ import annotations

from datetime import datetime, timezone

from src import pmus_coverage_report as cov

NOW = 1_788_600_000.0


def _iso(ts): return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _book(ts, slug, league="cs2"):
    return {"kind": "bbo_book", "t": _iso(ts), "slug": slug, "league": league,
            "bid": "0.48", "ask": "0.50", "bid_touch_qty": "500",
            "ask_touch_qty": "400", "ask_book_missing": False,
            "shares_traded_cum": "100"}


def _disco(ts, targets, diag=None):
    return {"kind": "discovery", "t": _iso(ts), "targets": targets,
            "diagnostics": diag or {}}


def test_counts_distinct_live_events_not_just_markets():
    """Three markets on one event is ONE event of coverage, not three."""
    rows = [_disco(NOW - 300, [
        {"slug": "m1", "league": "cs2", "event_slug": "ev-a"},
        {"slug": "m2", "league": "cs2", "event_slug": "ev-a"},
        {"slug": "m3", "league": "atp", "event_slug": "ev-b"}])]
    for i in range(3):
        for s in ("m1", "m2", "m3"):
            rows.append(_book(NOW - 240 + i * 20, s))
    out = cov.coverage(rows, now=NOW)
    assert out["coverage"]["distinct_markets_polled"] == 3
    assert out["coverage"]["distinct_live_events_polled"] == 2
    assert out["coverage"]["markets_without_event_identity"] == 0


def test_categories_come_from_recorded_league_then_slug():
    rows = [_disco(NOW - 300, [{"slug": "aec-atp-x-2026-09-04",
                                "league": "atp", "event_slug": "e"}]),
            _book(NOW - 200, "aec-atp-x-2026-09-04", league="atp"),
            _book(NOW - 100, "aec-cs2-y-2026-09-04", league=None)]
    out = cov.coverage(rows, now=NOW)
    cats = out["coverage"]["categories"]
    assert cats["atp"] == 1
    assert cats["cs2"] == 1          # derived from the slug when league null


def test_gap_stats_split_pinned_from_rotating():
    """The whole point of pinning: pinned cadence must be measurably
    tighter than the rotating tail, and the 60s breach count is explicit."""
    diag = {"pinned_markets": [{"slug": "pin", "reason": "operator_pin"}],
            "rotation_cycle": 0}
    rows = [_disco(NOW - 900, [{"slug": "pin", "event_slug": "e1"},
                               {"slug": "rot", "event_slug": "e2"}], diag)]
    for i in range(20):                      # pinned every 10s
        rows.append(_book(NOW - 600 + i * 10, "pin"))
    for i in range(3):                       # rotating every 120s
        rows.append(_book(NOW - 600 + i * 120, "rot"))
    out = cov.coverage(rows, now=NOW)
    assert out["gaps"]["pinned"]["max"] <= 60.0
    assert out["gaps"]["pinned"]["markets_over_60s"] == 0
    assert out["gaps"]["rotating"]["max"] > 60.0
    assert out["gaps"]["rotating"]["markets_over_60s"] == 1
    assert out["pinning"]["pinned_markets"][0]["slug"] == "pin"
    assert out["pinning"]["pinned_markets"][0]["polls"] == 20


def test_live_incentive_programs_are_surfaced_from_diagnostics():
    diag = {"incentive_programs_live": ["aec-cfb-a-2026-09-05"],
            "pinned_markets": [{"slug": "aec-cfb-a-2026-09-05",
                                "reason": "live_incentive_program"}]}
    rows = [_disco(NOW - 300, [{"slug": "aec-cfb-a-2026-09-05",
                                "event_slug": "e"}], diag),
            _book(NOW - 200, "aec-cfb-a-2026-09-05", league="cfb"),
            _book(NOW - 190, "aec-cfb-a-2026-09-05", league="cfb")]
    out = cov.coverage(rows, now=NOW)
    assert out["pinning"]["incentive_programs_live"] == ["aec-cfb-a-2026-09-05"]
    assert out["pinning"]["pinned_markets"][0]["reason"] == \
        "live_incentive_program"


def test_errors_and_empty_tape_are_reported_not_hidden():
    out = cov.coverage([], now=NOW)
    assert out["coverage"]["distinct_markets_polled"] == 0
    assert out["window"]["newest_row_age_s"] is None
    rows = [{"kind": "error", "stage": "poll", "t": _iso(NOW - 60)},
            {"kind": "error", "stage": "discovery", "t": _iso(NOW - 30)}]
    out2 = cov.coverage(rows, now=NOW)
    assert out2["window"]["error_rows"] == 2
    assert out2["window"]["error_stages"]["poll"] == 1


def test_report_module_has_no_execution_or_credential_path():
    """Coverage proof is a reader. It must never grow an order path."""
    from pathlib import Path
    src = Path(cov.__file__).read_text()
    for banned in ("http_get_json", "/v1/orders", "X-PM-", "armed",
                   "requests", "urllib", "POST"):
        assert banned not in src, banned
