"""Three-way soccer fair (src/pmus_sports_soccer_fair.py). Fixtures are
trimmed from the public venue event lal-bet-ray-2026-11-28 (28 Nov)."""
import copy
from decimal import Decimal as D

import pytest

from src import pmus_sports_sharp as SH
from src import pmus_sports_soccer_fair as SF

START = "2026-11-28T13:00:00Z"
NOW = SH._ts(START) - 3600
UPD = "2026-11-28T11:55:00Z"            # 5 min before NOW
TEXT = ("This market will settle to the winner at the end of 90 minutes plus "
        "stoppage time in the Real Betis Balompié vs Rayo Vallecano de "
        "Madrid La Liga match scheduled for November 28, 2026 at 8:00AM ET "
        "(as of the listing of the event's first instrument). If the match "
        "is tied following 90 minutes plus stoppage time, the market will "
        "settle to Tie. If the match is delayed, postponed, suspended or "
        "otherwise rescheduled to a date and time not within two days of the "
        "originally scheduled date and time, the market will settle to the "
        "last fair market price. Outcome sourced from La Liga.")
BET = {"name": "Real Betis Balompié", "abbreviation": "bet",
       "ordering": "home"}
RAY = {"name": "Rayo Vallecano de Madrid", "abbreviation": "ray",
       "ordering": "home"}


def _team_mk(abbr, team, q):
    return {"slug": "atc-lal-bet-ray-2026-11-28-" + abbr,
            "question": q, "description": TEXT, "gameStartTime": START,
            "sportsMarketType": "soccer_team_full_time_winner",
            "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME",
            "marketSides": [
                {"long": True, "description": "Yes", "team": dict(team)},
                {"long": False, "description": "No",
                 "team": dict(team, ordering="away")}]}


MK_BET = _team_mk("bet", BET, "Will Real Betis Balompie win against Rayo "
                  "Vallecano de Madrid in the La Liga match scheduled for "
                  "Nov 28, 2026?")
MK_RAY = _team_mk("ray", RAY, "Will Rayo Vallecano de Madrid win against "
                  "Real Betis Balompie in the La Liga match scheduled for "
                  "Nov 28, 2026?")
MK_DRAW = {"slug": "atc-lal-bet-ray-2026-11-28-draw",
           "question": "Will the La Liga match Real Betis Balompie vs Rayo "
                       "Vallecano de Madrid scheduled for Nov 28, 2026 end in "
                       "a draw?",
           "description": TEXT, "gameStartTime": START,
           "sportsMarketType": "soccer_team_full_time_winner",
           "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME",
           "marketSides": [{"long": True, "description": "Yes"},
                           {"long": False, "description": "No"}]}
EVENT_MKS = [MK_BET, MK_DRAW, MK_RAY]


def _book(key, prices, upd=UPD, names=("Real Betis", "Rayo Vallecano",
                                        "Draw")):
    return {"key": key, "last_update": upd,
            "markets": [{"key": "h2h", "last_update": upd,
                         "outcomes": [{"name": n, "price": p}
                                      for n, p in zip(names, prices)]}]}


def _event(*books, home="Real Betis", away="Rayo Vallecano", ct=START):
    return {"id": "ev1", "home_team": home, "away_team": away,
            "commence_time": ct, "bookmakers": list(books)}


PIN = _book("pinnacle", [2.0, 4.0, 4.0])      # 0.5/0.25/0.25 -> sum 1.0


# ------------------------------------------------------------ h2h3 / devig
def test_h2h3_devig_sums_to_one_and_keys():
    got = SF.h2h3(_book("x", [2.1, 3.4, 3.6]), now=NOW)
    assert got is not None
    probs, _ = got
    assert set(probs) == {"real betis", "rayo vallecano", "draw"}
    assert abs(sum(probs.values()) - 1) < D("1e-20")
    inv = [1 / D("2.1"), 1 / D("3.4"), 1 / D("3.6")]
    assert probs["real betis"] == inv[0] / sum(inv)


def test_h2h3_integer_prices_ok_and_bool_refused():
    assert SF.h2h3(_book("x", [2, 4, 4]), now=NOW)[0]["draw"] == D("0.25")
    assert SF.h2h3(_book("x", [True, 4, 4]), now=NOW) is None
    assert SF.h2h3(_book("x", [None, 4, 4]), now=NOW) is None


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", 1, "1.0", 0.5, -2,
                                 "x", ""])
def test_h2h3_refuses_unusable_odds(bad):
    assert SF.h2h3(_book("x", [bad, 4.0, 4.0]), now=NOW) is None


def test_h2h3_refuses_two_way_and_four_way():
    assert SF.h2h3(_book("x", [1.9, 1.9], names=("A", "B")), now=NOW) is None
    assert SF.h2h3(_book("x", [3, 3, 3, 3], names=("A", "B", "Draw", "C")),
                   now=NOW) is None


def test_h2h3_refuses_duplicates_missing_draw_empty_names():
    assert SF.h2h3(_book("x", [2, 4, 4], names=("A", "a", "Draw")),
                   now=NOW) is None
    assert SF.h2h3(_book("x", [2, 4, 4], names=("A", "B", "C")),
                   now=NOW) is None
    assert SF.h2h3(_book("x", [2, 4, 4], names=("A", "Draw", "DRAW")),
                   now=NOW) is None
    assert SF.h2h3(_book("x", [2, 4, 4], names=("", "B", "Draw")),
                   now=NOW) is None


def test_h2h3_refuses_stale_future_and_missing_stamp(monkeypatch):
    monkeypatch.setattr(SH, "MAX_ODDS_AGE_S", 900.0)
    old = "2026-11-28T11:00:00Z"                     # 1 h old
    assert SF.h2h3(_book("x", [2, 4, 4], upd=old), now=NOW) is None
    fut = "2026-11-28T12:10:00Z"
    assert SF.h2h3(_book("x", [2, 4, 4], upd=fut), now=NOW) is None
    b = _book("x", [2, 4, 4], upd=None)
    assert SF.h2h3(b, now=NOW) is None
    # integer epoch stamps are accepted
    b = _book("x", [2, 4, 4], upd=int(NOW) - 60)
    assert SF.h2h3(b, now=NOW) is not None


def test_h2h3_refuses_two_h2h_markets():
    b = _book("x", [2, 4, 4])
    b["markets"].append(copy.deepcopy(b["markets"][0]))
    assert SF.h2h3(b, now=NOW) is None


def test_two_way_h2h_unchanged_still_refuses_three_way():
    assert SH._h2h(_book("x", [2, 4, 4])) is None
    two = SH._h2h(_book("x", [2, 2], names=("A", "B")))
    assert two is not None and two[0] == {"a": D("0.5"), "b": D("0.5")}


# ----------------------------------------------------- fair3_from_event
def test_fair3_prefers_pinnacle():
    ev = _event(_book("other", [3.0, 2.0, 3.5]), PIN)
    f = SF.fair3_from_event(ev, now=NOW)
    assert f["source"] == "pinnacle"
    assert f["probs"]["real betis"] == D("0.5")


def test_fair3_median_without_pinnacle_and_stale_pinnacle_skipped():
    stale = _book("pinnacle", [2, 4, 4], upd="2026-11-28T09:00:00Z")
    ev = _event(stale, _book("a", [2, 4, 4]), _book("b", [2.2, 3.6, 3.8]),
                _book("c", [2.1, 3.8, 3.9]))
    f = SF.fair3_from_event(ev, now=NOW)
    assert f["source"] == "median_of_3"
    assert abs(sum(f["probs"].values()) - 1) < D("1e-20")


def test_fair3_refuses_book_names_not_matching_event_teams():
    ev = _event(_book("pinnacle", [2, 4, 4], names=("X", "Y", "Draw")))
    assert SF.fair3_from_event(ev, now=NOW) is None
    ev = _event(PIN, _book("a", [2, 4, 4], names=("X", "Y", "Draw")))
    # pinnacle good -> used; mixing only matters without pinnacle
    assert SF.fair3_from_event(ev, now=NOW)["source"] == "pinnacle"
    ev = _event(_book("a", [2, 4, 4]),
                _book("b", [2, 4, 4], names=("X", "Y", "Draw")))
    assert SF.fair3_from_event(ev, now=NOW) is None


def test_fair3_refuses_team_named_draw_or_same_teams():
    assert SF.fair3_from_event(_event(PIN, home="Draw"), now=NOW) is None
    assert SF.fair3_from_event(_event(PIN, away="Real Betis"),
                               now=NOW) is None


# ------------------------------------------------------- market spec
def test_spec_team_and_draw():
    s, why = SF.soccer_market_spec(MK_BET)
    assert why == "ok" and s["outcome"] == "team" and s["abbr"] == "bet"
    assert s["team"] == "Real Betis Balompié"
    assert s["sport_key"] == "soccer_spain_la_liga"
    assert s["event"] == "lal-bet-ray-2026-11-28"
    s, why = SF.soccer_market_spec(MK_DRAW)
    assert why == "ok" and s["outcome"] == "draw" and s["team"] is None


@pytest.mark.parametrize("mut,reason", [
    (lambda m: m.update(sportsMarketType="soccer_team_first_half_winner"),
     "unsupported_type:soccer_team_first_half_winner"),
    (lambda m: m.update(sportsMarketTypeV2="SPORTS_MARKET_TYPE_MONEYLINE"),
     "unsupported_type_v2:SPORTS_MARKET_TYPE_MONEYLINE"),
    (lambda m: m.update(slug="atc-lal-bet-ray-2026-11-28-sh-bet"),
     "slug_unparsed"),
    (lambda m: m.update(slug="atc-xyz-bet-ray-2026-11-28-bet"),
     "league_not_configured:xyz"),
    (lambda m: m.update(slug="atc-lal-bet-ray-2026-11-28-rma"),
     "suffix_not_in_event"),
    (lambda m: m.update(description="Settles on the winner of the match."),
     "settlement_not_regulation"),
    (lambda m: m.update(description=TEXT + " Extra time and penalty "
                        "shootouts are included."),
     "settlement_includes_extra_time"),
    (lambda m: m["marketSides"][0]["team"].update(abbreviation="ray"),
     "team_abbr_mismatch"),
    (lambda m: m["marketSides"][1]["team"].update(name="Sevilla"),
     "sides_name_different_teams"),
    (lambda m: m["marketSides"][0].pop("team"), "team_unreadable"),
    (lambda m: m["marketSides"][1].update(long=True), "side_unparsed"),
])
def test_spec_refusals(mut, reason):
    m = copy.deepcopy(MK_BET)
    mut(m)
    assert SF.soccer_market_spec(m) == (None, reason)


def test_spec_draw_refusals():
    m = copy.deepcopy(MK_DRAW)
    m["marketSides"][0]["team"] = dict(BET)
    assert SF.soccer_market_spec(m) == (None, "draw_market_has_team")
    m = copy.deepcopy(MK_DRAW)
    m["question"] = "Will Betis win?"
    assert SF.soccer_market_spec(m) == (None, "draw_question_mismatch")


def test_existing_moneyline_path_still_refuses_soccer():
    assert SH.is_moneyline(MK_BET) is False
    assert SH.fair_yes(MK_BET, [_event(PIN)], now=NOW) is None
    assert SH.market_teams(MK_BET) is None     # both sides name one team


# ------------------------------------------------------- market fair
def test_fair_team_draw_and_sum():
    evs = [_event(PIN)]
    out = {}
    for mk in EVENT_MKS:
        f, why = SF.soccer_market_fair(mk, EVENT_MKS, evs, now=NOW)
        assert why == "ok", (mk["slug"], why)
        out[mk["slug"].rsplit("-", 1)[1]] = f["fair_yes"]
        assert f["source"] == "pinnacle" and f["event_id"] == "ev1"
    assert out == {"bet": D("0.5"), "ray": D("0.25"), "draw": D("0.25")}
    assert sum(out.values()) == 1


def test_fair_away_home_swapped_in_feed():
    ev = _event(_book("pinnacle", [4.0, 2.0, 4.0],
                      names=("Rayo Vallecano", "Real Betis", "Draw")),
                home="Rayo Vallecano", away="Real Betis")
    f, why = SF.soccer_market_fair(MK_BET, EVENT_MKS, [ev], now=NOW)
    assert why == "ok" and f["fair_yes"] == D("0.5")


def test_fair_explicit_teams_tuple():
    f, why = SF.soccer_market_fair(
        MK_RAY, ("Real Betis Balompie", "Rayo Vallecano de Madrid"),
        [_event(PIN)], now=NOW)
    assert why == "ok" and f["fair_yes"] == D("0.25")


def test_fair_refuses_wrong_team():
    f, why = SF.soccer_market_fair(MK_BET, ("Sevilla", "Rayo Vallecano"),
                                   [_event(PIN)], now=NOW)
    assert (f, why) == (None, "team_not_in_event")
    ev = _event(_book("pinnacle", [2, 4, 4], names=("Sevilla", "Rayo "
                                                    "Vallecano", "Draw")),
                home="Sevilla")
    assert SF.soccer_market_fair(MK_BET, EVENT_MKS, [ev], now=NOW) == \
        (None, "no_event_match")


def test_fair_matching_is_alias_based_not_words_mode(monkeypatch):
    # PMUS_SHARP_NAME_MATCH is irrelevant: the club key table decides
    for mode in ("exact", "words"):
        monkeypatch.setattr(SH, "NAME_MATCH", mode)
        f, why = SF.soccer_market_fair(MK_BET, EVENT_MKS, [_event(PIN)],
                                       now=NOW)
        assert why == "ok"
    # a feed spelling with no alias and no strip rule is refused
    ev = _event(_book("pinnacle", [2, 4, 4], names=("Betis Sevilla",
                                                    "Rayo Vallecano",
                                                    "Draw")),
                home="Betis Sevilla")
    assert SF.soccer_market_fair(MK_BET, EVENT_MKS, [ev], now=NOW) == \
        (None, "no_event_match")


def test_fair_refuses_start_time_mismatch_and_ambiguous():
    late = _event(PIN, ct="2026-11-28T15:00:00Z")
    assert SF.soccer_market_fair(MK_BET, EVENT_MKS, [late], now=NOW) == \
        (None, "no_event_match")
    assert SF.soccer_market_fair(MK_BET, EVENT_MKS, [_event(PIN),
                                                     _event(PIN)],
                                 now=NOW) == (None, "ambiguous_event")


def test_fair_refuses_stale_two_way_missing_draw():
    stale = _book("pinnacle", [2, 4, 4], upd="2026-11-28T09:00:00Z")
    assert SF.soccer_market_fair(MK_BET, EVENT_MKS, [_event(stale)],
                                 now=NOW) == (None, "no_fresh_3way_h2h")
    two = _book("pinnacle", [1.5, 2.8], names=("Real Betis",
                                                "Rayo Vallecano"))
    assert SF.soccer_market_fair(MK_DRAW, EVENT_MKS, [_event(two)],
                                 now=NOW) == (None, "no_fresh_3way_h2h")
    nod = _book("pinnacle", [2, 4, 4], names=("Real Betis", "Rayo Vallecano",
                                               "Tie"))
    assert SF.soccer_market_fair(MK_DRAW, EVENT_MKS, [_event(nod)],
                                 now=NOW) == (None, "no_fresh_3way_h2h")


def test_fair_refuses_unresolved_event_teams():
    assert SF.soccer_market_fair(MK_DRAW, [MK_DRAW, MK_BET], [_event(PIN)],
                                 now=NOW) == (None, "event_teams_unresolved")
    dup = copy.deepcopy(MK_BET)
    assert SF.soccer_market_fair(MK_DRAW, EVENT_MKS + [dup], [_event(PIN)],
                                 now=NOW) == (None, "event_teams_unresolved")
    assert SF.soccer_market_fair(MK_DRAW, ("A", "a"), [_event(PIN)],
                                 now=NOW) == (None, "teams_invalid")
    assert SF.soccer_market_fair(MK_DRAW, "Betis", [_event(PIN)],
                                 now=NOW) == (None, "teams_invalid")


def test_fair_refuses_non_regulation_text():
    m = copy.deepcopy(MK_BET)
    m["description"] = TEXT.replace("90 minutes plus stoppage time",
                                    "the end of the match")
    assert SF.soccer_market_fair(m, EVENT_MKS, [_event(PIN)], now=NOW) == \
        (None, "settlement_not_regulation")


def test_fair_refuses_missing_start():
    m = copy.deepcopy(MK_BET)
    m["gameStartTime"] = None
    assert SF.soccer_market_fair(m, EVENT_MKS, [_event(PIN)], now=NOW) == \
        (None, "no_start_time")


# ------------------------------------------------------- keys / flag
def test_soccer_keys_separate_from_sport_keys():
    assert not set(SF.SOCCER_KEYS.values()) & set(SH.SPORT_KEYS.values())
    assert not set(SF.SOCCER_KEYS) & set(SH.SPORT_KEYS)
    assert all(v.startswith("soccer_") for v in SF.SOCCER_KEYS.values())


def test_shadow_rows_default_off(monkeypatch):
    monkeypatch.delenv("PMUS_SHARP_SOCCER", raising=False)
    assert SF.enabled() is False
    assert SF.soccer_shadow_rows(EVENT_MKS, [_event(PIN)], now=NOW) == []
    monkeypatch.setenv("PMUS_SHARP_SOCCER", "0")
    assert SF.soccer_shadow_rows(EVENT_MKS, [_event(PIN)], now=NOW) == []


def test_shadow_rows_on(monkeypatch):
    monkeypatch.setenv("PMUS_SHARP_SOCCER", "1")
    rows = SF.soccer_shadow_rows(EVENT_MKS + [{"sportsMarketType": "x"}],
                                 [_event(PIN)], now=NOW)
    assert [r["why"] for r in rows] == ["ok", "ok", "ok"]
    assert sorted(r["fair_yes"] for r in rows) == ["0.25", "0.25", "0.5"]


def test_engine_soccer_flags_default_off():
    import importlib
    from src import pmus_sports_two_sided as M
    import os
    for k in ("PMUS_SHARP_SOCCER", "PMUS_RN1_SOCCER",
              "PMUS_RN1_SOCCER_LEAGUES"):
        assert not os.environ.get(k)
    assert M.SHARP_SOCCER is False and M.RN1_SOCCER is False
    assert M.RN1_SOCCER_LEAGUES == ()


# ------------------------------------------------ club aliases (real names)
# venue names: public catalogue 28 Nov (slate) and 22 Nov (settled Serie A);
# feed names: usual sportsbook spellings, UNVERIFIED against the provider.
@pytest.mark.parametrize("venue,feed", [
    ("Real Betis Balompié", "Real Betis"),
    ("Rayo Vallecano de Madrid", "Rayo Vallecano"),
    ("FC Internazionale Milano", "Inter Milan"),
    ("Genoa CFC", "Genoa"), ("Torino FC", "Torino"), ("SS Lazio", "Lazio"),
    ("Parma Calcio 1913", "Parma"), ("Frosinone Calcio", "Frosinone"),
    ("Bologna FC 1909", "Bologna"), ("Udinese Calcio", "Udinese"),
    ("US Sassuolo Calcio", "Sassuolo"), ("PSV", "PSV Eindhoven"),
    ("SBV Excelsior", "Excelsior"), ("Telstar 1963", "Telstar"),
    ("SC Cambuur-Leeuwarden", "SC Cambuur"),
    ("1. FC Köln", "FC Koln"), ("SC Paderborn 07", "SC Paderborn"),
    ("FC Schalke 04", "Schalke 04"), ("SV 07 Elversberg", "SV Elversberg"),
    ("Ipswich Town FC", "Ipswich Town"), ("Aston Villa FC", "Aston Villa"),
    ("Leeds United FC", "Leeds United"),
    ("Coventry City FC", "Coventry City"),
])
def test_club_key_venue_and_feed_agree(venue, feed):
    assert SF.club_key(venue) == SF.club_key(feed) != ""


def test_club_key_strip_rules_are_edge_only_and_explicit():
    assert SF.club_key("AC Milan") == "milan"
    assert SF.club_key("FC") == "fc"            # never strips to nothing
    assert SF.club_key("Real Madrid") == "real madrid"   # "madrid" kept
    assert SF.club_key("Atletico de Madrid") == "atletico"
    assert SF.club_key("Inter") != SF.club_key("AC Milan")
    assert SF.club_key("Sparta Rotterdam") == "sparta rotterdam"


def test_club_pair_one_to_one_and_ambiguity_refused():
    t = ("Real Betis Balompié", "Rayo Vallecano de Madrid")
    assert SF.club_pair(t, "Real Betis", "Rayo Vallecano") == {
        t[0]: "Real Betis", t[1]: "Rayo Vallecano"}
    assert SF.club_pair(t, "Rayo Vallecano", "Real Betis") == {
        t[0]: "Real Betis", t[1]: "Rayo Vallecano"}
    assert SF.club_pair(t, "Real Betis", "Real Betis Balompie") is None
    assert SF.club_pair(t, "Real Betis", "Sevilla") is None
    assert SF.club_pair(("Inter", "FC Internazionale Milano"),
                        "Inter", "Genoa") is None     # venue keys collide
    assert SF.club_pair(t, "Real Betis", "Draw") is None


def test_fair_ambiguous_when_two_feed_events_alias_to_same_match():
    a = _event(PIN)
    b = dict(_event(PIN, home="Real Betis Balompie",
                    away="Rayo Vallecano de Madrid"), id="ev2")
    assert SF.soccer_market_fair(MK_BET, EVENT_MKS, [a, b], now=NOW) == \
        (None, "ambiguous_event")


# ------------------------------------------- settled venue outcomes (real)
# Public venue records (closed, 22 Nov), outcomePrices [Yes, No]:
SETTLED = {
    "atc-sea-bol-udi-2026-11-22-bol": ["0", "1"],
    "atc-sea-bol-udi-2026-11-22-draw": ["1", "0"],     # ended in a draw
    "atc-sea-bol-udi-2026-11-22-udi": ["0", "1"],
    "atc-sea-sas-gen-2026-11-22-sas": ["0", "1"],
    "atc-sea-sas-gen-2026-11-22-draw": ["0", "1"],
    "atc-sea-sas-gen-2026-11-22-gen": ["1", "0"],      # Genoa won
}


def test_settled_draw_pays_draw_yes_and_both_team_markets_no():
    """'If the match is tied ... settle to Tie' = team market NO."""
    for match, result in (("atc-sea-bol-udi-2026-11-22", "draw"),
                          ("atc-sea-sas-gen-2026-11-22", "gen")):
        mo = SF.match_of(match + "-draw")
        for slug, o in zip(mo["siblings"], mo["outcomes"]):
            yes = SETTLED[slug][0] == "1"
            assert yes == (o == result)
        # exactly one outcome market of a match settles YES
        assert sum(SETTLED[s][0] == "1" for s in mo["siblings"]) == 1
        # and that is what outcome_pnl's payoff assumes
        hold = {o: {"pos": {"YES": "1", "NO": "0"},
                    "cost": {"YES": "0", "NO": "0"}}
                for o in mo["outcomes"]}
        pnl = SF.outcome_pnl(hold, mo["outcomes"])
        assert pnl[result] == 1 and all(v == 1 for v in pnl.values())
