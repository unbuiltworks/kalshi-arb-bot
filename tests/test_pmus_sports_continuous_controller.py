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
