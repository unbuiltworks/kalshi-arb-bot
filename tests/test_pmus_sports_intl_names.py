"""Tennis is listed internationally under surname slugs, so the guard's
exact-slug lookup found nothing (live 2026-11-22: 7 of 7 ATP matches read
"no international market"). The name resolver must find the right match and
must refuse anything it cannot prove is the same match, same side."""
import json

import pytest

from src import pmus_sports_xv as XV

PM = "aec-atp-yukmoc-kosogu-2026-11-21"
OUT = ["Yuki Mochizuki", "Kosuke Ogura"]


def mkt(slug, outcomes, typ="moneyline", toks=("T1", "T2"), closed=False):
    return {"slug": slug, "outcomes": json.dumps(outcomes),
            "clobTokenIds": json.dumps(list(toks)), "closed": closed,
            "sportsMarketType": typ}


def event(slug="atp-mochiz-ogura-2026-11-21", markets=None, closed=False):
    if markets is None:
        markets = [
            mkt(slug, OUT),
            mkt(slug + "-first-set-winner-Mochizuki-vs-Ogura", OUT,
                "tennis_first_set_winner", ("S1", "S2")),
            mkt(slug + "-set-handicap-away-1pt5", OUT[::-1],
                "tennis_set_handicap", ("H1", "H2")),
            mkt(slug + "-match-total-21pt5", ["Over", "Under"],
                "tennis_match_totals", ("O", "U"))]
    return {"slug": slug, "closed": closed, "markets": markets}


def gamma(events, calls=None):
    def get(path):
        if calls is not None:
            calls.append(path)
        if path.startswith("/public-search?"):
            return {"events": events}
        return []                       # exact slug: nothing, as live
    return get


def test_live_case_resolves_to_the_match_winner_token():
    calls = []
    tok = XV.resolve_token(gamma([event()], calls), PM, OUT)
    assert tok == "T1"
    assert calls[0] == "/markets?slug=atp-yukmoc-kosogu-2026-11-21"
    assert "q=mochizuki" in calls[-1]            # searched by surname


def test_exact_slug_leagues_never_use_the_name_search():
    calls = []
    assert XV.resolve_token(gamma([event()], calls),
                            "aec-nba-bos-sac-2026-11-21",
                            ["Celtics", "Kings"]) is None
    assert not any(c.startswith("/public-search") for c in calls)


def test_set_winner_and_handicap_markets_are_never_the_anchor():
    slug = "atp-mochiz-ogura-2026-11-21"
    only_sets = event(markets=[
        mkt(slug + "-first-set-winner-Mochizuki-vs-Ogura", OUT,
            "tennis_first_set_winner"),
        mkt(slug, OUT, "tennis_set_winner")])     # right slug, wrong type
    assert XV.resolve_by_names(gamma([only_sets]), PM, OUT) is None


@pytest.mark.parametrize("ev", [
    event(markets=[mkt("atp-mochiz-ogura-2026-11-21", OUT[::-1])]),  # reversed
    event(markets=[mkt("atp-mochiz-ogura-2026-11-21",
                       ["Yuki Mochizuki", "Kosuke Okura"])]),        # other man
    event(markets=[mkt("atp-mochiz-ogura-2026-11-21",
                       ["Y. Mochizuki", "Kosuke Ogura"])]),          # initials
    event(slug="atp-doubles-mochogu-xy-2026-11-21"),                 # doubles
    event(slug="wta-mochiz-ogura-2026-11-21"),                       # league
    event(slug="atp-mochiz-ogura-2026-11-24"),                       # 3 days
    event(closed=True),
    event(markets=[mkt("atp-mochiz-ogura-2026-11-21", OUT, closed=True)]),
    event(markets=[mkt("atp-mochiz-ogura-2026-11-21", OUT,
                       toks=("T1",))]),                              # 1 token
])
def test_anything_not_provably_the_same_match_is_refused(ev):
    assert XV.resolve_by_names(gamma([ev]), PM, OUT) is None


def test_date_may_differ_by_one_day_only():
    assert XV.resolve_by_names(
        gamma([event(slug="atp-mochiz-ogura-2026-11-22")]), PM, OUT) == "T1"
    assert XV.resolve_by_names(
        gamma([event(slug="atp-mochiz-ogura-2026-11-23")]), PM, OUT) is None


def test_two_different_candidates_are_ambiguous_and_refused():
    a = event()
    b = event(slug="atp-mochiz-ogura-2026-11-22",
              markets=[mkt("atp-mochiz-ogura-2026-11-22", OUT,
                           toks=("X1", "X2"))])
    assert XV.resolve_by_names(gamma([a, b]), PM, OUT) is None
    assert XV.resolve_by_names(gamma([a, a]), PM, OUT) == "T1"   # same token


def test_bad_inputs_are_refused():
    g = gamma([event()])
    assert XV.resolve_by_names(g, PM, None) is None
    assert XV.resolve_by_names(g, PM, ["Yuki Mochizuki"]) is None
    assert XV.resolve_by_names(g, PM, ["Mochizuki", "Ogura"]) is None
    assert XV.resolve_by_names(g, PM, ["Yuki Mochizuki",
                                       "Yuki Mochizuki"]) is None
    assert XV.resolve_by_names(g, "aec-atp-x-2026-11-21", OUT) is None
    assert XV.resolve_by_names(g, "aec-utr-yukmoc-kosogu-2026-11-21",
                               OUT) is None
    assert XV.resolve_by_names(lambda p: None, PM, OUT) is None
    assert XV.resolve_by_names(lambda p: {"events": "x"}, PM, OUT) is None


def test_league_list_is_configurable_and_can_be_emptied(monkeypatch):
    monkeypatch.setattr(XV, "NAME_MATCH_LEAGUES", ())
    assert XV.resolve_token(gamma([event()]), PM, OUT) is None


def test_guard_prices_a_tennis_market_through_the_name_match():
    """End to end through IntlGuard: resolved, then judged like any other."""
    def pm_get(path):
        return {"market": {"slug": PM, "outcomes": json.dumps(OUT)}}
    g = XV.IntlGuard(pm_get, gamma([event()]), margin_ticks=1, require=True)
    g._resolve_one(PM)
    assert g.token(PM) == "T1"
    assert g.verdict(PM, {"YES": 0.60}, 0.01)["why"] == "no_fresh_intl"
    g.intl.clock = lambda: 100.0
    g.intl.ingest({"event_type": "book", "asset_id": "T1",
                   "bids": [{"price": "0.64", "size": "100"}],
                   "asks": [{"price": "0.65", "size": "100"}]})
    assert g.verdict(PM, {"YES": 0.63}, 0.01)["ok"] is True
    assert g.verdict(PM, {"YES": 0.645}, 0.01)["why"] == "intl_says_not_cheap"
