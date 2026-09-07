"""OWNER 5561482606: 'MLB games exist' != 'maker rebates exist' !=
'liquidity-reward pools exist' -- the scan keeps the three apart per
market, reconciles by slug, timestamps, and never says 'MLB has none'."""
from __future__ import annotations

from src import pmus_incentive_audit as ia
from src import pmus_q1_executor as q1


class FakeT:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, path, body=None, base=q1.API):
        self.calls.append((method, path, base))
        for (b, prefix), v in self.routes.items():
            if b == base and path.startswith(prefix):
                if isinstance(v, Exception):
                    raise v
                return v
        raise RuntimeError(f"HTTP 404 {path}")


def _mkt(slug, fee=0.06, status="MARKET_STATUS_OPEN", start="2026-09-06T17:10:00Z"):
    return {"slug": slug, "feeCoefficient": fee, "status": status, "active": True,
            "closed": False, "gameStartTime": start}


LIVE = {"events": [
    {"slug": "mlb-mil-cin-2026-09-06", "live": True, "title": "Brewers vs Reds",
     "markets": [_mkt("aec-mlb-mil-cin-2026-09-06"), _mkt("tsc-mlb-mil-cin-2026-09-06-9pt5")]},
    {"slug": "lal-mal-lev-2026-09-06", "live": True, "title": "Malaga vs Levante",
     "markets": [_mkt("atc-lal-mal-lev-2026-09-06-draw")]},
]}
PREGAME = {"events": [
    {"slug": "mlb-nyy-bos-2026-09-06", "live": False, "title": "Yankees vs Red Sox",
     "markets": [_mkt("aec-mlb-nyy-bos-2026-09-06", start="2026-09-06T23:05:00Z"),
                 _mkt("astatc-mlb-nyy-bos-2026-09-06-k-cole-gte6", fee=0, start="2026-09-06T23:05:00Z")]},
]}
PROGRAMS = {"programs": [
    {"marketSlug": "aqc-atp-uso-2026-09-09-quarterq-alezve", "instrumentState": "INSTRUMENT_STATE_OPEN",
     "timePeriods": [{"programId": "atp_wta_futures_20260904", "programType": "liquidityProgram",
                      "status": "active", "rewardPool": 500, "targetSize": 10000,
                      "discountFactor": 0.9, "period": "daily_event",
                      "start": "2026-09-05T00:00:00Z", "createdAt": "2026-09-04T02:59:23Z"}]},
    {"marketSlug": "aec-mlb-nyy-bos-2026-09-06", "instrumentState": "INSTRUMENT_STATE_OPEN",
     "timePeriods": [{"programId": "mlb_live_20260906", "programType": "liquidityProgram",
                      "status": "active", "rewardPool": 800, "targetSize": 5000,
                      "discountFactor": 0.9, "period": "live",
                      "start": "2026-09-06T00:00:00Z", "createdAt": "2026-09-06T01:00:00Z"}]},
    {"marketSlug": "aec-mlb-atl-mil-2026-08-23", "instrumentState": "INSTRUMENT_STATE_CLOSED",
     "timePeriods": [{"programId": "mlb_live_20260823", "programType": "liquidityProgram",
                      "status": "ended", "rewardPool": 800, "targetSize": 5000}]},
]}


def _routes(programs=PROGRAMS, pregame=PREGAME):
    return {(q1.GATEWAY, "/v1/events?live=true"): LIVE,
            (q1.GATEWAY, "/v1/events?active=true&closed=false&categories=sports&limit=500&offset=500"): {"events": []},
            (q1.GATEWAY, "/v1/events?active=true"): pregame,
            (q1.GATEWAY, "/v1/incentives"): programs}


def test_scan_keeps_live_rebate_and_pool_apart_per_market_and_reconciles_by_slug():
    t = FakeT(_routes())
    out = ia.mlb_program_scan(t)
    assert out["ok"] and out["errors"] == [] and out["scanned_at"].endswith("+00:00")
    assert out["mlb_markets"] == 4 and out["mlb_live"] == 2
    assert out["rebate_eligible_by_fee_coefficient"] == 3       # the fee-0 prop is not
    assert out["liquidity_pool_any_period"] == 1 and out["liquidity_pool_active_period"] == 1
    rows = {r["slug"]: r for r in out["markets"]}
    assert "atc-lal-mal-lev-2026-09-06-draw" not in rows        # soccer never counted as MLB
    assert rows["tsc-mlb-mil-cin-2026-09-06-9pt5"]["live"] is True
    assert rows["tsc-mlb-mil-cin-2026-09-06-9pt5"]["program_periods"] == []
    assert rows["aec-mlb-nyy-bos-2026-09-06"]["live"] is False
    assert rows["aec-mlb-nyy-bos-2026-09-06"]["in_program_list"] is True
    assert rows["aec-mlb-nyy-bos-2026-09-06"]["program_periods"][0]["programId"] == "mlb_live_20260906"
    assert rows["aec-mlb-nyy-bos-2026-09-06"]["program_periods"][0]["createdAt"] == "2026-09-06T01:00:00Z"
    assert rows["astatc-mlb-nyy-bos-2026-09-06-k-cole-gte6"]["rebate_eligible_by_fee_coefficient"] is False
    # program-list entries that are MLB but not in today's universe are still reported (lifecycle)
    assert [e["slug"] for e in out["mlb_program_entries"]] == [
        "aec-mlb-nyy-bos-2026-09-06", "aec-mlb-atl-mil-2026-08-23"]
    assert out["mlb_program_entries"][1]["in_current_universe"] is False
    assert out["program_list_entries"] == 3
    assert out["statement"].startswith("MLB markets present: 4 (2 live) across 2 games (1 live) at ")
    # one summary row per GAME, main markets first within the per-market list
    assert out["mlb_games"] == 2 and out["mlb_games_live"] == 1
    g = {x["event"]: x for x in out["games"]}
    assert g["mlb-mil-cin-2026-09-06"]["live"] is True and g["mlb-mil-cin-2026-09-06"]["markets"] == 2
    assert g["mlb-mil-cin-2026-09-06"]["main_markets"] == 2 and g["mlb-mil-cin-2026-09-06"]["pool_any_period"] == 0
    assert g["mlb-nyy-bos-2026-09-06"]["main_markets"] == 1 and g["mlb-nyy-bos-2026-09-06"]["rebate_eligible"] == 1
    assert g["mlb-nyy-bos-2026-09-06"]["pool_active_period"] == 1
    assert [r["slug"] for r in out["markets"]][:2] == ["aec-mlb-mil-cin-2026-09-06",
                                                      "tsc-mlb-mil-cin-2026-09-06-9pt5"]
    assert [r["slug"] for r in out["markets"]][-1] == "astatc-mlb-nyy-bos-2026-09-06-k-cole-gte6"
    assert "3/4 carry a positive feeCoefficient" in out["statement"]
    assert "1/4 currently have an ACTIVE liquidity-reward program period" in out["statement"]
    assert out["discovery_gap"] is None
    assert all(m == "GET" for m, _, _ in t.calls)


def test_scan_flags_a_discovery_gap_instead_of_concluding_absence():
    out = ia.mlb_program_scan(FakeT(_routes(programs={"programs": PROGRAMS["programs"][:1]})))
    assert out["mlb_markets"] == 4 and out["liquidity_pool_any_period"] == 0
    assert out["mlb_program_entries"] == []
    assert "August accrual" in out["discovery_gap"]
    assert "never 'MLB has none'" in out["discovery_gap"]
    # the reason absence cannot mean "never": the list only ever returns
    # ACTIVE periods, so a finished MLB program is invisible to it by design
    assert "ACTIVE-ONLY" in out["discovery_gap"]
    assert out["program_list_census"]["exhausted"] is True
    assert any("ACTIVE-ONLY" in x for x in out["program_list_census"]["limits"])
    assert "0/4 currently have an ACTIVE" in out["statement"]


def test_earnings_rows_without_program_id_are_keyed_by_program_type(monkeypatch, tmp_path):
    monkeypatch.setenv(q1.STATE_ENV, str(tmp_path))
    routes = _routes()
    routes[(q1.API, "/v1/portfolio/activities")] = {"activities": [], "eof": True}
    routes[(q1.API, "/v1/account/balances")] = {"balances": [{"buyingPower": {"value": "110"}}]}
    routes[(q1.API, "/v1/incentives/earnings")] = {"rewards": [
        {"reward": 0.14, "programType": "liquidityProgram", "marketSlug": "tsc-nfl-sea-ten-2026-08-23-4q-3pt5",
         "date": "2026-08-23", "status": "SKIPPED"},
        {"reward": 0.04, "programType": "liquidityProgram", "marketSlug": "astatc-mlb-atl-mil-2026-08-23-tb-ronacu-gte3",
         "date": "2026-08-23", "status": "SKIPPED"}]}
    out = ia.incentive_audit(FakeT(routes))
    assert out["earnings"]["rows"] == 2 and out["earnings"]["total_usd"] == "0.18"
    assert out["earnings"]["by_program"] == {"liquidityProgram": 2}


def test_scan_survives_a_failing_events_page_and_reports_it():
    routes = _routes()
    routes[(q1.GATEWAY, "/v1/events?active=true")] = RuntimeError("HTTP 503")
    out = ia.mlb_program_scan(FakeT(routes))
    assert out["ok"] and out["mlb_markets"] == 2 and len(out["errors"]) == 1
    assert "503" in out["errors"][0]


def test_audit_carries_the_mlb_statement_in_its_verdict(monkeypatch, tmp_path):
    monkeypatch.setenv(q1.STATE_ENV, str(tmp_path))
    routes = _routes()
    routes[(q1.API, "/v1/portfolio/activities")] = {"activities": [], "eof": True}
    routes[(q1.API, "/v1/account/balances")] = {"balances": [{"buyingPower": {"value": "110"}}]}
    routes[(q1.API, "/v1/incentives/earnings")] = {"earnings": []}
    out = ia.incentive_audit(FakeT(routes))
    assert out["mlb_scan"]["mlb_markets"] == 4
    assert out["verdict"]["mlb"].startswith("MLB markets present: 4 (2 live)")


def test_program_list_walks_every_page_and_says_it_is_an_active_only_view():
    """OWNER 5561482606: a census, not a snapshot. The venue serves a fixed
    100 rows per page behind nextPageToken, so the previous single read
    reported 'MLB entries in the program list: 0 of 100' -- a fact about one
    page. It is also ACTIVE-ONLY, which is why absence can never mean
    'never had one'."""
    from src import pmus_incentive_audit as ia
    from src import pmus_q1_executor as q1

    pages = {
        "/v1/incentives": {"programs": [
            {"marketSlug": "astatc-atp-a-b", "timePeriods": [
                {"programId": "us_open", "status": "active", "rewardPool": 400}]}],
            "nextPageToken": "t2"},
        "/v1/incentives?pageToken=t2": {"programs": [
            {"marketSlug": "astatc-wta-c-d", "timePeriods": [
                {"programId": "us_open", "status": "active", "rewardPool": 400}]}],
            "nextPageToken": "t3"},
        "/v1/incentives?pageToken=t3": {"programs": [
            {"marketSlug": "tsc-mlb-stl-sf-7pt5", "timePeriods": [
                {"programId": "mlb_live", "status": "active", "rewardPool": 900}]}]},
    }

    class T:
        def __init__(self):
            self.paths = []

        def request(self, method, path, body=None, base=q1.API):
            self.paths.append(path)
            return pages[path]

    t = T()
    out = ia.program_list(t)
    assert out["pages"] == 3 and out["rows"] == 3 and out["exhausted"] is True
    assert out["truncated_at_page_cap"] is False and out["error"] is None
    assert t.paths == list(pages)
    # the MLB row lives on page THREE: a single-page read would have missed it
    assert out["by_league"] == {"atp": 1, "wta": 1, "mlb": 1}
    assert out["period_statuses"] == {"active": 3}
    assert {p["program_id"] for p in out["programs"]} == {"us_open", "mlb_live"}
    assert out["programs"][0]["market_rows"] == 2          # us_open, most rows
    assert any("ACTIVE-ONLY" in x for x in out["limits"])
    assert any("never" in x.lower() for x in out["limits"])

    # a mid-walk failure is visible, not silent: keep what was gathered and
    # say the walk did NOT reach the end
    class Broken(T):
        def request(self, method, path, body=None, base=q1.API):
            if path.endswith("t2"):
                raise RuntimeError("HTTP 503")
            return super().request(method, path, body=body, base=base)

    bad = ia.program_list(Broken())
    assert bad["pages"] == 1 and bad["rows"] == 1
    assert bad["exhausted"] is False and "503" in bad["error"]

    # a page cap short of the end is reported as truncation, never as a census
    capped = ia.program_list(T(), max_pages=2)
    assert capped["pages"] == 2 and capped["exhausted"] is False
    assert capped["truncated_at_page_cap"] is True and capped["by_league"].get("mlb") is None
