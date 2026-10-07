"""Tests for RESIDUAL-EFFICIENCY-FRONTIER-LANE-B.

Three load-bearing properties, per the preregistration:

  1. the preregistered fingerprint is frozen AND non-vacuous;
  2. no rule can read a post-decision field;
  3. the metric definitions are applied identically to every cell.

No network, no venue, no order path, no S3 import.
"""
from __future__ import annotations

import copy
import json

import pytest

from analysis.l5.residual_frontier import replay, spec


# ------------------------------------------------------- 1. the fingerprint

def test_fingerprint_is_frozen():
    assert spec.fingerprint() == spec.FRONTIER_FINGERPRINT
    spec.assert_frozen()


def test_fingerprint_literal_is_a_real_sha256():
    assert len(spec.FRONTIER_FINGERPRINT) == 64
    int(spec.FRONTIER_FINGERPRINT, 16)


@pytest.mark.parametrize("dotted,value", [
    ("abs_caps_shares", [1.0]),
    ("frac_caps", [0.99]),
    ("tte_base_abs_cap", 1.0),
    ("n_cells", 999),
    ("action_rule", "whatever"),
    ("no_hindsight_clause", "trust me"),
])
def test_fingerprint_is_not_vacuous(dotted, value):
    """Mutating ANY frozen field must change the fingerprint and trip the
    assert. A fingerprint that survives mutation is decoration."""
    s = copy.deepcopy(spec.SPEC)
    s[dotted] = value
    assert spec.fingerprint(s) != spec.FRONTIER_FINGERPRINT
    with pytest.raises(SystemExit):
        spec.assert_frozen(s)


def test_fingerprint_detects_a_mutated_grid_cell():
    s = copy.deepcopy(spec.SPEC)
    s["grid"][5]["abs_cap"] = 12345.0
    assert spec.fingerprint(s) != spec.FRONTIER_FINGERPRINT


def test_fingerprint_detects_a_mutated_metric_definition():
    s = copy.deepcopy(spec.SPEC)
    s["metrics"]["net_cash_ex_incentives_usd"] = "whatever is convenient"
    assert spec.fingerprint(s) != spec.FRONTIER_FINGERPRINT


def test_grid_is_compact_and_unique():
    assert spec.N_CELLS == 30
    assert spec.N_CELLS == len({c["cell_id"] for c in spec.GRID})
    # "tens of cells, not thousands"
    assert 10 <= spec.N_CELLS <= 99


def test_rebates_are_exactly_zero_and_nothing_is_realized():
    assert spec.SPEC["rebates_rewards_usd"] == 0.0
    assert spec.SPEC["is_realized_profit"] is False
    assert spec.SPEC["profit_label"] == "historical replay"


# -------------------------------------------- 2. no rule reads a post-field

def test_decision_state_field_set_is_exactly_the_allowlist():
    assert set(replay.DecisionState.__slots__) == set(
        spec.ALLOWED_STATE_FIELDS)


def test_no_forbidden_field_is_reachable_on_the_state():
    st = replay.DecisionState(
        shares_up=10.0, shares_down=4.0, cand_is_up=True, cand_size=3.0,
        cand_price=0.5, tte_s=100.0, duration_s=300, taken_count=2)
    for name in spec.FORBIDDEN_STATE_FIELDS:
        assert not hasattr(st, name), name
    # and the slots layout refuses to acquire one
    with pytest.raises(AttributeError):
        st.winner_index = 0


def test_state_cannot_grow_a_field():
    st = replay.DecisionState(
        shares_up=0.0, shares_down=0.0, cand_is_up=False, cand_size=1.0,
        cand_price=0.5, tte_s=10.0, duration_s=900, taken_count=0)
    with pytest.raises(AttributeError):
        st.anything_at_all = 1


def test_every_rule_is_invariant_to_the_settlement_outcome():
    """The decisive no-hindsight test: flip the event's winner and every
    cell's taken-fill sequence must be byte-identical."""
    base = replay._Event(
        slug="btc-updown-5m-1773914700", ts=1773914700, day="2026-03-19",
        era="E1_GENUINELY_PRE_REBATE", duration_s=300,
        fills=[replay._Fill(ts=1773914700 + 10 * i,
                            is_up=(i % 3 != 0), size=40.0,
                            price=0.45 + 0.01 * (i % 5),
                            usdc=40.0 * (0.45 + 0.01 * (i % 5)),
                            tte_s=300 - 10 * i)
               for i in range(25)],
        winner_index=0)
    for cell in spec.GRID:
        takes = []
        for widx in (0, 1, None):
            ev = copy.deepcopy(base)
            ev.winner_index = widx
            r = replay._score_event(ev, cell)
            takes.append((r["taken"], r["taken_extend"], r["taken_complete"],
                          round(r["turnover_usd"], 9),
                          round(r["matched_usd"], 9)))
        assert takes[0] == takes[1] == takes[2], cell["cell_id"]


def test_allow_is_a_pure_function_of_state_and_params():
    """Same state + same cell -> same answer, and the answer does not depend
    on anything the state does not carry."""
    st = replay.DecisionState(
        shares_up=150.0, shares_down=0.0, cand_is_up=True, cand_size=60.0,
        cand_price=0.5, tte_s=200.0, duration_s=300, taken_count=4)
    for cell in spec.GRID:
        a = replay.allow(st, cell)
        assert replay.allow(st, cell) is a


def test_extend_only_semantics_never_blocks_a_complete():
    """Once the cap binds, EXTEND is refused and COMPLETE is still allowed."""
    cmp_state = replay.DecisionState(
        shares_up=5000.0, shares_down=0.0, cand_is_up=False,
        cand_size=9000.0, cand_price=0.5, tte_s=5.0, duration_s=300,
        taken_count=9)
    assert cmp_state.is_complete is True
    ext_state = replay.DecisionState(
        shares_up=5000.0, shares_down=0.0, cand_is_up=True,
        cand_size=9000.0, cand_price=0.5, tte_s=5.0, duration_s=300,
        taken_count=9)
    assert ext_state.is_complete is False
    for cell in spec.GRID:
        if cell["family"] == "BASELINE":
            continue
        if cell["semantics"] == "EXTEND_ONLY":
            assert replay.allow(cmp_state, cell) is True, cell["cell_id"]
        assert replay.allow(ext_state, cell) is False, cell["cell_id"]


def test_action_classification_matches_the_frozen_rule():
    # empty book -> EXTEND
    assert not replay.DecisionState(0., 0., True, 1., .5, 10., 300, 0
                                    ).is_complete
    # balanced book -> EXTEND
    assert not replay.DecisionState(7., 7., True, 1., .5, 10., 300, 2
                                    ).is_complete
    # lighter side -> COMPLETE
    assert replay.DecisionState(7., 2., False, 1., .5, 10., 300, 2
                                ).is_complete
    # heavier side -> EXTEND
    assert not replay.DecisionState(7., 2., True, 1., .5, 10., 300, 2
                                    ).is_complete


def test_a_cell_can_only_refuse_never_invent():
    ev = replay._Event(
        slug="btc-updown-15m-1773917100", ts=1773917100, day="2026-03-19",
        era="E1_GENUINELY_PRE_REBATE", duration_s=900,
        fills=[replay._Fill(ts=1773917100 + 30 * i, is_up=(i % 2 == 0),
                            size=30.0, price=0.5, usdc=15.0,
                            tte_s=900 - 30 * i) for i in range(20)],
        winner_index=1)
    bl = replay._score_event(ev, spec.GRID[0])
    for cell in spec.GRID:
        r = replay._score_event(ev, cell)
        assert r["available"] == bl["available"] == 20
        assert r["taken"] <= bl["taken"]
        assert r["turnover_usd"] <= bl["turnover_usd"] + 1e-9


# ----------------------------------- 3. metrics applied identically to all

def test_metric_block_key_set_is_identical_for_every_cell_and_stratum():
    out = _cached_run()
    for cid, c in out["cells"].items():
        for s, block in c["strata"].items():
            assert set(block) == set(spec.METRICS), (cid, s)


def test_every_cell_reports_every_stratum_that_the_baseline_reports():
    out = _cached_run()
    want = set(out["cells"]["BASELINE"]["strata"])
    for cid, c in out["cells"].items():
        assert set(c["strata"]) == want, cid


def test_net_identity_holds_in_every_cell_and_stratum():
    out = _cached_run()
    for cid, c in out["cells"].items():
        for s, b in c["strata"].items():
            lhs = b["net_cash_ex_incentives_usd"]
            rhs = (b["matched_retained_usd"] + b["residual_pnl_usd"]
                   - b["fees_usd"])
            assert abs(lhs - rhs) < 0.02, (cid, s, lhs, rhs)


def test_baseline_reconciles_to_the_frozen_norebate_matched_component():
    """BASELINE's matched economics must equal the frozen audit's, exactly
    as reported in analysis/l5/norebate/Q1_NOREBATE.json."""
    out = _cached_run()
    got = out["cells"]["BASELINE"]["strata"]["FULL_CORPUS"][
        "matched_retained_usd"]
    audit = json.load(open("analysis/l5/norebate/Q1_NOREBATE.json",
                           encoding="utf-8"))
    want = audit["FULL_CORPUS"]["components_usd"]["matched_pair_economics"]
    assert abs(got - want) < 0.05, (got, want)


def test_baseline_is_the_denominator_of_every_relative_metric():
    out = _cached_run()
    b = out["cells"]["BASELINE"]["strata"]["FULL_CORPUS"]
    assert b["turnover_retained_frac"] == 1.0
    assert b["residual_loss_avoided_usd"] == 0.0
    assert b["suppressed_action_frac"] == 0.0


def test_gutting_verdict_is_applied_with_the_preregistered_thresholds():
    out = _cached_run()
    for cid, c in out["cells"].items():
        f = c["strata"]["FULL_CORPUS"]
        if f["net_cash_ex_incentives_usd"] <= 0:
            assert c["GUTTED_ENGINE"] is False, cid
            continue
        expect = ((f["turnover_retained_frac"] or 0) < 0.50
                  or (f["suppressed_action_frac"] or 0) > 0.50
                  or (c["matched_retained_frac_of_baseline"] or 0) < 0.50)
        assert c["GUTTED_ENGINE"] is bool(expect), cid


def test_no_order_path_and_no_s3_import():
    import analysis.l5.residual_frontier.replay as m
    src = open(m.__file__, encoding="utf-8").read()
    for bad in ("analysis.l5.s3", "l5.s3", "import requests",
                "urllib.request", "urllib.parse", "http.client",
                "place_order", "order_body", "submit_order", "railway",
                "deploy/", "scripts.l5_exec"):
        assert bad not in src.lower(), bad


_RUN = {}


def _cached_run():
    if "out" not in _RUN:
        _RUN["out"] = replay.run()
    return _RUN["out"]
