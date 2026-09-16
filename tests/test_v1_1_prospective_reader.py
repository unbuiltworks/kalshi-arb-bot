"""The reader aggregates what the shadow actually logs, and refuses to read an
empty window as a quiet market."""
from __future__ import annotations

import json

from analysis.l5.v1_1_shadow import read_prospective as R


def _dump(*msgs):
    return json.dumps({"deploy": [{"message": "L5_V1_1_SHADOW " + json.dumps(m)}
                                  for m in msgs]})


CYCLES = [{"kind": "cycle", "cycles": i, "rows": 50, "proposed": 30,
           "stage1_wait": 20, "v1_would_have": 50,
           "universe_rows_total": 50 * i} for i in (1, 2, 3, 4)]


def test_both_controllers_are_counted_on_the_same_observations():
    c = R.cumulative(list(R.events(_dump(*CYCLES))))
    assert c["observations"] == 200
    assert c["v1_proposals"] == 200 and c["v1_propose_rate"] == 1.0
    assert c["v1_1_proposals"] == 120 and c["v1_1_propose_rate"] == 0.6
    assert c["reduction_vs_v1"] == 0.4
    assert c["stage1_waits"] == 80


def test_an_empty_window_says_so_rather_than_reporting_zeros():
    """A window with no cycles means the log did not reach us, not that the
    shadow proposed nothing. Those must never look the same."""
    c = R.cumulative([])
    assert c["cycles"] == 0 and "note" in c
    assert "propose_rate" not in c


def test_a_restart_or_a_missing_stage_one_is_surfaced_not_averaged_away():
    evs = list(R.events(_dump({"kind": "shadow_start", "horizon_s": 10},
                              {"kind": "act_artifact_missing"},
                              {"kind": "no_status", "cycles": 1}, *CYCLES)))
    c = R.cumulative(evs)
    assert c["restarts_in_window"] == 1
    assert c["act_artifact_missing"] == 1
    assert c["no_status_events"] == 1


def test_it_parses_raw_text_as_well_as_a_railway_json_dump():
    raw = "\n".join("2026-09-16T13:15:00Z L5_V1_1_SHADOW " + json.dumps(m)
                    for m in CYCLES)
    assert R.cumulative(list(R.events(raw)))["observations"] == 200


def test_nothing_it_prints_could_be_mistaken_for_profit():
    c = R.cumulative(list(R.events(_dump(*CYCLES))))
    assert "not realized" in c["labels"] and "NOT OBSERVABLE" in c["labels"]
    for money in ("pnl", "profit", "realized_usd", "usd"):
        assert not any(money in k.lower() for k in c), money
