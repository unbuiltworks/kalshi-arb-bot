"""Offline tests for the sequential-measurement arithmetic. No network, no
credential, no order. Run: python3 -m pytest analysis/l5/measurement/ -q
or: python3 analysis/l5/measurement/test_sample_size.py
"""
from __future__ import annotations

import math
import os
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from analysis.l5.measurement import sample_size as S  # noqa: E402


# ------------------------------------------------- the rebate cliff is REAL

def test_rebate_is_exactly_zero_at_the_two_tiny_sizes():
    for qty in (D("0.01"), D("0.10")):
        for p in (D("0.30"), D("0.45"), D("0.50"), D("0.60")):
            assert S.maker_rebate_usd(p, qty) == D("0.00")


def test_rebate_floor_reproduces_the_frozen_1_62_at_p_045():
    """bounds.py states the floor as 1.62 shares at p=0.45. Independently
    recomputed here from the published formula, not copied."""
    assert S.min_rebate_bearing_contracts(D("0.45")) == D("1.62")


def test_half_even_sends_exactly_half_a_cent_to_zero():
    """The floor is STRICT. theta*C*p*(1-p) == 0.005 must round to $0.00."""
    # 0.0125 * C * 0.25 == 0.005  ->  C = 1.60 exactly, at p = 0.50
    assert S.MAKER_THETA * D("1.60") * D("0.50") * D("0.50") == D("0.005000")
    assert S.maker_rebate_usd(D("0.50"), D("1.60")) == D("0.00")
    assert S.maker_rebate_usd(D("0.50"), D("1.61")) == D("0.01")


def test_1_62_shares_earns_a_cent_only_in_a_narrow_price_band():
    band = S.rebate_bearing_price_band(D("1.62"))
    assert band[0] == D("0.45") and band[-1] == D("0.55")
    assert len(band) == 11


def test_rebate_floor_rises_away_from_fifty_cents():
    assert (S.min_rebate_bearing_contracts(D("0.50"))
            < S.min_rebate_bearing_contracts(D("0.45"))
            < S.min_rebate_bearing_contracts(D("0.60"))
            < S.min_rebate_bearing_contracts(D("0.30")))


# ------------------------------------------------------- worst-case dollars

def test_worst_case_per_leg_is_qty_times_the_frozen_ceiling():
    assert S.worst_case_per_first_leg_usd(D("1.00")) == D("0.6000")
    assert S.worst_case_per_first_leg_usd(D("0.01")) == D("0.0060")
    assert S.worst_case_per_first_leg_usd(D("0.10")) == D("0.0600")
    assert S.worst_case_per_first_leg_usd(D("1.62")) == D("0.9720")


def test_frozen_1_00_share_bound_matches_lane4_exactly():
    """Lane 4's MAX_POSSIBLE_LOSS_USD is $0.60. Must agree, un-redesigned."""
    from analysis.l5.stage6 import bounds as b
    assert (S.worst_case_per_first_leg_usd(b.FIRST_LEG_QTY)
            == b.MAX_POSSIBLE_LOSS_USD.quantize(D("0.0001")))
    assert S.MAX_FIRST_LEG_PRICE == b.MAX_FIRST_LEG_PRICE


def test_worst_case_scales_linearly_in_size():
    a = S.worst_case_per_first_leg_usd(D("0.01"))
    b_ = S.worst_case_per_first_leg_usd(D("1.00"))
    assert b_ == a * 100


def test_cascade_inflates_placements_as_fill_probability_falls():
    assert S.cascade_placements(431, 1.0) == 431
    assert S.cascade_placements(431, 0.5) == 862
    assert S.cascade_placements(431, 0.1) == 4310
    assert S.cascade_placements(100, 0.3) == math.ceil(100 / 0.3)


# ------------------------------------------------------------- sample sizes

def test_posterior_width_quadruples_when_width_halves():
    assert S.n_for_posterior_width(0.10) == 385
    assert S.n_for_posterior_width(0.05) == 4 * 385 - 3  # 1537, ceil effects
    assert S.n_for_posterior_width(0.20) == 97


def test_posterior_width_is_maximal_at_p_half():
    n_half = S.n_for_posterior_width(0.10, 0.5)
    for p in (0.1, 0.25, 0.75, 0.9):
        assert S.n_for_posterior_width(0.10, p) <= n_half


def test_sprt_increments_have_the_right_signs():
    s, f = S.sprt_increments(0.6772, 0.7873)
    assert s > 0 > f


def test_sprt_rejects_degenerate_hypotheses():
    for bad in ((0.5, 0.5), (0.8, 0.2), (0.0, 0.5), (0.5, 1.0)):
        try:
            S.sprt_increments(*bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted degenerate pair {bad}")


def test_sprt_expected_n_is_worst_at_indifference():
    oc = S.sprt_operating_characteristics(0.6772, 0.7873)
    assert oc["expected_n_at_indifference"] > oc["expected_n_under_h0"]
    assert oc["expected_n_at_indifference"] > oc["expected_n_under_h1"]
    assert 0.6772 < oc["indifference_p"] < 0.7873
    assert 80 < oc["expected_n_under_h0"] < 82
    assert 88 < oc["expected_n_under_h1"] < 90
    assert 137 < oc["expected_n_at_indifference"] < 139


def test_sprt_boundaries_are_symmetric_when_alpha_equals_beta():
    a, b_ = S.sprt_boundaries(0.05, 0.05)
    assert math.isclose(a, -b_)


def test_tighter_hypotheses_cost_more_observations():
    near = S.sprt_operating_characteristics(0.75, 0.7873)
    far = S.sprt_operating_characteristics(0.50, 0.7873)
    assert (near["expected_n_at_indifference"]
            > far["expected_n_at_indifference"])


def test_quantile_n_is_cheaper_in_the_tail_than_at_the_median():
    assert S.n_for_quantile(0.90, 0.05) < S.n_for_quantile(0.50, 0.05)
    assert S.n_for_quantile(0.90, 0.05) == 139
    assert S.n_for_quantile(0.50, 0.05) == 385


def test_pnl_sd_is_zero_at_the_degenerate_mixtures():
    assert math.isclose(S.per_leg_pnl_sd_usd_per_share(1.0), 0.0, abs_tol=1e-12)
    assert math.isclose(S.per_leg_pnl_sd_usd_per_share(0.0), 0.0, abs_tol=1e-12)


def test_pnl_sd_uses_the_frozen_stage6_two_point_mixture():
    sd = S.per_leg_pnl_sd_usd_per_share(S.STAGE6_OBSERVED_MATCHED_FRACTION)
    assert 0.16 < sd < 0.17


def test_mean_halfwidth_n_quadruples_when_halfwidth_halves():
    """Exact 4x before ceiling; compared with a 1-unit tolerance for ceil."""
    sd = 0.1647541978381926
    n_wide = S.n_for_mean_halfwidth(sd, 0.08)
    n_tight = S.n_for_mean_halfwidth(sd, 0.04)
    assert abs(n_tight - 4 * n_wide) <= 4
    assert n_tight > n_wide


# -------------------------------------------------------------- the tables

def test_binding_fill_gate_is_the_residual_duration_requirement():
    """1 - 0.6772 = 0.3228 of fills become residuals, so the residual-duration
    p90 is the most expensive fill-denominated gate -- more than the SPRT's
    truncation, more than the economics CI."""
    assert S.binding_fill_gate() == math.ceil(139 / (1 - 0.6772)) == 431


def test_dollar_table_has_exactly_the_four_mandated_sizes():
    rows = S.dollar_table()
    assert [r["qty_shares"] for r in rows] == ["0.01", "0.10", "1.00", "1.62"]


def test_only_the_largest_size_can_observe_a_rebate():
    rows = {r["qty_shares"]: r["rebate_observable"] for r in S.dollar_table()}
    assert rows["0.01"] is False
    assert rows["0.10"] is False
    assert rows["1.00"] is False
    assert rows["1.62"] is True


def test_dollar_table_worst_case_is_monotone_in_size_and_in_rarity():
    rows = S.dollar_table()
    for pf in S.P_FILL_GRID:
        key = f"all_gates_worst_case_usd_at_p_fill_{pf}"
        vals = [D(r[key]) for r in rows]
        assert vals == sorted(vals)
    for r in rows:
        seq = [D(r[f"all_gates_worst_case_usd_at_p_fill_{pf}"])
               for pf in S.P_FILL_GRID]
        assert seq == sorted(seq, reverse=True)  # rarer fills cost more


def test_dollar_table_arithmetic_is_reproducible_by_hand():
    rows = {r["qty_shares"]: r for r in S.dollar_table()}
    r = rows["1.00"]
    assert r["max_loss_per_first_leg_usd"] == "0.6000"
    assert r["max_loss_per_session_usd"] == "0.6000"   # MAX_FIRST_LEGS = 1
    assert r["e1_gate_placements"] == 385
    assert r["e1_gate_worst_case_usd"] == "231.00"     # 385 * 0.60
    assert r["placements_at_p_fill_0.5"] == 862        # ceil(431 / 0.5)
    assert r["all_gates_worst_case_usd_at_p_fill_0.5"] == "517.20"  # *0.60


def test_gate_table_covers_all_five_mandated_estimands():
    keys = list(S.gate_table())
    assert len(keys) == 5
    assert all(k.startswith(("E1_", "E2_", "E3_", "E4_", "E5_")) for k in keys)


def test_this_module_touches_no_live_path():
    """Hygiene: the CODE (docstrings and comments stripped) must contain no
    network, credential or order-submission surface at all."""
    import ast
    tree = ast.parse(open(S.__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            node.value.value = ""          # drop docstrings
    code = ast.unparse(tree)
    for forbidden in ("live_exec", "requests", "http", "api_key", "secret",
                      "private_key", "urllib", "socket", "post_order",
                      "place_order", "credential", "wallet", "signer"):
        assert forbidden not in code.lower(), forbidden


def test_module_imports_only_the_standard_library():
    import ast
    tree = ast.parse(open(S.__file__).read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert mods <= {"__future__", "math", "decimal", "typing", "json"}, mods


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {e!r}")
    print(f"\n{fails} failure(s)")
    sys.exit(1 if fails else 0)
