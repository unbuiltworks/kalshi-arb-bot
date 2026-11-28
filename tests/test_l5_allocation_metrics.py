"""Allocation measurement (OWNER 5586921649).

Three of these pin bugs I actually hit while building it, because each one
produced a plausible-looking number rather than an error: an escaped-JSON
harvest that parsed to nothing, funnel counters ordered by cycle number
across separate deployments, and a per-deployment aggregate read as if it
described one build."""
from __future__ import annotations

import json

from src import l5_allocation_metrics as am


def _funnel(dep, cycles, ns, t):
    return {"t": t, "deployment": dep, "cycles": cycles, "sessions": 0,
            "submitted": 0, "not_started": dict(ns)}


def test_escaped_harvest_messages_are_parsed_not_silently_dropped(tmp_path):
    """The first version brace-scanned the escaped text directly and found
    nothing, which looked like 'no selections' rather than a parser fault."""
    p = tmp_path / "harvest.txt"
    p.write_text(json.dumps({"deploymentId": "dep1", "deploy": [
        {"message": "PMUS_L5_CAMPAIGN " + json.dumps(
            {"kind": "selection", "log_ts": "2026-09-08T14:00:00Z",
             "chosen": "aec-atp-a-b-2026-09-08",
             "admitted": [{"rank": 1, "slug": "aec-atp-a-b-2026-09-08",
                           "pair_score": 0.84,
                           "components": {"flow": 1.0, "depth": 0.9}},
                          {"rank": 2, "slug": "aec-wta-c-d-2026-09-08",
                           "pair_score": 0.80, "components": {}}]})}]}),
        encoding="utf-8")
    rows = am.selection_timeline([p])
    assert len(rows) == 1
    assert rows[0]["chosen"] == "aec-atp-a-b-2026-09-08"
    assert rows[0]["admitted"][0]["components"]["flow"] == 1.0


def test_raw_stdout_still_parses(tmp_path):
    p = tmp_path / "raw.log"
    p.write_text("noise\nPMUS_L5_CAMPAIGN " + json.dumps(
        {"kind": "selection", "log_ts": "2026-09-08T14:00:00Z",
         "chosen": "s1", "admitted": [{"rank": 1, "slug": "s1"}]}) + "\n",
        encoding="utf-8")
    assert len(am.selection_timeline([p])) == 1


def test_runs_never_diff_one_deployment_against_another():
    """Every deployment counts cycles from 1. Grouping only on a cycle
    decrease made two builds' counters subtract from each other."""
    f = [_funnel("depA", 1, {}, 100.0),
         _funnel("depA", 40, {"no_candidate": 5}, 200.0),
         _funnel("depB", 1, {}, 300.0),
         _funnel("depB", 30, {"complement_acceptance_unproven": 12}, 400.0)]
    runs = am.campaign_runs(f)
    assert len(runs) == 2
    assert {r[0]["deployment"] for r in runs} == {"depA", "depB"}
    out = am.chose_but_never_started(f)
    assert out["by_reason"] == {"complement_acceptance_unproven": 12,
                                "no_candidate": 5}
    assert out["cycles_observed"] == 39 + 29


def test_counters_are_differenced_first_to_last_not_pairwise():
    """Harvesting is sparse. An adjacent-pair diff with a step limit dropped
    every interval it had not sampled and reported 4 blocked cycles where
    the lane had 181."""
    f = [_funnel("d", 1, {"complement_acceptance_unproven": 0}, 1.0),
         _funnel("d", 181, {"complement_acceptance_unproven": 181}, 2.0)]
    out = am.chose_but_never_started(f)
    assert out["cycles_chose_but_never_started"] == 181


def test_a_restart_inside_one_deployment_still_splits():
    f = [_funnel("d", 10, {"no_candidate": 3}, 1.0),
         _funnel("d", 2, {"no_candidate": 1}, 2.0)]
    assert len(am.campaign_runs(f)) == 2


def test_thin_coverage_understates_and_says_so():
    out = am.chose_but_never_started(
        [_funnel("d", 1, {}, 1.0), _funnel("d", 3, {"no_candidate": 1}, 2.0)])
    assert "understates" in out["coverage_caveat"]
    assert "per_deployment" in out


def test_held_cycles_are_never_counted_as_idle():
    """NARROW CAPITAL is the design, not a defect. A cycle skipped for a held
    position must never land in the idle number."""
    sel = [{"t": 0.0, "chosen": "s1", "n_admitted": 3, "admitted": []},
           {"t": 20.0, "chosen": "s1", "n_admitted": 3, "admitted": []}]
    skipped = [{"t": 5.0, "reason": "CAMPAIGN_HOLD:POSITION_HELD"}] * 4
    out = am.flat_time_with_eligible_candidate(sel, skipped)
    assert out["cycles_flat_with_candidate_and_no_choice"] == 0
    assert out["cycles_skipped_position_held"] == 4
    assert "never as idle" in out["reading"]


def test_idle_counts_only_a_cycle_that_had_a_candidate_and_chose_nothing():
    sel = [{"t": 0.0, "chosen": None, "n_admitted": 2, "admitted": []},
           {"t": 20.0, "chosen": None, "n_admitted": 0, "admitted": []},
           {"t": 40.0, "chosen": "s", "n_admitted": 1, "admitted": []}]
    out = am.flat_time_with_eligible_candidate(sel, [])
    assert out["cycles_flat_with_candidate_and_no_choice"] == 1
    assert out["idle_s"] == 20.0


def test_switches_while_holding_is_asserted_from_the_code_path():
    """Zero here must not be read as 'we sampled and saw none'."""
    out = am.switches([{"t": 0.0, "chosen": "a", "admitted": []},
                       {"t": 1.0, "chosen": "b", "admitted": []}],
                      {"per_market": []})
    assert out["switches_while_flat"] == 1
    assert out["switches_while_holding"] == 0
    assert "structural" in out["switches_while_holding_basis"]


def test_the_opportunity_cost_refuses_to_invent_a_dollar_figure():
    out = am.occupied_slot_cost([], [], {"per_market": []})
    assert out["usd_opportunity_cost"] is None
    assert "not a dollar" in out["why_null"]
    assert "b86fe09178ea78a2" in out["why_null"]


def test_incentives_are_never_netted_into_trading_edge():
    econ = am.economics({"per_market": {}})
    assert econ["incentives"]["credited_rebates_usd"] is None
    assert "never netted" in econ["incentives"]["basis"]


def test_the_module_changes_nothing_live():
    from pathlib import Path
    src = Path(am.__file__).read_text(encoding="utf-8")
    for forbidden in ("place_limit_buy", "run_campaign", "run_session",
                      "os.environ[", "save_complement_proof"):
        assert forbidden not in src, forbidden


def test_rows_without_an_in_payload_clock_use_the_railway_timestamp(tmp_path):
    """The bug this pins reported ZERO held cycles against a funnel showing
    44. Harvested resume_start rows carry no log_ts, so requiring one dropped
    every single skipped-cycle row -- and a metric that silently returns
    nothing looks exactly like a metric that found nothing."""
    p = tmp_path / "h.txt"
    p.write_text(json.dumps({"deploymentId": "d", "deploy": [
        {"timestamp": "2026-09-08T15:00:00.000000000Z",
         "message": "PMUS_L5_CAMPAIGN " + json.dumps(
             {"kind": "resume_start", "slug": "s1",
              "selection_skipped": "CAMPAIGN_HOLD:POSITION_HELD (1/1)"})},
        {"timestamp": "2026-09-08T15:00:20.000000000Z",
         "message": "PMUS_L5_CAMPAIGN " + json.dumps(
             {"kind": "selection", "chosen": "s2",
              "admitted": [{"rank": 1, "slug": "s2", "pair_score": 0.8}]})},
    ]}), encoding="utf-8")

    skipped = am.skipped_timeline([p])
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "CAMPAIGN_HOLD:POSITION_HELD"

    sel = am.selection_timeline([p])
    assert len(sel) == 1 and sel[0]["chosen"] == "s2"


def test_a_row_with_no_clock_anywhere_is_dropped_not_guessed(tmp_path):
    p = tmp_path / "h.txt"
    p.write_text(json.dumps({"deploymentId": "d", "deploy": [
        {"message": "PMUS_L5_CAMPAIGN " + json.dumps(
            {"kind": "selection", "chosen": "s1", "admitted": []})},
    ]}), encoding="utf-8")
    assert am.selection_timeline([p]) == []
