"""Deterministic tests for the L5 restriction audit (owner program 2026-09-07)."""
from __future__ import annotations

import json

from src import l5_restriction_audit as ra


def _row(window, price, qty, side="UP", pair=None, matched=0, res=0, mt="maker"):
    return {"window_id": window, "price": str(price), "quantity": str(qty), "dollars": str(price * qty),
            "maker_taker": mt, "matched_pair_unit_cost": "" if pair is None else str(pair),
            "matched_qty": str(matched), "residual_qty_signed": str(res), "residual_side": side,
            "cum_up_qty": str(qty), "cum_up_spend": str(price * qty), "cum_down_qty": "0", "cum_down_spend": "0"}


def test_l5_numbers_from_synthetic_rows():
    rows = [_row("w1", 0.40, 10), _row("w1", 0.62, 10, pair=1.02, matched=10, res=0),
            _row("w2", 0.30, 20, mt="taker"), _row("w2", 0.72, 10, pair=1.02, matched=10, res=10),
            _row("w3", 0.50, 5, mt="unknown")]
    n = ra.l5_numbers(rows=rows)
    assert n["fills"] == 5 and n["windows"] == 3
    assert n["taker_share"]["of_known"] == 0.25          # 1 taker of 4 known
    assert n["shares_per_fill"]["p50"] == 10.0
    assert n["pair_unit_cost_at_completion"]["n"] == 2 and n["pair_unit_cost_at_completion"]["share_above_1_00"] == 1.0
    assert n["residual_usd_per_window"]["share_windows_with_residual"] == round(1 / 3, 4)
    assert n["price_band_share"]["fills_with_price_in_0_35_0_65"] == 0.6


def test_shell_math_in_dollars_of_the_account():
    sh = ra.shell(111.1422, per_market_cap=7.5, daily_stop=15.0, concurrency=2)
    assert sh["worst_case_simultaneous_usd"] == 15.0 and sh["preflight_deployed_required_usd"] == 45.0
    assert sh["preflight_ok"] is True and 0.13 < sh["worst_case_share_of_bankroll"] < 0.14
    assert ra.shell(30.0, concurrency=2)["preflight_ok"] is False


def test_live_bindings_dedup_and_counts(tmp_path):
    dec = [{"decision_id": "a", "v2_recommended": "extend", "acted": "hold",
            "refusal": "unmatched_usd_cap:3.98>2.50", "would_act_without_safety_cap": "extend"},
           {"decision_id": "b", "v2_recommended": "extend", "acted": "complete", "refusal": None,
            "would_act_without_safety_cap": "complete",
            "counterfactual_l5_price": {"pair_at_touch": "1.02", "within_rung_bound": False, "class": "safety_override:fee_safe_complement_bound"}}]
    end = {"kind": "session_end", "spring_gates": {"safety_shell_version": "R4/cap7.50"},
           "decisions": dec, "orders": [{"stage": "spring_complete_2", "outcome": "no_fill_cancelled"}]}
    msg = "PMUS_L5_CAMPAIGN " + json.dumps(end)
    (tmp_path / "a.txt").write_text(json.dumps({"deploy": [{"message": msg}, {"message": msg}]}))
    (tmp_path / "b.txt").write_text(json.dumps({"deploy": [{"message": msg}]}))
    lb = ra.live_bindings([tmp_path])
    assert lb["decisions"] == 2                            # duplicates across files collapse
    assert {"key": ["R4", "unmatched_usd_cap"], "n": 1} in lb["refusals"]
    assert {"key": ["R4", "extend", "hold"], "n": 1} in lb["would_act_without_safety_cap_vs_acted"]
    assert lb["complete_counterfactuals"]["l5_touch_pair_above_1_00"] == 1
    assert lb["orders"][0]["key"] == ["R4", "complete", "no_fill_cancelled"]


def test_removals_rank_by_live_binding_and_r5_rung_never_widens_the_shell():
    rows = [_row("w1", 0.40, 10), _row("w1", 0.62, 10, pair=1.02, matched=10)]
    l5 = ra.l5_numbers(rows=rows)
    studies = ra.study_numbers()
    live = {"refusals": [{"key": ["R4", "unmatched_usd_cap"], "n": 54}],
            "would_act_without_safety_cap_vs_acted": [{"key": ["R4", "extend", "hold"], "n": 62},
                                                      {"key": ["R4", "complete", "hold"], "n": 22}],
            "complete_counterfactuals": {"l5_touch_pair_above_1_00": 101}, "decisions": 100}
    sh = ra.shell(111.1422, concurrency=2)
    rem = ra.removals(l5, studies, live, sh)
    assert rem[0]["id"] in ("unmatched_usd_cap", "pair_cost_ceiling")
    assert all(r["recommend"].startswith("REMOVE") for r in rem[:5])
    assert all(r["worst_case_usd"] is None or r["worst_case_usd"] <= sh["worst_case_simultaneous_usd"] for r in rem)
    rung = ra.r5_rung(sh, rem)
    assert rung["shell"]["per_market_cap_usd"] == 7.5 and rung["shell"]["concurrency"] == 2
    assert "unmatched_usd_cap" in rung["removed"] and "strict_evidence_gate" in rung["kept"]
    assert "PROPOSAL" in rung["labels"]
    doc = {"generated_utc": "t", "l5": l5, "studies": studies, "live_bindings": {**live, "l5_recommended_vs_acted": [],
           "complete_counterfactuals": {"n": 0, "l5_touch_pair_above_1_00": 0, "l5_touch_pair_above_1_00_share": None,
                                        "pair_at_touch_p50": None, "pair_at_touch_p90": None}},
           "shell": sh, "removals": rem, "r5_rung": rung}
    md = ra.render_md(doc)
    assert "# L5 RESTRICTION AUDIT" in md and "REMOVE in R5" in md and "owner writes the id" in md
