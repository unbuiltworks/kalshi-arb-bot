"""TERMINAL SHADOW PAYOFF, break-even, and the uncertainty around the sign.

The loop's expectancy is dominated by a term nobody had measured. These pin
the arithmetic of that term, the break-even it has to clear, and -- most
importantly -- that an unresolved outcome is never quietly treated as a zero.
"""
import math

import pytest

from src.gemini_execution.econ import fill_sequence as FS
from src.gemini_execution.econ import terminal as TM


def row(**kw):
    base = {"first_fill": True, "matched": False, "first_fill_leg": "YES",
            "first_fill_price": "0.40", "first_fill_qty": "1",
            "residual_qty": "1", "settlement_leg": "YES",
            "residual_settlement_value": "0.60",
            "symbol": "S", "cohort": "btc5m", "seq_id": 1, "arm": "PP_F1",
            "tte_s": 180.0, "book_t0": {}, "markout_ticks_by_horizon_s": {},
            "complement_price_appeared": True, "complement_filled": False,
            "rested_legs": ["YES", "NO"], "candidate_leg": "YES"}
    base.update(kw)
    return base


# ------------------------------------------------------------- the payoff
def test_a_winning_residual_pays_one_less_its_cost_less_the_published_fee():
    t = TM.residual_terminal(row(first_fill_price="0.40", settlement_leg="YES"))
    assert t["won"] is True
    assert t["entry_cost_usd"] == "0.40"
    assert t["terminal_payout_usd"] == "1"
    # 0.0175 * 1 * 0.40 * 0.60
    assert float(t["fees_usd"]) == pytest.approx(0.0042)
    assert float(t["residual_net_usd"]) == pytest.approx(0.5958)


def test_a_losing_residual_loses_its_cost_and_still_pays_the_fee():
    t = TM.residual_terminal(row(first_fill_price="0.40", settlement_leg="NO"))
    assert t["won"] is False
    assert t["terminal_payout_usd"] == "0"
    assert float(t["residual_net_usd"]) == pytest.approx(-0.4042)


def test_the_NO_leg_is_priced_from_its_own_price_not_the_yes_price():
    """A NO fill at 0.40 wins when the contract resolves NO."""
    t = TM.residual_terminal(row(first_fill_leg="NO", first_fill_price="0.40",
                                 settlement_leg="NO"))
    assert t["won"] is True and float(t["residual_net_usd"]) == pytest.approx(
        0.5958)
    t2 = TM.residual_terminal(row(first_fill_leg="NO", first_fill_price="0.40",
                                  settlement_leg="YES"))
    assert t2["won"] is False


def test_the_fee_is_symmetric_in_price_as_the_published_formula_is():
    a = TM.maker_fee("1", "0.30")
    b = TM.maker_fee("1", "0.70")
    assert a == b


def test_a_matched_pair_or_an_unlabelled_row_yields_NO_terminal_row():
    assert TM.residual_terminal(row(matched=True)) is None
    assert TM.residual_terminal(row(settlement_leg=None)) is None
    assert TM.residual_terminal(row(first_fill=False)) is None


# ----------------------------------------------------------- the break-even
def test_the_breakeven_residual_is_the_completion_weighted_pair_profit():
    be = TM.breakeven_residual(0.5, 0.002)
    # r* = -0.5 * 0.002 / 0.5
    assert be["breakeven_residual_net_per_contract_usd"] == pytest.approx(
        -0.002)


def test_the_authoritative_cohort_numbers_reproduce_the_reported_breakeven():
    """The figure quoted to the owner, from the cohort values rather than
    rounded ones: completion 10/14, pair net 0.0011337."""
    be = TM.breakeven_residual(10 / 14, 0.0011337)
    r = be["breakeven_residual_net_per_contract_usd"]
    assert r == pytest.approx(-(10 / 14 * 0.0011337) / (4 / 14))
    assert r == pytest.approx(-0.00283425, abs=1e-8)


def test_a_completion_rate_of_one_has_no_breakeven_to_compute():
    assert TM.breakeven_residual(1.0, 0.002)["computable"] is False


# ------------------------------------------------------------- expectancy
def test_an_unresolved_residual_is_EXCLUDED_not_counted_as_zero():
    rows = [row(matched=True, seq_id=1,
                pair_economics={"net_pair_economics": "0.0011337", "matched_qty": "0.01"}),
            row(seq_id=2, settlement_leg=None,
                residual_settlement_value=None)]
    e = TM.expectancy(rows)
    assert e["residual_unresolved"] == 1
    assert e["residual_settled"] == 0
    assert e["outcome_known"] == 1                 # the pair only
    assert e["completion_rate"]["n"] == 1
    assert "EXCLUDED" in e["why_incomplete"]
    # and the mean is the pair alone, NOT averaged against a zero
    assert e["e_cycle_usd"] == pytest.approx(0.0011337 / 0.01)


def test_no_known_outcome_makes_expectancy_UNCOMPUTABLE_rather_than_zero():
    e = TM.expectancy([row(settlement_leg=None,
                           residual_settlement_value=None)])
    assert e["e_cycle_usd"] is None
    assert "UNCOMPUTABLE" in e["why_incomplete"]


def test_expectancy_splits_by_leg_and_the_legs_do_not_contaminate():
    rows = [row(seq_id=1, first_fill_leg="YES", matched=True,
                pair_economics={"net_pair_economics": "0.001", "matched_qty": "0.01"}),
            row(seq_id=2, first_fill_leg="NO", first_fill_price="0.30",
                settlement_leg="YES")]           # NO leg lost
    y, n = TM.expectancy(rows, leg="YES"), TM.expectancy(rows, leg="NO")
    assert y["first_fills"] == 1 and y["matched"] == 1
    assert n["first_fills"] == 1 and n["residual_settled"] == 1
    assert y["e_cycle_usd"] > 0 > n["e_cycle_usd"]


def test_the_confidence_interval_brackets_the_point_estimate_and_narrows():
    few = [row(seq_id=i, first_fill_price="0.40",
               settlement_leg=("YES" if i % 2 else "NO")) for i in range(8)]
    many = [row(seq_id=i, first_fill_price="0.40",
                settlement_leg=("YES" if i % 2 else "NO"))
            for i in range(200)]
    a, b = TM.expectancy(few), TM.expectancy(many)
    for e in (a, b):
        assert e["e_cycle_ci"]["lo"] <= e["e_cycle_usd"] <= e["e_cycle_ci"]["hi"]
    assert (b["e_cycle_ci"]["hi"] - b["e_cycle_ci"]["lo"]) < \
           (a["e_cycle_ci"]["hi"] - a["e_cycle_ci"]["lo"])


def test_wilson_never_leaves_the_unit_interval_at_the_extremes():
    for n in (1, 5, 40, 140):
        for k in (0, n):
            w = TM.wilson(k, n)
            assert 0.0 <= w["lo"] <= w["hi"] <= 1.0
    # 0 of 140 is still not proof of zero
    assert TM.wilson(0, 140)["hi"] > 0.0


# ------------------------------------------------------------ checkpoints
def test_a_checkpoint_reports_the_sign_and_whether_it_is_certain():
    rows = [row(seq_id=i, first_fill_price="0.40", settlement_leg="NO")
            for i in range(25)]
    cp = TM.checkpoint(rows, target=20, leg="YES")
    assert cp["reached"] is True
    assert cp["sign"] == "NEGATIVE"
    assert cp["sign_is_certain_at_95"] is True


def test_a_checkpoint_says_what_the_REMAINING_sample_must_do_to_flip_it():
    rows = ([row(seq_id=i, matched=True,
                 pair_economics={"net_pair_economics": "0.0011337", "matched_qty": "0.01"})
             for i in range(10)]
            + [row(seq_id=100 + i, first_fill_price="0.40",
                   settlement_leg="NO") for i in range(2)])
    cp = TM.checkpoint(rows, target=20, leg="YES")
    fr = cp["flip_requires"]
    assert fr["remaining"] == 18
    need = fr["mean_residual_net_per_contract_needed_usd"]
    # the identity: blending what is banked with what is needed lands the
    # whole target-sized sample exactly on break-even, by construction
    blended = (fr["currently"] * 2 + need * 18) / 20
    assert blended == pytest.approx(fr["breakeven"])
    assert "%d" % 18 in fr["reading"]


def test_the_checkpoint_sign_is_UNCERTAIN_when_the_interval_straddles_zero():
    rows = [row(seq_id=1, matched=True,
                pair_economics={"net_pair_economics": "0.0011337", "matched_qty": "0.01"}),
            row(seq_id=2, first_fill_price="0.40", settlement_leg="NO"),
            row(seq_id=3, first_fill_price="0.40", settlement_leg="YES")]
    cp = TM.checkpoint(rows, target=50, leg="YES")
    assert cp["reached"] is False
    assert cp["sign_is_certain_at_95"] in (False, None)


# ------------------------------------------- the funnel's own denominators
def test_the_per_leg_funnel_counts_from_the_rung_not_from_the_fill():
    rows = [
        # a YES rung and a NO rung rested; YES filled and completed
        row(seq_id=1, first_fill_leg="YES", matched=True,
            complement_filled=True, rested_legs=["YES", "NO"]),
        # both rested; NO filled and never completed
        row(seq_id=2, first_fill_leg="NO", complement_filled=False,
            rested_legs=["YES", "NO"]),
        # both rested, nothing filled
        row(seq_id=3, first_fill=False, first_fill_leg=None,
            rested_legs=["YES", "NO"]),
    ]
    y = FS.FillSequenceLedger._leg_funnel(rows, "YES")
    n = FS.FillSequenceLedger._leg_funnel(rows, "NO")
    assert y["rungs_rested"] == n["rungs_rested"] == 3
    assert y["first_fills"] == 1 and n["first_fills"] == 1
    assert y["first_fill_rate_of_rested_rungs"] == pytest.approx(1 / 3)
    assert y["completion_rate_of_first_fills"] == 1.0
    assert n["completion_rate_of_first_fills"] == 0.0
    # the rate that matters for capacity: completions per rung offered
    assert y["completion_rate_of_rested_rungs"] == pytest.approx(1 / 3)
    assert n["completion_rate_of_rested_rungs"] == 0.0


def test_the_breakouts_are_always_split_by_leg():
    first = [row(seq_id=1, cohort="btc5m", first_fill_leg="YES", tte_s=100,
                 first_fill_price="0.45", complement_filled=True,
                 settlement_leg=None),
             row(seq_id=2, cohort="btc15m", first_fill_leg="NO", tte_s=500,
                 first_fill_price="0.75", complement_filled=False,
                 settlement_leg=None)]
    b = FS.FillSequenceLedger()._breakouts(first)
    assert b["by_cohort"]["btc5m"]["YES"]["completion_rate"] == 1.0
    assert b["by_cohort"]["btc15m"]["NO"]["completion_rate"] == 0.0
    assert "60-120" in b["by_tte_bucket_s"]
    assert "0.40-0.60" in b["by_entry_price_bucket"]
    assert "UNRESOLVED" in b["by_market_direction"]


# --------------------------------------------------------------- the unit
def test_the_pair_term_is_normalised_to_ONE_CONTRACT_like_the_residual():
    """THE UNIT TRAP. `net_pair_economics` is an absolute figure at the
    quantity the pair was struck in -- 0.01 in the shadow -- while the
    residual term is per contract. Averaging them raw understates the pair
    term a hundredfold and makes the break-even look far harder than it is.
    """
    rows = [row(seq_id=1, matched=True,
                pair_economics={"net_pair_economics": "0.00004724",
                                "matched_qty": "0.01"})]
    e = TM.expectancy(rows)
    assert e["completed_pair_net_per_contract_mean_usd"] == pytest.approx(
        0.004724)
    assert e["unit"].startswith("USD per ONE contract")


def test_a_pair_with_no_published_quantity_is_SKIPPED_not_assumed():
    rows = [row(seq_id=1, matched=True,
                pair_economics={"net_pair_economics": "0.001"})]
    e = TM.expectancy(rows)
    assert e["pair_rows_without_a_quantity"] == 1
    assert e["completed_pair_net_per_contract_mean_usd"] is None


def test_the_breakeven_at_the_CORRECTED_per_contract_pair_net():
    """The figure the owner gets: completion 10/14, pair net $0.004724 per
    contract (0.0011337 total over 24 pairs of 0.01)."""
    per_contract = 0.0011337 / (24 * 0.01)
    assert per_contract == pytest.approx(0.0047237, abs=1e-6)
    be = TM.breakeven_residual(10 / 14, per_contract)
    r = be["breakeven_residual_net_per_contract_usd"]
    assert r == pytest.approx(-0.0118094, abs=1e-6)
