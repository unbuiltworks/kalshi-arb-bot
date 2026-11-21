"""Both lanes compared only where Model A trades; EARLY_15M kept offline."""
from decimal import Decimal as D

import json
import pytest

from src.gemini_execution import early_15m as EA
from src.gemini_execution import lanes as LN


# ===================================== the comparison is TTE-restricted

def test_THE_REACHABLE_INTERVAL_IS_READ_FROM_MODEL_A_NOT_SET_HERE():
    """quote_between_tte_s is an OURS_ADAPTED parameter, not a measurement of
    L5 (his is [0, 900]). This module reads it
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
    # inventory lives on the CYCLE rows, which is where a running lane
    # actually persists it -- the window row carries none.
    ws = [{"symbol": "S", "cycles": 5, "adapter_vetoes": 2,
           "cycles_detail": [
               {"model_a_wanted": [{"action": "OPEN"}, {"action": "OPEN"}],
                "inventory": {"matched_qty": "0",
                              "total_spend_usd": "0"}},
               {"model_a_wanted": [{"action": "COMPLETE"}],
                "inventory": {"matched_qty": "0.01",
                              "effective_matched_pair_cost": "0.93",
                              "directional_residual": "0.00",
                              "total_spend_usd": "0.0093"}}]}]
    c = LN.lane_census(ws, {"S": {"prints": 6, "notional_usd": "40",
                                  "two_sided": True, "buy_prints": 4,
                                  "sell_prints": 2}})
    for k in ("windows", "windows_with_tape", "prints_in_reachable_window",
              "notional_in_reachable_window_usd", "two_sided_windows",
              "two_sided_share", "buy_prints", "sell_prints",
              "model_a_quotes", "quotes_per_window", "adapter_vetoes",
              "first_fills", "complete_fills", "matched_contracts",
              "gross_contracts", "matched_over_gross",
              "capital_at_risk_usd"):
        assert k in c, k
    assert c["model_a_quotes"] == 3
    assert c["adapter_vetoes"] == 2
    assert c["capital_at_risk_usd"] == "0.0093"


def test_A_FILL_COLUMN_IS_ABSENT_NOT_ZERO():
    """The strongest wrong claim this file could make.

    A shadow lane rests no order, so it cannot fill. Printing 0 first fills
    beside a real print count would read as 'the lane quoted and the flow
    refused it', which is a finding. It is not one: it is the absence of a
    fill mechanism, and the census has to say which.
    """
    c = LN.lane_census([{"symbol": "S", "cycles": 3, "cycles_detail": []}], {})
    for k in ("first_fills", "complete_fills", "matched_contracts",
              "gross_contracts", "matched_over_gross"):
        assert c[k] is None, k
        assert "no mechanism here" in c["unmeasurable_here"][k]
        assert "Not a measured zero" in c["unmeasurable_here"][k]


def test_the_lane_is_read_from_its_evidence_stream_not_its_final_report(tmp_path):
    """A `--windows 0` lane never writes the report at the end of its loop."""
    ev = tmp_path / "continuous_v1.jsonl"
    ev.write_text(
        json.dumps({"kind": "window", "symbol": "S", "cycles": 2,
                    "started_utc": "2026-01-01T00:00:00+00:00",
                    "finished_utc": "2026-01-01T00:05:00+00:00"}) + "\n"
        + json.dumps({"kind": "cycle", "symbol": "S",
                      "utc": "2026-01-01T00:01:00+00:00",
                      "model_a_wanted": [{"action": "OPEN"}],
                      "inventory": {"matched_qty": "0"}}) + "\n"
        # a cycle on the same symbol from OUTSIDE this window must not be
        # pooled into it
        + json.dumps({"kind": "cycle", "symbol": "S",
                      "utc": "2026-01-01T09:00:00+00:00",
                      "model_a_wanted": [{"action": "OPEN"}],
                      "inventory": {"matched_qty": "0"}}) + "\n",
        encoding="utf-8")
    ws = LN.lane_windows(str(ev))
    assert len(ws) == 1
    assert len(ws[0]["cycles_detail"]) == 1


def test_the_trigger_cannot_fire_in_a_lane_that_rests_nothing():
    s = LN.the_sequence([{"symbol": "S", "cycles_detail": [
        {"inventory": {"matched_qty": "0", "n_fills": 0}}]}])
    assert s["occurrences"] == 0
    assert s["can_fire_in_these_lanes"] is False
    assert "no rung rests" in s["why_not"]
    assert s["windows_whose_inventory_moved"] == 0


def test_ONE_OCCURRENCE_IS_NOT_THE_TRIGGER():
    one = [{"symbol": "S", "cycles_detail": [
        {"inventory": {"matched_qty": "0.01",
                       "effective_matched_pair_cost": "0.93"}}]}]
    s = LN.the_sequence(one)
    assert s["occurrences"] == 1 and s["at_or_below_par"] == 1
    assert s["is_the_trigger"] is False


def test_a_pair_above_par_is_not_counted_as_par_constrained():
    s = LN.the_sequence([{"symbol": "S", "cycles_detail": [
        {"inventory": {"matched_qty": "0.01",
                       "effective_matched_pair_cost": "1.02"}}]}])
    assert s["occurrences"] == 1 and s["at_or_below_par"] == 0


def test_repetition_is_the_trigger():
    two = [{"symbol": "A", "cycles_detail": [
                {"inventory": {"matched_qty": "0.01",
                               "effective_matched_pair_cost": "0.93"}}]},
           {"symbol": "B", "cycles_detail": [
                {"inventory": {"matched_qty": "0.01",
                               "effective_matched_pair_cost": "0.97"}}]}]
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


# ================================ the ledger is what makes the fills real

def test_the_fill_columns_become_measured_where_the_ledger_ran():
    w = {"symbol": "S", "cycles": 4, "cycles_detail": [],
         "hypothetical": {"rungs_placed": 6, "first_fills": 1,
                          "complete_fills": 1,
                          "fills_resting_on_an_unknown_queue": 0},
         "inventory": {"matched_qty": "0.01", "directional_residual": "0",
                       "effective_matched_pair_cost": "0.95",
                       "total_spend_usd": "0.0095"}}
    c = LN.lane_census([w], {})
    assert c["windows_with_a_hypothetical_ledger"] == 1
    assert c["first_fills"] == 1 and c["complete_fills"] == 1
    assert c["matched_contracts"] == "0.01"
    assert c["gross_contracts"] == "0.02"
    assert c["matched_over_gross"] == "0.5000"
    assert c["unmeasurable_here"] == {}
    assert "HYPOTHETICAL" in c["fills_are_hypothetical"]


def test_A_HYPOTHETICAL_OCCURRENCE_IS_NEVER_REPORTED_AS_A_REAL_ONE():
    w = {"symbol": "S", "cycles": 4, "cycles_detail": [],
         "hypothetical": {"rungs_placed": 2},
         "inventory": {"matched_qty": "0.01",
                       "effective_matched_pair_cost": "0.95"}}
    s = LN.the_sequence([w])
    assert s["occurrences"] == 1
    assert s["can_fire_in_these_lanes"] is True
    assert s["why_not"] is None
    assert "never placed" in s["occurrences_are_hypothetical"]


def test_the_residual_counts_toward_gross_even_with_no_match():
    w = {"symbol": "S", "cycles": 4, "cycles_detail": [],
         "hypothetical": {"rungs_placed": 3, "first_fills": 1},
         "inventory": {"matched_qty": "0", "directional_residual": "0.01"}}
    c = LN.lane_census([w], {})
    assert c["gross_contracts"] == "0.01"
    assert c["matched_over_gross"] == "0.0000"
