"""The evaluator must refuse an early read, and must never attribute the
window's behaviour to the change."""
from __future__ import annotations

import inspect
import json

import pytest

from src import l5_completion_extension_eval as ev
from src import l5_completion_extension_gate as gate


def _session(status, orders, sid="s1", slug="m"):
    return {"kind": "session_end", "slug": slug, "session_id": sid,
            "status": status, "orders": orders}


def _o(stage, outcome="no_fill_cancelled", relation="complete"):
    return {"stage": stage, "relation": relation, "px": "0.5", "qty": "1",
            "filled_qty": "1" if outcome == "filled" else "0",
            "end_reason": ("rejected:ORDER_REJECTED_ZERO_FILL"
                           if outcome == "rejected" else "cancelled")}


def _logs(tmp_path, sessions):
    msgs = [{"message": "PMUS_L5_CAMPAIGN " + json.dumps(s)}
            for s in sessions]
    p = tmp_path / "l.json"
    p.write_text(json.dumps({"deploymentId": "d", "deploy": msgs}))
    return [str(p)]


def test_the_gate_is_checked_before_anything_is_computed():
    src = inspect.getsource(ev.score)
    assert src.index("assert_unmodified") < src.index("measure(")


def test_no_criterion_is_restated_in_the_scorer():
    """A threshold copied here can drift away from the frozen one."""
    src = inspect.getsource(ev)
    body = src.split('"""', 2)[-1]
    for literal in ("0.2011", "0.2413", "1.25", "0.02"):
        assert literal not in body, literal


def test_an_early_read_is_structurally_impossible(tmp_path):
    """18 minutes of data must not produce a direction of travel."""
    rep = ev.score(_logs(tmp_path, [_session("SPRING_MATCHED",
                                             [_o("spring_complete_7")])]),
                   forward_hours=0.3, settled_venue_confirmed=0)
    assert rep["verdict"] == "NOT_YET_ENOUGH_EVIDENCE"
    assert "unmet_sample_minimums" in rep
    # counts are reported; a verdict beside them is not
    assert rep["measured"]["sessions"] == 1
    for k in ("KEEP", "REVERT", "NO_MATERIAL_IMPROVEMENT"):
        assert k not in json.dumps(rep)


def test_only_sessions_the_extension_touched_count_as_treated(tmp_path):
    """THE ATTRIBUTION TRAP. In the first live window the rejected-complete
    share was 38% against a 17.9% baseline -- which reads as a breach until
    you notice 20 of the 21 completes never touched the extension."""
    untouched = _session("SPRING_RESIDUAL_HOLD",
                         [_o("spring_complete_3", "rejected"),
                          _o("spring_complete_5", "rejected")], sid="a")
    touched = _session("SPRING_MATCHED",
                       [_o("spring_complete_7", "filled")], sid="b")
    m = ev.measure(_logs(tmp_path, [untouched, touched]))
    assert m["sessions"] == 2
    assert m["sessions_where_the_extension_fired"] == 1
    assert m["window"]["complete_rejected"] == 2
    assert m["treated_only"]["complete_rejected"] == 0
    assert m["treated_only"]["complete_filled"] == 1


def test_the_two_populations_are_never_pooled(tmp_path):
    m = ev.measure(_logs(tmp_path, [_session("SPRING_MATCHED",
                                             [_o("spring_complete_2")])]))
    assert m["window"] is not m["treated_only"]
    assert "never pooled" in m["attribution_note"]


def test_a_session_under_the_opening_budget_is_a_control(tmp_path):
    m = ev.measure(_logs(tmp_path, [_session("SPRING_MATCHED",
                                             [_o("spring_complete_6")])]),
                   opening_budget=6)
    assert m["sessions_where_the_extension_fired"] == 0


def test_the_minimums_come_from_the_gate_not_from_here():
    src = inspect.getsource(ev.score)
    assert "gate.SAMPLE_MINIMUMS" in src


def test_it_reads_and_never_acts():
    src = inspect.getsource(ev)
    body = src.split('"""', 2)[-1]
    for forbidden in ("place_limit_buy", "run_campaign(", "session_runner",
                      "os.environ[", "requests."):
        assert forbidden not in body, forbidden


def test_the_pre_filter_is_not_spacing_sensitive(tmp_path):
    """A parser that finds nothing is indistinguishable from a lane that
    did nothing. The compact-only filter returned zero sessions for
    pretty-printed JSON and looked exactly like a quiet lane."""
    import json as _j
    spaced = "PMUS_L5_CAMPAIGN " + _j.dumps(
        _session("SPRING_MATCHED", [_o("spring_complete_2")]))
    assert '"kind": "session_end"' in spaced      # spaced, not compact
    p = tmp_path / "spaced.json"
    p.write_text(_j.dumps({"deploymentId": "d",
                           "deploy": [{"message": spaced}]}))
    assert ev.measure([str(p)])["sessions"] == 1


# ---------------------------------------------- the dedupe key that collided
def _srow(slug, status, oids, stop=None):
    r = {"kind": "session_end", "slug": slug, "session_id": None,
         "status": status,
         "orders": [{"order_id": o, "stage": "spring_complete_1",
                     "relation": "complete", "px": "0.5", "qty": "1",
                     "filled_qty": "0", "end_reason": "deadline"}
                    for o in oids]}
    if stop is not None:
        r["stop_reason"] = stop
    return r


def test_two_different_sessions_are_not_collapsed_into_one(tmp_path):
    """session_id is None on every emitted row, so a field key collides.

    (slug, session_id, len(orders), status) degenerates to
    (slug, len(orders), status). On the R5F tape that merged 53 keys whose
    rows carried DIFFERENT order sets and destroyed 151 distinct sessions,
    undercounting every session-level figure this module reports."""
    p = _logs(tmp_path, [_srow("m", "SPRING_RESIDUAL_HOLD", ["A1"]),
                         _srow("m", "SPRING_RESIDUAL_HOLD", ["B1"])])
    got = ev._sessions(p)
    assert len(got) == 2
    assert {o["order_id"] for s in got for o in s["orders"]} == {"A1", "B1"}


def test_a_byte_identical_re_emission_is_still_collapsed(tmp_path):
    """One session seen in two overlapping log windows is one session."""
    r = _srow("m", "SPRING_MATCHED", ["A1"])
    p = _logs(tmp_path, [r, r, r])
    assert len(ev._sessions(p)) == 1


def test_zero_order_sessions_differing_in_stop_reason_are_kept_apart(tmp_path):
    p = _logs(tmp_path, [_srow("m", "SPRING_RESIDUAL_HOLD", [],
                               stop="spend_cap_reached"),
                         _srow("m", "SPRING_RESIDUAL_HOLD", [],
                               stop="entry_band_left")])
    assert len(ev._sessions(p)) == 2


def test_session_derived_orders_reconcile_with_the_order_denominator(tmp_path):
    """The proof the fix is right: both readers must see the same orders.

    all_orders dedupes by (order_id, stage) and was always correct; _sessions
    dropped 559 complete orders before the fix."""
    import os
    from src import l5_completion_economics as ce
    logs = "../r5f_logs"
    if not os.path.isdir(logs):
        pytest.skip("harvested logs not present")
    paths = [os.path.join(logs, f) for f in sorted(os.listdir(logs))]
    from_sessions = {o.get("order_id") for s in ev._sessions(paths)
                     for o in (s.get("orders") or [])}
    from_orders = {o.get("order_id") for o in ce.all_orders(paths)}
    assert from_sessions == from_orders
