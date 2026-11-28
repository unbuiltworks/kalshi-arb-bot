"""Marginal net EV per bid (PMUS_RN1_MARGINAL_EV) and fitting every level
(PMUS_RN1_FIT_ALL_LEVELS), both off by default.

External review of 1b496656c: judge each opening / same-side addition by
expected trading value + incremental reward + rebates - fees - adverse
selection; do not reject an attractive bid only because its standalone
reward is zero, and do not let a positive reward carry a negative total;
rank capacity-constrained removals by that value, not by price; size every
level down to fit before dropping it; keep resting bids that pass on
their own through the market-level hold / release timers.
"""
import subprocess
import sys
import time
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_rewards as RW
from src import pmus_sports_two_sided as M
from tests.test_pmus_rn1_reward_aware import Guard
from tests.test_pmus_sports_continuous_controller import (  # noqa: F401
    SLUG, eng, resting)

T = D("0.01")


# ------------------------------------------------------------ defaults
def test_both_flags_are_off_by_default_from_a_clean_interpreter():
    code = ("import os;[os.environ.pop(k,None) for k in list(os.environ) "
            "if k.startswith('PMUS_')];"
            "from src import pmus_sports_two_sided as M;"
            "print(M.RN1_MARGINAL_EV, M.RN1_FIT_ALL_LEVELS)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout.split()
    assert out == ["False", "False"]


# ------------------------------------------------------------ pure
def test_zero_reward_does_not_reject_an_attractive_bid():
    r = R.marginal_ev(fair="0.50", px="0.47", qty=2, fill_rate=0.2,
                      gate_adverse="0.01", adverse="0.01",
                      reward_usd_per_hour=0.0)
    assert r["ok"] is True
    assert D(r["per_contract"]) == D("0.02")
    assert abs(r["ev_usd_per_hour"] - 0.2 * 2 * 0.02) < 1e-12


def test_a_positive_reward_cannot_rescue_a_negative_trade_edge():
    r = R.marginal_ev(fair="0.47", px="0.47", qty=2, fill_rate=0.2,
                      gate_adverse="0.01", adverse="0.01",
                      reward_usd_per_hour=100.0)
    assert r["ok"] is False and r["why"] == "trade_edge_negative"


def test_a_negative_total_is_refused_and_a_reward_may_cover_fill_loss():
    """Trade edge 2c at the gate's 1c allowance, but the measured fill loss
    is 3.5c: losing per fill. No reward -> refused; an incremental reward
    larger than the hourly fill loss -> accepted."""
    kw = dict(fair="0.50", px="0.47", qty=2, fill_rate=0.2,
              gate_adverse="0.01", adverse="0.035")
    lose = R.marginal_ev(**kw, reward_usd_per_hour=0.0)
    assert lose["ok"] is False and lose["why"] == "marginal_ev_not_positive"
    assert lose["ev_usd_per_hour"] < 0
    small = R.marginal_ev(**kw, reward_usd_per_hour=0.001)
    assert small["ok"] is False
    big = R.marginal_ev(**kw, reward_usd_per_hour=0.01)
    assert big["ok"] is True


def test_fees_risk_and_rebates_enter_per_contract():
    r = R.marginal_ev(fair="0.50", px="0.45", qty=1, fill_rate=1.0,
                      fee="0.004", gate_adverse="0.01", adverse="0.02",
                      risk="0.01", rebate="0.001")
    assert D(r["per_contract"]) == D("0.50") - D("0.45") - D("0.004") - \
        D("0.02") - D("0.01") + D("0.001")


# ------------------------------------------- pure: two-bid market cap
def _held(**c):
    """4 YES and 4 NO held at 0.45 each; extensions on both sides."""
    return R.quotes(yes_bid="0.48", yes_ask="0.50", tick=T,
                    pos={"YES": 2, "NO": 2},
                    cost={"YES": "1.80", "NO": "1.80"},
                    c=dict({"edge_gate": True, "fair_yes": "0.48",
                            "residual_cap_usd": "100"}, **c))


def test_market_cap_default_drops_the_dearer_bid():
    q = _held(per_market_usd="4.4", qty=1)
    assert q["YES"] is not None and q["NO"] is None
    assert q["why_NO"] == "per_market_cap"


def test_market_cap_with_ev_priority_drops_the_lower_value_bid():
    """YES 0.47 is worth 0 a fill, NO 0.49 is worth 2c: the dearer NO is
    kept and YES goes."""
    q = _held(per_market_usd="4.4", qty=1, ev_priority=True)
    assert D(q["edge"]["YES"]["net_edge"]) < D(q["edge"]["NO"]["net_edge"])
    assert q["NO"] is not None and q["YES"] is None
    assert q["why_YES"] == "per_market_cap"


def test_market_cap_with_fit_sizes_down_before_dropping():
    q = _held(per_market_usd="5.2", qty=2)
    assert q["NO"] is None                                # default: drop
    q = _held(per_market_usd="5.2", qty=2, fit_pair_cap=True)
    assert q["YES"]["qty"] == 2 and q["NO"]["qty"] == 1
    assert q["NO"]["why"] == "per_market_cap_sized"


# ------------------------------------------------------------ engine
@pytest.fixture
def ev(eng, monkeypatch):
    """Marginal test on (production default), fair 0.49, book 0.48/0.50:
    YES 0.47 and NO 0.49 each clear the 1c allowance by 1c."""
    e, v = eng
    monkeypatch.setattr(R, "EDGE_GATE", True)
    monkeypatch.setattr(M, "RN1_FILL_RATE", {0: {"": 0.5}, 1: {"": 0.2}})
    monkeypatch.setattr(M, "RN1_FILL_LOSS", {0: {"": 0.005},
                                             1: {"": 0.005}})
    e.b.m(SLUG)
    return e, v


def _net_negative(e, monkeypatch):
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: False)


def test_off_a_non_positive_market_estimate_blocks_everything(ev,
                                                               monkeypatch):
    e, v = ev
    _net_negative(e, monkeypatch)
    e.step_market(SLUG)
    assert resting(v) == []


def test_on_a_bid_with_positive_ev_and_no_reward_still_rests(ev,
                                                             monkeypatch):
    e, v = ev
    _net_negative(e, monkeypatch)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]


def test_on_bids_that_lose_on_their_fills_are_still_blocked(ev,
                                                            monkeypatch):
    """Fill loss 3.5c behind the touch > the 2c edge: negative EV, no
    reward to cover it -> nothing rests, block reported as before."""
    e, v = ev
    _net_negative(e, monkeypatch)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    monkeypatch.setattr(M, "RN1_FILL_LOSS", {0: {"": 0.05},
                                             1: {"": 0.035}})
    e.step_market(SLUG)
    assert resting(v) == []


def test_on_without_a_fair_value_nothing_changes(ev, monkeypatch):
    e, v = ev
    _net_negative(e, monkeypatch)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    e.guard = Guard(None)
    e.step_market(SLUG)
    assert resting(v) == []


def _paying(e, monkeypatch, incr):
    monkeypatch.setattr(e, "rn1_reward", lambda s: {
        "df": 0.5, "targetSize": 10, "pool_rate_usd_s": 0.001})
    monkeypatch.setattr(e, "rn1_incentive", lambda *a, **k: {
        "ticks": 0, "why": "qualifies"})
    monkeypatch.setattr(e, "_reward_get",
                        lambda: (lambda path: {"marketData": {}}))
    calls = []

    def marg(book, per, y, n, tick, oy, on, side, price, qty):
        calls.append((side, price, qty))
        return {"incremental_reward_usd_per_hour": incr}
    monkeypatch.setattr(RW, "marginal_ladder_reward", marg)
    return calls


def test_on_a_qualifying_incremental_reward_covers_fill_loss(ev,
                                                             monkeypatch):
    e, v = ev
    _net_negative(e, monkeypatch)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    monkeypatch.setattr(M, "RN1_FILL_LOSS", {0: {"": 0.05},
                                             1: {"": 0.035}})
    calls = _paying(e, monkeypatch, 1.0)      # x haircut 0.5 = 0.50/h
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]
    assert calls


def test_on_a_reward_that_does_not_qualify_is_not_credited(ev, monkeypatch):
    e, v = ev
    _net_negative(e, monkeypatch)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    monkeypatch.setattr(M, "RN1_FILL_LOSS", {0: {"": 0.05},
                                             1: {"": 0.035}})
    calls = _paying(e, monkeypatch, 1.0)
    monkeypatch.setattr(e, "rn1_incentive", lambda *a, **k: {
        "ticks": 0, "why": "below_minimum_payout"})
    e.step_market(SLUG)
    assert resting(v) == [] and calls == []


def test_on_a_reward_never_passes_a_negative_trade_edge(ev, monkeypatch):
    """fair 0.475: YES 0.47 fails the marginal test itself; NO 0.49 is
    judged against fair 0.525 and passes. A large reward does not bring
    YES back."""
    e, v = ev
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    e.guard = Guard(0.475)
    _paying(e, monkeypatch, 100.0)
    e.step_market(SLUG)
    assert [r[0] for r in resting(v)] == ["NO"]


def test_on_each_ladder_level_is_judged_on_its_own(ev, monkeypatch):
    """Three levels a side, 3c concentration charge per unpaired clip: level
    0 nets 1c a fill, level 1 nets 0 (2c cheaper, 3c more risk), level 2
    loses. Off: all three rest. On: only level 0 does."""
    e, v = ev
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", 3)
    monkeypatch.setattr(M, "RN1_LADDER_STEP_TICKS", 2)
    monkeypatch.setattr(R, "RISK_STEP_TICKS", 3)
    e.step_market(SLUG)
    assert len(resting(v)) == 6
    for o in list(v.orders.values()):
        o["open"] = False
    for o in e.b.m(SLUG)["orders"].values():
        o["open"] = False
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]


def test_on_bids_that_pass_are_not_released_as_unqualified(ev, monkeypatch):
    e, v = ev
    _net_negative(e, monkeypatch)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    monkeypatch.setattr(M, "RN1_RELEASE_UNQUALIFIED_S", 600.0)
    e.step_market(SLUG)
    mk = e.b.m(SLUG)
    before = resting(v)
    assert before
    mk["unqualified_since"] = time.time() - 601
    e.step_market(SLUG)
    assert resting(v) == before and not mk.get("done")


def test_off_the_unqualified_release_still_cancels(ev, monkeypatch):
    e, v = ev
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(M, "RN1_RELEASE_UNQUALIFIED_S", 600.0)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: True)
    e.step_market(SLUG)
    assert resting(v)
    _net_negative(e, monkeypatch)
    e.b.m(SLUG)["unqualified_since"] = time.time() - 601
    e.step_market(SLUG)
    assert resting(v) == []


def test_on_the_hold_expiry_does_not_cancel_bids_that_pass(ev, monkeypatch):
    e, v = ev
    monkeypatch.setattr(M, "RN1_NET_HOLD_S", 300.0)
    _net_negative(e, monkeypatch)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    e.step_market(SLUG)
    before = resting(v)
    e.b.m(SLUG)["net_neg_since"] = time.time() - 301
    e.step_market(SLUG)
    assert resting(v) == before


def test_on_a_completion_is_never_judged_or_dropped(ev, monkeypatch):
    """4 YES held at 0.45, fill loss large: the YES extension is refused on
    its EV, the NO completion still rests."""
    e, v = ev
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("4"), D("1.80")
    mk.setdefault("last_fill_t", {})["YES"] = time.time() - 3600
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    monkeypatch.setattr(M, "RN1_FILL_LOSS", {0: {"": 0.2}, 1: {"": 0.2}})
    e.step_market(SLUG)
    rs = resting(v)
    assert [r[0] for r in rs] == ["NO"]
    stages = [o["stage"] for o in mk["orders"].values() if o.get("open")]
    assert stages == ["two_sided_complete"]


# ------------------------------------------- engine: campaign cap order
def _expensive_inventory(e, monkeypatch, spend_cap="9.7"):
    """4 YES and 4 NO held at 0.80 each: both bids filling together is the
    worst case, so removing EITHER bid brings the plan inside the cap."""
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("4"), D("3.20")
    mk["pos"]["NO"], mk["cost"]["NO"] = D("4"), D("3.20")
    monkeypatch.setattr(R, "TOTAL_SPEND_USD", D(spend_cap))
    e.guard = Guard(0.485)        # YES 0.47 worth 0.5c, NO 0.49 worth 1.5c


def test_campaign_cap_default_drops_the_dearest(ev, monkeypatch):
    e, v = ev
    _expensive_inventory(e, monkeypatch)
    e.step_market(SLUG)
    assert [r[0] for r in resting(v)] == ["YES"]


def test_campaign_cap_with_marginal_ev_drops_the_lower_value(ev,
                                                             monkeypatch):
    e, v = ev
    _expensive_inventory(e, monkeypatch)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    e.step_market(SLUG)
    assert [r[0] for r in resting(v)] == ["NO"]


def test_campaign_cap_keeps_dearest_first_when_only_it_fixes(ev,
                                                             monkeypatch):
    """Flat book, campaign unpaired cap 1.90: one-sided fills are the worst
    case, so only removing the dearer NO helps; EV ranking must not drop
    the cheaper YES and then NO as well."""
    e, v = ev
    e.guard = Guard(0.485)
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("1.90"))
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    e.step_market(SLUG)
    assert resting(v) == [("YES", "0.47", "4")]


def test_campaign_cap_with_fit_sizes_down_instead_of_dropping(ev,
                                                              monkeypatch):
    e, v = ev
    e.guard = Guard(0.485)
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("1.90"))
    e.step_market(SLUG)
    assert resting(v) == [("YES", "0.47", "4")]           # default: drop
    for o in list(v.orders.values()):
        o["open"] = False
    for o in e.b.m(SLUG)["orders"].values():
        o["open"] = False
    monkeypatch.setattr(M, "RN1_FIT_ALL_LEVELS", True)
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "3"), ("YES", "0.47", "4")]


# ------------------------------------------- engine: ladder fitting
def test_ladder_levels_size_down_and_skip_with_fit_all_levels(ev,
                                                              monkeypatch):
    """$3 unpaired a market, 4 a level. Off (and FIT_QTY off): YES 0.47 x4
    (1.88) fits, level 1 at x4 does not and ends the ladder. On: level 1
    is sized to what still fits under the cap; level 2 at the minimum
    would breach it and is skipped."""
    e, v = ev
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", 3)
    monkeypatch.setattr(M, "RN1_LADDER_STEP_TICKS", 2)
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("3"))
    e.step_market(SLUG)
    off = resting(v)
    assert [r for r in off if r[0] == "YES"] == [("YES", "0.47", "4")]
    for o in list(v.orders.values()):
        o["open"] = False
    for o in e.b.m(SLUG)["orders"].values():
        o["open"] = False
    monkeypatch.setattr(M, "RN1_FIT_ALL_LEVELS", True)
    e.step_market(SLUG)
    on = resting(v)
    yes = [r for r in on if r[0] == "YES"]
    # 1.88 + 0.45 x 2 = 2.78 fits; x3 (3.23) would not; 0.43 x1 no longer
    assert yes == [("YES", "0.45", "2"), ("YES", "0.47", "4")]


def test_rn1_ladder_skips_a_level_that_cannot_fit_and_tries_deeper(
        ev, monkeypatch):
    """Direct: level 1 cannot fit even at one contract but a cheaper level
    2 can (unpaired cap between them)."""
    e, _v = ev
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", 3)
    monkeypatch.setattr(M, "RN1_LADDER_STEP_TICKS", 20)
    mk = e.b.m(SLUG)
    want = {"YES": {"px": D("0.70"), "qty": D("1"), "role": "open"},
            "NO": None}
    # level 1 at 0.50, level 2 at 0.30; cap 1.05 holds 0.70 + 0.30 only
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("1.05"))
    monkeypatch.setattr(R, "PX_MIN", D("0.20"))
    assert e.rn1_ladder(SLUG, mk, want, T) == []
    monkeypatch.setattr(M, "RN1_FIT_ALL_LEVELS", True)
    got = e.rn1_ladder(SLUG, mk, want, T)
    assert [(l_["px"], l_["qty"], l_["level"]) for l_ in got] == [
        (D("0.30"), D("1"), 2)]
