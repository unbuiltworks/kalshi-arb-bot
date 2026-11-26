"""Marginal net EV for markets with NO fair value
(PMUS_RN1_MARGINAL_EV_NO_FAIR, off by default; needs PMUS_RN1_MARGINAL_EV=1
and applies only with PMUS_RN1_EDGE_GATE=0).

Live 26 Nov: doubles, table tennis, UTR and ITF have no international feed,
so marginal EV judged none of their bids. In this mode each opening /
extension bid and each deeper level of such a market is judged on its own:
qualifying incremental theoretical reward (x RANK_REWARD_HAIRCUT) minus
fills/h x (RN1_FILL_LOSS + maker fee + concentration risk). No trade edge:
there is no fair value and none is invented.
"""
import json
import subprocess
import sys
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
def test_flag_is_off_by_default_from_a_clean_interpreter():
    code = ("import os;[os.environ.pop(k,None) for k in list(os.environ) "
            "if k.startswith('PMUS_')];"
            "from src import pmus_sports_two_sided as M;"
            "print(M.RN1_MARGINAL_EV_NO_FAIR)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout.split()
    assert out == ["False"]


# ------------------------------------------------------------ pure
def test_no_reward_means_no_bid():
    r = R.marginal_ev_no_fair(px="0.47", qty=4, fill_rate=0.2,
                              fill_loss="0.035", reward_usd_per_hour=0.0)
    assert r["ok"] is False and r["why"] == "marginal_ev_not_positive"
    assert r["trade_edge"] is None and r["fair"] is None
    assert r["basis"] == "no_fair"
    assert abs(r["ev_usd_per_hour"] + 0.2 * 4 * 0.035) < 1e-12


def test_reward_must_exceed_the_expected_fill_cost():
    kw = dict(px="0.47", qty=4, fill_rate=0.2, fill_loss="0.035",
              fee="0.002", risk="0.01")
    cost_h = 0.2 * 4 * (0.035 + 0.002 + 0.01)
    lo = R.marginal_ev_no_fair(**kw, reward_usd_per_hour=cost_h - 0.001)
    assert lo["ok"] is False and lo["ev_usd_per_hour"] < 0
    zero = R.marginal_ev_no_fair(px="0.47", qty=0, fill_rate=0.2,
                                 fill_loss="0.035", reward_usd_per_hour=0)
    assert zero["ok"] is False                  # zero total is not positive
    hi = R.marginal_ev_no_fair(**kw, reward_usd_per_hour=cost_h + 0.001)
    assert hi["ok"] is True
    assert D(hi["per_contract"]) == -(D("0.035") + D("0.002") + D("0.01"))


def test_concentration_risk_is_the_marginal_tests_charge():
    for tick, clips, steps in ((T, 0, None), (T, 3, None), (D("0.0025"), 2,
                                                            2)):
        ev = R.edge(side="YES", px="0.40", tick=tick, fair_yes="0.6",
                    adds_exposure=True, unpaired_clips=clips,
                    risk_step_ticks=steps)
        assert R.concentration_risk(tick=tick, unpaired_clips=clips,
                                    risk_step_ticks=steps) == D(ev["risk"])


# ------------------------------------------------------------ engine
@pytest.fixture
def nf(eng, monkeypatch):
    """Marginal test OFF, no international fair value, marginal EV on.
    Book 0.48/0.50: YES 0.47 and NO 0.49, one tick behind (fill rate 0.2,
    fill loss 3.5c): 4 contracts cost 0.028/h to fill."""
    e, v = eng
    monkeypatch.setattr(R, "EDGE_GATE", False)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV", True)
    monkeypatch.setattr(M, "RN1_FILL_RATE", {0: {"": 0.5}, 1: {"": 0.2}})
    monkeypatch.setattr(M, "RN1_FILL_LOSS", {0: {"": 0.05},
                                             1: {"": 0.035}})
    e.guard = Guard(None)
    e.b.m(SLUG)
    return e, v


def _paying(e, monkeypatch, incr):
    """`incr`: a number, or {(side, price): incremental reward/h}."""
    monkeypatch.setattr(e, "rn1_reward", lambda s: {
        "df": 0.5, "targetSize": 10, "pool_rate_usd_s": 0.001})
    monkeypatch.setattr(e, "rn1_incentive", lambda *a, **k: {
        "ticks": 0, "why": "qualifies"})
    monkeypatch.setattr(e, "_reward_get",
                        lambda: (lambda path: {"marketData": {}}))
    calls = []

    def marg(book, per, y, n, tick, oy, on, side, price, qty):
        calls.append((side, round(price, 4), qty))
        v = incr.get((side, round(price, 4)), 0.0) \
            if isinstance(incr, dict) else incr
        return {"incremental_reward_usd_per_hour": v}
    monkeypatch.setattr(RW, "marginal_ladder_reward", marg)
    return calls


def _clear(e, v):
    for o in list(v.orders.values()):
        o["open"] = False
    for o in e.b.m(SLUG)["orders"].values():
        o["open"] = False


def _quote_logs(capsys):
    pre = "PMUS_TWO_SIDED "
    rows = [json.loads(l[len(pre):])
            for l in capsys.readouterr().out.splitlines()
            if l.startswith(pre)]
    return [r for r in rows if r.get("kind") == "RN1_QUOTE"]


def test_off_no_fair_market_is_not_judged(nf, monkeypatch):
    """Flag off: exactly as 9b28e6bfe -- nothing evaluated, both bids rest
    even though they would lose on their fills with no reward."""
    e, v = nf
    mk = e.b.m(SLUG)
    assert e.rn1_marginal_ev(SLUG, mk, {"bid": D("0.48"), "ask": D("0.50")},
                             T, e.rn1_quote_cfg(SLUG, mk, T), [], []) is None
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]


def test_on_without_a_reward_nothing_rests(nf, monkeypatch, capsys):
    e, v = nf
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    e.step_market(SLUG)
    assert resting(v) == []
    q = _quote_logs(capsys)[-1]
    assert {r["basis"] for r in q["marginal_ev"]} == {"no_fair"}
    assert q["why"] == {"YES": "marginal_ev:marginal_ev_not_positive",
                        "NO": "marginal_ev:marginal_ev_not_positive"}


def test_on_a_reward_carried_bid_rests(nf, monkeypatch):
    e, v = nf
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    calls = _paying(e, monkeypatch, 1.0)          # x haircut 0.5 = 0.50/h
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]
    assert calls


def test_on_a_losing_bid_is_rejected(nf, monkeypatch):
    """YES earns 0.025/h after the haircut, below its 0.028/h fill cost;
    NO earns 0.50/h. Only NO rests."""
    e, v = nf
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    _paying(e, monkeypatch, {("YES", 0.47): 0.05, ("NO", 0.49): 1.0})
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4")]


def test_on_a_reward_that_does_not_qualify_is_not_credited(nf, monkeypatch):
    e, v = nf
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    calls = _paying(e, monkeypatch, 1.0)
    monkeypatch.setattr(e, "rn1_incentive", lambda *a, **k: {
        "ticks": 0, "why": "below_minimum_payout"})
    e.step_market(SLUG)
    assert resting(v) == [] and calls == []


def test_on_deeper_levels_are_judged_individually(nf, monkeypatch):
    """Three YES levels 0.47 / 0.45 / 0.43; the middle one adds no reward,
    the deepest adds enough to cover its fill loss plus 2 clips of risk.
    NO adds nothing anywhere. Off: all six rest. On: YES 0.47 and 0.43."""
    e, v = nf
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", 3)
    monkeypatch.setattr(M, "RN1_LADDER_STEP_TICKS", 2)
    _paying(e, monkeypatch, {("YES", 0.47): 1.0, ("YES", 0.45): 0.0,
                             ("YES", 0.43): 0.2})
    e.step_market(SLUG)
    assert len(resting(v)) == 6
    _clear(e, v)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    e.step_market(SLUG)
    assert resting(v) == [("YES", "0.43", "4"), ("YES", "0.47", "4")]


def test_on_concentration_risk_counts_levels_above(nf, monkeypatch):
    """Deepest level (2 clips ahead, 2c risk): 0.8 fills/h x 5.5c = 0.044/h.
    A 0.03/h reward (0.06 before haircut) does not carry it."""
    e, v = nf
    monkeypatch.setattr(M, "RN1_LADDER_LEVELS", 3)
    monkeypatch.setattr(M, "RN1_LADDER_STEP_TICKS", 2)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    _paying(e, monkeypatch, {("YES", 0.47): 1.0, ("YES", 0.45): 1.0,
                             ("YES", 0.43): 0.06})
    e.step_market(SLUG)
    assert resting(v) == [("YES", "0.45", "4"), ("YES", "0.47", "4")]


def test_on_the_market_level_block_still_applies_when_nothing_passes(
        nf, monkeypatch):
    e, v = nf
    monkeypatch.setattr(M, "RN1_NET_RANK", True)
    monkeypatch.setattr(e, "rn1_quote_qualifies", lambda s, m: False)
    e.step_market(SLUG)
    assert resting(v) == []                       # off: blocked
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    e.step_market(SLUG)
    assert resting(v) == []                       # on, no reward: blocked
    _paying(e, monkeypatch, 1.0)
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]


def test_on_a_completion_is_never_judged_or_dropped(nf, monkeypatch):
    e, v = nf
    import time
    mk = e.b.m(SLUG)
    mk["pos"]["YES"], mk["cost"]["YES"] = D("4"), D("1.80")
    mk.setdefault("last_fill_t", {})["YES"] = time.time() - 3600
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    e.step_market(SLUG)
    assert [r[0] for r in resting(v)] == ["NO"]
    stages = [o["stage"] for o in mk["orders"].values() if o.get("open")]
    assert stages == ["two_sided_complete"]


# ------------------------------------------- the edge gate is not bypassed
def test_edge_gate_on_a_no_fair_market_still_gets_no_bid(nf, monkeypatch):
    """PMUS_RN1_EDGE_GATE=1: the marginal test refuses every bid without a
    fair value ("no_fair"); the new mode is inert and never re-admits one,
    whatever the reward."""
    e, v = nf
    monkeypatch.setattr(R, "EDGE_GATE", True)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    _paying(e, monkeypatch, 100.0)
    e.step_market(SLUG)
    assert resting(v) == []


# ------------------------------------------- fair-value markets unchanged
def _fair_run(e, v, monkeypatch, capsys, flag):
    _clear(e, v)
    e.b.m(SLUG).pop("rn1_logged", None)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", flag)
    e.step_market(SLUG)
    q = _quote_logs(capsys)
    return resting(v), (q[-1]["marginal_ev"] if q else None)


@pytest.mark.parametrize("gate", [True, False])
def test_fair_value_markets_are_unchanged(nf, monkeypatch, capsys, gate):
    e, v = nf
    monkeypatch.setattr(R, "EDGE_GATE", gate)
    e.guard = Guard(0.49)
    _paying(e, monkeypatch, {("YES", 0.47): 1.0, ("NO", 0.49): 0.01})
    capsys.readouterr()
    off = _fair_run(e, v, monkeypatch, capsys, False)
    on = _fair_run(e, v, monkeypatch, capsys, True)
    assert on == off
    if gate:
        assert off[1] and all("basis" not in r for r in off[1])
    else:
        assert off[1] is None        # gate off + fair: not judged, as before
        assert off[0] == [("NO", "0.49", "4"), ("YES", "0.47", "4")]


# ------------------------------------------- caps unchanged
def test_campaign_cap_is_unchanged(nf, monkeypatch):
    e, v = nf
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("1.90"))
    _paying(e, monkeypatch, 1.0)
    e.step_market(SLUG)
    off = resting(v)
    assert off == [("YES", "0.47", "4")]
    _clear(e, v)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    e.step_market(SLUG)
    assert resting(v) == off


def test_residual_cap_is_unchanged(nf, monkeypatch):
    e, v = nf
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("1.5"))
    _paying(e, monkeypatch, 1.0)
    e.step_market(SLUG)
    off = resting(v)
    _clear(e, v)
    monkeypatch.setattr(M, "RN1_MARGINAL_EV_NO_FAIR", True)
    e.step_market(SLUG)
    assert resting(v) == off
