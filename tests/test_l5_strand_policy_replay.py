"""The strand-policy replay must stay anchored to what actually happened.

The single most important property: with no policy applied, the replay's
baseline must reproduce the REALIZED campaign net of +$0.1256 exactly. A
replay that cannot reproduce the past is not evidence about the future.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_strand_policy_replay as R


@pytest.fixture(scope="module")
def frames():
    return R.load_frames()


def test_baseline_reproduces_the_realized_campaign_net():
    """+$0.1256 is the settled figure; realized_today moved -11.6213 ->
    -11.49570000, a delta of exactly this."""
    assert sum(R.baseline_pnl(e) for e in R.EPISODES) == D("0.1256")


def test_the_strand_is_booked_as_a_total_loss():
    ep = [e for e in R.EPISODES if e["w"] == "0130z"][0]
    assert ep["complete_t"] is None
    assert R.baseline_pnl(ep) == -(D("1.87") * D("0.62")) == D("-1.1594")


def test_every_episode_has_frames_except_the_simultaneous_pair(frames):
    """0045z filled both legs in the same instant, so it never held a
    residual and no taker decision can arise there. Everything else must
    have book evidence, or the replay is quietly guessing."""
    for ep in R.EPISODES:
        n = len(R.episode_frames(frames, ep))
        if ep["held"] is None:
            assert n == 0, ep["w"]
        else:
            assert n > 0, ep["w"]


def test_side_semantics_match_the_engines_own_taker_walk(frames):
    """Holding DOWN we buy UP across the ASK, so own price is the wire price.
    Holding UP we buy DOWN across the BID, so own price is 1 - wire. If this
    inverts, every taker number is wrong."""
    checked = 0
    for f in frames:
        if not f.get("walk_vwap") or not f.get("asks") or not f.get("bids"):
            continue
        v = D(str(f["walk_vwap"]))
        q = D(str(f["walk_qty"]))
        mine = R.walk_for_qty(f, f["held"], q)
        assert mine is not None
        # our walk must reproduce the ENGINE's own vwap, which is the real
        # cross-check: same side, same rungs, same arithmetic
        assert abs(mine[0] - v) < D("0.000001"), (f["slug"], f["t"], v, mine)
        checked += 1
    assert checked > 100


def test_a_limit_order_does_not_chase_the_book(frames):
    """THE LATENCY FINDING. 0130z decides at 01:32:56 with a limit of own
    0.33; the next frame is 6.36 s later at own 0.34. The order must NOT
    fill -- if it does, the replay is assuming away the whole problem."""
    ep = [e for e in R.EPISODES if e["w"] == "0130z"][0]
    at_zero = R.replay_episode(frames, ep, 0.0, None, D("0.762"))
    assert at_zero["crossed"][0]["why"] == "FILLED"
    for lam in (0.5, 1.0, 2.0, 8.818):
        r = R.replay_episode(frames, ep, lam, None, D("0.762"))
        assert r["crossed"][0]["why"] == "BOOK_LEFT_THE_LIMIT", lam
        assert r["total"] == D("-1.1594"), lam


def test_taker_completion_is_net_negative_at_every_real_latency(frames):
    base = sum(R.baseline_pnl(e) for e in R.EPISODES)
    for lam in (0.5, 1.0, 2.0, 8.818):
        tot = D(0)
        for ep in R.EPISODES:
            if ep["held"] is None:
                tot += R.baseline_pnl(ep)
            else:
                tot += R.replay_episode(frames, ep, lam, None, D("0.762"))["total"]
        assert tot < base, (lam, tot, base)


def test_0145z_is_preempted_unless_a_short_horizon_protects_it(frames):
    """The mandatory counterexample. It completed as a maker at pair 0.87
    after 5m23s for +$0.2028. An unhorizoned rule crosses at 0.93 instead."""
    ep = [e for e in R.EPISODES if e["w"] == "0145z"][0]
    assert R.baseline_pnl(ep) == D("0.2028")
    loose = R.replay_episode(frames, ep, 1.0, None, D("0.762"))
    assert loose["crossed"] and loose["total"] < D("0.2028")
    for hor in (120.0, 60.0):
        tight = R.replay_episode(frames, ep, 1.0, hor, D("0.762"))
        assert tight["crossed"] == []
        assert tight["total"] == D("0.2028")


def test_the_pair_cap_is_respected_by_every_crossing(frames):
    for ep in R.EPISODES:
        if ep["held"] is None:
            continue
        for lam in (0.0, 1.0):
            for c in R.replay_episode(frames, ep, lam, None, D("0.762"))["crossed"]:
                assert ep["own"] + c["limit"] <= R.PAIR_CAP


def test_sizing_improves_at_every_budget_and_bounds_the_worst_case():
    """P2's shape is the point: no threshold cliff, and the worst case is
    the budget by construction rather than by luck."""
    base = sum(R.baseline_pnl(e) for e in R.EPISODES)
    for K in (D("0.50"), D("0.60"), D("0.70"), D("0.80"), D("0.90")):
        net = sum(R.baseline_pnl(e) * min(D(1), K / (e["q"] * e["own"]))
                  for e in R.EPISODES)
        assert net > base, K


def test_the_opening_price_cap_is_a_cliff_not_a_gradient():
    """Why P1 is NOT recommended: it is non-monotonic in its own threshold,
    and at 0.65 it is WORSE than doing nothing."""
    base = sum(R.baseline_pnl(e) for e in R.EPISODES)
    net = lambda th: sum(R.baseline_pnl(e) for e in R.EPISODES
                         if e["own"] <= th)
    assert net(D("0.60")) > base
    assert net(D("0.65")) < base
    assert net(D("0.80")) == base


def test_the_published_taker_fee_formula():
    assert R.taker_fee(D("1.87"), D("0.34")) == D("0.03")
    assert R.taker_fee(D("0.43"), D("0.20")) == D("0.00")


# ---------------------------------------- the approved cap, as implemented

def test_the_cap_sizes_on_the_dearer_leg_not_the_one_that_filled():
    """For three of eight episodes the dearer leg is NOT the one that
    filled, and the cap sizes on the dearer one because that is the leg
    whose solo fill is the strand. Sizing on the filled leg would have
    understated 0100z's exposure by half."""
    a = [e for e in R.EPISODES if e["w"] == "0100z" and e["own"] == D("0.20")][0]
    assert R.dearer_leg_own(a) == D("0.76") != a["own"]
    s = [e for e in R.EPISODES if e["w"] == "0130z"][0]
    assert R.dearer_leg_own(s) == D("0.62")


def test_the_cap_bounds_the_worst_single_leg_notional_everywhere():
    for b in (D("0.40"), D("0.60"), D("0.80"), D("1.00")):
        for ep in R.EPISODES:
            q = R.capped_qty(ep, b)
            if q is None:
                continue
            assert q * R.dearer_leg_own(ep) <= b, (ep["w"], b)


def test_uncapped_worst_case_was_larger_than_the_strand_that_happened():
    """$1.52 on 0100z (2.00 at own 0.76) against the $1.1594 that actually
    stranded -- the realized loss was not even the worst the book allowed."""
    worst = max(ep["q"] * R.dearer_leg_own(ep) for ep in R.EPISODES)
    assert worst == D("1.5200")
    assert worst > D("1.1594")


def test_the_cap_changes_no_completion_outcome():
    """Every episode still opens and all seven completions survive, which is
    the property the owner required: no profitable delayed completion lost."""
    for b in (D("0.40"), D("0.60"), D("0.80"), D("1.00")):
        rows = [R.capped_episode(e, b) for e in R.EPISODES]
        assert all(r["opened"] for r in rows), b
        assert sum(1 for r in rows if r["completed"]) == 7, b


def test_0145z_the_counterexample_survives_the_cap():
    ep = [e for e in R.EPISODES if e["w"] == "0145z"][0]
    r = R.capped_episode(ep, D("0.80"))
    assert r["opened"] and r["completed"]
    assert r["qty"] == D("1.53")                 # 1.56 -> 1.53, one grid step
    assert r["pnl"] > D("0.19")                  # 0.2028 -> 0.1989


def test_eighty_cents_is_the_optimum_on_this_campaign():
    base = sum(R.baseline_pnl(e) for e in R.EPISODES)
    nets = {b: sum(R.capped_episode(e, b)["pnl"] for e in R.EPISODES)
            for b in (D("0.40"), D("0.60"), D("0.80"), D("1.00"), D("1.20"))}
    assert nets[D("0.80")] == max(nets.values())
    assert nets[D("0.80")] > base
    assert nets[D("1.20")] < base                # too loose to help


def test_a_budget_below_one_grid_step_refuses_rather_than_sizing_to_zero():
    ep = [e for e in R.EPISODES if e["w"] == "0130z"][0]
    assert R.capped_qty(ep, D("0.001")) is None
    assert R.capped_episode(ep, D("0.001"))["opened"] is False


def test_quantities_stay_on_the_grid_and_only_shrink():
    for ep in R.EPISODES:
        q = R.capped_qty(ep, D("0.80"))
        assert q is not None
        assert q % D("0.01") == 0
        assert q <= ep["q"]
