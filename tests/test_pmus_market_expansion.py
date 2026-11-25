"""Owner 2026-11-25 -- the next market expansion, inside the one controller.

"For the next market expansion, prioritize NBA, NHL and NFL main-line
spreads and totals. Extend existing discovery to secondary event markets
and introduce aggregate event exposure accounting. Do not build a separate
controller. Correct the reward model to account for shared program pools.
We cannot make capital allocation decisions using potentially duplicated
reward estimates."

Three additions, each OFF by default:
  1. secondary-market discovery   PMUS_TWO_SIDED_SECONDARY_MARKETS
  2. per-game exposure limit      PMUS_RN1_EVENT_CAP_USD
  3. shared reward pools          PMUS_REWARD_POOL_SCOPE
Market records below have the shape the venue served on 2026-11-25
(analysis/l5/props/PROPS_COMBOS_SCAN_20261125.md).
"""
import json
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_rewards as RW
from src import pmus_sports_two_sided as M
from src import pmus_sports_xv as XV
from tests.test_pmus_sports_two_sided import FakeVenue

EV = "nba-lal-bos-2026-11-25"
ML = "aec-" + EV
SPREAD = "asc-" + EV + "-neg-3pt5"
TOTAL = "tsc-" + EV + "-221pt5"
Q4 = {"type": "basketball", "period": "Q4", "score": "90-88"}
Q1 = {"type": "basketball", "period": "Q1", "score": "10-8"}


def mkt(slug, mtype, smt, title, bid, ask, **kw):
    m = {"slug": slug, "marketType": mtype, "sportsMarketType": smt,
         "title": title, "active": True, "closed": False, "archived": False,
         "hidden": False, "ep3Status": "OPEN",
         "status": "MARKET_STATUS_OPEN", "orderPriceMinTickSize": 0.01,
         "bestBidQuote": {"value": bid, "currency": "USD"},
         "bestAskQuote": {"value": ask, "currency": "USD"},
         "marketSides": [{"long": True, "tradable": True},
                         {"long": False, "tradable": True}]}
    m.update(kw)
    return m


def event(ek=EV, extra=()):
    lg = ek.split("-")[0]
    return {"event": {"slug": ek, "live": True, "markets": [
        mkt("aec-" + ek, "moneyline", "moneyline", "A vs B", "0.60", "0.62"),
        mkt("asc-%s-neg-3pt5" % ek, "spreads",
            "basketball_team_full_game_spread",
            "Lakers wins by over 3.5 points", "0.52", "0.54"),
        mkt("asc-%s-neg-5pt5" % ek, "spreads",
            "basketball_team_full_game_spread",
            "Lakers wins by over 5.5 points", "0.40", "0.42"),
        mkt("asc-%s-pos-1pt5" % ek, "spreads",
            "basketball_team_full_game_spread",
            "Celtics wins by over 1.5 points", "0.30", "0.32"),
        # a quarter line sitting exactly at 0.50: must never be chosen
        mkt("asc-%s-q1-neg-1pt5" % ek, "spreads",
            "basketball_team_first_quarter_spread",
            "Lakers wins by over 1.5 points in 1st quarter", "0.50", "0.51"),
        mkt("asc-%s-fh-neg-2pt5" % ek, "spreads",
            "basketball_team_first_half_spread",
            "Lakers wins by over 2.5 points in 1st half", "0.50", "0.51"),
        mkt("tsc-%s-221pt5" % ek, "totals",
            "basketball_team_full_game_total", "Over 221.5 total points",
            "0.49", "0.51"),
        mkt("tsc-%s-225pt5" % ek, "totals",
            "basketball_team_full_game_total", "Over 225.5 total points",
            "0.38", "0.40"),
        mkt("tsc-%s-fh-110pt5" % ek, "totals",
            "basketball_team_first_half_total",
            "Over 110.5 total points in 1st half", "0.50", "0.51"),
        mkt("tsc-%s-tt-lal-fg-110pt5" % ek, "totals",
            "basketball_team_total_points",
            "Los Angeles Lakers over 110.5 total points", "0.50", "0.51"),
        mkt("astatc-%s-pts-xyz-25pt5" % ek, "props",
            "basketball_player_points", "X over 25.5 points", "0.50",
            "0.51"),
        *extra]},
        "league": lg}


# ------------------------------------------------- pure: which line it is
@pytest.mark.parametrize("slug, mtype, smt, title, want", [
    # real shapes, three leagues
    ("asc-nba-sac-gs-2026-11-25-pos-1pt5", "spreads",
     "basketball_team_full_game_spread",
     "Golden State Warriors wins by over 1.5 points", "spread"),
    ("asc-nhl-sj-ana-2026-11-25-neg-3pt5", "spreads",
     "hockey_team_full_game_spread", "Sharks wins by over 3.5 goals",
     "spread"),
    ("asc-nfl-gb-lar-2026-11-25-neg-23pt5", "spreads",
     "football_team_full_game_spread",
     "Green Bay Packers wins by over 23.5 points", "spread"),
    ("tsc-nba-sac-gs-2026-11-25-248pt5", "totals",
     "basketball_team_full_game_total", "Over 248.5 total points", "total"),
    ("tsc-nhl-sj-ana-2026-11-25-9pt5", "totals",
     "hockey_team_full_game_total", "Over 9.5 total goals", "total"),
    ("tsc-nfl-gb-lar-2026-11-25-total-73pt5", "totals",
     "football_team_full_game_total", "Over 73.5 total points", "total"),
    # period lines
    ("asc-nba-dal-den-2026-11-24-q4-neg-8pt5", "spreads",
     "basketball_team_fourth_quarter_spread",
     "Dallas Mavericks wins by over 8.5 points in 4th quarter "
     "(excluding overtime)", None),
    ("asc-nba-dal-den-2026-11-24-sh-pos-0pt5", "spreads",
     "basketball_team_second_half_spread",
     "Denver Nuggets wins by over 0.5 points in 2nd half", None),
    ("asc-nfl-gb-lar-2026-11-25-1h-neg-19pt5", "spreads",
     "football_team_first_half_spread",
     "Green Bay Packers wins by over 19.5 points in 1st half", None),
    ("asc-nhl-sj-ana-2026-11-25-p3-pos-1pt5", "spreads",
     "hockey_team_third_period_spread",
     "Ducks wins by over 1.5 goals in 3rd period", None),
    ("tsc-nfl-gb-lar-2026-11-25-1q-26pt5", "totals",
     "football_game_first_quarter_total",
     "Over 26.5 total points in 1st quarter", None),
    ("tsc-nhl-sj-ana-2026-11-25-p1-3pt5", "totals",
     "hockey_team_first_period_total",
     "Over 3.5 total goals in 1st period", None),
    # team totals (the venue files the NHL ones under props)
    ("tsc-nba-bos-was-2026-11-24-tt-bos-fg-98pt5", "totals",
     "basketball_team_total_points", "Boston Celtics over 98.5 total points",
     None),
    ("tsc-nfl-gb-lar-2026-11-25-tt-gb-41pt5", "totals",
     "football_team_points_full_game_total",
     "Green Bay Packers over 41.5 total points", None),
    ("tsc-nfl-gb-lar-2026-11-25-tt1h-gb-14pt5", "totals",
     "football_team_first_half_total",
     "Green Bay Packers over 14.5 total points in 1st half", None),
    ("tsc-nhl-sj-ana-2026-11-25-tt-ana-5pt5", "props",
     "hockey_team_total_goals", "Ducks over 5.5 total goals", None),
    # a period line whose slug LOOKS full-game is still refused by its text
    ("asc-nba-sac-gs-2026-11-25-pos-2pt5", "spreads",
     "basketball_team_full_game_spread",
     "Golden State Warriors wins by over 2.5 points in 1st half", None),
    # a whole-number line could push: not a known shape
    ("asc-nba-sac-gs-2026-11-25-pos-2pt0", "spreads",
     "basketball_team_full_game_spread",
     "Golden State Warriors wins by over 2 points", None),
    # other products
    ("aec-nba-sac-gs-2026-11-25", "moneyline", "moneyline", "A vs B", None),
    ("astatc-nba-sac-gs-2026-11-25-pts-x-25pt5", "props",
     "basketball_player_points", "X over 25.5 points", None),
])
def test_only_full_game_spreads_and_game_totals_are_recognised(
        slug, mtype, smt, title, want):
    m = mkt(slug, mtype, smt, title, "0.50", "0.52")
    assert M.secondary_kind(m, M.event_key(slug)) == want


def test_a_line_of_another_game_is_not_this_games_line():
    m = mkt(SPREAD, "spreads", "basketball_team_full_game_spread",
            "Lakers wins by over 3.5 points", "0.5", "0.52")
    assert M.secondary_kind(m, EV) == "spread"
    assert M.secondary_kind(m, "nba-lal-bos-2026-11-26") is None


@pytest.mark.parametrize("kw, why", [
    ({}, None),
    ({"active": False}, "not_active"),
    ({"active": None}, "not_active"),
    ({"closed": True}, "closed"),
    ({"archived": True}, "closed"),
    ({"ep3Status": "HALTED"}, "not_open"),
    ({"ep3Status": None}, "not_open"),
    ({"marketSides": [{"tradable": True}, {"tradable": False}]},
     "side_not_tradable"),
    ({"marketSides": [{"tradable": True}, {}]}, "side_not_tradable"),
    ({"marketSides": None}, "side_not_tradable"),
    ({"bestAskQuote": None}, "no_two_sided_quote"),
    ({"bestBidQuote": {"value": "0"}}, "no_two_sided_quote"),
    ({"bestBidQuote": {"value": "0.55"}}, "no_two_sided_quote"),  # crossed
])
def test_a_market_that_is_not_open_and_tradable_both_ways_is_refused(
        kw, why):
    m = mkt(SPREAD, "spreads", "basketball_team_full_game_spread", "t",
            "0.50", "0.52", **kw)
    assert M.secondary_untradable(m) == why


def test_main_line_is_the_usable_full_game_line_nearest_a_half():
    got = M.secondary_main_lines(event()["event"], EV)
    assert got["spread"]["slug"] == SPREAD
    assert got["total"]["slug"] == TOTAL
    assert got["spread"]["market_meta"]["bestBidQuote"]["value"] == "0.52"
    assert got["lines"] == {"spread": 3, "total": 2}


def test_main_line_skips_the_nearest_line_when_it_cannot_trade():
    ev = event()["event"]
    for m in ev["markets"]:
        if m["slug"] == SPREAD:
            m["marketSides"][1]["tradable"] = False
        if m["slug"].startswith("tsc-") and "full_game" in \
                m["sportsMarketType"]:
            m["ep3Status"] = "CLOSED"
    got = M.secondary_main_lines(ev, EV)
    assert got["spread"]["slug"] == "asc-" + EV + "-neg-5pt5"
    assert got["total"] is None
    assert got["skipped"] == {"spread:side_not_tradable": 1,
                              "total:not_open": 2}


def test_a_game_with_no_usable_line_adds_nothing():
    got = M.secondary_main_lines({"markets": [mkt(
        ML, "moneyline", "moneyline", "A vs B", "0.5", "0.52")]}, EV)
    assert got["spread"] is None and got["total"] is None


# ------------------------------------------------------- game state copy
def test_game_state_reaches_registered_markets_and_leaves_with_the_game():
    reg = {EV: (SPREAD, TOTAL)}
    st = M.spread_secondary_states({ML: Q4, "aec-atp-a-b-2026-11-25": {}},
                                   reg)
    assert st[SPREAD] is Q4 and st[TOTAL] is Q4
    # the game is no longer listed: its moneyline has no state, nor do they
    gone = M.spread_secondary_states({SPREAD: Q4, TOTAL: Q4}, reg)
    assert SPREAD not in gone and TOTAL not in gone
    # nothing registered: nothing changes
    assert M.spread_secondary_states({ML: Q4}, {}) == {ML: Q4}


def test_the_venue_object_registers_the_state_at_every_rebuild():
    """The live list is re-read outside selection too (slots full, kickoff
    re-check); each read rebuilds the states, so the copy happens there."""
    from src import pmus_spring_gateway_candidates as gw
    v = object.__new__(M.Venue)
    v.gw = gw
    v.states = {}
    v.secondary_by_event = {EV: (SPREAD,)}
    state = {"v": Q1}
    v.gget = lambda path: {"events": [{
        "slug": EV, "live": True, "eventState": state["v"],
        "markets": [{"slug": ML, "bestBidQuote": "0.5",
                     "bestAskQuote": "0.52"}]}]}
    assert [t["slug"] for t in v._live_sports()] == [ML]   # list unchanged
    assert v.states[SPREAD] == Q1 and M.late_for(SPREAD, v.states[SPREAD]) \
        is None
    state["v"] = Q4
    v._live_sports()
    assert M.late_for(SPREAD, v.states.get(SPREAD)) == "Q4"
    assert M.late_for(ML, v.states.get(ML)) == "Q4"


# ---------------------------------------------------------------- engine
@pytest.fixture
def eng(tmp_path, monkeypatch):
    """The venue-native profile: RN1 path, no international guard, no
    fair-value test (the conftest default)."""
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("200"))
    monkeypatch.setattr(M, "SLATE", "live")
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    monkeypatch.delenv("PMUS_RN1_PIN", raising=False)
    for mod, name, val in (
            (R, "QTY", D("1")), (R, "PAIR_CAP", D("0.97")),
            (R, "PER_MARKET_USD", D("8")), (R, "RESIDUAL_CAP_USD", D("3")),
            (R, "TOTAL_SPEND_USD", D("16")),
            (R, "TOTAL_UNMATCHED_USD", D("6")),
            (M, "RN1_PREGAME_QTY", None), (M, "RN1_LIVE_QTY", None),
            (M, "MIN_TOUCH_QTY", D("0")),
            (M, "SECONDARY_LEAGUES", frozenset({"nba", "nhl", "nfl"})),
            (M, "SECONDARY_TTL_S", 120.0),
            (M, "SECONDARY_NEW_PER_ROUND", 2), (M, "EVENT_CAP_USD", None),
            (RW, "POOL_SCOPE", "market"), (RW, "POOL_REQUIRE_CENSUS", False),
            (RW, "POOL_CENSUS_PER_ROUND", 1)):
        monkeypatch.setattr(mod, name, val)
    v = FakeVenue()
    v.states = {ML: Q1}
    v.bid, v.ask = D("0.48"), D("0.50")
    v.targets = [
        {"slug": ML, "league": "nba", "why": "v1_live_event_market",
         "event_state": Q1,
         "market_meta": {"bestBidQuote": "0.60", "bestAskQuote": "0.62"}},
        {"slug": "aec-atp-a-b-2026-11-25", "league": "atp",
         "why": "v1_live_event_market", "event_state": {},
         "market_meta": {"bestBidQuote": "0.10", "bestAskQuote": "0.12"}}]
    v.paths = []
    v.events = {EV: event()}

    def gget(path):
        v.paths.append(path)
        ek = path.rsplit("/", 1)[1]
        if ek not in v.events:
            raise RuntimeError("404")
        return v.events[ek]
    v.gget = gget
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e.since = None
    return e, v


def logged(capsys, name):
    pre = "PMUS_TWO_SIDED "
    rows = [json.loads(l[len(pre):])
            for l in capsys.readouterr().out.splitlines()
            if l.startswith(pre)]
    return [r for r in rows if r.get("kind") == name]


def sent(v):
    return [(o["slug"], o["side"], str(o["px"]), str(o["qty"]))
            for o in v.orders.values()]


def test_everything_is_off_by_default():
    assert M.SECONDARY_MARKETS is False
    assert M.EVENT_CAP_USD is None
    assert RW.POOL_SCOPE == "market" and RW.POOL_REQUIRE_CENSUS is False
    assert sorted(M.SECONDARY_LEAGUES) == ["nba", "nfl", "nhl"]
    assert M.SECONDARY_TTL_S == 120 and M.SECONDARY_NEW_PER_ROUND == 2
    cfg = M.safeguards_cfg()                 # the boot log says so too
    assert cfg["secondary_markets"]["on"] is False
    assert cfg["event_cap_usd"] is None
    assert cfg["reward_pool"] == {"scope": "market", "require_census": False}


def test_the_defaults_come_from_a_clean_interpreter():
    """Read the settings as a fresh process would, with none of them set."""
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PMUS_TWO_SIDED_SECONDARY", "PMUS_REWARD_POOL",
                                "PMUS_RN1_EVENT_CAP"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    out = subprocess.run(
        [sys.executable, "-c",
         "from src import pmus_sports_two_sided as M, pmus_sports_rewards "
         "as R; print(M.SECONDARY_MARKETS, M.EVENT_CAP_USD, R.POOL_SCOPE, "
         "R.POOL_REQUIRE_CENSUS, R.POOL_CENSUS_PER_ROUND)"],
        env=env, capture_output=True, text=True, timeout=120,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert out.stdout.split() == ["False", "None", "market", "False", "1"], \
        out.stderr[-400:]


def test_off_the_candidates_are_the_listed_ones_and_no_event_is_read(eng):
    e, v = eng
    assert e.slate_targets() == v.live_sports()
    e.select(v.account())
    assert not [p for p in v.paths if p.startswith("/v1/events/")]
    assert e.active() == [ML]
    assert SPREAD not in v.states and e._sec_reg == {}


def test_on_a_listed_nba_game_gains_its_main_line_spread_and_total(
        eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    got = e.slate_targets()
    assert v.paths == ["/v1/events/slug/" + EV]      # the tennis game: none
    assert [t["slug"] for t in got] == [ML, "aec-atp-a-b-2026-11-25",
                                        SPREAD, TOTAL]
    sp = got[2]
    # the same fields the game's listed market carries
    assert sp["league"] == "nba" and sp["event_state"] is Q1
    assert sp["why"] == "secondary_main_line_spread"
    assert sp["market_meta"] == {
        "bestBidQuote": {"value": "0.52", "currency": "USD"},
        "bestAskQuote": {"value": "0.54", "currency": "USD"}}
    # the game's state is registered under the new slugs
    assert v.states[SPREAD] is Q1 and v.states[TOTAL] is Q1
    assert v.secondary_by_event == {EV: (SPREAD, TOTAL)}
    row = logged(capsys, "SECONDARY_MARKETS")
    assert len(row) == 1 and row[0]["event"] == EV
    assert row[0]["spread"]["slug"] == SPREAD
    assert row[0]["total"]["slug"] == TOTAL
    # cached: no second read, no second log line
    e.slate_targets()
    assert len(v.paths) == 1 and not logged(capsys, "SECONDARY_MARKETS")


def test_a_pregame_game_passes_on_its_start_time(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    monkeypatch.setattr(M, "SLATE", "pregame")
    start = time.time() + 3600
    v.states = {}
    v.pregame_sports = lambda: [{
        "slug": ML, "game_start": start,
        "market_meta": {"bestBidQuote": "0.60", "bestAskQuote": "0.62"}}]
    got = {t["slug"]: t for t in e.slate_targets()}
    assert got[SPREAD]["game_start"] == start == got[TOTAL]["game_start"]
    assert got[SPREAD]["league"] == "nba"
    # not listed live: no state, exactly like the moneyline (kickoff hold)
    assert SPREAD not in v.states and ML not in v.states
    e.select(v.account())
    assert e.b.m(e.active()[0])["game_start"] == start


def test_the_answer_is_refreshed_after_its_lifetime_and_logged_on_change(
        eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    clock = {"t": 1000.0}
    monkeypatch.setattr(M.time, "monotonic", lambda: clock["t"])
    e.slate_targets()
    clock["t"] += 119
    e.slate_targets()
    assert len(v.paths) == 1
    # the game moves: another line is now nearest a half
    for m in v.events[EV]["event"]["markets"]:
        if m["slug"] == SPREAD:
            m["bestBidQuote"]["value"], m["bestAskQuote"]["value"] = \
                "0.70", "0.72"
        if m["slug"] == "asc-" + EV + "-neg-5pt5":
            m["bestBidQuote"]["value"], m["bestAskQuote"]["value"] = \
                "0.51", "0.53"
    clock["t"] += 2
    logged(capsys, "x")
    got = [t["slug"] for t in e.slate_targets()]
    assert len(v.paths) == 2
    assert "asc-" + EV + "-neg-5pt5" in got and SPREAD not in got
    assert logged(capsys, "SECONDARY_MARKETS")[0]["spread"]["slug"] == \
        "asc-" + EV + "-neg-5pt5"


def test_at_most_n_new_events_are_read_per_round(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    eks = ["nba-a%d-b%d-2026-11-25" % (i, i) for i in range(5)]
    v.targets = [{"slug": "aec-" + ek, "league": "nba", "event_state": Q1,
                  "market_meta": {"bestBidQuote": "0.5",
                                  "bestAskQuote": "0.52"}} for ek in eks]
    v.events = {ek: event(ek) for ek in eks}
    v.states = {"aec-" + ek: Q1 for ek in eks}
    n1 = len(e.slate_targets())
    assert len(v.paths) == 2 and n1 == 5 + 4
    e.slate_targets()
    assert len(v.paths) == 4
    assert len(e.slate_targets()) == 5 + 10 and len(v.paths) == 5
    e.slate_targets()
    assert len(v.paths) == 5


def test_a_failed_read_adds_nothing_and_is_not_retried_every_round(
        eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    v.events = {}
    assert e.slate_targets() == v.live_sports()
    assert logged(capsys, "secondary_discovery_error")[0]["event"] == EV
    e.slate_targets()
    assert len(v.paths) == 1


def test_a_stale_answer_is_dropped_when_the_venue_stops_answering(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    clock = {"t": 1000.0}
    monkeypatch.setattr(M.time, "monotonic", lambda: clock["t"])
    assert len(e.slate_targets()) == 4
    v.events = {}
    clock["t"] += 121
    assert len(e.slate_targets()) == 4            # last answer still used
    clock["t"] += 3 * 120
    assert e.slate_targets() == v.live_sports()   # too old: none
    assert SPREAD not in v.states


def test_an_answer_for_another_event_is_refused(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    v.events[EV] = event("nba-lal-bos-2026-11-26")
    assert e.slate_targets() == v.live_sports()


def test_only_listed_leagues_are_read_and_cricket_never(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    monkeypatch.setattr(M, "SECONDARY_LEAGUES",
                        frozenset({"nba", "ipl", "atp"}))
    v.targets.append({"slug": "aec-ipl-a-b-2026-11-25", "league": "ipl",
                      "event_state": {}, "market_meta": {}})
    v.events["atp-a-b-2026-11-25"] = event("atp-a-b-2026-11-25")
    v.events["ipl-a-b-2026-11-25"] = event("ipl-a-b-2026-11-25")
    e.slate_targets()
    assert sorted(v.paths) == ["/v1/events/slug/atp-a-b-2026-11-25",
                               "/v1/events/slug/" + EV]


def test_esports_stays_refused_for_a_secondary_market(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    monkeypatch.setattr(M, "SECONDARY_LEAGUES", frozenset({"cs2"}))
    ek = "cs2-a-b-2026-11-25"
    v.targets = [{"slug": "aec-" + ek, "league": "cs2", "event_state": {},
                  "market_meta": {"bestBidQuote": "0.5",
                                  "bestAskQuote": "0.52"}}]
    v.states = {"aec-" + ek: {}}
    v.events = {ek: event(ek)}
    assert len(e.slate_targets()) == 3
    e.select(v.account())
    assert e.active() == []                     # esports_format_unknown


# ------------------------------------------------- late rule and one game
def test_a_fourth_quarter_blocks_the_spread_exactly_as_the_moneyline(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    v.states = {ML: Q4}
    v.targets[0]["event_state"] = Q4
    got = [t["slug"] for t in e.slate_targets()]
    assert SPREAD in got and TOTAL in got
    for slug in (ML, SPREAD, TOTAL):
        assert M.late_for(slug, v.states.get(slug)) == "Q4"
    e.select(v.account())
    assert e.active() == []
    # the same game in the first quarter: one of its markets is selected
    v.states = {ML: Q1}
    v.targets[0]["event_state"] = Q1
    e.select(v.account())
    assert len(e.active()) == 1


def test_without_the_registered_state_the_spread_would_not_be_blocked():
    """Why the registration matters: a slug the list never carried has no
    state, and no state means no late rule."""
    assert M.late_for(SPREAD, None) is None
    assert M.late_for(SPREAD, Q4) == "Q4"


@pytest.mark.parametrize("slug", [ML, SPREAD, TOTAL])
def test_a_held_market_stops_opening_when_its_game_turns_late(
        eng, monkeypatch, slug):
    """Quoting parity: the same steps on the moneyline, the spread and the
    total of one game rest the same orders early and none late."""
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    e.slate_targets()
    e.b.m(slug)
    e.step_market(slug)
    early = sorted((o["side"], str(o["px"])) for o in v.orders.values()
                   if o["open"])
    assert early == [("NO", "0.49"), ("YES", "0.47")]
    v.states[ML] = Q4
    e.slate_targets()                       # the round that re-reads states
    e.step_market(slug)
    assert [o for o in v.orders.values() if o["open"]] == []


def test_a_spread_already_in_the_book_keeps_its_state_after_a_restart(
        eng, tmp_path):
    """Setting off, or the line no longer the main line: a market the bot
    holds is registered from the book, so its late rule never lapses."""
    e, v = eng
    old = "asc-" + EV + "-neg-9pt5"
    e.b.m(old)
    e.b.save()
    v2 = FakeVenue()
    e2 = M.Engine(v2, M.Book(tmp_path / "s.json"))
    assert e2._sec_reg == {EV: (old,)}
    assert v2.secondary_by_event is e2._sec_reg
    assert M.spread_secondary_states({ML: Q4}, e2._sec_reg)[old] is Q4


def test_a_held_line_stays_registered_when_the_main_line_moves(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    old = "asc-" + EV + "-neg-9pt5"
    e.b.m(old)
    e.slate_targets()
    assert v.secondary_by_event[EV] == tuple(sorted((old, SPREAD, TOTAL)))
    assert v.states[old] is Q1


def test_one_market_per_game_still_holds(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    monkeypatch.setattr(M, "MAX_MARKETS", 8)
    monkeypatch.setattr(M, "MAX_TRACKED", 12)
    e.select(v.account())
    # tightest spread, nearest a half first: the total takes the game
    assert e.active() == [TOTAL]
    e.select(v.account())
    e.select(v.account())
    assert e.active() == [TOTAL]
    assert not e._try_select(SPREAD, v.account(), None)
    assert not e._try_select(ML, v.account(), None)
    assert M.safeguards_cfg()["one_market_per_game"] is True


# ------------------------------------------- the price gates are not bypassed
class NoPriceGuard:
    """An international-price guard that, as in production, can name no
    price for a spread or a total."""
    require = True
    max_age = 20.0

    def __init__(self):
        self.asked = []

    def prefetch(self, slugs):
        self.asked += list(slugs)

    def token(self, slug):
        return "TOK" if str(slug).startswith("aec-") else None

    def track(self, slugs):
        pass

    def status(self):
        return {}


def test_the_international_feed_cannot_name_a_price_for_these_markets():
    def boom(_path):
        raise AssertionError("no lookup is made for a spread or a total")
    for slug in (SPREAD, TOTAL):
        assert XV.resolve_market(boom, slug, {"outcomes": '["A", "B"]',
                                              "title": "t"}) is None


def test_with_the_guard_required_a_secondary_market_is_refused(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    e.guard = NoPriceGuard()
    v.targets = v.targets[:1]
    v.targets[0]["market_meta"] = {"bestBidQuote": "0.95",
                                   "bestAskQuote": "0.97"}   # out of band
    e.select(v.account())
    assert SPREAD in e.guard.asked and TOTAL in e.guard.asked
    assert e.active() == []
    e.guard = None                     # venue-native profile: it may trade
    e.select(v.account())
    assert e.active() == [TOTAL]


@pytest.mark.rn1_edge_gate
def test_with_the_fair_value_test_on_a_secondary_market_gets_no_bid(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    assert R.EDGE_GATE is True
    e.slate_targets()
    for slug in (SPREAD, TOTAL):
        e.b.m(slug)
        e.step_market(slug)
    assert sent(v) == []


# ------------------------------------------------------ per-game exposure
def _hold(e, slug, side, qty, cost):
    mk = e.b.m(slug)
    mk["pos"][side], mk["cost"][side] = D(qty), D(cost)
    return mk


def test_event_exposure_sums_every_market_of_one_game(eng):
    e, v = eng
    assert e.event_exposure(ML) == 0
    _hold(e, ML, "YES", "2", "0.90")                    # unpaired 0.90
    mk = _hold(e, SPREAD, "NO", "1", "0.45")            # unpaired 0.45
    mk["orders"]["o1"] = {"side": "NO", "px": "0.40", "qty": "2",
                          "filled": "1", "open": True, "stage": "rn1_extend"}
    _hold(e, "aec-nba-xxx-yyy-2026-11-25", "YES", "5", "2.50")  # other game
    # 0.90 + (0.45 + one more NO at 0.40)
    assert e.event_exposure(ML) == D("1.75")
    assert e.event_exposure(TOTAL) == D("1.75")         # same game, any slug
    # an order in flight counts, also in a market with no book entry yet
    e.reserve_order(TOTAL, "YES", D("0.30"), D("1"), "rn1_open")
    assert e.event_exposure(ML) == D("2.05")
    # a hypothetical bid on top
    assert e.event_exposure(ML, extra=(ML, "YES", D("0.50"), D("1"))) == \
        D("2.55")


def test_a_bid_on_each_side_of_one_market_is_not_counted_twice(eng):
    e, v = eng
    mk = e.b.m(ML)
    mk["orders"]["a"] = {"side": "YES", "px": "0.47", "qty": "1",
                         "filled": "0", "open": True, "stage": "rn1_open"}
    mk["orders"]["b"] = {"side": "NO", "px": "0.49", "qty": "1",
                         "filled": "0", "open": True, "stage": "rn1_open"}
    assert e.event_exposure(ML) == D("0.49")     # worst single fill


def test_a_settled_market_no_longer_counts(eng):
    e, v = eng
    _hold(e, ML, "YES", "2", "0.90")["settle_px"] = "1"
    assert e.event_exposure(ML) == 0


def test_unreadable_figures_are_infinite_exposure(eng):
    e, v = eng
    _hold(e, ML, "YES", "2", "0.90")["cost"]["YES"] = "junk"
    assert e.event_exposure(ML) == D("Infinity")


def test_without_a_limit_nothing_is_refused_for_the_game(eng, capsys):
    e, v = eng
    _hold(e, SPREAD, "YES", "4", "2.00")
    e.place(ML, "YES", D("0.47"), D("1"), "rn1_open")
    assert sent(v) == [(ML, "YES", "0.47", "1")]
    assert not logged(capsys, "EVENT_CAP_REFUSED")


def test_the_limit_refuses_an_opening_that_would_exceed_it(
        eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "EVENT_CAP_USD", D("2.40"))
    _hold(e, SPREAD, "YES", "4", "2.00")
    e.place(ML, "YES", D("0.47"), D("1"), "rn1_open")        # 2.47 > 2.40
    e.place(TOTAL, "NO", D("0.41"), D("1"), "rn1_extend")    # 2.41 > 2.40
    assert sent(v) == []
    rows = logged(capsys, "place_refused_final_gate")
    assert [r["reason"] for r in rows] == ["event_exposure:2.47>2.40",
                                           "event_exposure:2.41>2.40"]
    # under the limit, and another game, are untouched
    e.place(ML, "YES", D("0.40"), D("1"), "rn1_open")
    e.place("aec-nba-xxx-yyy-2026-11-25", "YES", D("0.47"), D("1"),
            "rn1_open")
    assert sent(v) == [(ML, "YES", "0.40", "1"),
                       ("aec-nba-xxx-yyy-2026-11-25", "YES", "0.47", "1")]


def test_the_refusal_has_its_own_log_line(eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "EVENT_CAP_USD", D("2.40"))
    _hold(e, SPREAD, "YES", "4", "2.00")
    e.place(ML, "YES", D("0.47"), D("1"), "rn1_open")
    e.place(ML, "YES", D("0.47"), D("1"), "rn1_open")
    rows = logged(capsys, "EVENT_CAP_REFUSED")
    assert len(rows) == 1                      # once per order, not per try
    assert rows[0]["event"] == EV and rows[0]["cap_usd"] == "2.40"
    assert rows[0]["event_exposure_now_usd"] == "2.00"
    assert rows[0]["event_exposure_with_order_usd"] == "2.47"


def test_the_limit_never_refuses_a_completion(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "EVENT_CAP_USD", D("0.10"))
    _hold(e, ML, "YES", "1", "0.47")
    e.place(ML, "NO", D("0.49"), D("1"), "two_sided_complete")
    assert sent(v) == [(ML, "NO", "0.49", "1")]
    e.place(ML, "YES", D("0.47"), D("1"), "rn1_extend")
    assert len(sent(v)) == 1


def test_the_limit_counts_what_is_resting_and_in_flight(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "EVENT_CAP_USD", D("1.00"))
    e.place(ML, "YES", D("0.47"), D("1"), "rn1_open")        # 0.47
    e.reserve_order(SPREAD, "YES", D("0.30"), D("1"), "rn1_open")   # 0.77
    e.place(TOTAL, "YES", D("0.25"), D("1"), "rn1_open")     # 1.02: refused
    e.place(TOTAL, "YES", D("0.23"), D("1"), "rn1_open")     # 1.00: allowed
    assert sent(v) == [(ML, "YES", "0.47", "1"), (TOTAL, "YES", "0.23", "1")]


def test_the_limit_applies_on_the_older_path_too(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(R, "ON", False)
    monkeypatch.setattr(M, "EVENT_CAP_USD", D("0.40"))
    e.place(ML, "YES", D("0.47"), D("1"), "two_sided_open")
    assert sent(v) == []
    e.place(ML, "YES", D("0.47"), D("1"), "two_sided_exit")
    assert len(sent(v)) == 1
    monkeypatch.setattr(M, "EVENT_CAP_USD", None)
    e.place(ML, "YES", D("0.47"), D("1"), "two_sided_open")
    assert len(sent(v)) == 2


@pytest.mark.parametrize("raw, want", [
    (None, None), ("", None), ("  ", None), ("25", D("25")),
    ("0", D("0")), ("junk", D("0")), ("-5", D("0")), ("NaN", D("0")),
])
def test_an_unreadable_limit_is_a_limit_of_zero_not_no_limit(raw, want):
    assert M._event_cap(raw) == want


def test_the_heartbeat_reports_exposure_per_game_with_or_without_a_limit(
        eng, capsys):
    e, v = eng
    _hold(e, ML, "YES", "2", "0.90")
    _hold(e, SPREAD, "NO", "1", "0.45")
    _hold(e, "aec-nba-xxx-yyy-2026-11-25", "YES", "5", "2.50")
    e.b.m("aec-atp-a-b-2026-11-25")                      # nothing at risk
    e.report()
    hb = logged(capsys, "heartbeat")[0]["event_exposure"]
    assert hb["cap_usd"] is None and hb["events_with_exposure"] == 2
    assert hb["top"] == [
        {"event": "nba-xxx-yyy-2026-11-25", "usd": "2.50", "markets": 1},
        {"event": EV, "usd": "1.35", "markets": 2}]


# ----------------------------------------------------------- reward pools
BOOK = {"bids": [{"px": "0.50", "qty": "5"}, {"px": "0.49", "qty": "5"}],
        "offers": [{"px": "0.52", "qty": "5"}, {"px": "0.53", "qty": "5"}]}
PER = {"programId": "nba_regular_spreads_live_20261118", "pool": 450,
       "period": "live", "df": 0.5, "targetSize": 10, "maxSpread": None,
       "period_s": 10800.0, "pool_rate_usd_s": 450 / 10800.0}


def _prog(pid="nba_regular_spreads_live_20261118", period="live",
          start="2026-11-19T02:00:00Z", status="active",
          game="2026-11-25 01:00:00+00"):
    return {"event": "x", "eventStartTime": game,
            "periods": [{"programId": pid, "pool": 450, "period": period,
                         "df": 0.5, "target": 10, "maxSpread": None,
                         "status": status, "start": start}]}


NOW = 1795575600.0          # 2026-11-25 03:00Z: after the start above


def test_by_default_the_estimate_is_the_figure_it_always_was():
    est = RW.quote_estimate(BOOK, PER, 0.49, 0.47, 1, 0.01)
    # our 1 at 0.49 behind 5 at 0.50: 0.5 / (5 + 0.5*6); same on the ask
    share = 0.5 / 8.0
    assert est["share"] == pytest.approx(share)
    assert est["rate_usd_s"] == PER["pool_rate_usd_s"] * est["bid_share"] \
        * 0.5 + PER["pool_rate_usd_s"] * est["ask_share"] * 0.5
    assert est["est_usd_per_hour"] == round(
        PER["pool_rate_usd_s"] * share * 3600, 6)
    assert est["pool_scope"] == "market" and est["pool_divisor"] == 1
    assert est["est_usd_per_hour_per_market_reading"] == \
        est["est_usd_per_hour"]


@pytest.mark.parametrize("scope, div", [("event", 34), ("programme", 367)])
def test_a_shared_pool_divides_every_figure_and_says_so(scope, div):
    base = RW.quote_estimate(BOOK, PER, 0.49, 0.47, 1, 0.01)
    est = RW.quote_estimate(BOOK, RW.scoped_period(PER, scope, div, True),
                            0.49, 0.47, 1, 0.01)
    assert est["pool_scope"] == scope and est["pool_divisor"] == div
    assert est["share"] == base["share"]              # the book is the book
    assert est["rate_usd_s"] == pytest.approx(base["rate_usd_s"] / div)
    assert est["est_usd_per_hour"] == pytest.approx(
        base["est_usd_per_hour"] / div, abs=1e-6)
    assert est["est_usd_per_day"] == pytest.approx(
        base["est_usd_per_day"] / div, abs=1e-6)
    assert est["est_usd_per_hour_per_market_reading"] == \
        base["est_usd_per_hour"]
    assert "EVENLY" in est["pool_reading"]            # an approximation
    assert est["pool_divisor_complete"] is True
    # the older log-only estimate carries the same reading
    old = RW.estimate(BOOK, RW.scoped_period(PER, scope, div, False), 1,
                      0.01)
    assert old["pool_scope"] == scope and old["pool_divisor"] == div
    assert old["est_usd_per_hour"] == pytest.approx(
        old["est_usd_per_hour_per_market_reading"] / div, abs=1e-6)
    assert old["pool_divisor_complete"] is False


def test_an_unknown_count_is_no_estimate_not_the_whole_pool():
    est = RW.quote_estimate(BOOK, RW.scoped_period(PER, "event", None, False),
                            0.49, 0.47, 1, 0.01)
    assert est["rate_usd_s"] == 0 and est["est_usd_per_hour"] == 0
    assert est["pool_divisor"] is None
    assert est["est_usd_per_hour_per_market_reading"] > 0
    assert RW.pool_rate(RW.scoped_period(PER, "event", None, False)) == 0
    assert RW.pool_rate(PER) == PER["pool_rate_usd_s"]
    assert RW.pool_rate(RW.scoped_period(PER, "event", 4, True)) == \
        pytest.approx(PER["pool_rate_usd_s"] / 4)


@pytest.mark.parametrize("raw, want", [
    (None, "market"), ("", "market"), ("market", "market"),
    ("EVENT", "event"), ("game", "event"), ("programme", "programme"),
    ("program", "programme"),
    ("per-market", "programme"), ("evnt", "programme"),   # typo: smallest
])
def test_the_scope_setting_never_reads_a_typo_as_the_largest_figure(
        raw, want):
    assert RW.pool_scope_from(raw) == want


def test_divisors_count_the_programmes_markets_that_are_paying_now():
    other_ev = "nba-mia-nyk-2026-11-25"
    recs = {
        SPREAD: _prog(),
        "asc-" + EV + "-neg-5pt5": _prog(),
        "asc-" + EV + "-pos-1pt5": _prog(),
        # same game, another programme (period lines)
        "asc-" + EV + "-q1-neg-1pt5": _prog("nba_period_spreads_live"),
        # same programme, another game
        "asc-" + other_ev + "-neg-2pt5": _prog(),
        "asc-" + other_ev + "-neg-4pt5": _prog(),
        # same programme but not paying: closed, not started, game not live
        "asc-" + other_ev + "-neg-6pt5": _prog(status="closed"),
        "asc-" + other_ev + "-neg-8pt5": _prog(start="2026-12-01T00:00:00Z"),
        "asc-nba-not-live-2026-11-25-neg-1pt5": _prog(
            game="2026-11-25 23:00:00+00"),
        "tsc-" + EV + "-221pt5": None,
    }
    counts = RW.pool_counts(recs, NOW, lambda s: "not-live" not in s,
                            M.event_key)
    pid = PER["programId"]
    assert RW.pool_divisor(counts, SPREAD, pid, "market", M.event_key) == 1
    assert RW.pool_divisor(counts, SPREAD, pid, "event", M.event_key) == 3
    assert RW.pool_divisor(counts, SPREAD, pid, "programme",
                           M.event_key) == 5
    # a market the bot holds no row for still counts itself
    assert RW.pool_divisor(counts, "asc-nba-zz-yy-2026-11-25-neg-1pt5", pid,
                           "event", M.event_key) == 1
    assert RW.pool_divisor(counts, SPREAD, "unknown", "programme",
                           M.event_key) == 1


def test_default_scope_leaves_the_paying_period_untouched(
        eng, monkeypatch):
    e, v = eng
    per = dict(PER)
    assert e.pool_scoped(SPREAD, per) is per
    e._prog[SPREAD] = (time.monotonic(), _prog())
    monkeypatch.setattr(M.time, "time", lambda: NOW)
    got = e.rn1_reward(SPREAD)
    assert got is not None and "pool_scope" not in got \
        and "pool_divisor" not in got


def _census_setup(e, v, monkeypatch, scope):
    monkeypatch.setattr(M, "SECONDARY_MARKETS", True)
    monkeypatch.setattr(RW, "POOL_SCOPE", scope)
    monkeypatch.setattr(M.time, "time", lambda: NOW)
    asked = []

    def get(path):
        asked.append(path)
        slugs = [p.split("=", 1)[1] for p in path.split("?", 1)[1].split("&")]
        rows = []
        for s in slugs:
            if "-q1-" in s or "-fh-" in s:
                pid = "nba_regular_period_spreads_live_20261118"
            else:
                pid = "nba_regular_spreads_live_20261118"
            rows.append({"marketSlug": s,
                         "eventStartTime": "2026-11-25 01:00:00+00",
                         "timePeriods": [{
                             "programId": pid,
                             "programType": "liquidityProgram",
                             "start": "2026-11-19T02:00:00Z",
                             "rewardPool": 450, "status": "active",
                             "discountFactor": 0.5, "targetSize": 10,
                             "period": "live"}]})
        return {"programs": rows}
    monkeypatch.setattr(e, "_reward_get", lambda: get)
    e.slate_targets()                 # holds the game's full market list
    return asked


def test_event_scope_counts_the_game_after_looking_up_every_line(
        eng, monkeypatch, capsys):
    e, v = eng
    asked = _census_setup(e, v, monkeypatch, "event")
    rw = e.reward_programs([SPREAD])
    # one lookup for the candidate, one for the rest of the game's asc-
    assert len(asked) == 2
    assert rw[SPREAD]["pool_scope"] == "event"
    assert rw[SPREAD]["pool_divisor"] == 3          # three full-game lines
    assert rw[SPREAD]["pool_divisor_complete"] is True
    row = logged(capsys, "reward_pool_census")[0]
    assert row["event"] == EV and row["product"] == "asc" and \
        row["markets"] == 5
    # cached: nothing more is asked
    e.reward_programs([SPREAD])
    assert len(asked) == 2
    # the measurement line uses the same reading (cached programme read)
    assert e.rn1_reward(SPREAD)["pool_divisor"] == 3


def test_programme_scope_is_a_lower_bound_and_says_so(eng, monkeypatch):
    e, v = eng
    _census_setup(e, v, monkeypatch, "programme")
    other = "asc-nba-mia-nyk-2026-11-25-neg-2pt5"
    rw = e.reward_programs([SPREAD, other])
    assert rw[SPREAD]["pool_divisor"] == 4          # 3 in the game + 1 held
    assert rw[SPREAD]["pool_divisor_complete"] is False
    assert rw[other]["pool_divisor"] == 4


def test_without_a_count_the_figure_is_an_upper_bound_or_zero_if_required(
        eng, monkeypatch):
    e, v = eng
    _census_setup(e, v, monkeypatch, "event")
    lone = "asc-nba-mia-nyk-2026-11-25-neg-2pt5"    # no market list held
    rw = e.reward_programs([lone])
    assert rw[lone]["pool_divisor"] == 1
    assert rw[lone]["pool_divisor_complete"] is False
    monkeypatch.setattr(RW, "POOL_REQUIRE_CENSUS", True)
    rw = e.reward_programs([lone, SPREAD])
    assert rw[lone]["pool_divisor"] is None
    assert RW.quote_estimate(BOOK, rw[lone], 0.49, 0.47, 1,
                             0.01)["est_usd_per_hour"] == 0
    assert rw[SPREAD]["pool_divisor"] == 3          # counted: unaffected


def test_share_score_and_ranking_use_the_shared_pool_figure(
        eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(R, "DEPTH_TICKS", 1)
    monkeypatch.setattr(M, "rn1_max_clip", lambda: D("1"))
    monkeypatch.setattr(M, "RANK_SHARE_NEW_PER_ROUND", 10)
    monkeypatch.setattr(e, "_reward_get",
                        lambda: (lambda p: {"marketData": BOOK}))
    one = e._reward_share_score(SPREAD, PER)
    assert "pool_scope" not in one                  # default: as before
    shared = e._reward_share_score(
        SPREAD, RW.scoped_period(PER, "event", 34, True))
    assert shared["usd_per_hour"] == pytest.approx(
        one["usd_per_hour"] / 34, abs=1e-6)
    assert shared["per_dollar_hour"] == pytest.approx(
        one["per_dollar_hour"] / 34, abs=1e-6)
    assert shared["pool_scope"] == "event" and shared["pool_divisor"] == 34
    assert shared["est_usd_per_hour_per_market_reading"] == \
        one["usd_per_hour"]
    # ranking: a ladder line on a big pool no longer outranks a moneyline
    # with a pool of its own once the pool is shared 34 ways
    ml = "aec-nhl-e-f-2026-11-25"
    small = dict(PER, programId="nhl_ml", pool_rate_usd_s=PER[
        "pool_rate_usd_s"] / 10)
    rows = [(D("0.02"), D("0.01"), SPREAD), (D("0.02"), D("0.01"), ml)]
    got = e.rank_by_reward_share(rows, {SPREAD: PER, ml: small})
    assert [r[2] for r in got] == [SPREAD, ml]
    e._share_cache.clear()
    got = e.rank_by_reward_share(rows, {
        SPREAD: RW.scoped_period(PER, "event", 34, True),
        ml: RW.scoped_period(small, "event", 1, True)})
    assert [r[2] for r in got] == [ml, SPREAD]
    row = logged(capsys, "RN1_SHARE_RANK")[-1]
    assert row["first"][1]["pool_divisor"] == 34


def test_the_incentive_projection_uses_the_shared_pool_figure(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_REWARD_CREDIT_TICKS", 1)
    monkeypatch.setattr(M, "RN1_REWARD_PROJECT_H", 10.0)
    monkeypatch.setattr(M, "RN1_REWARD_MIN_DAY_USD", 5.0)
    monkeypatch.setattr(e, "_reward_get",
                        lambda: (lambda p: {"marketData": BOOK}))
    mk = e.b.m(SPREAD)
    mk["orders"] = {
        "a": {"side": "YES", "px": "0.49", "qty": "1", "filled": "0",
              "open": True, "stage": "rn1_open"},
        "b": {"side": "NO", "px": "0.47", "qty": "1", "filled": "0",
              "open": True, "stage": "rn1_open"}}
    per = {"v": PER}
    monkeypatch.setattr(e, "rn1_reward", lambda s: per["v"])
    one = e.rn1_incentive(SPREAD, mk, D("0.01"))
    assert one["why"] == "qualifies" and "pool_scope" not in one
    mk.pop("rn1_reward_est")
    per["v"] = RW.scoped_period(PER, "event", 34, True)
    shared = e.rn1_incentive(SPREAD, mk, D("0.01"))
    assert shared["projected_usd"] == pytest.approx(
        one["projected_usd"] / 34, abs=1e-3)
    assert shared["why"] == "below_minimum_payout" and shared["ticks"] == 0
    assert shared["pool_scope"] == "event" and shared["pool_divisor"] == 34
    assert mk["rn1_reward_est"]["pool_divisor"] == 34


# ------------------------------------------- which reading fits what is paid
def _measured():
    # two lines of one game on one programme, 3 h rested, pool $450 / 3 h
    rate = 450 / 10800.0
    return {
        "asc-g1-neg-3pt5": {"share_seconds": 0.10 * 10800,
                            "pool_rate_usd_s": rate, "event_divisor": 30,
                            "programme_divisor": 300},
        "asc-g1-neg-5pt5": {"share_seconds": 0.20 * 10800,
                            "pool_rate_usd_s": rate, "event_divisor": 30,
                            "programme_divisor": 300}}


@pytest.mark.parametrize("scope, div", [("market", 1), ("event", 30),
                                        ("programme", 300)])
def test_the_fit_names_the_reading_the_credits_match(scope, div):
    rows = [{"marketSlug": "asc-g1-neg-3pt5", "reward": 45.0 / div},
            {"marketSlug": "asc-g1-neg-5pt5", "amount": "%s" % (90.0 / div)},
            {"marketSlug": "aec-not-measured", "reward": 3.0},
            {"marketSlug": None, "reward": 9.0}, "junk"]
    got = RW.pool_scope_fit(rows, _measured(), min_payout_usd=0.0)
    assert got["best"] == scope
    r = got["readings"][scope]
    assert r["markets_compared"] == 2
    assert r["credited_over_predicted"] == pytest.approx(1.0)
    assert r["mean_abs_log_error"] == pytest.approx(0.0, abs=1e-6)
    assert got["readings"]["market"]["predicted_usd"] == pytest.approx(135)


def test_the_fit_does_not_guess_without_data_or_between_equal_readings():
    assert RW.pool_scope_fit([], _measured())["best"] is None
    m = {k: dict(v, event_divisor=1, programme_divisor=1)
         for k, v in _measured().items()}
    got = RW.pool_scope_fit(
        [{"marketSlug": "asc-g1-neg-3pt5", "reward": 45.0}], m)
    assert got["best"] is None and "not distinguishable" in got["why"]


def test_a_reading_that_predicts_pay_the_venue_did_not_make_is_penalised():
    # only one line was credited, at the per-event amount; the per-market
    # reading says the other line should have been paid $90 and was not
    rows = [{"marketSlug": "asc-g1-neg-3pt5", "reward": 1.5}]
    got = RW.pool_scope_fit(rows, _measured())
    assert got["readings"]["market"][
        "predicted_payable_but_not_credited"] == 1
    assert got["readings"]["programme"][
        "predicted_payable_but_not_credited"] == 0
    assert got["best"] == "event"
