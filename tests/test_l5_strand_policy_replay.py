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
