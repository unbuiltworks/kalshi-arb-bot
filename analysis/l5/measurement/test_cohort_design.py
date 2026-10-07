"""Tests for the E1/E2 prospective-cohort arithmetic.

Offline only: no network, no credential, no order. The first test asserts
that property of the module itself.
"""
from __future__ import annotations

import math
import pathlib
import re
from decimal import Decimal as D, ROUND_HALF_EVEN

import pytest

from analysis.l5.measurement import cohort_design as C
from analysis.l5.measurement.sample_size import maker_rebate_usd

MOD = pathlib.Path(C.__file__)


# ----------------------------------------------------------- safety envelope

def _code_only(path: pathlib.Path) -> str:
    """The module's source with its docstrings removed, so the safety scan
    tests what the module DOES rather than what its prose says about it."""
    import ast
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree))


def test_module_is_offline_and_places_no_order():
    src = _code_only(MOD)
    for forbidden in ("requests", "urllib", "httpx", "socket", "aiohttp",
                      "live_exec", "os.environ", "getenv", "open(",
                      "private_key", "api_key", "subprocess"):
        assert forbidden not in src, f"module references {forbidden!r}"


def test_module_imports_nothing_beyond_stdlib_and_cited_frozen_modules():
    import ast
    allowed_prefixes = ("math", "decimal", "typing", "__future__", "json",
                        "analysis.l5.measurement.sample_size",
                        "analysis.l5.stage6.", "src.pmus_fee_schedule")
    tree = ast.parse(MOD.read_text())
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    for m in mods:
        assert m.startswith(allowed_prefixes), m


def test_module_declares_not_authorized_not_armed():
    head = MOD.read_text()[:1200]
    assert "NOT AUTHORIZED" in head and "NOT ARMED" in head


def test_frozen_stage6_modules_untouched_by_this_lane():
    """This lane reads analysis/l5/stage6/ and never writes it."""
    import analysis.l5.stage6.bounds as B
    B.assert_bounds_coherent()
    assert B.FIRST_LEG_QTY == D("1.00")
    assert B.MAX_FIRST_LEG_PRICE == D("0.60")
    assert B.PAIR_COST_CEILING == D("0.96")
    assert B.REBATE_IS_ASSUMED_ZERO is True


def test_fee_schedule_fingerprint_is_the_mandated_one():
    import src.pmus_fee_schedule as F
    F.assert_frozen()
    assert F.FINGERPRINT == (
        "ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629")
    assert F.taker_theta(F.parse_ts("2026-10-07T00:00:00+00:00")) == D("0.0695")


def test_taker_theta_constant_is_not_the_stale_six_percent():
    assert C.TAKER_THETA == D("0.0695")
    assert C.TAKER_THETA != D("0.06")


# ------------------------------------------------- observability arithmetic

def test_venue_minimum_has_exactly_one_fill_state():
    """At 0.01 a partial fill is indistinguishable from a full one."""
    assert C.distinguishable_fill_states(D("0.01")) == 1
    assert C.fill_fraction_resolution(D("0.01")) == 1.0


@pytest.mark.parametrize("qty,states", [
    (D("0.01"), 1), (D("0.02"), 2), (D("0.10"), 10),
    (D("0.13"), 13), (D("1.00"), 100), (D("1.62"), 162),
])
def test_fill_states_are_qty_over_grid(qty, states):
    assert C.distinguishable_fill_states(qty) == states


def test_off_grid_and_sub_minimum_qty_rejected():
    with pytest.raises(ValueError):
        C.distinguishable_fill_states(D("0.005"))
    with pytest.raises(ValueError):
        C.distinguishable_fill_states(D("0.015"))


@pytest.mark.parametrize("eps,qty", [
    (0.50, D("0.02")), (0.25, D("0.04")), (0.10, D("0.10")),
    (0.05, D("0.20")), (0.01, D("1.00")),
])
def test_min_qty_for_fill_fraction_resolution(eps, qty):
    assert C.min_qty_for_fill_fraction_resolution(eps) == qty
    assert C.fill_fraction_resolution(qty) <= eps + 1e-12


def test_the_frozen_1_00_share_is_exactly_the_eps_0_01_choice():
    """S6P's 1.00 share corresponds to percentile fill-fraction resolution --
    an observability choice (fitted: False), not an economic one."""
    assert C.min_qty_for_fill_fraction_resolution(0.01) == D("1.00")
    import analysis.l5.stage6.bounds as B
    assert B.BOUND_PROVENANCE["FIRST_LEG_QTY"]["fitted"] is False
    # and it sits BELOW every rebate floor on the band
    assert maker_rebate_usd(D("0.50"), B.FIRST_LEG_QTY) == D("0.00")


# ------------------------------- the cent-observable settlement floor, 0.13

def test_locked_pair_gross_is_four_cents_per_share():
    assert C.MIN_LOCKED_PAIR_GROSS_USD == D("0.04")


def test_settlement_cent_floor_is_0_13_and_is_strict():
    floor = C.min_qty_for_cent_observable(C.MIN_LOCKED_PAIR_GROSS_USD)
    assert floor == D("0.13")
    # 0.13 * 0.04 = 0.0052 -> $0.01 ; 0.12 * 0.04 = 0.0048 -> $0.00
    assert (D("0.13") * D("0.04")).quantize(
        D("0.01"), rounding=ROUND_HALF_EVEN) == D("0.01")
    assert (D("0.12") * D("0.04")).quantize(
        D("0.01"), rounding=ROUND_HALF_EVEN) == D("0.00")


def test_cent_observable_floor_rejects_non_positive():
    with pytest.raises(ValueError):
        C.min_qty_for_cent_observable(D("0"))


# ------------------------------------------------------ E1 minimum quantity

def test_e1_minimum_is_derived_as_the_max_of_the_floors():
    m = C.e1_minimum_quantity()
    floors = [D(v) for v in m["per_estimand_floor_shares"].values()]
    assert D(m["derived_minimum_shares"]) == max(floors)
    assert D(m["derived_minimum_shares"]) == D("0.13")
    assert m["binding_estimands"] == ["8_settlement_end_to_end"]


def test_e1_minimum_covers_all_eight_estimands():
    m = C.e1_minimum_quantity()
    assert len(m["per_estimand_floor_shares"]) == 8
    assert len(C.ESTIMAND_OBSERVABILITY) == 8
    q = D(m["derived_minimum_shares"])
    for k, v in C.ESTIMAND_OBSERVABILITY.items():
        assert q >= D(v["floor_shares"]), k


def test_e1_minimum_is_not_rebate_bearing_and_says_so():
    m = C.e1_minimum_quantity()
    assert m["rebate_bearing_anywhere"] is False
    assert m["not_chosen_for"].startswith("expected return")
    assert m["owner_chooses"] is True


def test_e1_minimum_worst_case_per_placement():
    assert C.worst_case_per_first_leg_usd(D("0.13")) == D("0.0780")


# =================================================== the dynamic rebate floor

@pytest.mark.parametrize("price,contracts", [
    (D("0.01"), D("40.41")),
    (D("0.05"), D("8.43")),
    (D("0.10"), D("4.45")),
    (D("0.20"), D("2.51")),
    (D("0.30"), D("1.91")),
    (D("0.40"), D("1.67")),
    (D("0.45"), D("1.62")),
    (D("0.50"), D("1.61")),
    (D("0.55"), D("1.62")),
    (D("0.60"), D("1.67")),
])
def test_min_rebate_bearing_contracts_matches_the_published_formula(
        price, contracts):
    assert C.min_rebate_bearing_contracts(price) == contracts
    # the floor earns a cent and one grid step below it earns nothing
    assert maker_rebate_usd(price, contracts) > D("0")
    assert maker_rebate_usd(price, contracts - D("0.01")) == D("0.00")


def test_reproduces_the_bounds_py_stated_floor_at_p_0_45():
    import analysis.l5.stage6.bounds as B
    assert C.min_rebate_bearing_contracts(D("0.45")) == \
        B.REBATE_CENT_FLOOR_SHARES


def test_global_minimum_of_the_floor_is_1_61_and_includes_p_0_50():
    floors = {p: C.min_rebate_bearing_contracts(D(f"0.{p:02d}"))
              for p in range(1, 100)}
    assert min(floors.values()) == D("1.61")
    assert floors[50] == D("1.61")
    # the minimum is a plateau around 0.50, symmetric about it
    plateau = sorted(p for p, c in floors.items() if c == D("1.61"))
    assert plateau[0] + plateau[-1] == 100


# --- the exact $0.005 ROUND_HALF_EVEN boundary, which is the whole point ---

@pytest.mark.parametrize("price,contracts", [
    (D("0.20"), D("2.50")),   # 0.0125 * 2.50 * 0.16 == 0.005 exactly
    (D("0.50"), D("1.60")),   # 0.0125 * 1.60 * 0.25 == 0.005 exactly
])
def test_exactly_half_a_cent_rounds_to_zero_so_the_floor_is_strict(
        price, contracts):
    raw = C.MAKER_THETA * contracts * price * (D(1) - price)
    assert raw == D("0.005"), "this case must be the exact boundary"
    assert maker_rebate_usd(price, contracts) == D("0.00"), \
        "ROUND_HALF_EVEN sends exactly $0.005 to $0.00 (0 is the even digit)"
    # so the floor is one grid step ABOVE the exact-boundary count
    assert C.min_rebate_bearing_contracts(price) == contracts + D("0.01")
    assert C._rebate_threshold_contracts(price) == contracts


def test_threshold_is_the_exact_half_cent_crossing_everywhere_on_the_band():
    p = D("0.01")
    while p <= D("0.60"):
        t = C._rebate_threshold_contracts(p)
        raw = C.MAKER_THETA * t * p * (D(1) - p)
        assert abs(raw - D("0.005")) < D("1E-20"), p
        p += D("0.01")


def test_whole_first_leg_band_every_tick_floor_is_correct_and_strict():
    """Exhaustive over p <= 0.60: the floor earns a cent, the step below
    earns nothing, and the floor is the SMALLEST grid count that does."""
    p = D("0.01")
    while p <= D("0.60"):
        c = C.min_rebate_bearing_contracts(p)
        assert maker_rebate_usd(p, c) > D("0"), p
        below = c - D("0.01")
        assert below >= D("0.01")
        assert maker_rebate_usd(p, below) == D("0.00"), (p, below)
        p += D("0.01")


def test_floor_is_monotone_moving_away_from_one_half():
    """The floor scales as 1/(p(1-p)), so it rises toward either band edge."""
    for lo in range(1, 50):
        a = C.min_rebate_bearing_contracts(D(f"0.{lo:02d}"))
        b = C.min_rebate_bearing_contracts(D(f"0.{lo + 1:02d}"))
        assert a >= b, lo


@pytest.mark.parametrize("lo,hi,contracts", [
    (D("0.45"), D("0.55"), D("1.62")),
    (D("0.30"), D("0.60"), D("1.91")),
    (D("0.20"), D("0.60"), D("2.51")),
    (D("0.10"), D("0.60"), D("4.45")),
    (D("0.01"), D("0.60"), D("40.41")),
])
def test_across_band_is_the_max_over_the_band(lo, hi, contracts):
    assert C.min_rebate_bearing_contracts_across_band(lo, hi) == contracts
    p = lo
    while p <= hi:
        assert maker_rebate_usd(p, contracts) > D("0"), p
        p += D("0.01")


def test_the_full_placeable_band_is_not_1_91():
    """The prior protocol's '1.91 covers p <= 0.60' holds only for a band
    that stops at p = 0.30. The full range reaches p = 0.01."""
    assert C.min_rebate_bearing_contracts_across_band() == D("40.41")
    assert maker_rebate_usd(D("0.01"), D("1.91")) == D("0.00")


def test_1_62_is_rebate_bearing_only_near_fifty_cents():
    band = [p for p in (D(f"0.{i:02d}") for i in range(1, 100))
            if maker_rebate_usd(p, D("1.62")) > D("0")]
    assert band[0] == D("0.45") and band[-1] == D("0.55")
    assert len(band) == 11


def test_bad_price_rejected():
    for bad in (D("0"), D("1"), D("1.5"), D("-0.1")):
        with pytest.raises(ValueError):
            C.min_rebate_bearing_contracts(bad)
    with pytest.raises(ValueError):
        C.min_rebate_bearing_contracts_across_band(D("0.60"), D("0.10"))


def test_max_maker_rebate_per_share_bound():
    """theta/4 = $0.003125 asymptotically; the generous bound with cent
    rounding at the smallest rebate-bearing size is $0.006231."""
    b = C.max_maker_rebate_per_share_usd()
    assert b == D("0.006231")
    assert b > C.MAKER_THETA / 4
    # the asymptotic per-share ceiling, cent rounding aside, is theta/4
    assert C.MAKER_THETA / 4 == D("0.003125")
    # the bound is attained at the global-minimum floor, p = 0.50, C = 1.61
    attained = ((C.MAKER_THETA * D("1.61") * D("0.25") + D("0.005"))
                / D("1.61")).quantize(D("0.000001"), rounding=ROUND_HALF_EVEN)
    assert attained == b


# ======================================================== E1 stopping gates

def test_eight_estimands_are_preregistered_with_unit_method_falsifier():
    g = C.e1_gate_table()
    assert len(g) == 8
    for k, v in g.items():
        assert v["method"], k
        assert v["unit"], k
        assert "falsified_by" in v, k
        assert v["denominated_in"] in (
            "placements", "fills", "completions", "residuals"), k


def test_estimand_order_and_names_match_the_observability_table():
    assert list(C.e1_gate_table()) == list(C.ESTIMAND_OBSERVABILITY)


def test_binding_gates_reuse_the_prior_protocols_arithmetic():
    b = C.e1_binding_gates()
    assert b["placement_gate"] == 385      # Beta(1,1) width 0.10 at p = 0.5
    assert b["fill_gate"] == 431           # 139 / (1 - 0.6772)
    assert b["binding_fill_estimand"] == "5_residual_frequency_and_duration"


def test_cancellation_opportunity_costs_no_extra_dollars():
    v = C.e1_gate_table()["6_cancellation_opportunity"]
    assert v["denominated_in"] == "placements"
    assert "ZERO" in v["extra_cost"]


def test_adverse_selection_does_not_inherit_the_1_000_loss_rate():
    v = C.e1_gate_table()["7_adverse_selection"]
    note = v["historical_prior_NOT_inherited"]
    assert "not a forecast" in note and "NOT used" in note
    assert v["denominated_in"] == "fills"


def test_no_mean_time_to_completion_is_an_estimand():
    for k in ("4_time_to_completion_distribution", "3_partial_fill_structure"):
        assert "No mean" in C.e1_gate_table()[k]["method"] or \
               "no mean" in C.e1_gate_table()[k]["method"]


def test_settlement_estimand_forbids_premature_realized_label():
    v = C.e1_gate_table()["8_settlement_end_to_end"]
    assert "REALIZED" in v["method"] and "confirmed cash" in v["method"]


def test_placements_cascade_through_the_unknown_p_fill():
    assert C.e1_placements(0.10) == 4310
    assert C.e1_placements(0.25) == 1724
    assert C.e1_placements(0.50) == 862
    assert C.e1_placements(0.87) == 496
    assert C.e1_placements(1.00) == 431


def test_placements_never_below_the_placement_gate():
    assert C.e1_placements(1.0) >= 385


# --------------------------------------------------- PASS / FAIL / CONTINUE

def test_all_three_verdicts_exist_with_numeric_boundaries():
    r = C.e1_decision_rules()
    assert set(("PASS", "FAIL", "CONTINUE")) <= set(r)
    assert r["PASS"]["meaning"].startswith("E2 IS JUSTIFIED")
    assert "error_rate" in r["PASS"]
    for rule in r["FAIL"]["any_of"]:
        assert rule["numeric"] and rule["learned"] and rule["error_rate"]


def test_continue_has_a_hard_truncation_so_it_cannot_run_forever():
    r = C.e1_decision_rules()["CONTINUE"]
    assert r["cannot_run_forever"] is True
    assert r["hard_truncation_placements"] == 4310
    assert C.e1_hard_truncation_placements() == C.e1_placements(0.10)
    assert "INCONCLUSIVE" in r["on_truncation"]


def test_abort_is_reported_as_a_failure_to_measure_not_as_a_verdict():
    r = C.e1_decision_rules()
    assert "FAILURE TO MEASURE" in r["ABORT_IS_NOT_A_VERDICT"]
    assert "OWNER VALUE" in r["ABORT_IS_NOT_A_VERDICT"]


def test_beta_ci_boundary_cases_are_exact_powers():
    lo, hi = C.beta_ci(0, 40)
    assert lo == 0.0
    assert hi == pytest.approx(1 - 0.025 ** (1 / 41), abs=1e-12)
    # the prior protocol's stated figure for 0 fills in 40 placements
    assert hi == pytest.approx(0.086, abs=0.001)
    lo2, hi2 = C.beta_ci(40, 40)
    assert hi2 == 1.0
    assert lo2 == pytest.approx(0.025 ** (1 / 41), abs=1e-12)


def test_beta_ci_rejects_bad_input():
    for k, n in ((-1, 10), (11, 10), (0, 0)):
        with pytest.raises(ValueError):
            C.beta_ci(k, n)


def test_fail_boundary_consecutive_misses_is_35_and_is_consistent():
    n = C.e1_fail_boundary_consecutive_misses()
    assert n == 35
    assert C.beta_ci(0, n)[1] < C.E1_FAIL_P_FILL_UPPER
    assert C.beta_ci(0, n - 1)[1] >= C.E1_FAIL_P_FILL_UPPER


def test_fail_boundary_is_derived_from_the_priced_grid_not_invented():
    assert C.E1_FAIL_P_FILL_UPPER == min(C.P_FILL_GRID)


def test_eight_consecutive_non_completions_cross_the_sprt_h0_boundary():
    from analysis.l5.measurement.sample_size import (
        sprt_operating_characteristics)
    oc = sprt_operating_characteristics(0.6772, 0.7873)
    assert 8 * oc["log_lr_failure"] < oc["log_b"]
    assert 7 * oc["log_lr_failure"] > oc["log_b"]


def test_economics_foreclosure_boundary_is_the_rebate_ceiling_negated():
    assert C.e1_economics_foreclosure_usd_per_share() == D("-0.006231")
    assert C.e1_economics_foreclosure_usd_per_share() == \
        -C.max_maker_rebate_per_share_usd()


# =========================================================== worst-case $

def test_worst_case_per_placement_is_qty_times_the_frozen_ceiling():
    for qty in (D("0.01"), D("0.10"), D("0.13"), D("1.00"), D("1.62")):
        assert C.worst_case_per_first_leg_usd(qty) == \
            (qty * C.MAX_FIRST_LEG_PRICE).quantize(D("0.0001"))


def test_worst_case_table_reproduces_the_prior_protocols_cells():
    rows = {r["qty_shares"]: r for r in C.worst_case_table(
        [D("0.01"), D("0.10"), D("1.00")])}
    assert rows["0.01"]["estimand_1_only_worst_case_usd"] == "2.31"
    assert rows["1.00"]["estimand_1_only_worst_case_usd"] == "231.00"
    assert rows["1.00"]["all_gates_worst_case_usd_at_p_fill_0.5"] == "517.20"
    assert rows["0.10"]["all_gates_worst_case_usd_at_p_fill_0.1"] == "258.60"


def test_worst_case_at_the_derived_minimum():
    r = C.worst_case_table([D("0.13")])[0]
    assert r["worst_case_per_placement_usd"] == "0.0780"
    assert r["estimand_1_only_worst_case_usd"] == "30.03"
    assert r["all_gates_worst_case_usd_at_p_fill_0.1"] == "336.18"
    assert r["all_gates_worst_case_usd_at_p_fill_0.25"] == "134.47"
    assert r["all_gates_worst_case_usd_at_p_fill_0.5"] == "67.24"
    assert r["all_gates_worst_case_usd_at_p_fill_0.87"] == "38.69"


def test_reference_sizes_fourth_entry_is_computed_not_1_62():
    sizes = C.reference_sizes()
    assert sizes[:3] == [D("0.01"), D("0.10"), D("1.00")]
    assert sizes[3] == D("40.41")
    assert sizes[3] != D("1.62")


def test_worst_case_is_a_bound_monotone_in_qty_and_in_placements():
    rows = C.worst_case_table([D("0.01"), D("0.10"), D("1.00")])
    vals = [D(r["all_gates_worst_case_usd_at_p_fill_0.5"]) for r in rows]
    assert vals == sorted(vals)
    for r in rows:
        a = D(r["all_gates_worst_case_usd_at_p_fill_0.1"])
        b = D(r["all_gates_worst_case_usd_at_p_fill_0.87"])
        assert a > b  # rarer fills cost more placements


# ======================================= E2 IS STRUCTURALLY GATED ON A PASS

E2_ENTRY_POINTS = (
    lambda v: C.e2_quantity(D("0.50"), e1_verdict=v),
    lambda v: C.e2_gate_table(e1_verdict=v),
    lambda v: C.e2_decision_rules(e1_verdict=v),
    lambda v: C.e2_worst_case_table(e1_verdict=v),
)


@pytest.mark.parametrize("fn", E2_ENTRY_POINTS)
@pytest.mark.parametrize("verdict", [
    "FAIL", "CONTINUE", "INCONCLUSIVE", "ABORT", "pass", "", "PASS ", "None"])
def test_every_e2_entry_point_refuses_without_an_e1_pass(fn, verdict):
    with pytest.raises(C.E2NotAuthorized):
        fn(verdict)


@pytest.mark.parametrize("fn", E2_ENTRY_POINTS)
def test_e2_entry_points_work_only_on_an_exact_pass(fn):
    assert fn("PASS") is not None


def test_e2_refusal_message_says_a_pass_is_still_not_an_authorization():
    with pytest.raises(C.E2NotAuthorized) as e:
        C.e2_gate_table(e1_verdict="CONTINUE")
    msg = str(e.value)
    assert "not an authorization" in msg
    assert "INCONCLUSIVE truncation is not a PASS" in msg


# ------------------------------------------------------------ E2 mechanics

def test_e2_quantity_is_price_dependent_and_never_hardcodes_1_62():
    for p, c in ((D("0.50"), "1.61"), (D("0.45"), "1.62"),
                 (D("0.60"), "1.67"), (D("0.30"), "1.91"),
                 (D("0.01"), "40.41")):
        q = C.e2_quantity(p, e1_verdict="PASS")
        assert q["min_rebate_bearing_contracts_at_this_price"] == c
        assert q["assumed_1_62"] is False


def test_e2_quantity_reports_the_across_band_requirement_too():
    q = C.e2_quantity(D("0.50"), e1_verdict="PASS")
    assert q["min_rebate_bearing_contracts_across_band"] == "40.41"
    assert q["worst_case_per_placement_across_band_usd"] == "24.2460"


def test_e2_has_exactly_seven_economic_lines_kept_separate():
    assert len(C.E2_ECONOMIC_LINES) == 7
    assert "maker_rebates_CREDITED_ONLY" in C.E2_ECONOMIC_LINES
    assert "liquidity_rewards_CREDITED_ONLY" in C.E2_ECONOMIC_LINES
    g = C.e2_gate_table(e1_verdict="PASS")["R3_seven_line_economics"]
    assert "NEVER summed" in g["summation_rule"]
    assert "UNMEASURED, not zero" in g["summation_rule"]


def test_e2_rebates_are_credited_only_never_accrued():
    g = C.e2_gate_table(e1_verdict="PASS")
    est = g["R1_credit_occurrence"]["estimand"]
    assert "CREDITS" in est and "NEGATIVE commissionNotionalCollected" in est
    assert "never read as zero credited" in est


def test_e2_credit_estimand_matches_the_frozen_settlement_reader():
    """R1's source must be the frozen settlement module's own rule."""
    from analysis.l5.stage6.settlement import credited_rebates_usd
    out = credited_rebates_usd([
        {"commissionNotionalCollected": "-0.01"},
        {"commissionNotionalCollected": "0.02"},
        {},
    ])
    assert out["credited_usd"] == "0.0100"
    assert out["charged_usd"] == "0.0200"
    assert out["executions_missing_the_field"] == 1
    assert out["absent_field_read_as_zero_credited"] is False


def test_e2_credit_fail_rule_is_35_consecutive_uncredited_fills():
    g = C.e2_gate_table(e1_verdict="PASS")
    assert "35 consecutive" in g["R1_credit_occurrence"]["fail_rule"]
    assert C.n_consecutive_for_upper_bound(0.10) == 35


def test_e2_taker_line_flags_the_date_ambiguous_window():
    n = C.e2_gate_table(e1_verdict="PASS")["R4_taker_cost"]["notes"]
    assert "DATE-AMBIGUOUS" in n and "2026-09-17" in n and "FLAGGED" in n
    assert "taker_rebate_available_to_this_account: False" in n


def test_e2_decision_rules_have_all_three_verdicts_and_a_truncation():
    r = C.e2_decision_rules(e1_verdict="PASS")
    assert set(("PASS", "FAIL", "CONTINUE")) <= set(r)
    assert r["CONTINUE"]["hard_truncation_placements"] == 4310
    assert r["ABORT_IS_NOT_A_VERDICT"] is True


def test_e2_worst_case_covers_the_rebate_bearing_size_family():
    rows = C.e2_worst_case_table(e1_verdict="PASS")
    qtys = [r["qty_shares"] for r in rows]
    assert qtys == ["1.61", "1.62", "1.91", "2.51", "4.45", "40.41"]
    for r in rows:
        assert r["rebate_bearing_at_p_0.50"] is True
    assert rows[0]["worst_case_per_placement_usd"] == "0.9660"
    assert rows[-1]["worst_case_per_placement_usd"] == "24.2460"


def test_e2_sizes_are_all_at_or_above_the_global_rebate_floor():
    for r in C.e2_worst_case_table(e1_verdict="PASS"):
        assert D(r["qty_shares"]) >= D("1.61")


# ---------------------------------------------------- documents and headers

@pytest.mark.parametrize("name", ["E1_COHORT_DESIGN.md", "E2_COHORT_DESIGN.md"])
def test_design_documents_carry_the_not_authorized_header(name):
    p = MOD.parent / name
    assert p.exists(), name
    head = p.read_text()[:1500]
    assert "NOT AUTHORIZED" in head
    assert "NOT ARMED" in head


def test_e2_document_states_it_is_void_without_an_e1_pass():
    head = (MOD.parent / "E2_COHORT_DESIGN.md").read_text()[:2500]
    assert "VOID" in head
    assert "E1" in head and "PASS" in head


def test_documents_do_not_claim_authorization_or_set_owner_values():
    for name in ("E1_COHORT_DESIGN.md", "E2_COHORT_DESIGN.md"):
        txt = (MOD.parent / name).read_text()
        assert not re.search(r"\bI authorize\b", txt, re.I)
        assert not re.search(r"\bARMED: *(yes|true)\b", txt, re.I)
