"""PMUS_RN1_TF_ADAPTIVE_HEDGE: the completion priced and sized from the
independent fair and the inventory (src/pmus_adaptive_hedge). Default off;
never above the pair cap, never above today's completion price, never a
lower worst case; no fair / error = today's completion."""
import itertools
from decimal import Decimal as D

import pytest

from src import pmus_adaptive_hedge as AH
from src import pmus_directional_alloc as DA
from src import pmus_directional_backtest as B
from src import pmus_rn1_replica as RN1
from src import pmus_sports_two_sided as M
from tests.test_pmus_enabled_mode_safety import en, quotes  # noqa: F401
from tests.test_pmus_sports_continuous_controller import SLUG

TICK = D("0.01")


def _fair(e, monkeypatch, yes):
    monkeypatch.setattr(e, "rn1_directional_prob", lambda slug: (
        None if yes is None else DA.probability(D(yes), D(yes), D(yes),
                                                source="intl")))


def _orders(e):
    mk = e.b.m(SLUG)
    return sorted((o["side"], str(o["px"]), str(o["qty"]), o.get("stage"))
                  for o in mk["orders"].values() if o.get("open"))


def _held(e, yes_qty="2", yes_cost="0.80"):
    mk = e.b.m(SLUG)
    mk["pos"] = {"YES": yes_qty, "NO": "0"}
    mk["cost"] = {"YES": yes_cost, "NO": "0"}
    return mk


def _plan(**kw):
    a = dict(side="NO", px="0.50", qty="2", pos={"YES": "4", "NO": "0"},
             cost={"YES": "1.60", "NO": "0"}, fair_side="0.45", tick=TICK,
             clip="2", min_qty="1", pair_cap="0.97", unpaired_cap_usd="5")
    a.update(kw)
    return AH.plan(**a)


# ------------------------------------------------------------ pure rule
def test_give_discount_while_held_leg_has_edge_and_has_not_weakened():
    g = AH.give(held_edge=D("0.05"), weakening=D("0"), exposure=D("1"))
    assert g <= 0
    assert AH.give(held_edge=D("0.01"), weakening=D("0"),
                   exposure=D("0")) == D("-0.01")


def test_give_grows_with_weakening_and_exposure_and_is_clamped():
    c = {"give_max": "0.03"}
    g0 = AH.give(held_edge=D("-0.02"), weakening=D("0"), exposure=D("0"),
                 cfg=c)
    g1 = AH.give(held_edge=D("-0.02"), weakening=D("0.04"), exposure=D("0"),
                 cfg=c)
    g2 = AH.give(held_edge=D("-0.02"), weakening=D("0.04"),
                 exposure=D("1"), cfg=c)
    assert g0 == 0 and g0 < g1 < g2 <= D("0.03")
    assert AH.give(held_edge=D("-0.5"), weakening=D("0.9"),
                   exposure=D("9"), cfg=c) == D("0.03")
    assert AH.give(held_edge=D("0.9"), weakening=D("0"),
                   exposure=D("0")) == D(AH.DEFAULTS["give_min"])


def test_price_is_fair_plus_give_and_never_raised():
    # held YES avg .40, fair YES .55 -> edge .15, discount to give_min
    p = _plan()
    assert D(p["give"]) == D("-0.02") and p["px"] == D("0.43")
    # today's price already below the cap: unchanged, never raised
    p = _plan(px="0.30")
    assert p["px"] == D("0.30")


def test_weakened_leg_gets_a_positive_give():
    # YES 4 @ .40 entered at fair .80, now .50: weakened .30, edge .10
    p = _plan(fair_side="0.50", px="0.55", entry_fair_held="0.80",
              cfg={"give_max": "0.03"})
    assert D(p["give"]) == D("0.03") and p["px"] == D("0.53")
    p = _plan(fair_side="0.50", px="0.55", entry_fair_held="0.80")
    assert D(p["give"]) == D("0.01") and p["px"] == D("0.51")
    # the same leg NOT weakened: discount
    p = _plan(fair_side="0.50", px="0.55", entry_fair_held="0.50")
    assert D(p["give"]) < 0 and p["px"] < D("0.50")


def test_size_is_partial_when_the_hedge_costs_ev_and_risk_is_small():
    # hedge at fair + 1c costs 1c EV per contract; imbalance 4
    base = dict(fair_side="0.50", px="0.55", entry_fair_held="0.80")
    lo = _plan(cfg={"risk_aversion": "0.001"}, **base)
    hi = _plan(cfg={"risk_aversion": "0.5"}, **base)
    mid = _plan(cfg={"risk_aversion": "0.01"}, **base)
    assert lo["hold"] and lo["qty"] == 0 and lo["why"] == "ah_hold"
    assert not hi["hold"] and D(hi["target"]) == 4 and hi["qty"] == D(2)
    assert D(mid["target"]) == 2             # a FRACTION of the imbalance
    assert mid["px"] == D("0.51") and mid["qty"] == D(2)
    one = _plan(cfg={"risk_aversion": "0.006"}, **base)
    assert D(one["target"]) == 1 and one["qty"] == D(1)


def test_below_fair_hedge_completes_the_imbalance():
    p = _plan(px="0.30")                     # NO fair .45, bid .30
    assert not p["hold"] and D(p["target"]) == 4 and p["qty"] == D(2)


def test_invariants_over_a_grid():
    """Never above today's price, never above the pair cap, never more
    than today's quantity, never a lower worst case."""
    n = 0
    for po, avg, f_s, px, clip, ef, lam, gmax in itertools.product(
            ("1", "2", "5", "9"), ("0.30", "0.55", "0.80"),
            ("0.10", "0.35", "0.50", "0.72"), ("0.12", "0.30", "0.60"),
            ("1", "2", "4"), (None, "0.40", "0.90"),
            ("0", "0.005", "0.1"), ("0.01", "0.05")):
        cost = {"YES": str(D(po) * D(avg)), "NO": "0"}
        if D(px) + D(avg) > D("0.97"):
            continue                          # not a price today's code bids
        q0 = min(D(clip), D(po))
        p = AH.plan(side="NO", px=px, qty=q0, pos={"YES": po, "NO": "0"},
                    cost=cost, fair_side=f_s, tick=TICK, clip=clip,
                    min_qty="1", pair_cap="0.97", unpaired_cap_usd="5",
                    entry_fair_held=ef,
                    cfg={"risk_aversion": lam, "give_max": gmax})
        assert p is not None
        n += 1
        if p["hold"]:
            assert p["qty"] == 0
            continue
        assert p["px"] <= D(px)
        assert p["px"] + D(avg) <= D("0.97")
        assert p["px"] <= (D(f_s) + D(gmax)).quantize(TICK) + D("1e-12")
        assert 0 < p["qty"] <= q0
        tot = D(po) * D(avg)
        w0 = min(AH.outcomes(D(po), D(0), tot, D(0), p["px"]))
        w1 = min(AH.outcomes(D(po), D(0), tot, p["qty"], p["px"]))
        assert w1 >= w0
    assert n > 1000


def test_min_qty_overfill_that_lowers_the_worst_case_is_held():
    # imbalance 0.2, venue minimum 1: buying 1 @ .40 lowers the worst case
    p = AH.plan(side="NO", px="0.40", qty="1",
                pos={"YES": "2", "NO": "1.8"},
                cost={"YES": "1.00", "NO": "0.72"}, fair_side="0.45",
                tick=TICK, clip="2", min_qty="1", pair_cap="0.97",
                unpaired_cap_usd="5")
    assert p["hold"] and p["qty"] == 0


@pytest.mark.parametrize("kw", [
    {"fair_side": None}, {"fair_side": "1.2"}, {"fair_side": "x"},
    {"pos": {"YES": "0", "NO": "0"}}, {"pos": {"YES": "1", "NO": "3"}},
    {"tick": "0"}, {"px": "nan?"}, {"cost": None}, {"min_qty": "0"},
])
def test_unusable_input_returns_none(kw):
    assert _plan(**kw) is None


def test_note_entry_blends_growth_and_resets():
    r = AH.note_entry(None, "YES", "2", "0.60")
    assert r == {"side": "YES", "qty": "2", "fair": "0.60"}
    r = AH.note_entry(r, "YES", "4", "0.50")
    assert D(r["fair"]) == D("0.55") and r["qty"] == "4"
    r2 = AH.note_entry(r, "YES", "2", "0.30")       # shrink keeps fair
    assert D(r2["fair"]) == D("0.55") and r2["qty"] == "2"
    assert AH.note_entry(r, "YES", "4", None) == r   # no fair: unchanged
    assert AH.note_entry(r, "NO", "1", "0.40")["fair"] == "0.40"
    assert AH.note_entry(r, "YES", "0", "0.40") is None
    assert AH.note_entry({"side": "YES", "qty": "x"}, "YES", "1",
                         "0.4") is None


# --------------------------------------------------------------- engine
def test_flag_default_off():
    assert M.RN1_TF_ADAPTIVE_HEDGE is False


def test_off_never_consults_the_rule_and_decisions_are_identical(
        en, monkeypatch, capsys):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_HEDGE_GIVE_USD", D("0.01"))
    _fair(e, monkeypatch, "0.55")
    _held(e)

    def boom(**_kw):
        raise AssertionError("adaptive rule consulted with the flag off")
    monkeypatch.setattr(AH, "plan", boom)
    monkeypatch.setattr(AH, "note_entry", boom)
    e.step_market(SLUG)
    off = _orders(e)
    comp = [o for o in off if o[3] == "two_sided_complete"]
    assert comp and all(D(o[1]) <= D("0.46") for o in comp)
    assert "_ah_ref" not in e.b.m(SLUG)


def _today(en_fix, monkeypatch, give, fair):
    """The decision with the adaptive flag off (reference)."""
    e, v, _ = en_fix
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_HEDGE_GIVE_USD", give)
    _fair(e, monkeypatch, fair)
    _held(e)
    e.step_market(SLUG)
    return _orders(e)


def test_on_no_fair_is_exactly_todays_completion(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_ADAPTIVE_HEDGE", True)
    _fair(e, monkeypatch, None)
    _held(e)
    e.step_market(SLUG)
    od = _orders(e)
    comp = [o for o in od if o[3] == "two_sided_complete"]
    assert comp and all(o[0] == "NO" for o in od)
    px_today = RN1.quotes(yes_bid="0.48", yes_ask="0.50", tick="0.01",
                          pos=e.b.m(SLUG)["pos"], cost=e.b.m(SLUG)["cost"],
                          min_qty=1)["NO"]
    assert max(D(o[1]) for o in comp) > D("0.47")   # pair cap alone
    assert all(D(o[2]) == D(str(px_today["qty"])) for o in comp[:1])
    assert "_ah_ref" not in e.b.m(SLUG)


def test_on_held_leg_with_edge_demands_a_discount(en, monkeypatch):
    """YES 2 @ .40, YES fair .55: edge .15 -> NO completion at or under
    NO fair .45 - .02 = .43; today's would rest near the .50 touch."""
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_ADAPTIVE_HEDGE", True)
    _fair(e, monkeypatch, "0.55")
    mk = _held(e)
    e.step_market(SLUG)
    comp = [o for o in _orders(e) if o[3] == "two_sided_complete"]
    assert comp and all(D(o[1]) <= D("0.43") for o in comp)
    assert all(D(o[1]) + D("0.40") <= D("0.97") for o in comp)
    rec = mk["_tf_last"]["hedge_NO"]
    assert D(rec["give"]) == D("-0.02") and D(rec["px"]) <= D("0.43")
    assert D(rec["px"]) <= D(rec["px_today"])
    assert mk["_ah_ref"]["side"] == "YES"


def test_on_takes_the_place_of_the_fixed_give_and_never_bids_above_today(
        en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_HEDGE_GIVE_USD", D("0.01"))
    monkeypatch.setattr(M, "RN1_TF_ADAPTIVE_HEDGE", True)
    _fair(e, monkeypatch, "0.55")
    mk = _held(e)
    e.step_market(SLUG)
    comp = [o for o in _orders(e) if o[3] == "two_sided_complete"]
    assert comp and all(D(o[1]) <= D("0.43") for o in comp)
    assert all(D(o[1]) <= D(mk["_tf_last"]["hedge_NO"]["px_today"])
               for o in comp)


def test_on_hold_places_no_completion_and_says_why(en, monkeypatch, capsys):
    """YES 2 @ .40 entered at fair .70, now .55 (weakened): the give lets
    the NO completion rest above its .45 fair, which costs EV; with risk
    aversion 0 the objective prefers the residual -> no completion bid."""
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_ADAPTIVE_HEDGE", True)
    monkeypatch.setattr(M, "RN1_TF_AH_CFG", dict(
        M.RN1_TF_AH_CFG, risk_aversion="0", edge_k="0", give_max="0.05"))
    _fair(e, monkeypatch, "0.55")
    mk = _held(e)
    mk["_ah_ref"] = {"side": "YES", "qty": "2", "fair": "0.70"}
    e.step_market(SLUG)
    od = _orders(e)
    assert [o for o in od if o[3] == "two_sided_complete"] == []
    assert mk["_tf_last"]["hedge_NO"]["why"] == "ah_hold"
    assert D(mk["_tf_last"]["hedge_NO"]["give"]) > 0
    q = quotes(capsys)[-1]
    assert "ah_hold" in (q.get("why") or {}).values()
    # the same state with real risk aversion hedges, above fair, <= today
    monkeypatch.setattr(M, "RN1_TF_AH_CFG", dict(
        M.RN1_TF_AH_CFG, risk_aversion="0.5", edge_k="0", give_max="0.05"))
    e.step_market(SLUG)
    comp = [o for o in _orders(e) if o[3] == "two_sided_complete"]
    assert comp and all(D("0.45") < D(o[1]) <= D(
        mk["_tf_last"]["hedge_NO"]["px_today"]) for o in comp)


def test_on_error_fails_to_todays_completion(en, monkeypatch):
    ref = None
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_HEDGE_GIVE_USD", D("0.01"))
    _fair(e, monkeypatch, "0.55")
    _held(e)
    e.step_market(SLUG)
    ref = _orders(e)
    assert ref
    # same state, adaptive on but broken -> the same orders stay
    monkeypatch.setattr(M, "RN1_TF_ADAPTIVE_HEDGE", True)

    def boom(**_kw):
        raise RuntimeError("x")
    monkeypatch.setattr(AH, "plan", boom)
    e.step_market(SLUG)
    assert _orders(e) == ref


def test_on_a_widening_plan_is_rejected(en, monkeypatch):
    """A plan that raised the price or the size is discarded (today's)."""
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_HEDGE_GIVE_USD", D("0.01"))
    monkeypatch.setattr(M, "RN1_TF_ADAPTIVE_HEDGE", True)
    _fair(e, monkeypatch, "0.55")
    _held(e)
    monkeypatch.setattr(AH, "plan", lambda **kw: {
        "px": D("0.56"), "qty": D("9"), "hold": False})
    e.step_market(SLUG)
    comp = [o for o in _orders(e) if o[3] == "two_sided_complete"]
    assert comp and all(D(o[1]) <= D("0.46") and D(o[2]) <= 2 for o in comp)


def test_on_openings_still_need_the_edge_gate(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_ADAPTIVE_HEDGE", True)
    monkeypatch.setattr(M, "RN1_TF_EDGE_USD", D("0.50"))
    _fair(e, monkeypatch, "0.60")
    e.step_market(SLUG)
    assert _orders(e) == []


def test_on_ref_cleared_when_paired(en, monkeypatch):
    e, v, _ = en
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_ADAPTIVE_HEDGE", True)
    _fair(e, monkeypatch, "0.55")
    mk = _held(e)
    e.step_market(SLUG)
    assert mk.get("_ah_ref")
    mk["pos"] = {"YES": "2", "NO": "2"}
    mk["cost"] = {"YES": "0.80", "NO": "0.86"}
    e.step_market(SLUG)
    assert "_ah_ref" not in mk


# -------------------------------------------------------------- harness
def _fr(t, bid, ask, ib=None, ia=None):
    return {"t": float(t), "yes_bid": D(bid), "yes_ask": D(ask),
            "bid_q": 100, "ask_q": 100, "state": "MARKET_STATE_OPEN",
            "ib": None if ib is None else D(ib),
            "ia": None if ia is None else D(ia), "ti": float(t)}


def _mk(policy):
    s = dict(B.SIM_DEFAULTS)
    s.update(ladder_levels=1, latency_s=3.0)
    m = B.Market("aec-x-a-b", D("0.01"), 1, dict(policy, directional=False),
                 s)
    m.pos = {"YES": D(2), "NO": D(0)}
    m.cost = {"YES": D("0.80"), "NO": D(0)}
    return m


def test_harness_mirrors_the_engine_price():
    f = _fr(0, "0.48", "0.50", "0.54", "0.56")      # YES fair .55
    a = _mk({})
    a.decide(f, flip=False)
    b = _mk({"adaptive_hedge": {}})
    b.decide(f, flip=False)
    assert a.orders[("NO", 0)]["px"] > D("0.45")
    assert b.orders[("NO", 0)]["px"] == D("0.43")
    assert b.orders[("NO", 0)]["px"] <= a.orders[("NO", 0)]["px"]
    assert b.decisions["ah_repriced"] == 1


def test_harness_no_fair_and_no_key_unchanged():
    f = _fr(0, "0.48", "0.50")
    a = _mk({})
    a.decide(f, flip=False)
    b = _mk({"adaptive_hedge": {}})
    b.decide(f, flip=False)
    assert a.orders == b.orders and b.ah_ref is None
