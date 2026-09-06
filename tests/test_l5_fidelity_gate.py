"""L5 fidelity gate (OWNER 5556232035 item 5): label from dimensions
only, overrides reported not excused, P&L never an input."""
from __future__ import annotations

import json

from src import l5_fidelity_gate as gate


def _dec(slug, state, rec, acted, sampled, refusal=None, t="2026-09-06T02:00:00+00:00",
         off=False, unit="0.02", up="0", dn="0", p_taker=0.03):
    ranked = {"extend": [{"relation": "extend", "p": 0.66}, {"relation": "complete", "p": 0.34}],
              "open": [{"relation": "open", "p": 0.9}, {"relation": "extend", "p": 0.1}],
              "complete": [{"relation": "complete", "p": 0.7}, {"relation": "extend", "p": 0.3}]}[rec]
    return {"slug": slug, "state": state, "v2_recommended": rec,
            "evidence_schema": 2,
            "relation_ranked": ranked, "acted": acted, "refusal": refusal,
            "off_distribution": off, "t_utc": t, "next_reevaluation_s": 2.0,
            "behavioral_unit": unit,
            "inventory_before": {"up": up, "dn": dn},
            "shadow": {"stochastic": {"sampled": sampled},
                       "l5_target_size": {"shares_p50": 10.0},
                       "l5_target_aggressiveness": {"p_taker": p_taker}}}


def _order(relation, contracts="0.02"):
    return {"relation": relation, "touch_join": relation in ("open", "extend"),
            "side_convention_ok": True if relation == "open" else None,
            "executed_safe_size": {"contracts": contracts},
            "l5_target_size": {"shares_p50": 10.0}}


def test_spec_is_frozen_and_names_every_owner_dimension():
    spec = gate.load_spec()
    for d in ("action_distribution_relation", "action_distribution_side",
              "post_fill_complete_gap", "price_aggressiveness", "size",
              "timing_cadence", "inventory_trajectory", "maker_taker",
              "off_distribution_rate"):
        assert d in spec["dimensions"], d
    assert spec["labels"]["until_met"] == "L5 replication in validation"
    assert spec["labels"]["when_met"] == "L5 operational"
    assert "P&L" in spec["no_tuning_rule"]


def test_thin_live_evidence_is_in_validation_with_every_gap_named():
    reports = [{"slug": "m1", "decisions": [
        _dec("m1", "p25-45c|tte>10m|flat", "open", "open", "open"),
        _dec("m1", "p25-45c|tte>10m|imb1-10", "extend", "hold", "complete",
             refusal="residual_cap:3.0>=3", up="0.06",
             t="2026-09-06T02:00:02+00:00"),
    ], "orders": [_order("open")]}]
    model = {"base": {"p25-45c|tte>10m|flat": {"open|q<5": 60},
                      "p25-45c|tte>10m|imb1-10": {"extend|q<5": 40}}}
    out = gate.evaluate(reports, rolling=None, model=model)
    v = out["verdict"]
    assert v["label"] == "L5 replication in validation"
    assert v["phase_1_met"] is False
    live = out["live"]
    assert live["scored_decisions"] == 2
    # HOLD counts against the EXECUTED view and its refusal is named
    rel = live["action_distribution_relation"]
    assert rel["value"]["hold_refusals"] == {"residual_cap": 1}
    assert rel["status"] in ("INSUFFICIENT", "FAIL")
    # declared overrides are reported, never counted as passes
    names = {o["dimension"] for o in v["declared_overrides"]}
    assert {"price_aggressiveness", "size", "maker_taker",
            "inventory_trajectory"} <= names
    assert live["size"]["value"]["median_executed_over_l5_p50"] == 0.002
    assert live["maker_taker"]["value"]["live_maker_share"] == 1.0
    # shadow evidence absent -> named as unmeasured, not ignored
    assert "shadow:status" not in v["unmeasured"]
    assert out["shadow"]["status"] == "UNMEASURED"
    assert "P&L" in v["labels"]


def test_gate_passes_only_with_enough_agreeing_live_and_shadow_evidence():
    decs, orders = [], []
    t0 = 1_788_660_000
    from datetime import datetime, timezone
    for i in range(260):
        slug = f"m{i % 6}"
        t = datetime.fromtimestamp(t0 + i * 2, timezone.utc).isoformat()
        if i % 3 == 0:
            d = _dec(slug, "p45-55c|tte>10m|imb1-10", "extend",
                     "extend" if i % 10 else "complete",
                     "extend" if i % 10 else "complete", t=t, up="0.02")
            # draws match the masses: 90% extend / 10% complete
            d["relation_ranked"] = [{"relation": "extend", "p": 0.9},
                                    {"relation": "complete", "p": 0.1}]
        else:
            d = _dec(slug, "p45-55c|tte>10m|flat", "open", "open", "open", t=t)
            d["relation_ranked"] = [{"relation": "open", "p": 1.0}]
        d["next_reevaluation_s"] = 12.0        # 6 markets x 2 s spacing
        decs.append(d)
        orders.append(_order("open"))
    reports = [{"slug": "all", "decisions": decs, "orders": orders}]
    model = {"base": {"p45-55c|tte>10m|flat": {"open|q<5": 67},
                      "p45-55c|tte>10m|imb1-10": {"extend|q<5": 33}}}
    rolling = {"research_arm_stochastic": {
        "arm": "reprice_180s:stochastic", "windows_since_freeze": 320,
        "action_scoring": {"scored_decisions": 400, "l1_sampled_vs_l5": 0.05,
                           "l1_acted_vs_l5": 0.15, "off_distribution_rate_pct": 2.0,
                           "post_fill": {"decisions": 80,
                                         "complete_freq_gap_pp_sampled": -3.0,
                                         "complete_freq_gap_pp_acted": -6.0}}}}
    out = gate.evaluate(reports, rolling=rolling, model=model)
    v = out["verdict"]
    assert v["failing"] == [] and v["unmeasured"] == [], v
    # post-fill decisions: 87 >= 50; relation L1 small; cadence 2s/2s = 1
    assert out["live"]["timing_cadence"]["status"] == "PASS"
    assert out["live"]["post_fill_complete_gap"]["status"] == "PASS"
    assert out["shadow"]["action_distribution_relation"]["status"] == "PASS"
    assert v["phase_1_met"] is True and v["label"] == "L5 operational"
    # same evidence with too few shadow windows -> still in validation
    rolling["research_arm_stochastic"]["windows_since_freeze"] = 24
    v2 = gate.evaluate(reports, rolling=rolling, model=model)["verdict"]
    assert v2["label"] == "L5 replication in validation"
    assert "shadow:action_distribution_relation" in v2["insufficient_sample"]


def test_cli_reads_campaign_log_lines(tmp_path):
    rep = {"kind": "session_end", "slug": "m1",
           "decisions": [_dec("m1", "p25-45c|tte>10m|flat", "open", "open", "open")],
           "orders": [_order("open")]}
    p = tmp_path / "log.txt"
    p.write_text("PMUS_L5_CAMPAIGN " + json.dumps(rep) + "\nnoise\n")
    assert len(gate._read_reports(p)) == 1
    assert gate.main([str(p)]) == 0


def test_shadow_windows_use_the_unique_entered_count_when_published():
    spec = gate.load_spec()
    rolling = {"research_arm_stochastic": {
        "arm": "reprice_180s:stochastic", "windows_since_freeze": 70,
        "windows_entered_unique": 67, "unique_window_ids": 70,
        "action_scoring": {"scored_decisions": 141, "l1_sampled_vs_l5": 0.066,
                           "l1_acted_vs_l5": 0.3, "off_distribution_rate_pct": 16.0,
                           "post_fill": {"decisions": 20,
                                         "complete_freq_gap_pp_sampled": -7.0,
                                         "complete_freq_gap_pp_acted": 19.0}}}}
    sh = gate.score_shadow(rolling, spec)
    assert sh["windows_since_freeze"] == 67            # entered, unique
    legacy = {"research_arm_stochastic": {"windows_since_freeze": 24,
                                          "action_scoring": {}}}
    assert gate.score_shadow(legacy, spec)["windows_since_freeze"] == 24
