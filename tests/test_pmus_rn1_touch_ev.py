"""Economically justified best-bid participation (PMUS_RN1_TOUCH_EV, off by
default) and the per-competition volatility threshold
(PMUS_RN1_JOIN_MAX_RANGE_COMP, unset = one threshold, as before).

Per opening/extension side, every placement (touch, one behind, ...) is
scored: calibrated incremental THEORETICAL reward - fills/h x qty x (fill
loss + fee + concentration charge). The touch is never chosen under a hard
protection, when crossing/locking, over the residual cap, with EV <= 0, or
when the pair cap would be breached. Selection and quoting use the same
function. Every figure here is THEORETICAL.
"""
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
BBO = {"bid": D("0.48"), "ask": D("0.50")}
FLAT = {"YES": "0", "NO": "0"}
PER = {"df": 0.3, "targetSize": 10, "pool_rate_usd_s": 0.001,
       "programId": "P1", "maxSpread": None}


def _reward(monkeypatch, table):
    """{(side, price): incremental reward/h}; anything else 0."""
    calls = []

    def marg(book, per, y, n, tick, oy, on, side, price, qty):
        calls.append((side, round(price, 4), qty, tuple(y), tuple(n)))
        return {"incremental_reward_usd_per_hour":
                table.get((side, round(price, 4)), 0.0)}
    monkeypatch.setattr(RW, "marginal_ladder_reward", marg)
    return calls


@pytest.fixture
def rates(monkeypatch):
    # explicit: these tests hold whatever the import-time flags are
    for name in ("RN1_TOUCH_EV", "RN1_REWARD_CALIBRATION", "RN1_MARGINAL_EV",
                 "RN1_MARGINAL_EV_NO_FAIR", "RN1_FIT_ALL_LEVELS",
                 "RN1_NET_RANK", "RN1_JOIN_TOUCH", "RN1_LIVE_FIRST",
                 "RN1_DURATION_RANK", "RN1_SELECT_NO_CHURN"):
        monkeypatch.setattr(M, name, False)
    monkeypatch.setattr(M, "RN1_REWARD_CAL_TABLE", "")
    monkeypatch.setattr(M, "RN1_JOIN_MAX_RANGE",
                        {"": M.RN1_JOIN_MAX_RANGE_USD})
    monkeypatch.setattr(M, "RN1_FILL_RATE", {0: {"": 3.0}, 1: {"": 1.5}})
    monkeypatch.setattr(M, "RN1_FILL_LOSS", {0: {"": 0.05}, 1: {"": 0.035}})
    monkeypatch.setattr(M, "RANK_REWARD_HAIRCUT", 0.5)
    monkeypatch.setattr(R, "MAKER_FEE_RATE", D("0"))
    monkeypatch.setattr(R, "PAIR_CAP", D("0.97"))
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("20"))
    monkeypatch.setattr(R, "QTY", D("2"))
    monkeypatch.setattr(R, "DEPTH_TICKS", 1)


def _ev(e, pos=FLAT, cost=FLAT, hard=None, qty=2, **k):
    return e.rn1_touch_ev(SLUG, {"bids": [], "offers": []}, PER, BBO, T,
                          D(str(qty)), pos, cost, hard, **k)


# ------------------------------------------------------------ defaults
def test_flags_are_off_by_default_from_a_clean_interpreter():
    code = ("import os;[os.environ.pop(k,None) for k in list(os.environ) "
            "if k.startswith('PMUS_')];"
            "from src import pmus_sports_two_sided as M;"
            "print(M.RN1_TOUCH_EV, M.RN1_REWARD_CALIBRATION, "
            "M.RN1_JOIN_MAX_RANGE == {'': M.RN1_JOIN_MAX_RANGE_USD}, "
            "M.RN1_TOUCH_EV_MAX_DEPTH, M.RN1_REWARD_POOL_SCOPE)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout.split()
    assert out == ["False", "False", "True", "2", "market"]


def test_quotes_without_a_per_side_depth_are_unchanged():
    kw = dict(yes_bid="0.48", yes_ask="0.50", tick=T, pos=FLAT, cost=FLAT)
    a = R.quotes(**kw, c={"depth_ticks": 1})
    b = R.quotes(**kw, c={"depth_ticks": 1, "depth_ticks_side": {}})
    assert a == b and a["YES"]["px"] == D("0.47")
    c = R.quotes(**kw, c={"depth_ticks": 1,
                          "depth_ticks_side": {"YES": 0}})
    assert c["YES"]["px"] == D("0.48") and c["NO"]["px"] == D("0.49")


# ------------------------------------------------------------ pure
def test_no_reward_never_joins_the_touch(eng, rates, monkeypatch):
    e, _ = eng
    _reward(monkeypatch, {})
    got = _ev(e)
    assert got["depth_side"] == {"YES": 1, "NO": 1}
    assert got["touch"] is False
    assert all(got["sides"][s]["why"] == "no_positive_placement"
               for s in ("YES", "NO"))


def test_the_best_placement_by_ev_is_chosen(eng, rates, monkeypatch):
    """YES touch: 0.5 x 0.8 - 3.0 x 2 x 0.05 = +0.10; behind:
    0.5 x 0.3 - 1.5 x 2 x 0.035 = +0.045 -> touch. NO: touch reward too
    small to beat its fill cost, behind positive -> behind."""
    e, _ = eng
    _reward(monkeypatch, {("YES", 0.48): 0.8, ("YES", 0.47): 0.3,
                          ("NO", 0.5): 0.5, ("NO", 0.49): 0.3})
    got = _ev(e)
    rows = got["sides"]["YES"]["rows"]
    assert rows[0]["ev_usd_per_hour"] == pytest.approx(0.10)
    assert rows[1]["ev_usd_per_hour"] == pytest.approx(0.045)
    assert got["depth_side"] == {"YES": 0, "NO": 1}
    assert got["touch"] is True
    assert got["net_usd_per_hour"] == pytest.approx(0.10 + 0.045)


def test_ties_prefer_the_deeper_placement(eng, rates, monkeypatch):
    e, _ = eng
    monkeypatch.setattr(M, "RN1_FILL_RATE", {0: {"": 1.0}, 1: {"": 1.0}})
    monkeypatch.setattr(M, "RN1_FILL_LOSS", {0: {"": 0.01}, 1: {"": 0.01}})
    _reward(monkeypatch, {("YES", 0.48): 0.2, ("YES", 0.47): 0.2})
    assert _ev(e)["depth_side"]["YES"] == 1


@pytest.mark.parametrize("hard", ["price_moving", "spread_too_tight",
                                  "recent_jump"])
def test_a_hard_protection_never_joins_the_touch(eng, rates, monkeypatch,
                                                 hard):
    e, _ = eng
    _reward(monkeypatch, {("YES", 0.48): 50.0, ("NO", 0.5): 50.0})
    got = _ev(e, hard=hard)
    assert got["touch"] is False
    for s in ("YES", "NO"):
        assert got["sides"][s]["rows"][0]["why"] == "hard:" + hard
        assert got["depth_side"][s] >= 1


def test_the_pair_cap_moves_the_dearer_touch_bid_back(eng, rates,
                                                      monkeypatch):
    """Both touches: 0.48 + 0.50 = 0.98 > 0.97. The dearer (NO 0.50) goes
    to its best positive deeper placement; YES stays at the touch."""
    e, _ = eng
    _reward(monkeypatch, {("YES", 0.48): 2.0, ("NO", 0.5): 2.0,
                          ("YES", 0.47): 0.3, ("NO", 0.49): 0.3})
    got = _ev(e)
    assert got["depth_side"] == {"YES": 0, "NO": 1}
    assert got["sides"]["NO"]["rows"][0]["why"] == "pair_cap"
    y = D("0.48")
    n = D("0.49")
    assert y + n <= R.PAIR_CAP


def test_the_pair_cap_with_no_positive_deeper_level_falls_back(
        eng, rates, monkeypatch):
    e, _ = eng
    _reward(monkeypatch, {("YES", 0.48): 2.0, ("NO", 0.5): 2.0})
    got = _ev(e)
    assert got["depth_side"]["NO"] == 1
    assert got["sides"]["NO"]["why"] == "no_positive_placement"


def test_over_the_residual_cap_nothing_is_placed_at_any_depth(
        eng, rates, monkeypatch):
    e, _ = eng
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("1.00"))
    _reward(monkeypatch, {("YES", 0.48): 50.0, ("YES", 0.47): 50.0,
                          ("YES", 0.46): 50.0})
    got = _ev(e, pos={"YES": "2", "NO": "0"},
              cost={"YES": "0.90", "NO": "0"})
    rows = got["sides"]["YES"]["rows"]
    assert all(r.get("why") == "residual_cap" for r in rows.values())
    assert got["depth_side"]["YES"] >= 1 and got["touch"] is False


def test_the_concentration_charge_can_make_the_touch_not_worth_it(
        eng, rates, monkeypatch):
    """Flat: touch EV +0.10 (joined). Holding 6 unpaired contracts (3
    clips of 2) the charge is 3 x RISK_STEP_TICKS x 1c per contract filled
    and the touch EV turns negative."""
    e, _ = eng
    _reward(monkeypatch, {("YES", 0.48): 0.8})
    assert _ev(e)["depth_side"]["YES"] == 0
    got = _ev(e, pos={"YES": "6", "NO": "0"},
              cost={"YES": "2.82", "NO": "0"})
    r0 = got["sides"]["YES"]["rows"][0]
    assert D(r0["risk"]) > 0 and r0["ev_usd_per_hour"] <= 0
    assert got["depth_side"]["YES"] >= 1


def test_post_only_holds_on_every_book(eng, rates, monkeypatch):
    e, _ = eng
    _reward(monkeypatch, {})
    monkeypatch.setattr(RW, "marginal_ladder_reward",
                        lambda *a: {"incremental_reward_usd_per_hour": 9.0})
    for b, a in (("0.48", "0.49"), ("0.30", "0.33"), ("0.60", "0.61")):
        bbo = {"bid": D(b), "ask": D(a)}
        got = e.rn1_touch_ev(SLUG, {"x": 1}, PER, bbo, T, D("2"), FLAT,
                             FLAT, None)
        for s, d in got["depth_side"].items():
            touch = D(b) if s == "YES" else 1 - D(a)
            opp = D(a) if s == "YES" else 1 - D(b)
            assert touch - d * T < opp


def test_a_completion_side_keeps_its_placement(eng, rates, monkeypatch):
    e, _ = eng
    _reward(monkeypatch, {("NO", 0.5): 50.0, ("NO", 0.49): 50.0})
    got = _ev(e, pos={"YES": "2", "NO": "0"},
              cost={"YES": "0.90", "NO": "0"}, completion_depth=1)
    assert got["sides"]["NO"]["role"] == "complete"
    assert got["depth_side"]["NO"] == 1


def test_reward_is_credited_with_the_calibration_multiplier(
        eng, rates, monkeypatch):
    e, _ = eng
    _reward(monkeypatch, {("YES", 0.48): 1.0})
    r_off = _ev(e)["sides"]["YES"]["rows"][0]["reward_usd_per_hour"]
    assert r_off == pytest.approx(0.5)              # RANK_REWARD_HAIRCUT
    monkeypatch.setattr(M, "RN1_REWARD_CALIBRATION", True)
    monkeypatch.setattr(M, "RN1_REWARD_CAL_TABLE", "*:0.4")
    r_on = _ev(e)["sides"]["YES"]["rows"][0]["reward_usd_per_hour"]
    assert r_on == pytest.approx(0.4)


def test_with_max_spread_the_other_side_is_scored_alongside(
        eng, rates, monkeypatch):
    e, _ = eng
    calls = _reward(monkeypatch, {})
    per = dict(PER, maxSpread=0.06)
    e.rn1_touch_ev(SLUG, {"x": 1}, per, BBO, T, D("2"), FLAT, FLAT, None)
    yes_calls = [c for c in calls if c[0] == "YES"]
    assert yes_calls and all(c[4] == ((0.49, 2.0),) for c in yes_calls)


# ------------------------------------------------------------ hard checks
def test_the_volatility_threshold_is_per_competition(eng, monkeypatch):
    e, _ = eng
    mk = {}
    assert e.rn1_touch_hard(SLUG, mk, BBO, T, D("0.03")) == "price_moving"
    assert e.rn1_touch_hard(SLUG, mk, BBO, T, D("0.01")) is None
    assert e.rn1_touch_hard(SLUG, mk, BBO, T, None) == "price_moving"
    monkeypatch.setattr(M, "RN1_JOIN_MAX_RANGE", {"": 0.01, "x": 0.05})
    assert e.rn1_touch_hard(SLUG, mk, BBO, T, D("0.03")) is None
    assert e.rn1_touch_hard("aec-utr-a-b", mk, BBO, T,
                            D("0.03")) == "price_moving"
    tight = {"bid": D("0.48"), "ask": D("0.49")}
    assert e.rn1_touch_hard(SLUG, mk, tight, T, D("0")) == \
        "spread_too_tight"


def test_join_touch_unchanged_with_the_default_threshold(eng, monkeypatch):
    e, _ = eng
    monkeypatch.setattr(M, "RN1_JOIN_TOUCH", True)
    mk = e.b.m(SLUG)
    assert e.rn1_join_touch(SLUG, mk, BBO, T, D("0.011"), 0) == \
        "price_moving"
    assert e.rn1_join_touch(SLUG, mk, BBO, T, D("0.01"), 0) is None


# ------------------------------------------------------------ engine
@pytest.fixture
def tev(eng, rates, monkeypatch):
    e, v = eng
    monkeypatch.setattr(R, "EDGE_GATE", False)
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("20"))
    e.guard = Guard(None)
    e.b.m(SLUG)
    monkeypatch.setattr(e, "rn1_reward", lambda s: dict(PER))
    monkeypatch.setattr(e, "_reward_get",
                        lambda: (lambda path: {"marketData": {"x": 1}}))
    return e, v


def test_engine_off_is_unchanged(tev, monkeypatch):
    e, v = tev
    _reward(monkeypatch, {("YES", 0.48): 0.8, ("YES", 0.47): 0.3})
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]


def test_engine_on_rests_where_the_ev_says(tev, monkeypatch, capsys):
    e, v = tev
    monkeypatch.setattr(M, "RN1_TOUCH_EV", True)
    _reward(monkeypatch, {("YES", 0.48): 2.0, ("YES", 0.47): 0.3,
                          ("NO", 0.49): 0.5})
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.48", "4")]
    assert e._rn1_joined[SLUG] is True
    out = capsys.readouterr().out
    assert '"RN1_TOUCH_EV"' in out


def test_engine_on_never_joins_under_a_hard_protection(tev, monkeypatch):
    e, v = tev
    monkeypatch.setattr(M, "RN1_TOUCH_EV", True)
    monkeypatch.setattr(M, "RN1_JOIN_MAX_RANGE", {"": -1.0})  # always moving
    _reward(monkeypatch, {("YES", 0.48): 50.0, ("NO", 0.5): 50.0,
                          ("YES", 0.47): 0.3, ("NO", 0.49): 0.3})
    e.step_market(SLUG)
    assert resting(v) == [("NO", "0.49", "4"), ("YES", "0.47", "4")]
    assert e._rn1_joined[SLUG] is False


def test_engine_on_jump_pause_still_blocks_every_opening(tev, monkeypatch):
    import time as _t
    e, v = tev
    monkeypatch.setattr(M, "RN1_TOUCH_EV", True)
    e.ws = object()
    e.b.m(SLUG)["jump_pause_until"] = _t.time() + 60
    _reward(monkeypatch, {("YES", 0.48): 50.0})
    e.step_market(SLUG)
    assert resting(v) == []


def test_selection_and_quoting_use_the_same_placement(tev, monkeypatch):
    """The selection score (_reward_share_score) and the quote the
    controller then rests come from rn1_touch_ev on the same inputs."""
    e, v = tev
    monkeypatch.setattr(M, "RN1_TOUCH_EV", True)
    monkeypatch.setattr(M, "RN1_NET_SIZES", (D("4"),))
    _reward(monkeypatch, {("YES", 0.48): 2.0, ("YES", 0.47): 0.3,
                          ("NO", 0.49): 0.5})
    book = {"bids": [{"px": "0.48", "qty": "5"}],
            "offers": [{"px": "0.50", "qty": "5"}]}
    monkeypatch.setattr(e, "_reward_get",
                        lambda: (lambda path: {"marketData": book}))
    sel = e._reward_share_score(SLUG, dict(PER))
    assert sel["touch_ev"] is True
    e.step_market(SLUG)
    placed = {s: D(px) for s, px, _q in resting(v)}
    assert placed["YES"] == D("0.48") - sel["depth_side"]["YES"] * T
    assert placed["NO"] == D("0.50") - sel["depth_side"]["NO"] * T
    assert sel["net_usd_per_hour"] > 0


def test_selection_scores_the_ev_not_the_best_bid_reward(tev, monkeypatch):
    """A market whose touch reward looks big but cannot be joined (hard
    protection) is scored at the placement it would get: behind."""
    e, _ = tev
    monkeypatch.setattr(M, "RN1_TOUCH_EV", True)
    monkeypatch.setattr(M, "RN1_JOIN_MAX_RANGE", {"": -1.0})
    _reward(monkeypatch, {("YES", 0.48): 50.0, ("NO", 0.5): 50.0})
    book = {"bids": [{"px": "0.48", "qty": "5"}],
            "offers": [{"px": "0.50", "qty": "5"}]}
    monkeypatch.setattr(e, "_reward_get",
                        lambda: (lambda path: {"marketData": book}))
    sel = e._reward_share_score(SLUG, dict(PER))
    assert sel["depth_side"] == {"YES": 1, "NO": 1}
    assert sel["net_usd_per_hour"] <= 0
    assert sel["hard"] == "price_moving"
