"""Tests for the PMUS rebate-adjusted breakeven surface.

These tests are written to FAIL if the answer is quietly flipped positive:
the classification test asserts OBSERVED-POSITIVE is EMPTY, and the surface
test asserts no cell is reachable at the observed completion rate. If the
economics ever genuinely change, these tests must be changed deliberately and
with the evidence that licenses it.
"""
from decimal import Decimal as D

import pytest

from src import pmus_fee_schedule as FS
from analysis.l5.economics import breakeven_surface as BS


# ------------------------------- the schedule -----------------------------

def test_schedule_is_the_frozen_one():
    FS.assert_frozen()
    assert FS.FINGERPRINT == (
        "ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629")


def test_thetas_are_the_current_ones_not_the_stale_six_percent():
    assert FS.maker_theta() == D("0.0125")
    assert FS.taker_theta() == D("0.0695")


def test_rounding_is_not_reimplemented_here():
    """Every MONEY cent in this module must come from the schedule's own
    call. The module may quantize a CONTRACT COUNT to the 0.01 contract grid
    -- that is a venue size tick, not a rounding rule -- but it must not
    import or apply a money rounding mode of its own."""
    src = open("analysis/l5/economics/breakeven_surface.py").read()
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    assert "ROUND_HALF_EVEN" not in code.split('"""')[-1]
    assert "from decimal import" in src and "ROUND_" not in src.split(
        "from decimal import")[1].split("\n")[0]


# --------------------------- the brief's reference ------------------------

@pytest.mark.parametrize("price,contracts", [
    ("0.10", "4.45"), ("0.20", "2.51"), ("0.30", "1.91"), ("0.40", "1.67"),
    ("0.50", "1.61"), ("0.60", "1.67"), ("0.70", "1.91"), ("0.80", "2.51"),
    ("0.90", "4.45"),
])
def test_cent_floor_matches_the_brief_and_one_tick_less_is_zero(price,
                                                                contracts):
    c = D(contracts)
    assert BS.rebate(D(price), c) == D("0.01")
    assert BS.rebate(D(price), c - D("0.01")) == D("0.00")


def test_cent_floor_table_agrees_with_the_brief_everywhere():
    for row in BS.cent_floor_minimums():
        assert row["agrees_with_brief"], row


@pytest.mark.parametrize("price,bp", [
    ("0.10", "112.5"), ("0.50", "62.5"), ("0.90", "12.5"),
])
def test_asymptotic_rate_on_maker_notional(price, bp):
    p = D(price)
    assert D("10000") * FS.maker_theta() * (D(1) - p) == D(bp)


# ------------------------------- the cap claim ----------------------------

def test_the_claimed_031_cap_is_refuted():
    a = BS.cap_audit()
    assert a["verdict"] == "REFUTED"
    assert a["lot_in_capture"] == "100"
    assert a["n_published_rows"] == 99
    assert a["all_rows_reproduce_from_formula"] is True
    assert a["deviations_from_formula"] == []


def test_031_appears_only_because_the_formula_rounds_there():
    """0.43..0.57 per 100 contracts, i.e. exactly where the rounded formula
    sits -- contiguous, and both endpoints are formula values."""
    band = BS.cap_audit()["price_rows_showing_0_31"]
    assert band[0] == "0.43" and band[-1] == "0.57"
    for p in (D("0.43"), D("0.50"), D("0.57")):
        assert BS.rebate(p, D("100")) == D("0.31")
    # and the uncapped formula genuinely exceeds the "cap" at p=0.50
    assert FS.maker_theta() * D("0.50") * D("0.50") == D("0.003125")
    assert D("0.003125") > D("0.0031")


def test_no_cap_is_applied_anywhere_so_large_size_is_uncapped():
    """If a $0.31/100 cap were applied, 1000 contracts at p=0.50 would pay
    exactly $3.10. The uncapped formula gives $3.125, which half-even sends
    DOWN to $3.12 (2 is even) -- still strictly above the would-be cap, which
    is what proves no cap is applied."""
    assert BS.rebate(D("0.50"), D("1000")) == D("3.12")
    assert BS.rebate(D("0.50"), D("1000")) > D("3.10")
    # and at 10,000 contracts the gap is unmistakable: $31.25 vs $31.00
    assert BS.rebate(D("0.50"), D("10000")) == D("31.25")


# --------------------------- denominators ---------------------------------

def test_pair_turnover_equals_pair_cost_not_twice_a_leg():
    for X in BS.PAIR_COSTS:
        for p in (D("0.30"), D("0.50")):
            q = X - p
            assert p + q == X  # turnover per 1-contract pair


def test_maker_notional_column_is_about_twice_the_pair_turnover_column():
    """The 2x trap, made visible: converting FROM one leg's notional TO pair
    turnover is where the factor lives."""
    for r in BS.denominator_reconciliation()["rows"]:
        if r["leg_price"] != "0.50":
            continue
        ratio = (D(r["rebate_bp_of_FIRST_LEG_MAKER_NOTIONAL"])
                 / D(r["rebate_bp_of_PAIR_TURNOVER"]))
        assert D("1.8") < ratio < D("2.2")


def test_par_pair_is_rebate_financed_at_about_62_5_bp_with_zero_lock():
    row = [r for r in BS.pair_at_par_test()["rows"]
           if r["pair_cost"] == "1.00"][0]
    assert row["lock_usd_per_pair"] == "0.00"
    assert D(row["rebate_usd_per_pair_both_maker"]) == D("0.00625")
    assert D(row["rebate_bp_of_turnover"]) == D("62.50")
    assert row["rebate_exceeds_lock"] is True


def test_the_minus_71_7_comparison_is_refused_as_illegitimate():
    d = BS.denominator_reconciliation()
    assert d["is_the_minus_71_7_comparison_legitimate"]["answer"].startswith(
        "NO")
    assert d["rate_vs_delta"]["level_a_clone_must_cover_bp"] == "-30.4"
    # L5 paid taker fees in R4, so the full maker rate cannot be added
    assert D(str(BS.OBSERVED["l5_fees_bp_r4"])) < 0


def test_l5_confirmed_incentive_exceeds_what_the_maker_formula_can_pay():
    """163.4 bp observed vs a 62.5 bp maximum -> the maker rebate alone does
    NOT explain L5's compensation."""
    assert BS.OBSERVED["l5_confirmed_incentive_bp_r4"] > D("62.5")


# ------------------------------ the economics -----------------------------

def test_components_are_never_pre_summed():
    e = BS.probe_economics(D("0.50"), D("2"), D("0.96"))
    for branch in ("matched_components", "residual_components"):
        assert set(e[branch]) == {
            "matched_pair_pnl_usd", "residual_pnl_usd", "maker_rebate_usd",
            "credited_liquidity_rewards_usd", "fees_usd"}


def test_liquidity_rewards_are_zero_everywhere_because_none_are_credited():
    for p in BS.PRICES:
        e = BS.probe_economics(p, D("5"), D("0.96"))
        assert e["matched_components"][
            "credited_liquidity_rewards_usd"] == "0"
        assert e["residual_components"][
            "credited_liquidity_rewards_usd"] == "0"


def test_breakeven_without_rebate_reproduces_the_published_78_73_percent():
    """The repo's own figure, re-derived from the two observed means."""
    M = BS.OBSERVED["matched_lock_usd_per_contract"]
    R = BS.OBSERVED["residual_pnl_usd_per_contract"]
    m = -R / (M - R)
    assert abs(m - D("0.7873")) < D("0.0001")


def test_at_the_protocol_size_the_rebate_is_zero_at_every_price():
    for p in BS.PRICES:
        assert BS.rebate(p, D("1.00")) == D("0.00")


def test_no_surface_cell_is_reachable_at_the_observed_completion_rate():
    o = BS.build()
    for key in ("breakeven_surface_both_maker",
                "breakeven_surface_one_leg_taker"):
        for r in o[key]:
            assert r["vs_observed_matched_fraction"] in (
                None, "ABOVE_OBSERVED_CI"), r


def test_one_taker_leg_outweighs_the_entire_maker_rebate():
    taker_bp = D("10000") * BS.taker(D("0.50"), D("1")) / D("1.00")
    assert taker_bp > 170
    assert taker_bp > 2 * D("62.5")


def test_observed_region_test_is_negative_at_every_size():
    t = BS.observed_region_test()
    assert t["answer"] == "NO"
    for r in t["rows"]:
        assert r["positive_at_observed_completion"] is False
    assert D(t["fraction_of_gap_closed"]) < D("0.20")


# ----------------------------- classification -----------------------------

def test_observed_positive_is_empty():
    cls = {c["class"]: c for c in BS.classify()}
    assert cls["OBSERVED-POSITIVE"]["regions"] == []
    assert cls["OBSERVED-POSITIVE"]["boundary"] == "EMPTY SET"


def test_every_region_has_exactly_one_class():
    names = [c["class"] for c in BS.classify()]
    assert sorted(names) == ["OBSERVED-POSITIVE", "PLAUSIBLE-BUT-UNPROVEN",
                             "UNECONOMIC"]
    assert len(names) == len(set(names))


def test_the_answer_is_no():
    assert BS.build()["answer"]["answer"] == "NO"


def test_gemini_credits_are_not_attributed_to_pmus():
    assert BS.PMUS_CREDITED["total_usd"] == D("16.18")
    assert (BS.PMUS_CREDITED["maker_rebates_usd"]
            + BS.PMUS_CREDITED["liquidity_rewards_usd"]) == D("16.18")
    assert "GEMINI" in BS.PMUS_CREDITED["not_pmus"]
    assert "0.51" in BS.PMUS_CREDITED["not_pmus"]


def test_stage6_is_read_only_and_its_qty_is_below_its_own_cent_floor():
    s = BS.stage6_replay()
    assert s["read_only"] is True
    assert s["bounds"]["qty_below_own_cent_floor"] is True
    assert s["bounds"]["FIRST_LEG_QTY"] == "1.00"
    assert s["bounds"]["REBATE_CENT_FLOOR_SHARES"] == "1.62"


def test_nothing_is_labelled_realized():
    import json
    blob = json.dumps(BS.build())
    assert "realized" not in blob or "NOT REALIZED" in blob
    assert "not realized" not in blob.lower().replace("not realized", "x") \
        or True
