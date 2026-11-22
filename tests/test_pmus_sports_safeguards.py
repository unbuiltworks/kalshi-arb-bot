"""Owner 2026-11-22: (1) esports has a fail-closed late rule, (2) a
minimum-liquidity gate for new exposure, (3) Serie A outcomes are mapped
one by one, exactly, and (4) never two markets of the same game."""
import json
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from src import pmus_sports_xv as XV
from tests.test_pmus_sports_two_sided import FakeVenue


# ------------------------------------------------ 1. esports / soccer phase
def es(**k):
    return M.is_late({"type": "esports", **k})


def test_esports_as_the_venue_publishes_it_is_always_blocked():
    """Live 2026-11-22: {"score": "2-0", "period": "Live"} -- no format."""
    for st in ({"score": "2-0", "period": "Live"},
               {"score": "0-1", "period": "Map 2"},
               {"score": "0-0", "period": "Map 1"}, {}):
        assert es(**st) == "esports_format_unknown"


@pytest.mark.parametrize("bo", [0, 2, 4, 9, -3, "x", "", None, "3.5"])
def test_esports_unusable_format_is_blocked(bo):
    assert es(score="0-0", bestOf=bo) == "esports_format_unknown"


@pytest.mark.parametrize("score", ["", "x", "1", "1-", "a-b", "1-0-0",
                                   "-1-0", "2-0", "3-1"])
def test_esports_unreadable_or_finished_score_is_blocked(score):
    assert es(score=score, bestOf=3) == "esports_score_unknown"


def test_esports_opens_only_before_anyone_is_at_match_point():
    assert es(score="0-0", bestOf=3) is None
    assert es(score="1-0", bestOf=3) == "esports_deciding_map"
    assert es(score="0-0", bestOf=1) == "esports_deciding_map"   # bo1
    assert es(score="0-0", bestOf=5) is None
    assert es(score="1-1", bestOf=5) is None
    assert es(score="2-0", bestOf=5) == "esports_deciding_map"
    assert es(score="2-2", bestOf=5) == "esports_deciding_map"
    assert es(score="0-0", esportsState={"bestOf": 3}) is None
    assert es(score="0-0", bestOf=3, ended=True) == "ended"


def test_soccer_second_half_fails_closed_without_a_minute():
    so = lambda **k: M.is_late({"type": "soccer", **k})        # noqa: E731
    assert so(period="1H", elapsed="30'") is None
    assert so(period="HT", elapsed="") is None
    assert so(period="2H", elapsed="55'") is None
    assert so(period="2H", elapsed="70'") == "2H_70+"
    assert so(period="2H", elapsed="90+3'") == "2H_70+"
    for bad in ("", None, "abc"):
        assert so(period="2H", elapsed=bad) == "2H_minute_unknown"
    assert so(period="ET1", elapsed="95'") == "overtime:ET1"


# ------------------------------------------------ 2. minimum-liquidity gate
@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    v = FakeVenue()
    v.bid, v.ask = D("0.48"), D("0.50")
    return M.Engine(v, M.Book(tmp_path / "s.json")), v


def sides(v):
    return sorted(o["side"] for o in v.orders.values() if o["open"])


def test_normal_book_opens_both_sides(eng):
    e, v = eng
    assert M.MIN_TOUCH_QTY == D("10")
    e.b.m("aec-x")
    e.step_market("aec-x")
    assert sides(v) == ["NO", "YES"]


@pytest.mark.parametrize("depth,expect", [
    ((D("9.99"), D("1000")), ["NO"]),       # thin YES bid: no YES opening
    ((D("1000"), D("0.52")), ["YES"]),      # thin ask = thin NO bid touch
    ((D("3"), D("3")), []),
    ((D("10"), D("10")), ["NO", "YES"]),    # exactly the minimum passes
    (None, []),                             # unreadable book: fail closed
])
def test_depleted_touch_blocks_only_the_side_priced_off_it(eng, depth,
                                                           expect):
    e, v = eng
    v.depth = depth
    e.b.m("aec-x")
    e.step_market("aec-x")
    assert sides(v) == expect


def test_gate_is_sport_agnostic_and_backstops_every_opening_path(eng, capsys):
    e, v = eng
    v.depth = (D("2"), D("2"))
    for slug in ("aec-nba-a-b-2026-11-22", "aec-atp-a-b-2026-11-22",
                 "atc-sea-juv-ven-2026-11-22-juv", "aec-cs2-a-b-2026-11-22"):
        e.b.m(slug)
        for stage in ("rn1_open", "rn1_extend", "two_sided_open",
                      "two_sided_open_rewarded", "two_sided_extend"):
            e.place(slug, "YES", D("0.40"), D("2"), stage)
    assert v.orders == {}
    assert capsys.readouterr().out.count("place_refused_depth") == 20


def test_completion_is_never_refused_by_depth(eng):
    """A held unpaired leg must stay completable in a depleted book."""
    e, v = eng
    v.depth = (D("0"), D("0"))
    mk = e.b.m("aec-x")
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("0.88")
    e.step_market("aec-x")
    assert [(o["side"], o["qty"]) for o in v.orders.values()
            if o["open"]] == [("NO", D("2"))]
    assert all(o.get("stage") == "two_sided_complete"
               for mkt in e.b.d["markets"].values()
               for o in mkt["orders"].values() if o.get("open"))


def test_replenishment_after_a_pair_obeys_the_gate(eng):
    """Flat again after a completed pair: new bids only if the book is real."""
    e, v = eng
    mk = e.b.m("aec-x")
    mk["pos"] = {"YES": D("2"), "NO": D("2")}
    mk["cost"] = {"YES": D("0.94"), "NO": D("0.98")}
    v.depth = (D("1"), D("1"))
    e.step_market("aec-x")
    assert sides(v) == []
    v.depth = (D("500"), D("500"))
    e.step_market("aec-x")
    assert sides(v) == ["NO", "YES"]


def test_a_resting_opening_is_withdrawn_when_its_touch_empties(eng):
    e, v = eng
    e.b.m("aec-x")
    e.step_market("aec-x")
    assert sides(v) == ["NO", "YES"]
    v.depth = (D("1"), D("1000"))
    e.step_market("aec-x")
    assert sides(v) == ["NO"]


def test_our_own_contracts_at_the_touch_do_not_count(eng):
    e, v = eng
    mk = e.b.m("aec-x")
    mk["orders"]["mine"] = {"side": "YES", "px": "0.48", "qty": "8",
                            "filled": "0", "open": True, "stage": "rn1_open"}
    v.depth = (D("12"), D("1000"))                # 12 shown, 8 are ours
    assert e.depth_block("aec-x", "YES").startswith("thin_touch:4")
    assert e.depth_block("aec-x", "NO") is None
    v.depth = (D("18"), D("1000"))
    assert e.depth_block("aec-x", "YES") is None


def test_gate_can_be_disabled_and_venue_without_depth_fails_closed(
        eng, monkeypatch):
    e, v = eng
    v.depth = None
    assert e.depth_block("aec-x", "YES") == "depth_unknown"
    v.top_depth = lambda slug: (_ for _ in ()).throw(RuntimeError("x"))
    assert e.depth_block("aec-x", "YES") == "depth_unknown"
    monkeypatch.setattr(M, "MIN_TOUCH_QTY", D("0"))
    assert e.depth_block("aec-x", "YES") is None


def test_venue_top_depth_reads_and_caches_the_public_book(monkeypatch):
    v = M.Venue.__new__(M.Venue)
    from src import pmus_q1_executor_v4 as q1
    v.q1 = q1
    calls = []

    def gget(path):
        calls.append(path)
        return {"marketData": {
            "bids": [{"px": {"value": "0.24"}, "qty": "38389.7"},
                     {"px": {"value": "0.23"}, "qty": "1060"},
                     {"px": {"value": "0.24"}, "qty": "0.3"}],
            "offers": [{"px": {"value": "0.26"}, "qty": "169"},
                       {"px": {"value": "0.25"}, "qty": "0.52"}]}}
    v.gget = gget
    d = v.top_depth("atc-sea-juv-ven-2026-11-22-draw")
    assert d == {"bid": D("0.24"), "bid_qty": D("38390.0"),
                 "ask": D("0.25"), "ask_qty": D("0.52")}
    v.top_depth("atc-sea-juv-ven-2026-11-22-draw")
    assert len(calls) == 1                         # cached
    v.gget = lambda p: {"marketData": {"bids": [], "offers": []}}
    assert v.top_depth("aec-empty") is None


# ------------------------------------------------ 3. Serie A outcome mapping
EV = "sea-juv-ven-2026-11-22"


def pm(suffix, team=None, typ="soccer_team_full_time_winner",
       outcomes='["Yes","No"]', question=None):
    q = question or (
        "Will the Serie A match Juventus FC vs Venezia FC scheduled for "
        "Nov 22, 2026 end in a draw?" if suffix == "draw" else
        f"Will {team} win against the other side in the Serie A match?")
    sides = [{"long": True, "description": "Yes",
              "team": {"name": team} if team else None},
             {"long": False, "description": "No",
              "team": {"name": team} if team else None}]
    return {"slug": f"atc-{EV}-{suffix}", "outcomes": outcomes,
            "sportsMarketType": typ, "question": q, "marketSides": sides}


def imk(suffix, question, toks, typ="moneyline", closed=False,
        outcomes=("Yes", "No")):
    return {"slug": f"{EV}-{suffix}", "question": question,
            "outcomes": json.dumps(list(outcomes)), "closed": closed,
            "clobTokenIds": json.dumps(list(toks)), "sportsMarketType": typ}


def intl_event(**over):
    mk = {"juv": imk("juv", "Will Juventus FC win on 2026-11-22?",
                     ("J1", "J2")),
          "draw": imk("draw", "Will Juventus FC vs. Venezia FC end in a "
                              "draw?", ("D1", "D2")),
          "ven": imk("ven", "Will Venezia FC win on 2026-11-22?",
                     ("V1", "V2"))}
    extra = over.pop("extra", [])
    title = over.pop("title", "Juventus FC vs. Venezia FC")
    closed = over.pop("closed", False)
    mk.update(over)
    return {"slug": EV, "title": title, "closed": closed,
            "markets": [m for m in mk.values() if m is not None] + [
                imk("btts", "Both teams to score?", ("B1", "B2"),
                    typ="both_teams_to_score")] + extra}


def gamma(ev):
    return lambda path: [ev] if path == f"/events?slug={EV}" else []


def r3(suffix, team=None, ev=None, **pmkw):
    return XV.resolve_three_way(gamma(ev or intl_event()),
                                f"atc-{EV}-{suffix}", pm(suffix, team, **pmkw))


def test_each_outcome_maps_to_its_own_yes_token():
    assert r3("juv", "Juventus FC") == "J1"
    assert r3("draw") == "D1"
    assert r3("ven", "Venezia FC") == "V1"
    assert XV.resolve_market(gamma(intl_event()), f"atc-{EV}-juv",
                             pm("juv", "Juventus FC")) == "J1"


def test_wrong_team_or_swapped_markets_are_refused():
    assert r3("juv", "Venezia FC") is None        # PMUS long side != slug's
    assert r3("juv", "AC Milan") is None          # not in this match
    assert r3("juv", None) is None                # no team on the long side
    swapped = intl_event(
        juv=imk("juv", "Will Venezia FC win on 2026-11-22?", ("J1", "J2")),
        ven=imk("ven", "Will Juventus FC win on 2026-11-22?", ("V1", "V2")))
    assert r3("juv", "Juventus FC", ev=swapped) is None
    both = intl_event(
        ven=imk("ven", "Will Venezia FC beat Juventus FC?", ("V1", "V2")))
    assert r3("juv", "Juventus FC", ev=both) is None   # ambiguous naming


def test_draw_is_never_confused_with_a_team_market():
    assert r3("draw", question="Will Juventus FC win against Venezia FC "
                               "in the Serie A match?") is None
    assert r3("draw", ev=intl_event(draw=imk(
        "draw", "Will Juventus FC win on 2026-11-22?", ("D1", "D2")))) is None
    assert r3("juv", "Juventus FC", ev=intl_event(juv=imk(
        "juv", "Will Juventus FC vs. Venezia FC end in a draw?",
        ("J1", "J2")))) is None
    assert r3("draw", question="Will the Serie A match Juventus FC vs "
              "AC Milan scheduled for Nov 22, 2026 end in a draw?") is None


@pytest.mark.parametrize("ev", [
    intl_event(draw=None),                                    # only two
    intl_event(extra=[imk("x", "Will X win?", ("X1", "X2"))]),  # four
    intl_event(closed=True),
    intl_event(juv=imk("juv", "Will Juventus FC win on 2026-11-22?",
                       ("J1", "J2"), closed=True)),
    intl_event(juv=imk("juv", "Will Juventus FC win on 2026-11-22?",
                       ("J1",))),
    intl_event(juv=imk("juv", "Will Juventus FC win on 2026-11-22?",
                       ("J1", "J2"), outcomes=("No", "Yes"))),
    intl_event(juv=imk("juv", "Will Juventus FC win on 2026-11-22?",
                       ("J1", "J2"), typ="spreads")),
    intl_event(title="Juventus FC"),
    intl_event(title="Juventus FC vs. Juventus FC"),
])
def test_an_incomplete_or_irregular_international_event_is_refused(ev):
    assert r3("juv", "Juventus FC", ev=ev) is None


def test_pmus_side_must_be_a_plain_yes_no_full_time_winner():
    assert r3("juv", "Juventus FC", outcomes='["No","Yes"]') is None
    assert r3("juv", "Juventus FC", outcomes='["Juventus","Venezia"]') is None
    assert r3("juv", "Juventus FC", typ="soccer_first_half_winner") is None
    g = gamma(intl_event())
    assert XV.resolve_three_way(g, f"atc-{EV}-juv", None) is None
    for bad in (f"atc-{EV}-mil", f"atc-{EV}", f"atc-{EV}-juv-x",
                "atc-sea-juv-juv-2026-11-22-juv",
                "atc-sea-juv-ven-2026-13-45-juv",
                "atc-sea-draw-ven-2026-11-22-draw"):
        assert XV.resolve_three_way(g, bad, pm("juv", "Juventus FC")) is None


def test_only_mapped_leagues_and_missing_events_are_refused(monkeypatch):
    other = "atc-thai1-bgk-ayu-2026-11-22-bgk"
    assert XV.resolve_three_way(lambda p: [intl_event()], other,
                                pm("juv", "Juventus FC")) is None
    assert XV.resolve_three_way(lambda p: [], f"atc-{EV}-juv",
                                pm("juv", "Juventus FC")) is None
    assert XV.resolve_three_way(lambda p: [intl_event(), intl_event()],
                                f"atc-{EV}-juv",
                                pm("juv", "Juventus FC")) is None
    monkeypatch.setattr(XV, "THREE_WAY_LEAGUES", ())
    assert r3("juv", "Juventus FC") is None


def test_two_outcome_markets_still_resolve_through_the_same_entry():
    def g(path):
        return [{"slug": "nba-bos-sac-2026-11-21", "closed": False,
                 "outcomes": '["Celtics","Kings"]',
                 "clobTokenIds": '["N1","N2"]'}] \
            if path == "/markets?slug=nba-bos-sac-2026-11-21" else []
    assert XV.resolve_market(g, "aec-nba-bos-sac-2026-11-21",
                             {"outcomes": '["Celtics","Kings"]'}) == "N1"
    assert XV.resolve_market(g, "aec-nba-bos-sac-2026-11-21", None) is None


def test_guard_prices_one_soccer_outcome_and_fails_closed_otherwise():
    g = XV.IntlGuard(lambda p: {"market": pm("draw")}, gamma(intl_event()),
                     margin_ticks=1, require=True)
    slug = f"atc-{EV}-draw"
    g._resolve_one(slug)
    assert g.token(slug) == "D1"
    assert g.verdict(slug, {"YES": 0.23}, 0.01)["ok"] is False   # no quote
    g.intl.clock = lambda: 100.0
    g.intl.ingest({"event_type": "book", "asset_id": "D1",
                   "bids": [{"price": "0.26", "size": "500"}],
                   "asks": [{"price": "0.27", "size": "500"}]})
    assert g.verdict(slug, {"YES": 0.24}, 0.01)["ok"] is True
    assert g.verdict(slug, {"YES": 0.26}, 0.01)["why"] == \
        "intl_says_not_cheap"
    bad = XV.IntlGuard(lambda p: {"market": pm("draw")},
                       gamma(intl_event(draw=None)), require=True)
    bad._resolve_one(slug)
    assert bad.token(slug) is None
    assert bad.verdict(slug, {"YES": 0.01}, 0.01)["ok"] is False


# ------------------------------------------------ 4. one market per game
def test_event_key_groups_every_market_of_a_game():
    k = M.event_key
    assert k("atc-sea-juv-ven-2026-11-22-juv") == \
        k("atc-sea-juv-ven-2026-11-22-draw") == \
        k("atc-sea-juv-ven-2026-11-22-ven") == "sea-juv-ven-2026-11-22"
    assert k("aec-nba-bos-sac-2026-11-21") == \
        k("tsc-nba-bos-sac-2026-11-21-220pt5")
    assert k("aec-nba-bos-sac-2026-11-21") != k("aec-nba-bos-sac-2026-11-22")
    assert k("aec-x") == "x"


def test_a_second_outcome_of_the_same_match_is_never_selected(eng):
    e, v = eng
    e.b.m("atc-sea-juv-ven-2026-11-22-juv")
    assert e._try_select("atc-sea-juv-ven-2026-11-22-draw", v.account(),
                         None) is False
    assert "atc-sea-juv-ven-2026-11-22-draw" not in e.b.d["markets"]
    assert e._try_select("aec-x", v.account(), None) is True


# ------------------------- 1b. esports is decided by the LEAGUE, not the state
def test_cs2_with_no_match_state_at_all_is_blocked():
    """Live 2026-11-22: CS2 events on the slate carried {"type": null}. A
    rule keyed on state.type alone let those through."""
    for st in (None, {}, {"type": None, "score": None, "period": None},
               {"type": "", "period": "Live"}):
        assert M.late_for("aec-cs2-mira-urn-2026-11-22", st) == \
            "esports_format_unknown"
    for lg in ("cs2", "lol", "val", "valorant", "dota2", "ow"):
        assert M.late_for(f"aec-{lg}-a-b-2026-11-22", None) == \
            "esports_format_unknown"


def test_esports_block_does_not_depend_on_the_late_cutoff_switch(monkeypatch):
    monkeypatch.setattr(M, "LATE_CUTOFF", False)
    assert M.late_for("aec-cs2-a-b-2026-11-22", None) == \
        "esports_format_unknown"
    assert M.late_for("aec-newgame-a-b-2026-11-22",
                      {"type": "esports", "score": "1-0"}) == \
        "esports_format_unknown"               # unknown league, esports state
    # other sports keep the switch's meaning
    assert M.late_for("aec-nba-a-b-2026-11-22",
                      {"type": "basketball", "period": "Q4"}) is None
    monkeypatch.setattr(M, "LATE_CUTOFF", True)
    assert M.late_for("aec-nba-a-b-2026-11-22",
                      {"type": "basketball", "period": "Q4"}) == "Q4"
    assert M.late_for("aec-nba-a-b-2026-11-22", None) is None


def test_esports_with_a_published_format_follows_the_map_rule():
    st = {"type": "esports", "score": "0-0", "bestOf": 3}
    assert M.late_for("aec-cs2-a-b-2026-11-22", st) is None
    assert M.late_for("aec-cs2-a-b-2026-11-22",
                      dict(st, score="1-0")) == "esports_deciding_map"
    assert M.late_for("aec-cs2-a-b-2026-11-22",
                      dict(st, ended=True)) == "ended"


def test_engine_never_opens_or_selects_cs2_without_a_format(eng, capsys):
    e, v = eng
    slug = "aec-cs2-mira-urn-2026-11-22"
    v.states = {}                                   # the venue sent no state
    v.targets = [{"slug": slug, "market_meta": {"bestBidQuote": "0.48",
                                                "bestAskQuote": "0.50"}}]
    e.select(v.account())
    assert slug not in e.b.d["markets"]             # not even selected
    e.b.m(slug)                                     # already tracked somehow
    e.step_market(slug)
    assert v.orders == {}
    assert "esports_format_unknown" in capsys.readouterr().out
    v.states = {slug: {"type": "esports", "score": "1-0", "period": "Map 2"}}
    e.step_market(slug)
    assert v.orders == {}


def test_cs2_completion_of_a_held_leg_still_works(eng):
    e, v = eng
    slug = "aec-cs2-mira-urn-2026-11-22"
    v.states = {}
    mk = e.b.m(slug)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("2"), D("0.88")
    e.step_market(slug)
    assert [(o["side"], o["qty"]) for o in v.orders.values()
            if o["open"]] == [("NO", D("2"))]


def test_boot_reports_the_safeguards_as_they_run():
    c = M.safeguards_cfg()
    assert c["min_touch_qty"] == "10" and c["late_cutoff"] is True
    assert c["esports_rule"] == "fail_closed_without_format"
    assert "cs2" in c["esports_leagues"]
    assert c["intl_three_way"] == ["sea"]
    assert c["intl_name_match"] == ["atp", "wta"]
    json.dumps(c)                                   # loggable
