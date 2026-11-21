"""A cap must brake ONE direction. Every 'does not mean' is pinned here."""
from decimal import Decimal as D

import pytest

from src import l5_residual_saturation as S


# ---------------------------------------------------------------- semantics
def test_a_cap_stops_increasing_residual_once_reached():
    d = S.decide(arm="cap20", residual_shares=20.0, side_up=True, add_shares=5.0)
    assert d["allow"] is False and "saturated" in d["reason"]
    d2 = S.decide(arm="cap20", residual_shares=19.0, side_up=True, add_shares=5.0)
    assert d2["allow"] is True


def test_a_cap_DOES_NOT_liquidate_the_residual():
    """Nothing in this module can emit a sell, a close or a reduce ORDER: it
    only ever answers allow/refuse on an action the engine proposed."""
    d = S.decide(arm="cap20", residual_shares=200.0, side_up=True, add_shares=5.0)
    assert d["allow"] is False
    # STRUCTURAL, not a scan of the prose -- the labels legitimately contain the
    # word "liquidate" while promising the opposite, and a substring check over
    # documentation is how the earlier no-order-path guards produced false
    # positives. The real guarantee is that the verdict carries NO instruction:
    # no side, no size, no order verb, no reduce target. Only allow + why.
    assert set(d) == {"schema", "arm", "cap_shares", "residual_shares",
                      "relation", "labels", "allow", "reason"}
    for forbidden in ("side", "qty", "size", "price", "order", "target",
                      "reduce_to", "action"):
        assert forbidden not in d


@pytest.mark.parametrize("resid", [20.0, 50.0, 500.0, -500.0])
def test_a_cap_DOES_NOT_refuse_an_economically_useful_complement(resid):
    """However saturated, an action that REDUCES |residual| is never blocked."""
    side_up = resid < 0                      # the side that repairs
    d = S.decide(arm="cap20", residual_shares=resid, side_up=side_up,
                 add_shares=5.0, relation="complete")
    assert d["allow"] is True
    assert "never blocked" in d["reason"]


def test_a_cap_DOES_NOT_wait_for_a_cheap_pair_threshold():
    """No pair cost is an input. A saturated arm cannot refuse a repair for
    being expensive, because it is not told the price at all."""
    import inspect
    sig = inspect.signature(S.decide)
    assert "pair" not in " ".join(sig.parameters)
    assert "price" not in " ".join(sig.parameters)


def test_a_cap_DOES_NOT_force_completion_at_expiry():
    """TTE is not an input either, so no arm can behave differently near
    expiry -- in particular none can force a completion there."""
    import inspect
    sig = inspect.signature(S.decide)
    assert "tte" not in " ".join(sig.parameters)


def test_control_is_exactly_current_behaviour():
    for resid in (0.0, 20.0, 5000.0, -5000.0):
        for up in (True, False):
            d = S.decide(arm="CONTROL", residual_shares=resid, side_up=up,
                         add_shares=99.0)
            assert d["allow"] is True and d["cap_shares"] is None


def test_unknown_residual_fails_closed_on_adding_and_only_on_adding():
    d = S.decide(arm="cap20", residual_shares=None, side_up=True, add_shares=5.0)
    assert d["allow"] is False and d["fail_closed"] is True
    assert d["reason"] == S.R_NO_SHARES


# --------------------------------------------------------------- conversion
def test_the_unit_chain_is_derived_not_defaulted():
    """20/35/50 are L5 SHARES. Setting residual_cap_units to those numbers
    would be a ~10x error, so the factor must come from the live state."""
    rep = S.conversion_report(residual_units=-1.9627659574468086,
                              residual_shares=-19.627659574468083,
                              unit_contracts="1.88")
    assert abs(rep["shares_per_unit"] - 10.0) < 1e-9
    assert abs(rep["arms"]["cap20"]["cap_units"] - 2.0) < 1e-9
    assert abs(rep["arms"]["cap50"]["cap_units"] - 5.0) < 1e-9
    assert abs(D(rep["arms"]["cap20"]["cap_contracts"]) - D("3.76")) < D("0.01")
    # and the headline safety fact: every arm is TIGHTER than production today
    assert abs(rep["production_cap_in_shares"] - 60.0) < 1e-9
    for arm in ("cap20", "cap35", "cap50"):
        assert rep["arms"][arm]["cap_units"] < rep["production_cap_units_today"]


def test_an_unestablishable_factor_is_null_with_a_reason_never_ten():
    rep = S.conversion_report(residual_units=0.0, residual_shares=None,
                              unit_contracts=None)
    assert rep["shares_per_unit"] is None
    assert rep["shares_per_unit_reason"]
    for arm in ("cap20", "cap35", "cap50"):
        assert rep["arms"][arm]["cap_units"] is None
        assert rep["arms"][arm]["cap_contracts"] is None
        assert rep["arms"][arm]["reason"]


def test_the_module_has_no_order_path():
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src/l5_residual_saturation.py").read_text()
    tree = ast.parse(src)
    called = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            called.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    for verb in ("place_limit_buy", "place_order", "submit", "cancel", "getenv"):
        assert verb not in called
    mods = {n.module.split(".")[0] for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= {"__future__", "decimal", "typing"}


def test_the_cap_is_a_stop_adding_threshold_not_a_hard_ceiling():
    """DOCUMENTED CONSEQUENCE, not a defect. The cap blocks a NEW increase once
    |residual| >= cap, so the fill taken while just under it lands above:
    cap35 with 10-share fills stops at 40, not 35. A hard ceiling would instead
    require refusing a partial fill the venue already gave us, which the fill
    standard forbids -- we can decline to act, never decline what filled."""
    from src import l5_saturation_shadow as SH
    arms = {n: SH.Arm(n) for n in S.ARMS}
    for _ in range(9):
        r = SH.observe(arms=arms, action="extend", price="0.44", size="1.81",
                       side_up=False, add_shares=10.0, residual_shares=None,
                       residual_units=None, unit_contracts="1.88", filled=True,
                       fill_cost="0.7964", rebate="0.01",
                       maker_evidence="passiveExecution")
    got = {k: v["residual_shares"] for k, v in r["arms"].items()}
    assert got["CONTROL"] == -90.0          # exact current behaviour, unbounded
    assert got["cap20"] == -20.0
    assert got["cap35"] == -40.0            # overshoot by one fill, by design
    assert got["cap50"] == -50.0
    # and every arm is strictly tighter than CONTROL
    for a in ("cap20", "cap35", "cap50"):
        assert abs(got[a]) < abs(got["CONTROL"])


def test_a_refusal_is_a_subtraction_never_a_substitution():
    """A capped arm must never acquire a fill CONTROL did not have: its taken
    count and inventory are a subset of CONTROL's, always."""
    from src import l5_saturation_shadow as SH
    arms = {n: SH.Arm(n) for n in S.ARMS}
    for up in (False, False, False, True, False, True):
        SH.observe(arms=arms, action="x", price="0.50", size="2.00",
                   side_up=up, add_shares=10.0, residual_shares=None,
                   residual_units=None, unit_contracts="1.88", filled=True,
                   fill_cost="1.00", rebate="0", maker_evidence="passiveExecution")
    for a in ("cap20", "cap35", "cap50"):
        assert arms[a].taken <= arms["CONTROL"].taken
        assert arms[a].up <= arms["CONTROL"].up
        assert arms[a].dn <= arms["CONTROL"].dn


def test_the_gate_fails_a_small_sample_rather_than_hiding_it():
    from src import l5_saturation_shadow as SH
    control = {"name": "CONTROL", "net": 0.0, "worst_residual": 90.0,
               "worst_stranded": 90.0}
    good = {"name": "cap20", "settled_campaigns": 3, "net": 5.0,
            "matched_pnl": 6.0, "complement_fills": 4,
            "worst_residual": 20.0, "worst_stranded": 20.0}
    g = SH.advancement_gate(control, good)
    assert all(g["checks"].values())          # every criterion met
    assert g["sample_size_sufficient"] is False and g["passes"] is False
    assert g["settled_campaigns"] == 3
    big = {**good, "settled_campaigns": 30}
    assert SH.advancement_gate(control, big)["passes"] is True


def test_an_arm_cannot_pass_the_gate_on_directional_residual():
    from src import l5_saturation_shadow as SH
    control = {"name": "CONTROL", "net": 0.0, "worst_residual": 90.0,
               "worst_stranded": 90.0}
    lucky = {"name": "cap20", "settled_campaigns": 50, "net": 10.0,
             "matched_pnl": -2.0,          # the matched leg LOST; residual won
             "complement_fills": 4, "worst_residual": 20.0,
             "worst_stranded": 20.0}
    g = SH.advancement_gate(control, lucky)
    assert g["checks"]["4_not_from_directional_residual"] is False
    assert g["passes"] is False
