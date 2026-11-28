"""Owner 2026-11-22 -- one continuous controller, before and during the game.

"I want BOTH pregame and in-game liquidity provision. Do not build a
pregame-only reward strategy that automatically stops at kickoff."
"At kickoff, revalidate or cancel stale orders before continuing. Pause
during unsafe price moves, then resume when conditions permit."
"In-game: ... smaller adaptive sizes, volatility controls, fresh fair-value
checks, and independent marginal-EV decisions for each side."
"Do not force pairs, chase losing inventory, or maximize rewards at the
expense of trading losses."
"A cap on the total value of outstanding orders, not just filled inventory,
so simultaneous fills cannot bypass the intended exposure limit."
"""
import json
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_phase as PH
from src import pmus_sports_two_sided as M
from tests.test_pmus_rn1_reward_aware import Guard
from tests.test_pmus_sports_two_sided import FakeVenue

SLUG = "aec-x"
PHASE = dict(pull_s=300, resume_min_s=120, max_hold_s=1800, resumed=False,
             confirmed_live=True, fair_ok=True, book_ok=True)


# ------------------------------------------------------------ pure: phase
def test_a_market_with_no_start_time_is_live():
    p = PH.phase(now=1000.0, game_start=None, **PHASE)
    assert p["phase"] == PH.LIVE and not p["resume"] and not p["expired"]


def test_before_the_pull_time_the_market_is_pregame():
    p = PH.phase(now=1000.0, game_start=1000.0 + 301, **PHASE)
    assert p["phase"] == PH.PREGAME


@pytest.mark.parametrize("dt, kw, why", [
    (-299, {}, "before_start"),
    (-1, {}, "before_start"),
    (0, {}, "minimum_hold"),
    (119, {}, "minimum_hold"),
    (121, {"confirmed_live": False}, "not_confirmed_live"),
    (121, {"book_ok": False}, "no_fresh_book"),
    (121, {"fair_ok": False}, "fair_not_settled"),
])
def test_the_kickoff_hold_and_each_reason_it_lasts(dt, kw, why):
    p = PH.phase(now=5000.0 + dt, game_start=5000.0, **{**PHASE, **kw})
    assert p["phase"] == PH.KICKOFF and p["why"] == why and not p["resume"]


def test_the_hold_ends_only_when_every_check_passes():
    p = PH.phase(now=5121.0, game_start=5000.0, **PHASE)
    assert p["phase"] == PH.LIVE and p["resume"] is True


def test_once_resumed_the_market_stays_live():
    p = PH.phase(now=5001.0, game_start=5000.0,
                 **{**PHASE, "resumed": True, "confirmed_live": False})
    assert p["phase"] == PH.LIVE and not p["resume"]


def test_a_recheck_that_never_passes_expires_without_resuming():
    p = PH.phase(now=5000.0 + 1800, game_start=5000.0,
                 **{**PHASE, "confirmed_live": False})
    assert p["phase"] == PH.LIVE and p["expired"] and not p["resume"]
    assert p["why"] == "hold_expired:not_confirmed_live"


# ------------------------------------------- pure: pause, size, budget, rank
def test_a_fast_move_starts_a_pause_and_a_calm_book_does_not():
    calm = [(0.0, D("0.50")), (10.0, D("0.51")), (20.0, D("0.50"))]
    fast = calm + [(25.0, D("0.54"))]
    kw = dict(window_s=30, move_usd=D("0.03"), calm_s=60)
    assert PH.pause_until(calm, 20.0, **kw) == 0.0
    assert PH.pause_until(fast, 25.0, **kw) == 85.0
    # an old move outside the window does not extend the pause
    assert PH.pause_until(fast, 70.0, prev_until=85.0, **kw) == 85.0
    # off by default
    assert PH.pause_until(fast, 25.0, window_s=30, move_usd=0, calm_s=60) == 0


def test_the_in_game_size_halves_while_the_price_is_jumpy():
    assert PH.live_qty(4, 1, D("0.01"), D("0.02")) == 4
    assert PH.live_qty(4, 1, D("0.02"), D("0.02")) == 2
    assert PH.live_qty(5, 1, D("0.05"), D("0.02")) == 2
    assert PH.live_qty(2, 2, D("0.05"), D("0.02")) == 2     # venue minimum
    assert PH.live_qty(1, 1, D("0.05"), D("0.02")) == 1
    assert PH.live_qty(4, 1, D("0.05"), 0) == 4             # rule off


def test_the_budget_counts_this_order_on_top_of_everything_at_risk():
    assert PH.at_risk_allows("40", "9.99", "50")
    assert PH.at_risk_allows("40", "10", "50")
    assert not PH.at_risk_allows("40", "10.01", "50")
    assert not PH.at_risk_allows(D("Infinity"), "1", "50")   # unknown: no
    assert PH.at_risk_allows("9999", "1", "0")               # not configured


def test_a_reward_never_makes_a_market_without_a_fair_value_a_candidate():
    o = PH.opportunity(edge_yes=None, edge_no=None,
                       reward_usd_per_contract_hour=5, horizon_h=1,
                       reward_pays=True)
    assert o["net"] is None and o["why"] == "no_fair"


def test_ranking_number_is_trading_result_plus_the_payable_reward():
    base = dict(edge_yes=D("-0.01"), edge_no=D("0.004"),
                reward_usd_per_contract_hour=D("0.02"), horizon_h=2,
                reward_haircut=D("0.5"))
    # the better side counts; an unpayable reward counts for nothing
    assert PH.opportunity(**base, reward_pays=False)["net"] == D("0.004")
    o = PH.opportunity(**base, reward_pays=True)
    assert o["trade"] == D("0.004") and o["reward"] == D("0.020")
    assert o["net"] == D("0.024")


def test_rank_puts_the_best_net_first_and_unpriced_markets_last():
    rows = [("a", {"net": D("0.001")}), ("b", {"net": None}),
            ("c", {"net": D("0.02")}), ("d", {"net": D("-0.01")})]
    assert [s for s, _o in PH.rank(rows)] == ["c", "a", "d", "b"]


# ---------------------------------------------------------------- engine
@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("200"))
    monkeypatch.setattr(M, "SLATE", "both")
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    monkeypatch.delenv(M.STALL_DRILL_ENV, raising=False)
    monkeypatch.setitem(M._LOOP, "beat", None)
    monkeypatch.setitem(M._LOOP, "thread", None)
    monkeypatch.setitem(M._LOOP, "max_gap", 0.0)
    monkeypatch.setattr(M, "RN1_FAIR_STABLE_S", 0.0)
    # room for a 10-contract quote; the caps themselves have their own tests
    for name, val in (("RESIDUAL_CAP_USD", "20"), ("PER_MARKET_USD", "40"),
                      ("TOTAL_SPEND_USD", "200"),
                      ("TOTAL_UNMATCHED_USD", "100")):
        monkeypatch.setattr(R, name, D(val))
    monkeypatch.setattr(M, "RN1_PREGAME_QTY", D("10"))
    monkeypatch.setattr(M, "RN1_LIVE_QTY", D("4"))
    v = FakeVenue()
    v.states = {}
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e.guard = Guard(0.49)
    return e, v


def resting(v):
    return sorted((o["side"], str(o["px"]), str(o["qty"]))
                  for o in v.orders.values() if o["open"])


def logged(capsys, name):
    pre = "PMUS_TWO_SIDED "
    rows = [json.loads(l[len(pre):])
            for l in capsys.readouterr().out.splitlines()
            if l.startswith(pre)]
    return [r for r in rows if r.get("kind") == name]


@pytest.mark.rn1_edge_gate
def test_pregame_quotes_rest_with_the_pregame_size(eng):
    e, v = eng
    e.b.m(SLUG)["game_start"] = time.time() + 3600
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "10"), ("YES", "0.47", "10")]


@pytest.mark.rn1_edge_gate
def test_a_market_from_the_live_slate_uses_the_in_game_size(eng):
    e, v = eng
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]


@pytest.mark.rn1_edge_gate
def test_every_order_is_cancelled_before_the_start_completions_too(
        eng, capsys):
    e, v = eng
    mk = e.b.m(SLUG)
    mk["game_start"] = time.time() + 3600
    mk["pos"]["YES"], mk["cost"]["YES"] = D("4"), D("1.60")   # unpaired
    e.step_market(SLUG)
    assert resting(v)                                          # quoting
    mk["game_start"] = time.time() + M.KICKOFF_PULL_S - 1
    e.step_market(SLUG)
    assert resting(v) == []
    assert mk["kickoff"]["hold_since"]
    hold = logged(capsys, "KICKOFF_HOLD")
    assert hold and hold[-1]["why"] == "before_start"
    # nothing is placed while the hold lasts
    e.step_market(SLUG)
    assert resting(v) == []


@pytest.mark.rn1_edge_gate
def test_quoting_resumes_in_game_only_after_the_recheck(eng, capsys):
    e, v = eng
    mk = e.b.m(SLUG)
    mk["game_start"] = time.time() - M.KICKOFF_RESUME_MIN_S - 5
    e.step_market(SLUG)                      # venue does not list it as live
    assert resting(v) == [] and not (mk.get("kickoff") or {}).get(
        "resumed_at")
    v.states = {SLUG: {"type": "nba"}}
    e.guard.mid = None                       # live, but no fair value yet
    e.step_market(SLUG)
    assert resting(v) == []
    e.guard.mid = 0.49
    e.step_market(SLUG)
    assert mk["kickoff"]["resumed_at"]
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]
    assert logged(capsys, "KICKOFF_RESUME")


@pytest.mark.rn1_edge_gate
def test_minimum_hold_after_the_start_even_when_everything_checks_out(eng):
    e, v = eng
    mk = e.b.m(SLUG)
    mk["game_start"] = time.time() - 5
    v.states = {SLUG: {"type": "nba"}}
    e.step_market(SLUG)
    assert resting(v) == [] and not mk["kickoff"].get("resumed_at")


@pytest.mark.rn1_edge_gate
def test_an_unconfirmed_start_ends_in_no_new_exposure(eng, capsys):
    e, v = eng
    mk = e.b.m(SLUG)
    mk["game_start"] = time.time() - M.KICKOFF_MAX_HOLD_S - 1
    e.step_market(SLUG)
    assert resting(v) == []
    rows = logged(capsys, "RN1_QUOTE")
    assert rows and rows[-1]["block"] == "kickoff_unconfirmed"


@pytest.mark.rn1_edge_gate
def test_a_fast_move_pulls_new_bids_and_calm_brings_them_back(
        eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "VOL_PAUSE_USD", D("0.03"))
    mk = e.b.m(SLUG)
    e.step_market(SLUG)
    assert len(resting(v)) == 2
    v.bid, v.ask = D("0.52"), D("0.54")      # four cents inside the window
    e.guard.mid = 0.53
    e.step_market(SLUG)
    assert resting(v) == []
    assert mk["vol_pause_until"] > time.time()
    assert logged(capsys, "VOL_PAUSE")
    # calm: the pause has run out and the move has left the window
    mk["vol_pause_until"] = time.time() - 1
    e._vol_hist[SLUG][:] = [(time.time() - 1, D("0.53"))]
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.45", "4"), ("YES", "0.51", "4")]


@pytest.mark.rn1_edge_gate
def test_a_completion_keeps_resting_through_a_pause(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "VOL_PAUSE_USD", D("0.03"))
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("4"), D("1.60")   # YES at 0.40
    mk["vol_pause_until"] = time.time() + 60
    e.step_market(SLUG)
    got = resting(v)
    assert [s for s, _p, _q in got] == ["NO"]                 # completion
    oid = next(k for k, o in mk["orders"].items() if o["open"])
    assert mk["orders"][oid]["stage"] == "two_sided_complete"


@pytest.mark.rn1_edge_gate
def test_the_in_game_size_shrinks_when_the_price_is_jumpy(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "VOL_SHRINK_USD", D("0.02"))
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert {q for _s, _p, q in resting(v)} == {"4"}
    v.bid, v.ask = D("0.50"), D("0.52")      # two cents: shrink, no pause
    e.guard.mid = 0.51
    e.step_market(SLUG)
    assert {q for _s, _p, q in resting(v)} == {"2"}


@pytest.mark.rn1_edge_gate
def test_resting_orders_count_against_the_portfolio_budget(
        eng, monkeypatch, capsys):
    e, v = eng
    # YES 4 x 0.47 = 1.88 fits; NO 4 x 0.49 = 1.96 more does not
    monkeypatch.setattr(M, "AT_RISK_BUDGET_USD", D("2.50"))
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert resting(v) == [("YES", "0.47", "4")]
    assert e.own_exposure_usd() == D("1.88")
    ref = logged(capsys, "place_refused_at_risk_budget")
    assert len(ref) == 1 and ref[0]["side"] == "NO"
    e.step_market(SLUG)                       # refused again, logged once
    assert not logged(capsys, "place_refused_at_risk_budget")


@pytest.mark.rn1_edge_gate
def test_the_budget_spans_markets(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "AT_RISK_BUDGET_USD", D("4.00"))
    monkeypatch.setattr(M, "MAX_MARKETS", 5)
    e.b.m(SLUG)
    e.b.m("aec-y")
    e.step_market(SLUG)                       # 1.88 + 1.96 = 3.84
    assert len(resting(v)) == 2
    e.step_market("aec-y")                    # nothing more fits
    assert len(resting(v)) == 2
    assert e.own_exposure_usd() <= D("4.00")


@pytest.mark.rn1_edge_gate
def test_a_completion_is_not_refused_by_the_budget(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "AT_RISK_BUDGET_USD", D("0.50"))
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("4"), D("1.60")
    e.step_market(SLUG)
    assert [s for s, _p, _q in resting(v)] == ["NO"]


def test_the_clip_the_latch_checks_is_the_largest_phase_size(eng):
    assert M.rn1_max_clip() == D("10")


def test_unset_phase_sizes_are_the_base_size(monkeypatch):
    monkeypatch.setattr(M, "RN1_PREGAME_QTY", None)
    monkeypatch.setattr(M, "RN1_LIVE_QTY", None)
    monkeypatch.setattr(R, "QTY", D("3"))
    assert M.rn1_pregame_qty() == M.rn1_live_qty() == M.rn1_max_clip() == 3


def test_both_slates_feed_one_selection_and_a_market_is_listed_once(eng):
    e, v = eng
    start = time.time() + 3600
    v.pregame_sports = lambda: [
        {"slug": "aec-pre", "game_start": start,
         "market_meta": {"bestBidQuote": "0.48", "bestAskQuote": "0.50"}},
        {"slug": "aec-x", "game_start": start,
         "market_meta": {"bestBidQuote": "0.44", "bestAskQuote": "0.46"}}]
    got = [t["slug"] for t in e.slate_targets()]
    assert got.count("aec-x") == 1 and "aec-pre" in got
    assert got.index("aec-x") < got.index("aec-pre")     # live first


def test_the_default_slate_is_the_live_slate(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "SLATE", "live")
    v.pregame_sports = lambda: (_ for _ in ()).throw(AssertionError("read"))
    assert [t["slug"] for t in e.slate_targets()] == \
        [t["slug"] for t in v.live_sports()]


def test_which_reward_period_can_pay_follows_the_phase(eng, monkeypatch):
    e, v = eng
    mk = e.b.m(SLUG)
    assert e.event_live(SLUG) is True              # live slate, no start time
    mk["game_start"] = time.time() + 3600
    assert e.event_live(SLUG) is False             # not started
    v.states = {SLUG: {"type": "nba"}}
    assert e.event_live(SLUG) is True              # venue lists it as live
    v.states = {}
    mk["game_start"] = time.time() - 1
    assert e.event_live(SLUG) is True              # start has passed
    monkeypatch.setattr(M, "SLATE", "live")
    mk["game_start"] = time.time() + 3600
    assert e.event_live(SLUG) is True              # earlier behaviour kept


def test_net_ranking_reorders_candidates_and_prices_a_few_per_round(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RANK_NEW_PER_ROUND", 2)
    nets = {"a": D("0.001"), "b": D("0.02"), "c": D("0.5")}
    seen = []

    def opp(slug, _r):
        seen.append(slug)
        return {"net": nets[slug], "trade": nets[slug], "reward": D(0),
                "why": None}
    monkeypatch.setattr(e, "_opportunity", opp)
    scored = [(D("0.02"), D("0.01"), s) for s in ("a", "b", "c")]
    got = [r[2] for r in e.rank_net(scored)]
    assert seen == ["a", "b"] and got == ["b", "a", "c"]   # c not priced yet
    got = [r[2] for r in e.rank_net(scored)]
    assert seen == ["a", "b", "c"] and got == ["c", "b", "a"]


@pytest.mark.rn1_edge_gate
def test_the_ranking_number_without_a_programme_is_the_trading_result(eng):
    from src import pmus_sports_rewards as RW
    e, v = eng
    o = e._opportunity(SLUG, RW)
    want = max(D(R.edge(side=s, px=p, tick=D("0.01"), fair_yes=D("0.49"),
                        adds_exposure=True)["net_edge"])
               for s, p in (("YES", D("0.48")), ("NO", D("0.50"))))
    assert o["net"] == want and o["reward"] == 0
    e.guard.mid = None
    assert e._opportunity(SLUG, RW)["net"] is None


def test_the_pregame_period_pays_before_the_start_and_the_live_one_after(
        eng):
    from datetime import datetime, timezone
    e, v = eng
    start = time.time() + 3600
    per = dict(programId="p", df=0.35, target=10000.0, maxSpread=None,
               status="active", start="2026-01-01T00:00:00Z")
    prog = {"eventStartTime": datetime.fromtimestamp(
                start, timezone.utc).isoformat(),
            "periods": [dict(per, period="day_of", pool=180.0),
                        dict(per, period="live", pool=1440.0)]}
    e._prog[SLUG] = (time.monotonic(), prog)
    e.b.m(SLUG)["game_start"] = start
    assert e.rn1_reward(SLUG)["period"] == "day_of"
    assert e.reward_programs([SLUG])[SLUG]["period"] == "day_of"
    v.states = {SLUG: {"type": "soccer"}}          # the game is on
    assert e.rn1_reward(SLUG)["period"] == "live"
    assert e.reward_programs([SLUG])[SLUG]["period"] == "live"


def test_an_unset_slate_follows_the_older_pregame_switch(monkeypatch):
    monkeypatch.setattr(M, "SLATE", "")
    monkeypatch.setattr(M, "PREGAME", False)
    assert M.slate() == "live"
    monkeypatch.setattr(M, "PREGAME", True)
    assert M.slate() == "pregame"
    monkeypatch.setattr(M, "SLATE", "everything")      # unknown value
    assert M.slate() == "pregame"
    monkeypatch.setattr(M, "SLATE", "both")
    assert M.slate() == "both"


@pytest.mark.rn1_edge_gate
def test_candidates_are_followed_then_priced_then_selected(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RANK_WATCH_MAX", 2)
    scored = [(D("0.02"), D("0.01"), s) for s in ("aec-a", "aec-b", "aec-c")]
    e.guard.mid = None                     # the feed has nothing yet
    assert e.rank_net(scored) == []        # nothing can bid: nothing chosen
    assert e.guard_tracked() == ["aec-a", "aec-b"]      # but two are followed
    e.b.m(SLUG)
    assert e.guard_tracked() == [SLUG, "aec-a", "aec-b"]
    # a "no fair value" answer is retried soon, not kept for the long period
    e.guard.mid = 0.49
    for slug in list(e._rank_cache):
        t, o = e._rank_cache[slug]
        e._rank_cache[slug] = (t - M.RANK_NO_FAIR_TTL_S - 1, o)
    got = [r[2] for r in e.rank_net(scored)]
    assert got and set(got) <= {"aec-a", "aec-b", "aec-c"}


# ------------------------------------------------- hysteresis for a resting bid
def _q(bid, ask, fair, **c):
    return R.quotes(yes_bid=bid, yes_ask=ask, tick=D("0.01"),
                    pos={"YES": 0, "NO": 0}, cost={"YES": 0, "NO": 0},
                    last_fill_t={}, now=1000.0, min_qty=D("1"),
                    c={"edge_gate": True, "fair_yes": D(str(fair)), **c})


def test_a_new_bid_always_needs_the_full_test():
    # fair 0.475, YES bid would rest at 0.47: edge 0.005 - 0.01 < 0
    out = _q("0.48", "0.49", "0.475", keep_tol_usd=D("0.005"))
    assert out["YES"] is None and out["why_YES"] == "edge_negative"


def test_a_resting_bid_is_kept_within_the_tolerance_and_not_beyond():
    kw = dict(keep_px={"YES": D("0.47")}, keep_tol_usd=D("0.005"))
    out = _q("0.48", "0.49", "0.475", **kw)       # misses by half a cent
    assert out["YES"]["px"] == D("0.47") and out["YES"]["kept"] is True
    assert out["edge"]["YES"]["kept"] and \
        out["edge"]["YES"]["missed"] == "edge_negative"
    out = _q("0.48", "0.49", "0.474", **kw)       # misses by more
    assert out["YES"] is None and out["why_YES"] == "edge_negative"


def test_a_bid_that_is_not_worth_more_than_it_costs_is_never_kept():
    out = _q("0.48", "0.49", "0.47", keep_px={"YES": D("0.47")},
             keep_tol_usd=D("0.05"))              # fair == price: no value
    assert out["YES"] is None


def test_no_fair_value_now_means_no_bid_is_kept():
    out = R.quotes(yes_bid="0.48", yes_ask="0.49", tick=D("0.01"),
                   pos={"YES": 0, "NO": 0}, cost={"YES": 0, "NO": 0},
                   last_fill_t={}, now=1000.0, min_qty=D("1"),
                   c={"edge_gate": True, "fair_yes": None,
                      "keep_px": {"YES": D("0.47")},
                      "keep_tol_usd": D("0.005")})
    assert out["YES"] is None and out["why_YES"] == "no_fair"


def test_tolerance_off_by_default_pulls_at_once():
    out = _q("0.48", "0.49", "0.475", keep_px={"YES": D("0.47")})
    assert out["YES"] is None


@pytest.mark.rn1_edge_gate
def test_engine_keeps_a_resting_bid_through_a_half_cent_wobble(
        eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "RN1_KEEP_TOL_USD", D("0.005"))
    mk = e.b.m(SLUG)
    e.step_market(SLUG)                      # fair 0.49: YES 0.47, NO 0.49
    before = {k for k, o in v.orders.items() if o["open"]}
    assert len(before) == 2
    e.guard.mid = 0.475                      # YES now misses by half a cent
    e.step_market(SLUG)
    assert {k for k, o in v.orders.items() if o["open"]} == before
    assert "YES" in mk["keep_since"]
    e.guard.mid = 0.49                       # back: the clock is cleared
    e.step_market(SLUG)
    assert "YES" not in mk["keep_since"]
    assert {k for k, o in v.orders.items() if o["open"]} == before


@pytest.mark.rn1_edge_gate
def test_engine_pulls_a_kept_bid_when_the_miss_lasts_or_grows(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_KEEP_TOL_USD", D("0.005"))
    mk = e.b.m(SLUG)
    e.step_market(SLUG)
    e.guard.mid = 0.475
    e.step_market(SLUG)
    assert ("YES", "0.47", "4") in resting(v)
    mk["keep_since"]["YES"] = time.time() - M.RN1_KEEP_MAX_S - 1   # too long
    e.step_market(SLUG)
    assert ("YES", "0.47", "4") not in resting(v)
    # and a miss beyond the tolerance pulls at once
    e.guard.mid = 0.49
    mk["keep_since"].clear()
    e.step_market(SLUG)
    assert ("YES", "0.47", "4") in resting(v)
    e.guard.mid = 0.47
    e.step_market(SLUG)
    assert ("YES", "0.47", "4") not in resting(v)


@pytest.mark.rn1_edge_gate
def test_nothing_is_kept_through_a_block_or_a_stale_fair_value(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_KEEP_TOL_USD", D("0.005"))
    mk = e.b.m(SLUG)
    e.step_market(SLUG)
    e.guard.mid = None                       # the fair value is gone
    e.step_market(SLUG)
    assert resting(v) == []
    e.guard.mid = 0.49
    e.step_market(SLUG)
    assert len(resting(v)) == 2
    e.guard.mid = 0.475
    mk["vol_pause_until"] = time.time() + 60  # a pause beats the tolerance
    monkeypatch.setattr(M, "VOL_PAUSE_USD", D("0.03"))
    e.step_market(SLUG)
    assert resting(v) == []


def test_tolerance_off_changes_nothing_even_for_a_lower_resting_price():
    # resting a tick lower, where the full test would pass: with the rule
    # off the decision is what it always was (the bid is pulled)
    out = _q("0.48", "0.49", "0.475", keep_px={"YES": D("0.46")})
    assert out["YES"] is None and out["why_YES"] == "edge_negative"


def test_a_resting_price_that_passes_on_its_own_is_kept_off_the_clock():
    out = _q("0.48", "0.49", "0.475", keep_px={"YES": D("0.46")},
             keep_tol_usd=D("0.005"))
    assert out["YES"]["px"] == D("0.46") and "kept" not in out["YES"]
    assert out["YES"]["why"] == "resting_price_passes"


def test_the_tolerance_applies_to_the_net_after_the_inventory_charge():
    # 4 YES unpaired at 0.40 (two clips): adding costs a 2c risk charge.
    # fair 0.48, resting add at 0.47: trade edge 0, net -0.02 -> not kept
    out = R.quotes(yes_bid="0.48", yes_ask="0.49", tick=D("0.01"),
                   pos={"YES": 4, "NO": 0}, cost={"YES": "1.60", "NO": 0},
                   last_fill_t={}, now=1000.0, min_qty=D("1"),
                   c={"edge_gate": True, "fair_yes": D("0.48"), "qty": 2,
                      "extend_max_up": D("0.20"),
                      "keep_px": {"YES": D("0.47")},
                      "keep_tol_usd": D("0.005")})
    assert out["edge"]["YES"]["why"] == "edge_below_risk"
    assert out["YES"] is None


# ------------------------------------------ measured separately by phase
@pytest.mark.rn1_edge_gate
def test_orders_fills_and_resting_time_are_counted_by_phase(eng, capsys):
    e, v = eng
    mk = e.b.m(SLUG)
    mk["game_start"] = time.time() + 3600
    e.step_market(SLUG)                              # two pre-game bids of 10
    assert {o.get("phase") for o in mk["orders"].values()} == {"pregame"}
    assert mk["phase_stats"]["pregame"]["orders"] == "2"
    mk["phase_t"] = time.time() - 10                 # ten seconds resting
    e.step_market(SLUG)
    rest = D(mk["phase_stats"]["pregame"]["resting_contract_s"])
    assert D("195") <= rest <= D("205")              # 20 contracts x 10 s
    assert "qualifying_contract_s" not in mk["phase_stats"]["pregame"]
    # a fill is counted in the phase its order was placed in
    oid = next(k for k, o in mk["orders"].items() if o["side"] == "YES")
    v.orders[oid]["filled"], v.orders[oid]["open"] = D("10"), False
    e.step_market(SLUG)
    st = mk["phase_stats"]["pregame"]
    assert st["fills"] == "1" and st["filled_contracts"] == "10"
    fills = logged(capsys, "FILL")
    assert fills and fills[-1]["phase"] == "pregame"
    assert mk["markouts"][-1]["phase"] == "pregame"
    assert e.phase_summary()["pregame"]["fills"] == "1"


@pytest.mark.rn1_edge_gate
def test_resting_time_counts_as_qualifying_only_with_a_reward_share(eng):
    e, v = eng
    mk = e.b.m(SLUG)
    e.step_market(SLUG)                              # live, two bids of 4
    mk["rn1_reward_est"] = {"t": time.time(), "share": 0.01,
                            "est_usd_per_hour": 0.05,
                            "legs": mk.get("rn1_reward_est", {}).get("legs")}
    mk["phase_t"] = time.time() - 5
    e.step_market(SLUG)
    st = mk["phase_stats"]["live"]
    assert D("38") <= D(st["qualifying_contract_s"]) <= D("42")   # 8 x 5 s
    assert st["qualifying_contract_s"] == st["resting_contract_s"]


@pytest.mark.rn1_edge_gate
def test_a_long_gap_is_not_counted_as_resting_time(eng):
    e, v = eng
    mk = e.b.m(SLUG)
    e.step_market(SLUG)
    mk["phase_t"] = time.time() - 600                # the bot was not watching
    e.step_market(SLUG)
    assert "resting_contract_s" not in mk["phase_stats"]["live"]


# ------------------------------------------- exact markets and sides (pin)
def test_no_pin_by_default(monkeypatch):
    monkeypatch.delenv("PMUS_RN1_PIN", raising=False)
    assert M.rn1_pin() is None


@pytest.mark.parametrize("raw, want", [
    ("aec-x:YES", {"aec-x": "YES"}),
    (" aec-x:no , atc-y-draw ", {"aec-x": "NO", "atc-y-draw": None}),
    ("aec-x:MAYBE", {}), ("aec-x:", {}), ("btc-x:YES", {}),
    ("aec-x:YES,aec-x:NO", {}),
])
def test_pin_parsing_fails_closed(raw, want):
    assert M.parse_pin(raw) == want


@pytest.mark.rn1_edge_gate
def test_a_pinned_side_is_the_only_side_bid(eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setenv("PMUS_RN1_PIN", SLUG + ":YES")
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert [(s, p) for s, p, _q in resting(v)] == [("YES", "0.47")]
    q = logged(capsys, "RN1_QUOTE")[-1]
    assert q["why"]["NO"] == "pinned_other_side"
    monkeypatch.setenv("PMUS_RN1_PIN", SLUG + ":NO")
    e.step_market(SLUG)
    assert [(s, p) for s, p, _q in resting(v)] == [("NO", "0.49")]


@pytest.mark.rn1_edge_gate
def test_a_pin_without_a_side_allows_both_sides_of_that_market(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setenv("PMUS_RN1_PIN", SLUG)
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert len(resting(v)) == 2


@pytest.mark.rn1_edge_gate
def test_a_market_that_is_not_pinned_gets_no_new_exposure(
        eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setenv("PMUS_RN1_PIN", "aec-other:YES")
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert resting(v) == []
    assert logged(capsys, "RN1_QUOTE")[-1]["block"] == "not_pinned"


@pytest.mark.rn1_edge_gate
def test_a_malformed_pin_opens_nothing(eng, monkeypatch):
    e, v = eng
    monkeypatch.setenv("PMUS_RN1_PIN", SLUG + ":BOTH")
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert resting(v) == []


@pytest.mark.rn1_edge_gate
def test_the_pin_never_drops_a_completion(eng, monkeypatch):
    e, v = eng
    monkeypatch.setenv("PMUS_RN1_PIN", SLUG + ":YES")
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("4"), D("1.60")   # YES held
    e.step_market(SLUG)
    stages = {o["side"]: mk["orders"][k]["stage"]
              for k, o in v.orders.items() if o["open"]}
    assert stages.get("NO") == "two_sided_complete"


def test_selection_considers_only_pinned_markets(eng, monkeypatch):
    e, v = eng
    e.guard = None
    monkeypatch.setattr(R, "EDGE_GATE", False)
    monkeypatch.setattr(M, "SLATE", "live")
    monkeypatch.setenv("PMUS_RN1_PIN", "aec-owner:YES")
    e.select(v.account())
    assert list(e.active()) == ["aec-owner"]
    monkeypatch.setenv("PMUS_RN1_PIN", "aec-nowhere:YES")
    for s in list(e.b.d["markets"]):
        e.b.d["markets"][s]["done"] = True
    e.select(v.account())
    assert list(e.active()) == []


# ------------------------- a pinned market that is not selected says why
def _pin_reasons(capsys):
    rows = logged(capsys, "PIN_NOT_CANDIDATE")
    return rows[-1]["reasons"] if rows else None


def _select_pinned(eng, monkeypatch, slug="aec-owner"):
    e, v = eng
    e.guard = None
    monkeypatch.setattr(R, "EDGE_GATE", False)
    monkeypatch.setattr(M, "SLATE", "live")
    monkeypatch.setenv("PMUS_RN1_PIN", slug + ":YES")
    return e, v


def test_a_pinned_market_outside_the_band_says_so(eng, monkeypatch, capsys):
    e, v = _select_pinned(eng, monkeypatch)
    monkeypatch.setattr(M, "BAND_LO", D("0.55"))      # aec-owner bids 0.50
    e.select(v.account())
    assert list(e.active()) == []
    why = _pin_reasons(capsys)
    assert set(why) == {"aec-owner"}
    assert why["aec-owner"].startswith("bid_outside_band:0.5")
    assert why["aec-owner"].endswith("not in 0.55..0.75")
    e.select(v.account())                             # logged once
    assert _pin_reasons(capsys) is None


def test_a_pinned_market_missing_from_the_slate_says_so(eng, monkeypatch,
                                                        capsys):
    e, v = _select_pinned(eng, monkeypatch, slug="aec-nowhere")
    e.select(v.account())
    assert _pin_reasons(capsys) == {"aec-nowhere": "not_in_slate:live"}


def test_a_pinned_market_in_an_unpermitted_competition_says_so(
        eng, monkeypatch, capsys):
    e, v = _select_pinned(eng, monkeypatch)
    monkeypatch.setattr(M, "OPEN_EV_GATE", True)
    monkeypatch.setattr(M, "OPEN_GATE_ALLOW_SPORTS", frozenset({"nfl"}))
    monkeypatch.setattr(e, "open_gate", lambda slug: {"allow": False})
    e.select(v.account())
    assert _pin_reasons(capsys) == {"aec-owner": "competition_not_permitted"}


def test_a_pinned_market_that_is_selected_logs_no_reason(eng, monkeypatch,
                                                        capsys):
    e, v = _select_pinned(eng, monkeypatch)
    e.select(v.account())
    assert list(e.active()) == ["aec-owner"]
    assert _pin_reasons(capsys) is None


def test_without_a_pin_no_reason_is_logged(eng, monkeypatch, capsys):
    e, v = eng
    e.guard = None
    monkeypatch.setattr(R, "EDGE_GATE", False)
    monkeypatch.setattr(M, "SLATE", "live")
    monkeypatch.delenv("PMUS_RN1_PIN", raising=False)
    monkeypatch.setattr(M, "BAND_LO", D("0.99"))
    e.select(v.account())
    assert _pin_reasons(capsys) is None


# ------------------------------------------- ranking by our reward share
def _share_rows(*slugs):
    return [(D("0.02"), D("0.01"), s) for s in slugs]


def test_share_ranking_puts_our_best_reward_per_dollar_first(
        eng, monkeypatch):
    """Pool size alone does not decide: a small pool where our one quote
    takes a large share ranks above a large pool where it takes nothing."""
    e, _v = eng
    monkeypatch.setattr(M, "RANK_SHARE_NEW_PER_ROUND", 10)
    rw = {"aec-atp-a-b-2026-11-25": {"pool_rate_usd_s": 0.017},
          "aec-utr-c-d-2026-11-25": {"pool_rate_usd_s": 0.004},
          "aec-nhl-e-f-2026-11-25": {"pool_rate_usd_s": 0.097}}
    per = {"aec-atp-a-b-2026-11-25": 0.003, "aec-utr-c-d-2026-11-25": 0.4,
           "aec-nhl-e-f-2026-11-25": 0.0}
    monkeypatch.setattr(e, "_reward_share_score", lambda s, _p: {
        "per_dollar_hour": per[s], "usd_per_hour": per[s],
        "capital_usd": "1"})
    scored = _share_rows("aec-nhl-e-f-2026-11-25", "aec-atp-a-b-2026-11-25",
                         "aec-zzz-no-prog-2026-11-25",
                         "aec-utr-c-d-2026-11-25")
    got = [r[2] for r in e.rank_by_reward_share(scored, rw)]
    assert got == ["aec-utr-c-d-2026-11-25", "aec-atp-a-b-2026-11-25",
                   "aec-nhl-e-f-2026-11-25", "aec-zzz-no-prog-2026-11-25"]
    assert len(got) == len(scored)                 # nothing is dropped


def test_share_ranking_reads_a_few_books_per_round_across_competitions(
        eng, monkeypatch):
    e, _v = eng
    monkeypatch.setattr(M, "RANK_SHARE_NEW_PER_ROUND", 2)
    slugs = ["aec-atp-a-b-2026-11-25", "aec-atp-c-d-2026-11-25",
             "aec-atp-e-f-2026-11-25", "aec-utr-g-h-2026-11-25"]
    rw = {s: {"pool_rate_usd_s": 0.01} for s in slugs}
    seen = []

    def score(s, _p):
        seen.append(s)
        return {"per_dollar_hour": 0.1}
    monkeypatch.setattr(e, "_reward_share_score", score)
    e.rank_by_reward_share(_share_rows(*slugs), rw)
    # one from each competition, not two from the deepest one
    assert seen == ["aec-atp-a-b-2026-11-25", "aec-utr-g-h-2026-11-25"]
    e.rank_by_reward_share(_share_rows(*slugs), rw)
    assert sorted(seen) == sorted(slugs)
    e.rank_by_reward_share(_share_rows(*slugs), rw)
    assert len(seen) == 4                          # cached: no new reads


def test_share_ranking_survives_an_unreadable_book(eng, monkeypatch):
    e, _v = eng
    rw = {"aec-atp-a-b-2026-11-25": {"pool_rate_usd_s": 0.01},
          "aec-utr-c-d-2026-11-25": {"pool_rate_usd_s": 0.02}}

    def score(s, _p):
        if "atp" in s:
            raise RuntimeError("boom")
        return {"per_dollar_hour": 0.2}
    monkeypatch.setattr(e, "_reward_share_score", score)
    got = [r[2] for r in e.rank_by_reward_share(
        _share_rows("aec-atp-a-b-2026-11-25", "aec-utr-c-d-2026-11-25"), rw)]
    assert got == ["aec-utr-c-d-2026-11-25", "aec-atp-a-b-2026-11-25"]


def test_share_score_uses_our_size_one_tick_behind_and_public_book(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(R, "DEPTH_TICKS", 1)
    monkeypatch.setattr(M, "rn1_max_clip", lambda: D("1"))
    monkeypatch.setattr(v, "spec", lambda s: {"tick": D("0.01"),
                                              "min_qty": D("1")},
                        raising=False)
    paths = []

    def get(path):
        paths.append(path)
        return {"marketData": {
            "bids": [{"px": "0.50", "qty": "5"}, {"px": "0.49", "qty": "5"}],
            "offers": [{"px": "0.52", "qty": "5"},
                       {"px": "0.53", "qty": "5"}]}}
    monkeypatch.setattr(e, "_reward_get", lambda: get)
    # the venue scores only the levels needed to reach targetSize: with the
    # touch (5) short of the target (10) the level behind it still scores
    per = {"df": 0.5, "targetSize": 10, "pool_rate_usd_s": 0.01,
           "maxSpread": None}
    got = e._reward_share_score("aec-utr-c-d-2026-11-25", per)
    assert paths == ["/v1/markets/aec-utr-c-d-2026-11-25/book"]
    # YES bid 0.49 and NO bid 1 - 0.52 - 0.01 = 0.47: one dollar... 0.96
    assert got["capital_usd"] == "0.96"
    assert got["usd_per_hour"] > 0
    assert got["per_dollar_hour"] == pytest.approx(
        got["usd_per_hour"] / 0.96, rel=1e-3)
    # a touch that already meets the target leaves a bid one tick behind
    # it outside the scored levels: estimated at zero, ranked last
    deep = e._reward_share_score("aec-utr-c-d-2026-11-25",
                                 dict(per, targetSize=5))
    assert deep["usd_per_hour"] == 0 and deep["per_dollar_hour"] == 0
    # a one-sided book gives no estimate, never an error
    monkeypatch.setattr(e, "_reward_get", lambda: (lambda p: {
        "marketData": {"bids": [{"px": "0.50", "qty": "5"}]}}))
    assert e._reward_share_score("aec-utr-c-d-2026-11-25", per) == {
        "why": "no_two_sided_book"}


# ------------------------------------------------ joining the best bid
@pytest.mark.rn1_edge_gate
def test_join_is_off_by_default_bids_rest_one_tick_behind(eng):
    e, v = eng
    e.b.m(SLUG)
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]


@pytest.mark.rn1_edge_gate
def test_join_rests_at_the_touch_on_a_wide_calm_market(eng, monkeypatch):
    """bid 0.48 / ask 0.50 (two ticks), calm: YES joins 0.48, NO joins
    1 - 0.50 = 0.50; the pair-cost limit (0.97) then lowers the dearer
    side, so the pair still costs no more than the limit."""
    e, v = eng
    monkeypatch.setattr(M, "RN1_JOIN_TOUCH", True)
    e.b.m(SLUG)
    e.step_market(SLUG)
    got = {s: D(px) for s, px, _q in resting(v)}
    assert got["YES"] == D("0.48")                 # at the best bid
    assert got["NO"] <= D("0.50")
    assert got["YES"] + got["NO"] <= R.PAIR_CAP
    assert all(D(px) < (v.ask if s == "YES" else 1 - v.bid)
               for s, px, _q in resting(v))        # never crossing


@pytest.mark.rn1_edge_gate
def test_join_never_happens_on_a_one_tick_spread(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "RN1_JOIN_TOUCH", True)
    v.bid, v.ask = D("0.48"), D("0.49")
    mk = e.b.m(SLUG)
    assert e.rn1_join_touch(SLUG, mk, {"bid": v.bid, "ask": v.ask},
                            D("0.01"), D("0"), time.time()) \
        == "spread_too_tight"
    # even if the setting asks for one tick, two is the floor
    monkeypatch.setattr(M, "RN1_JOIN_MIN_SPREAD_TICKS", 1)
    assert e.rn1_join_touch(SLUG, mk, {"bid": v.bid, "ask": v.ask},
                            D("0.01"), D("0"), time.time()) \
        == "spread_too_tight"


@pytest.mark.rn1_edge_gate
def test_join_steps_back_when_the_price_moves_or_just_jumped(
        eng, monkeypatch, capsys):
    e, v = eng
    monkeypatch.setattr(M, "RN1_JOIN_TOUCH", True)
    mk = e.b.m(SLUG)
    bbo = {"bid": D("0.48"), "ask": D("0.51")}
    now = time.time()
    assert e.rn1_join_touch(SLUG, mk, bbo, D("0.01"), D("0"), now) is None
    assert e._rn1_joined[SLUG] is True
    assert e.rn1_join_touch(SLUG, mk, bbo, D("0.01"), D("0.02"), now) \
        == "price_moving"
    assert e._rn1_joined[SLUG] is False
    mk["jump_pause_until"] = now + M.JUMP_PAUSE_S - 5     # jumped 5 s ago
    assert e.rn1_join_touch(SLUG, mk, bbo, D("0.01"), D("0"), now) \
        == "recent_jump"
    mk["jump_pause_until"] = now - 600
    assert e.rn1_join_touch(SLUG, mk, bbo, D("0.01"), D("0"), now) is None
    rows = logged(capsys, "RN1_JOIN_TOUCH")
    assert [r["joined"] for r in rows] == [False, True]


def test_a_joined_bid_is_pulled_only_when_within_a_tick_of_crossing():
    legs = {"YES": D("0.48")}
    t = D("0.01")
    # at the touch: the legacy rule (depth 1) calls that too close
    assert M.adverse_reasons(legs, {"bid": D("0.48"), "ask": D("0.50")},
                             t, 1) == ["yes_closer_than_1t"]
    # joined on purpose (depth 0): left resting
    assert M.adverse_reasons(legs, {"bid": D("0.48"), "ask": D("0.50")},
                             t, 0) == []
    # the spread closes to one tick: pulled whatever the depth
    assert M.adverse_reasons(legs, {"bid": D("0.48"), "ask": D("0.49")},
                             t, 0) == ["yes_near_cross"]
