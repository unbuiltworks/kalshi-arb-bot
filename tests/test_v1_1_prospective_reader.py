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


def test_a_boot_a_missing_stage_one_and_a_dead_handoff_are_all_surfaced():
    """Each of these fails or threatens M0, so none may be averaged away.

    Restated: this previously asserted restarts_in_window == 1 for a window
    containing a SINGLE shadow_start. The intent was right -- surface the
    lifecycle events -- but a first boot is not a restart, and asserting it was
    made a healthy window read as an M0 validity failure."""
    evs = list(R.events(_dump({"kind": "shadow_start", "horizon_s": 10},
                              {"kind": "act_artifact_missing"},
                              {"kind": "no_status", "cycles": 1}, *CYCLES)))
    c = R.cumulative(evs)
    assert c["shadow_start_events"] == 1
    assert c["restarts_in_window"] == 0
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


def test_the_reader_runs_as_a_command_not_only_as_an_import():
    """Run as a script, sys.path[0] is the script's own directory and the
    `analysis...` import fails. The tests import it as a module and would never
    have caught that."""
    import json as _json
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    rows = [{"kind": "cycle", "cycles": i, "t": 1000.0 + i * 20, "rows": 50,
             "proposed": 30, "v1_would_have": 45, "distinct_markets": 40}
            for i in range(1, 6)]
    dump = _json.dumps({"deploy": [{"message": "L5_V1_1_SHADOW " + _json.dumps(r)}
                                   for r in rows]})
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(dump)
        path = fh.name
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable,
                        "analysis/l5/v1_1_shadow/read_prospective.py", path],
                       capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, r.stderr
    out = _json.loads(r.stdout)
    assert out["cumulative"]["observations"] == 250
    assert out["maturity"]["mature"] is False       # nowhere near the floors
    assert "M1_coverage" in out["maturity"]["failing"]


def test_a_first_boot_is_not_counted_as_a_restart():
    """M0 fails on any restart in the window, so miscounting the initial boot
    would make a perfectly healthy sample read as invalid."""
    one = _dump({"kind": "shadow_start"}, *CYCLES)
    c = R.cumulative(list(R.events(one)))
    assert c["shadow_start_events"] == 1
    assert c["restarts_in_window"] == 0
    two = _dump({"kind": "shadow_start"}, *CYCLES, {"kind": "shadow_start"})
    assert R.cumulative(list(R.events(two)))["restarts_in_window"] == 1
