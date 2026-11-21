"""The shadow reporter must not collapse the stages it exists to separate."""
import io
import json

import pytest

from analysis.l5.surface_shadow import report as R
from src.l5_surface import evidence as EV


def _rung(sid, oc, px, stage, is_fill=False, rest_why=None, idx=0):
    reached = list(EV.STAGES[:EV.STAGES.index(stage) + 1])
    detail = {}
    if rest_why:
        detail[EV.RESTABLE] = {"satisfied": False, "why": rest_why}
    return {"surface_id": sid, "outcome": oc, "price": px, "rung_index": idx,
            "stage": stage, "reached": reached, "is_fill": is_fill,
            "detail": detail}


def _tape(tmp_path, rows):
    p = tmp_path / "surface_shadow_20260917T203814Z.jsonl"
    io.open(p, "w").write("".join(json.dumps(r) + "\n" for r in rows))
    return str(tmp_path)


def test_no_tape_is_reported_as_no_tape():
    assert R.report("/definitely/not/here")["ok"] is False


def test_stages_are_the_furthest_each_rung_reached_not_a_tick_count(tmp_path):
    """A rung is re-walked every tick. Counting appearances would multiply one
    rung into dozens and make a single quote look like a population."""
    rows = [{"kind": "boot", "spec": {"label": "L5_ADAPTED_V1",
                                      "spec_sha256": "fe9d6557"}}]
    for stage in (EV.RESTABLE, EV.RESTABLE, EV.BOOK_REACHED):
        rows.append({"kind": "rung_evidence",
                     "rows": [_rung("s1", "Up", "0.50", stage)]})
    d = _tape(tmp_path, rows)
    rep = R.report(d)
    assert rep["distinct_rungs"] == 1
    assert rep["by_stage"][EV.BOOK_REACHED] == 1
    assert rep["by_stage"][EV.RESTABLE] == 0
    assert rep["spec"] == "L5_ADAPTED_V1"


def test_the_gap_between_book_reached_and_fills_is_reported_not_hidden(
        tmp_path):
    rows = [{"kind": "boot", "spec": {"label": "L5_ADAPTED_V1"}},
            {"kind": "rung_evidence", "rows": [
                _rung("s", "Up", "0.50", EV.BOOK_REACHED),
                _rung("s", "Up", "0.49", EV.BOOK_REACHED, idx=1),
                _rung("s", "Down", "0.48", EV.TRADE_FLOW, idx=2),
                _rung("s", "Down", "0.47", EV.EVIDENCED_FILL, True, idx=3)]}]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["distinct_rungs"] == 4
    assert rep["book_reached_share"] == 1.0
    assert rep["trade_flow_share"] == 0.5
    assert rep["evidenced_fill_share"] == 0.25
    assert rep["evidenced_fills"] == 1
    joined = " ".join(rep["labels"])
    assert "is NOT a fill" in joined
    assert "optimistic backtest" in joined


def test_restability_refusals_are_reported_with_their_reason(tmp_path):
    rows = [{"kind": "boot", "spec": {}},
            {"kind": "rung_evidence", "rows": [
                _rung("s", "Up", "0.51", EV.DESIRED_SURFACE,
                      rest_why="a BUY at or above the ask crosses; that is a "
                               "taker"),
                _rung("s", "Up", "0.50", EV.BOOK_REACHED, idx=1)]}]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["restable_share"] == 0.5
    assert any("taker" in k for k in rep["restability_refusals"])


def test_refusals_name_the_unresolved_fields(tmp_path):
    rows = [{"kind": "boot", "spec": {}},
            {"kind": "surface_refused",
             "blocked_by": ["base_parent_qty", "withdrawal_policy"]},
            {"kind": "surface_refused", "blocked_by": ["base_parent_qty"]}]
    rep = R.report(_tape(tmp_path, rows))
    assert rep["surfaces_refused"] == 2 and rep["surfaces_emitted"] == 0
    assert rep["refusal_fields"] == ["base_parent_qty", "withdrawal_policy"]
