"""Regressions for the pre-registered bounded complement-cross shadow.

The spec is analysis/l5/cross_shadow/PREREGISTRATION.md, frozen at commit
135f5d79 before any of this code existed. These tests exist to make the
frozen rules mechanical rather than aspirational -- in particular the two
that are easiest to quietly violate: banker's rounding at the real clip
size, and never converting "we could not tell" into a fill.
"""
from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path

import pytest

from src import l5_cross_shadow as cs

ROOT = Path(__file__).resolve().parents[1]
OK = dict(held_residual=D("-8"), model_relation="complete", quote_age_s=3.0,
          depth=D("50"), want_qty=D("1.72"), pair_cost=D("0.96"),
          threshold=D("0.98"), risk_gates_pass=True, horizon_pass=True,
          evidence_ok=True)


# ----------------------------------------------- the confirmed fee schedule
def test_the_fee_is_the_charge_not_the_formula_value():
    """THE ONE THAT MATTERS AT OUR SIZE. 0.06 * 1.72 * 0.5 * 0.5 = $0.0258
    and the venue charges $0.03. Reporting the formula value would
    understate every cross by a double-digit percentage."""
    assert cs.taker_fee(D("1.72"), D("0.50")) == D("0.03")
    q = cs.fee_quantisation(D("1.72"), D("0.50"))
    assert q["formula_value"] == "0.025800"
    assert q["charged_after_bankers_rounding"] == "0.03"


def test_rounding_is_half_to_even_as_the_venue_states():
    """Half-up would charge a cent more on exact halves. The doc says
    banker's rounding, so 0.005 -> 0.00 and 0.015 -> 0.02."""
    # 0.06 * C * p * (1-p) == 0.005 exactly at C = 1/3, p = 0.5
    assert cs._cents(D("0.005")) == D("0.00")
    assert cs._cents(D("0.015")) == D("0.02")
    assert cs._cents(D("0.025")) == D("0.02")
    assert cs._cents(D("0.035")) == D("0.04")


def test_the_published_caps_are_enforced():
    assert cs.taker_fee(D("100000"), D("0.50")) == cs.TAKER_MAX
    assert cs.maker_rebate(D("100000"), D("0.50")) == cs.MAKER_REBATE_MAX


def test_the_maker_rebate_forgone_is_charged_as_a_cost_of_crossing():
    """Crossing gives up a rebate we would otherwise have been credited.
    Ignoring it flatters every arm."""
    e = cs.cross_economics(contracts=D("1.92"), taker_price=D("0.50"),
                           maker_price=D("0.50"), held_avg_cost=D("0.45"),
                           winner=None, held_side="dn")
    assert D(e["maker_rebate_forgone"]) > 0
    assert (D(e["net_locked_pair_pnl"])
            == D(e["gross_locked_pair_pnl"]) - D(e["confirmed_taker_fee"])
            - D(e["maker_rebate_forgone"]))


def test_a_cross_with_no_maker_alternative_forgoes_no_rebate():
    e = cs.cross_economics(contracts=D("1.72"), taker_price=D("0.50"),
                           maker_price=None, held_avg_cost=D("0.45"),
                           winner=None, held_side="dn")
    assert e["maker_rebate_forgone"] == "0"
    assert e["taker_premium_paid"] is None


# ------------------------------------------- unknown is never a fill
def test_missing_depth_is_unknown_executability_not_a_refusal():
    """THE PRE-REGISTERED DISTINCTION. 'We could not tell' and 'we would
    not have' are different facts and are reported separately."""
    st, why = cs.qualify(**{**OK, "depth": None})
    assert st == cs.UNKNOWN_EXEC and "depth" in why


def test_missing_quote_age_is_unknown_but_a_stale_quote_is_a_refusal():
    assert cs.qualify(**{**OK, "quote_age_s": None})[0] == cs.UNKNOWN_EXEC
    assert cs.qualify(**{**OK, "quote_age_s": 5.1})[0] == cs.REFUSED


def test_malformed_evidence_is_unknown_and_is_checked_first():
    st, why = cs.qualify(**{**OK, "evidence_ok": False,
                            "model_relation": "extend"})
    assert st == cs.UNKNOWN_EXEC and "malformed" in why


def test_insufficient_depth_is_a_refusal_not_a_partial_guess():
    st, why = cs.qualify(**{**OK, "depth": D("1.0")})
    assert st == cs.REFUSED and "depth" in why


# --------------------------------------------------- the envelope holds
def test_no_arm_exists_above_the_one_dollar_ceiling():
    assert max(cs.THRESHOLDS) == cs.PAIR_CEILING == D("1.00")


def test_a_pair_over_the_ceiling_is_refused_even_at_the_top_arm():
    st, why = cs.qualify(**{**OK, "pair_cost": D("1.01"),
                            "threshold": D("1.00")})
    assert st == cs.REFUSED


def test_the_arms_are_exactly_the_pre_registered_ones():
    assert cs.THRESHOLDS == (D("0.97"), D("0.98"), D("0.99"), D("1.00"))
    assert cs.TIMINGS == (0, 10, 30, 60)
    assert cs.MAX_QUOTE_AGE_S == 5.0


def test_a_fully_qualifying_row_is_supported():
    """...and the refusal tests above are not passing for the wrong
    reason."""
    st, why = cs.qualify(**OK)
    assert st == cs.SUPPORTED and why == "all_eight_conditions_met"


# ------------------------------------------------------- the accounting
def test_an_unsettled_market_reports_unsettled_not_zero():
    e = cs.cross_economics(contracts=D("1.72"), taker_price=D("0.50"),
                           maker_price=D("0.47"), held_avg_cost=D("0.45"),
                           winner=None, held_side="dn")
    assert e["settlement_pnl_of_that_residual_if_held"] == "UNSETTLED"
    assert e["incremental_net_vs_holding"] == "UNSETTLED"


def test_fees_are_labelled_confirmed_with_their_source():
    e = cs.cross_economics(contracts=D("1"), taker_price=D("0.50"),
                           maker_price=None, held_avg_cost=D("0.45"),
                           winner="dn", held_side="dn")
    assert e["fee_status"] == "CONFIRMED"
    assert "docs.polymarket.us/fees" in e["fee_source"]


# ------------------------------------------------------------ shadow only
def test_nothing_armed_imports_this_and_it_opens_no_socket():
    # check the IMPORT GRAPH, not raw text: the module's own docstring says
    # "opens no socket", and a substring test on the source made that
    # sentence fail its own assertion
    import ast
    tree = ast.parse((ROOT / "src/l5_cross_shadow.py").read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert mods <= {"__future__", "decimal", "typing"}, mods
    for rel in ("src/pmus_spring_l5_session.py",
                "src/pmus_l5_continuous_session.py",
                "src/pmus_q1_executor.py"):
        p = ROOT / rel
        if p.exists():
            assert "l5_cross_shadow" not in p.read_text(), rel
