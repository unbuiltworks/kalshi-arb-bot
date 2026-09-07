"""OWNER 5569845340 B/E/F/G/H/I over the checked-in artifacts: deterministic,
fail-closed, and never promoting."""
from __future__ import annotations

import json
import os

from decimal import Decimal as D

from src import l5_profitability as pf


def _sb():
    return pf._load(pf.SCOREBOARD)


def test_rebate_per_pair_uses_the_venue_cent_rounding():
    # 5 shares @0.50: 0.0125*5*0.25 = 0.015625 -> 0.02 per print, two prints per 5 pairs
    assert pf.rebate_per_pair_at_print_size(D("0.50"), D("5")) == D("0.008")
    # 1 share @0.50: 0.003125 -> 0.00
    assert pf.rebate_per_pair_at_print_size(D("0.50"), D("1")) == D("0")


def test_break_even_table_has_seven_rows_and_the_four_classes_in_order():
    t = pf.break_even_table(_sb())
    rows = t["table"]
    assert [r["pair_cost"] for r in rows] == [0.95, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01]
    assert all(r["class"] == "profitable before incentives" for r in rows[:5])
    assert rows[5]["class"].startswith("profitable only after the observed maker rebate")
    assert "NOT EXECUTABLE" in rows[6]["class"] and rows[6]["executable_under_r4"] is False
    assert rows[5]["executable_under_r4"] is True
    assert t["r4_hard_ceiling"] == 1.0
    # the observed rate is the venue's credits over at-size filled dollars
    obs = t["observed_rebate"]
    assert obs["credited_usd"] == 0.42 and 0.007 < obs["rate_per_filled_usd"] < 0.009
    assert 1.007 < t["break_even_pair_cost_after_observed_rebate"] < 1.009
    assert all(r["liquidity_rewards_per_pair"] == 0.0 for r in rows)


def test_operating_envelope_fails_closed_on_five_markets():
    env = pf.operating_envelope(_sb())
    assert env["at_size_markets"] == 5
    assert env["rankable_regimes"] == []
    for key, table in env["regimes"].items():
        for v in table.values():
            assert v["status"].startswith("INSUFFICIENT")
    at = env["all_at_size"]
    assert at["markets"] == 5 and at["se_net_per_market"] > abs(at["mean_net_per_market"])


def test_gates_do_not_pass_g1_without_a_sign_on_the_mean():
    sb = _sb()
    env = pf.operating_envelope(sb)
    h = pf.profitability_gates(sb, env)
    g1 = h["gates"][0]
    assert g1["met"] is False and h["all_met"] is False
    assert g1["have"]["markets"] == 5


def test_levers_promote_nothing_and_carry_worst_cases():
    sb = _sb()
    env = pf.operating_envelope(sb)
    curve = pf._load(pf.CURVE)
    g = pf.levers(sb, curve, env, pf._load(pf.CAP_STUDY))
    assert "Nothing is promoted" in g["smallest_rung_that_raises_expected_dollars"]
    reps = g["levers"][0]
    assert reps["changes_risk_parameter"] is False and reps["worst_case_usd"] == -7.5
    assert all(("worst_case_usd" in lv) or ("worst_case_usd_per_market" in lv) for lv in g["levers"])
    assert g["levers"][-1]["lever"].startswith("concurrency")


def test_capacity_model_is_conditional_and_scales_the_worst_case():
    sb = _sb()
    env = pf.operating_envelope(sb)
    i = pf.capacity_model(sb, env, pf._load(pf.UNIVERSE))
    assert i["status"].startswith("CONDITIONAL")
    rows = {r["daily_deployed_usd"]: r for r in i["rows"]}
    assert set(rows) == {25, 100, 480}
    assert rows[480]["worst_case_residual_per_day_at_r4_cap"] > rows[25]["worst_case_residual_per_day_at_r4_cap"]


def test_assemble_and_render_are_consistent_with_the_files_on_disk():
    out = pf.assemble()
    md = pf.render_md(out)
    assert "## F. Observed break-even table" in md and "NOT EXECUTABLE" in md
    assert "No rung is promoted" in md
    on_disk = json.load(open(pf.OUT_JSON))
    assert on_disk["F_break_even"]["table"] == out["F_break_even"]["table"]
    assert on_disk["H_gates"]["all_met"] is False
