"""Both lanes compared only where Model A trades; EARLY_15M kept offline."""
from decimal import Decimal as D

import pytest

from src.gemini_execution import early_15m as EA
from src.gemini_execution import lanes as LN


# ===================================== the comparison is TTE-restricted

def test_THE_REACHABLE_INTERVAL_IS_READ_FROM_MODEL_A_NOT_SET_HERE():
    """quote_between_tte_s is a measured L5 parameter. This module reads it
    and never writes it."""
    lo, hi = LN.quoting_window()
    assert (lo, hi) == (0.0, 300.0)
    assert LN.REACHABLE_TTE_S == hi
    # lanes.py READS the parameter; it must never assign to it
    import ast
    tree = ast.parse(open("src/gemini_execution/lanes.py").read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            tgts = node.targets if isinstance(node, ast.Assign) \
                else [node.target]
            for t in tgts:
                assert "quote_between_tte_s" not in ast.unparse(t)
                assert "TTE_policy" not in ast.unparse(t)


def _p(ms, typ, px, amt):
    return {"utc_ms": ms, "type": typ, "price": px, "amount": amt}


def test_venue_flow_outside_the_reachable_interval_is_excluded():
    exp = 1_000_000
    prints = [_p(exp - 800_000, "buy", "0.5", "100"),   # tte 800: excluded
              _p(exp - 200_000, "buy", "0.5", "10"),    # tte 200: counted
              _p(exp - 100_000, "sell", "0.5", "10")]   # tte 100: counted
    t = LN.tape_in_window(prints, expiry_ms=exp)
    assert t["prints"] == 2
    assert D(t["notional_usd"]) == D("10")
    assert t["two_sided"] is True


def test_a_one_sided_reachable_window_is_not_called_two_sided():
    exp = 1_000_000
    t = LN.tape_in_window([_p(exp - 100_000, "buy", "0.5", "10")],
                          expiry_ms=exp)
    assert t["two_sided"] is False


def test_the_census_reports_every_column_asked_for():
    ws = [{"symbol": "S", "cycles": 5,
           "inventory": {"matched_qty": "0.01",
                         "effective_matched_pair_cost": "0.93",
                         "directional_residual": "0.00",
                         "total_spend_usd": "0.0093"},
           "gross_contracts": "0.02", "first_fills": 1,
           "complement_fills": 1, "opposite_flow_after_fill": 2,
           "model_a_quotes": 8}]
    c = LN.lane_census(ws, {"S": {"prints": 6, "notional_usd": "40",
                                  "two_sided": True}})
    for k in ("windows", "prints_in_reachable_window",
              "notional_in_reachable_window_usd", "two_sided_windows",
              "model_a_quotes", "first_fills", "opposite_flow_after_fill",
              "complete_fills", "par_constrained_pairs",
              "matched_contracts", "gross_contracts", "matched_over_gross",
              "terminal_residual_contracts", "capital_at_risk_usd"):
        assert k in c, k
    assert c["par_constrained_pairs"] == 1
    assert c["matched_over_gross"] == "0.5000"


def test_a_pair_above_par_is_not_counted_as_par_constrained():
    ws = [{"symbol": "S", "cycles": 5,
           "inventory": {"matched_qty": "0.01",
                         "effective_matched_pair_cost": "1.02",
                         "directional_residual": "0",
                         "total_spend_usd": "0.0102"},
           "gross_contracts": "0.02"}]
    assert LN.lane_census(ws, {})["par_constrained_pairs"] == 0


# ============================================================ the trigger

def test_ONE_OCCURRENCE_IS_NOT_THE_TRIGGER():
    one = [{"symbol": "S", "inventory": {"matched_qty": "0.01",
                                         "effective_matched_pair_cost":
                                             "0.93"}}]
    s = LN.the_sequence(one)
    assert s["occurrences"] == 1 and s["at_or_below_par"] == 1
    assert s["is_the_trigger"] is False
    assert "one occurrence is an anecdote" in s["why"]


def test_repetition_is_the_trigger():
    two = [{"symbol": "A", "inventory": {"matched_qty": "0.01",
                                         "effective_matched_pair_cost":
                                             "0.93"}},
           {"symbol": "B", "inventory": {"matched_qty": "0.01",
                                         "effective_matched_pair_cost":
                                             "0.97"}}]
    assert LN.the_sequence(two)["is_the_trigger"] is True


def test_a_window_with_no_match_is_not_an_occurrence():
    assert LN.the_sequence([{"symbol": "S",
                             "inventory": {"matched_qty": "0"}}
                            ])["occurrences"] == 0


# ================================================= EARLY_15M stays offline

def test_EARLY_15M_NEVER_TOUCHES_THE_FROZEN_QUOTING_WINDOW():
    """It widens the interval IN THE REPLAY ONLY. If it ever wrote the
    parameter, the production policy would change underneath the lanes.

    SCANNED AS CODE, NOT AS TEXT. The module's docstring names
    quote_between_tte_s precisely to say it leaves it alone, and a raw string
    search cannot tell a promise from a mutation."""
    import ast
    tree = ast.parse(open("src/gemini_execution/early_15m.py").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    for forbidden in ("quote_between_tte_s", "TTE_policy", "probe_params",
                      "load_frozen"):
        assert forbidden not in names, forbidden


def test_the_running_lanes_do_not_import_the_research_module():
    for f in ("src/gemini_execution/shadow_soak.py",
              "src/gemini_execution/continuous_probe.py",
              "src/gemini_execution/run_continuous.py",
              "src/gemini_execution/contract_cohort.py"):
        assert "early_15m" not in open(f).read(), f


def test_the_early_interval_is_outside_model_as_window():
    lo, hi = LN.quoting_window()
    assert EA.EARLY_LO_S >= hi
    assert EA.EARLY_HI_S > EA.EARLY_LO_S


def test_every_early_result_carries_its_caveat():
    r = EA.replay_early("S", [], expiry_ms=1_000_000)
    assert r["label"] == EA.LABEL
    c = EA.census([r])
    assert "UPPER BOUND" in c["caveat"]
    assert "no book history" in c["caveat"]
    assert "UNCHANGED" in c["caveat"]


def test_a_contract_with_too_little_early_flow_refuses():
    exp = 1_000_000
    r = EA.replay_early("S", [_p(exp - 600_000, "buy", "0.5", "10")],
                        expiry_ms=exp)
    assert r["computable"] is False
    assert "fewer than two prints" in r["why"]


def test_prints_inside_model_as_window_are_not_part_of_the_EARLY_study():
    exp = 1_000_000
    late = [_p(exp - 200_000, "sell", "0.40", "10"),
            _p(exp - 100_000, "buy", "0.70", "10")]
    assert EA.replay_early("S", late, expiry_ms=exp)["computable"] is False


def test_a_complement_is_only_reached_by_the_opposite_aggressor():
    exp = 1_000_000
    # a seller at 0.40 opens us on YES at 0.39; a BUYER is needed to complete
    rows = [_p(exp - 800_000, "sell", "0.40", "10"),
            _p(exp - 700_000, "sell", "0.35", "10"),   # another seller: EXTEND
            _p(exp - 600_000, "buy", "0.95", "10")]    # buyer: COMPLETE
    r = EA.replay_early("S", rows, expiry_ms=exp)
    assert r["computable"] is True
    assert r["first_fill"]["leg"] == "YES"
    assert r["extends_suppressed"] >= 1
    assert r["matched"] is True
    assert r["complete"]["leg"] == "NO"


def test_THE_PAIR_BASIS_IS_CONSTRUCTED_AND_SAYS_SO():
    """The complement is quoted at 1 - held - tick, so the basis is 0.99 on
    EVERY match by construction. Reporting that as an observed pair cost
    would be reporting a tautology: the real finding is that external flow
    reached the price, not what the pair happened to cost. Caught when all
    29 matches came back at exactly 0.99."""
    exp = 1_000_000
    rows = [_p(exp - 800_000, "sell", "0.40", "10"),
            _p(exp - 600_000, "buy", "0.95", "10")]
    r = EA.replay_early("S", rows, expiry_ms=exp)
    assert r["matched"] is True
    assert r["constructed_pair_basis"] == "0.99"
    assert "BY CONSTRUCTION" in r["basis_is_constructed"]
    assert "par_constrained" not in r
    c = EA.census([r])
    assert c["flow_reached_a_fee_safe_complement"] == 1
    assert c["constructed_basis_values"] == ["0.99"]
    assert "tautology" not in c  # the caveat is a field, not a silent omission
    assert "BY CONSTRUCTION" in c["basis_is_constructed"]
