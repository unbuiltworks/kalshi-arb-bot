"""Venue-served game progress: the ranker can finally see how far along a
market's game is. Diagnostic and reported ONLY -- the candidate order stays
L5-state fit (OWNER 5556232035 #3); ordering by settlement proximity is the
owner's decision to make, not a change to slip in here."""
from __future__ import annotations

import pytest

from src import pmus_event_progress as prog
from src import pmus_l5_continuous_session as cont

NOW = 1788749220.0        # 2026-09-07T02:47:00Z, when the live read below
                          # measured endDate = gameStartTime + 14 days


def test_proximity_reads_the_tokens_the_venue_serves():
    assert prog.proximity("Q1", "football")[0] == 0.10
    assert prog.proximity("Q4", "football") == (0.85, "period_token/quarters/football")
    assert prog.proximity("HT", "soccer") == (0.50, "period_token/halftime/soccer")
    assert prog.proximity("S2", "tennis")[0] == 0.45
    assert prog.proximity("S5", "table-tennis")[0] == 0.95
    assert prog.proximity("86'", "soccer") == (round(86 / 95.0, 4), "match_minute/soccer")
    assert prog.proximity("Top 3rd", "baseball") == (round(3 / 9.0, 4), "inning/baseball")
    assert prog.proximity("FT", "soccer") == (1.0, "period_token/final")
    # the venue's own ended flag outranks any token
    assert prog.proximity("Q2", "football", ended=True) == (1.0, "venue_ended_flag")


def test_proximity_refuses_to_guess_what_the_venue_does_not_serve():
    # an esports series length is never served, so position in it is unknown
    assert prog.proximity("Map 2", "esports") == (None, "unknown_series_length")
    assert prog.proximity("Game 1", "esports")[0] is None
    assert prog.proximity("", "football") == (None, "no_period_served")
    assert prog.proximity(None, "football")[0] is None
    assert prog.proximity("SECOND SESSION", "cricket") == (
        None, "unrecognised_period_token")


def _event(slug, period, markets, sport="football", ended=False,
           start="2026-09-06T23:30:00Z", clock="7:57"):
    return {"slug": slug, "period": period, "elapsed": clock, "score": "13-34",
            "ended": ended, "startTime": start, "live": True,
            "eventState": {"type": sport},
            "markets": [{"slug": m} for m in markets]}


def test_progress_table_maps_every_market_of_a_live_event():
    rows = [_event("cfb-a", "Q4", ["asc-cfb-a-pos", "tsc-cfb-a-total"]),
            _event("esp-b", "Map 2", ["m1"], sport="esports")]
    tbl = prog.progress_table(rows, now=NOW)
    assert set(tbl) == {"asc-cfb-a-pos", "tsc-cfb-a-total", "m1"}
    a = tbl["asc-cfb-a-pos"]
    assert a["game_proximity"] == 0.85 and a["period"] == "Q4"
    assert a["clock"] == "7:57" and a["event_slug"] == "cfb-a"
    assert a["hours_since_event_start"] > 3
    assert "NOT a settlement time" in a["labels"]
    assert tbl["m1"]["game_proximity"] is None
    assert tbl["asc-cfb-a-pos"] is tbl["tsc-cfb-a-total"]   # one event, shared


@pytest.mark.real_event_progress
def test_table_caches_and_never_raises(monkeypatch):
    prog._CACHE.clear()
    calls = []

    def fake_get(path):
        calls.append(path)
        return [_event("cfb-a", "Q4", ["m"])]
    t0 = 1000.0
    tbl, diag = prog.table(get_json=fake_get, now=t0)
    assert diag["markets_with_progress"] == 1 and diag["recognised_proximity"] == 1
    prog.table(get_json=fake_get, now=t0 + 5)
    assert len(calls) == 1                       # served from cache
    prog.table(get_json=fake_get, now=t0 + 5 + prog.TTL_S)
    assert len(calls) == 2

    prog._CACHE.clear()

    def boom(path):
        raise RuntimeError("HTTP 503")
    tbl, diag = prog.table(get_json=boom, now=t0)
    assert tbl == {} and diag["error"].startswith("RuntimeError")


def test_pair_score_prefers_game_position_over_the_plus_14d_expiry():
    """The regression this exists for: PMUS serves endDate = gameStartTime
    + 14d for live sports, identical across markets, so the old
    residual_duration was 0.0 for everything and the component was dead."""
    now = NOW                                   # 2026-09-07T02:47Z
    row = {"bid": "0.45", "ask": "0.50", "bid_qty": "20", "ask_qty": "20",
           "trades_1h": 10, "two_sided_trades": True,
           "market_state": {"endDate": "2026-09-20T23:30:00Z"}}
    mv = {"off_distribution": False, "state_n": 300}
    dead = cont.pair_score(row, mv, now)
    assert dead["components"]["residual_duration"] == 0.0     # +14d expiry
    assert dead["horizon_basis"] == "market_endDate" and dead["hours_to_end"] > 300
    live = cont.pair_score(row, mv, now, {
        "game_proximity": 0.85, "proximity_basis": "period_token/quarters/football",
        "period": "Q4", "clock": "7:57"})
    assert live["components"]["residual_duration"] == 0.85
    assert live["horizon_basis"] == "period_token/quarters/football"
    assert live["game_period"] == "Q4" and live["game_clock"] == "7:57"
    assert live["pair_score"] > dead["pair_score"]
    # nothing served either way -> the neutral 0.5 the ranker always had
    bare = cont.pair_score({"bid": "0.45", "ask": "0.50"}, mv, now)
    assert bare["components"]["residual_duration"] == 0.5
    assert bare["horizon_basis"] == "none" and bare["game_proximity"] is None


def test_game_progress_is_reported_but_does_not_reorder_candidates(monkeypatch):
    """Anti-drift (OWNER 5556232035 #3): the order stays L5-state fit. The
    market deep in its game must NOT jump the queue just because it is."""
    from src import pmus_spring_l5_probe_entry as entry
    monkeypatch.setattr(entry, "strict_evidence_reason",
                        lambda slug, ev, now=None: None)
    monkeypatch.setattr(cont, "_model_view", lambda model, bid: {
        "off_distribution": False,
        "state_n": 400 if str(bid) == "0.44" else 100,
        "p_buy": 0.5, "state": "s", "relation_ranked": []})
    import src.pmus_spring_evidence_transfer as xfer
    monkeypatch.setattr(xfer, "compose_evidence_from_recorder",
                        lambda status, slug, now: {"slug": slug})
    monkeypatch.setattr(prog, "table", lambda *a, **k: ({
        "late-game": {"game_proximity": 0.95, "proximity_basis": "b",
                      "period": "Q4", "clock": "1:12"}},
        {"markets_with_progress": 1}))
    status = {"spring_watch": [
        {"slug": "late-game", "bid": "0.50", "ask": "0.55", "bid_qty": "10"},
        {"slug": "better-fit", "bid": "0.44", "ask": "0.49", "bid_qty": "10"}]}
    out = cont.rank_candidates(status, NOW)
    assert [x["slug"] for x in out["admitted"]] == ["better-fit", "late-game"]
    late = [x for x in out["admitted"] if x["slug"] == "late-game"][0]
    assert late["game_proximity"] == 0.95 and late["game_period"] == "Q4"
    assert out["progress_diag"]["markets_with_progress"] == 1
    # a market with no live event still ranks and simply reports nothing
    fit = [x for x in out["admitted"] if x["slug"] == "better-fit"][0]
    assert fit["game_proximity"] is None and fit["horizon_basis"] == "none"
