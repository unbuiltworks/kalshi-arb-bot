"""Sport-agnostic discovery funnel: every exclusion is named, the engine's
own thresholds are used, cricket is never eligible, and nothing here can
trade."""
from decimal import Decimal as D

from src import pmus_sports_discovery as DV
from src import pmus_sports_two_sided as M

ALLOW = ["cfb", "cs2", "nba", "nfl", "wta"]


def mk(slug="aec-nba-bos-sac-2026-11-21", bid=("0.50", "500"),
       ask=("0.51", "400"), tick="0.01", typ="basketball", period="Q2",
       intl=True, ibid=("0.50", "900"), iask=("0.51", "900"), **st):
    return {"slug": slug, "tick": tick, "bid": bid, "ask": ask,
            "state": {"type": typ, "period": period, **st},
            "intl_token": intl, "ibid": ibid, "iask": iask}


def reasons(**kw):
    return DV.classify(mk(**kw), ALLOW)["reasons"]


def test_a_clean_allowlisted_market_is_eligible_with_modelled_economics():
    c = DV.classify(mk(), ALLOW)
    assert c["eligible"] and c["primary"] is None
    # one tick behind each touch: 0.49 + 0.48 = 0.97
    assert D(c["modelled_pair_cost"]) == D("0.97")
    assert D(c["modelled_edge_per_pair"]) == D("0.03")
    assert c["guard_ok"] == {"YES": True, "NO": True}


def test_cricket_is_never_eligible_whatever_else_is_true():
    for slug, typ in (("aec-testcr-ind-aus-2026-11-22", "cricket"),
                      ("aec-plunket-a-b-2026-11-22", "cricket"),
                      ("aec-ipl-cricket-a-b-2026-11-22", "other")):
        c = DV.classify(mk(slug=slug, typ=typ), ALLOW + ["testcr", "plunket",
                                                         "ipl"])
        assert c["primary"] == "cricket_excluded" and not c["eligible"]
        assert not c["eligible_but_for_allowlist"]


def test_each_exclusion_is_named():
    assert reasons(slug="asc-nba-bos-sac-2026-11-21-pos-4pt5")[0] == \
        "incompatible_market_structure"
    assert reasons(ask=None)[0] == "no_two_sided_book"
    assert reasons(bid=("0.80", "9"), ask=("0.81", "9"))[0] == \
        "lopsided_price"
    assert reasons(bid=("0.40", "50"), ask=("0.47", "50"))[0] == \
        "wide_spread"
    assert reasons(bid=("0.50", "3"))[0] == "insufficient_depth"
    assert reasons(period="Q4")[0] == "final_phase"
    assert reasons(typ="volleyball")[0] == "no_phase_rule"
    assert reasons(intl=False)[0] == "no_international_match"
    assert reasons(iask=None)[0] == "international_book_one_sided"
    assert reasons(slug="aec-atp-a-b-2026-11-22", typ="tennis",
                   period="S1") == ["sport_not_on_allowlist"]


def test_thresholds_are_the_engines_own(monkeypatch):
    # 0.5c tick: the engine's 6-TICK limit is 3c, not 6c
    assert "wide_spread" in reasons(bid=("0.50", "50"), ask=("0.535", "50"),
                                    tick="0.005")
    assert "wide_spread" not in reasons(bid=("0.50", "50"),
                                        ask=("0.53", "50"), tick="0.005")
    monkeypatch.setattr(M, "BAND_HI", D("0.60"))
    assert reasons(bid=("0.65", "50"), ask=("0.66", "50"))[0] == \
        "lopsided_price"


def test_all_reasons_are_kept_in_engine_order():
    r = reasons(slug="aec-khl-a-b-2026-11-22", typ="hockey", period="P3",
                bid=("0.90", "1"), ask=("0.99", "1"), intl=False)
    assert r == ["lopsided_price", "wide_spread", "insufficient_depth",
                 "final_phase", "no_international_match",
                 "pair_cost_above_cap", "sport_not_on_allowlist"] or \
        r == ["lopsided_price", "wide_spread", "insufficient_depth",
              "final_phase", "no_international_match",
              "sport_not_on_allowlist"]


def test_guard_view_and_score_penalise_a_stale_us_quote():
    fresh = DV.classify(mk(), ALLOW)
    stale = DV.classify(mk(ibid=("0.45", "900"), iask=("0.46", "900")),
                        ALLOW)                 # intl already 5c lower
    assert stale["guard_ok"]["YES"] is False   # our YES bid is not cheap
    assert DV.score(stale) < DV.score(fresh)
    assert DV.score({"slug": "x"}) is None


def test_summary_gives_a_verdict_per_sport_and_ranks_by_score():
    rows = [DV.classify(m, ALLOW) for m in (
        mk(),
        mk(slug="aec-atp-a-b-2026-11-22", typ="tennis", period="S1"),
        mk(slug="aec-atp-c-d-2026-11-22", typ="tennis", period="S1",
           bid=("0.95", "50"), ask=("0.96", "50")),
        mk(slug="aec-utr-a-b-2026-11-22", typ="tennis", period="S1",
           intl=False),
        mk(slug="aec-vnl-a-b-2026-11-22", typ="volleyball", period="S1"),
        mk(slug="aec-testcr-a-b-2026-11-22", typ="cricket"),
        mk(slug="aec-nhl-a-b-2026-11-22", typ="hockey", period="P1",
           bid=("0.10", "50"), ask=("0.11", "50")),
        mk(slug="tsc-nba-bos-sac-2026-11-21-220pt5"))]
    s = DV.summarise(rows, ALLOW)
    v = {k: x["verdict"] for k, x in s["sports"].items()}
    assert v == {"nba": "supported", "atp": "candidate",
                 "utr": "blocked_no_international_listing",
                 "vnl": "blocked_no_phase_rule",
                 "testcr": "excluded_by_owner",
                 "nhl": "mapped_but_nothing_tradable_now"}
    assert s["primary_reason"]["incompatible_market_structure"] == 1
    assert [r["slug"] for r in s["ranked"]] and all(
        "testcr" not in r["slug"] for r in s["ranked"])
    assert {r["slug"]: r["on_allowlist"] for r in s["ranked"]} == {
        "aec-nba-bos-sac-2026-11-21": True, "aec-atp-a-b-2026-11-22": False}


def test_discover_is_read_only_and_uses_the_guards_resolver(monkeypatch):
    calls = []

    def pm_get(path):
        calls.append(("pm", path))
        if "live=true" in path:
            return {"events": [
                {"slug": "nba-x", "live": True, "eventState":
                 {"type": "basketball", "period": "Q1"}, "markets": [
                     {"slug": "aec-nba-bos-sac-2026-11-21"},
                     {"slug": "tsc-nba-bos-sac-2026-11-21-220pt5"}]},
                {"slug": "done", "live": True, "ended": True, "markets": [
                    {"slug": "aec-nba-a-b-2026-11-21"}]},
                {"slug": "props-only", "live": True, "eventState": {},
                 "markets": [{"slug": "tsc-xx-a-b-2026-11-21-1pt5"}]}]}
        if path.endswith("/book"):
            return {"marketData": {
                "bids": [{"px": {"value": "0.50"}, "qty": "500"}],
                "offers": [{"px": {"value": "0.51"}, "qty": "400"}]}}
        return {"market": {"orderPriceMinTickSize": "0.01",
                           "outcomes": '["Celtics","Kings"]'}}
    monkeypatch.setattr(DV.XV, "resolve_token",
                        lambda g, slug, po, al=None: "TOK")
    rep = DV.discover(pm_get, lambda p: [], lambda p: {
        "bids": [{"price": "0.50", "size": "9"}],
        "asks": [{"price": "0.51", "size": "9"}]}, ALLOW, workers=1)
    assert rep["live_events"] == 2                    # ended one dropped
    assert rep["events_without_match_winner"] == ["props-only"]
    assert rep["summary"]["primary_reason"] == {
        "eligible": 1, "incompatible_market_structure": 2}
    assert all(k == "pm" and p.startswith("/v1/") for k, p in calls)
    src = open(DV.__file__).read()
    for word in (".place(", ".cancel", '"POST"', ".request(", "Transport"):
        assert word not in src                        # no order path at all
