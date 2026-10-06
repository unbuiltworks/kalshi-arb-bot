"""The cohort report's gate order is the thing that must not drift.

These exercise paths no existing tape can reach: every Shadow S1 tape so
far was produced by a defective discovery implementation, so none contains
a successful book read. Without these, the first valid segment would
exercise the gate logic for the first time on live data.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mod():
    spec = importlib.util.spec_from_file_location(
        "shadow_cohort_report",
        os.path.join(HERE, "scripts", "shadow_cohort_report.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["shadow_cohort_report"] = m
    spec.loader.exec_module(m)
    return m


def _rows(polls, reads, both_obs, sides, status="COMPLETE", mk="m1"):
    rows = []
    # REAL row shape, taken from the collector: dynamic "<outcome>_bid"
    # keys, and the order's outcome nested inside "order".
    for i in range(both_obs):
        rows.append({"kind": "shadow_snapshot", "market": mk, "t": 100.0 + i,
                     "up_bid": 0.49, "up_ask": 0.51,
                     "down_bid": 0.50, "down_ask": 0.52,
                     "mint_sum": 0.99})
    for s in sides:
        rows.append({"kind": "shadow_order_placed", "market": mk,
                     "t": 100.0, "order": {"outcome": s, "price": 0.49,
                                           "quantity": 23.61}})
    rows.append({"kind": "market_terminal", "market": mk, "status": status,
                 "polls": polls, "successful_reads": reads,
                 "evidenced_fills": 7, "uncertain_fills": 3,
                 "inventory": {"up": 23.61, "down": 0.0},
                 "matched": 0.0, "residual": 23.61})
    return rows


def _gate(**kw):
    M = _mod()
    mkts, _ = M.summarise(_rows(**kw))
    return mkts[0]["measurement_gate"]


def test_no_polls_is_an_infrastructure_result_not_a_fill_free_market():
    assert _gate(polls=0, reads=0, both_obs=0,
                 sides=()) == "GATE3_NO_POLLS"


def test_polls_without_a_successful_read_fails_before_fill_statistics():
    assert _gate(polls=50, reads=0, both_obs=0,
                 sides=()) == "GATE3_NO_SUCCESSFUL_BOOK_READ"


def test_one_sided_book_is_not_a_valid_two_sided_observation():
    M = _mod()
    rows = _rows(polls=50, reads=50, both_obs=0, sides=("up", "down"))
    # A snapshot carrying only ONE outcome key is not a two-sided read.
    rows.insert(0, {"kind": "shadow_snapshot", "market": "m1", "t": 1.0,
                    "up_bid": 0.5, "up_ask": 0.51})
    mkts, _ = M.summarise(rows)
    assert mkts[0]["measurement_gate"] == "GATE4_NO_TWO_SIDED_OBSERVATION"


def test_orders_on_one_side_only_is_not_a_valid_measurement():
    assert _gate(polls=50, reads=50, both_obs=10, sides=("up",)) == \
        "GATE5_ORDERS_NOT_INSTANTIATED_BOTH_SIDES"


def test_all_gates_passed_is_valid():
    assert _gate(polls=50, reads=50, both_obs=10,
                 sides=("up", "down")) == "VALID_MEASUREMENT"


def test_an_earlier_gate_failure_masks_later_zeros():
    """A market with no reads must report the READ failure, never the
    downstream 'no orders placed' or 'no fills' that follows from it."""
    assert _gate(polls=0, reads=0, both_obs=0, sides=()) \
        .startswith("GATE3")


def test_fill_statistics_are_carried_but_the_gate_decides():
    M = _mod()
    mkts, _ = M.summarise(_rows(polls=0, reads=0, both_obs=0, sides=()))
    # 7 evidenced fills are present on the row and must NOT make an
    # invalid market countable.
    assert mkts[0]["evidenced_fills"] == 7
    assert mkts[0]["measurement_gate"] != "VALID_MEASUREMENT"


def test_an_unattributed_tape_is_quarantined_not_counted():
    M = _mod()
    assert M.attribute(
        M.parse_stamp("shadow_19700101T000000Z.jsonl"),
        [{"run_id": 1, "started_utc": "2026-10-06T02:16:29Z",
          "ended_utc": None}]) is None


def test_the_fingerprint_stamped_into_the_report_is_the_frozen_one():
    src = open(os.path.join(HERE, "scripts",
                            "shadow_cohort_report.py")).read()
    assert "441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea76caf333fe24b" \
        in src


def test_the_report_cannot_import_or_mutate_the_collector():
    src = open(os.path.join(HERE, "scripts",
                            "shadow_cohort_report.py")).read()
    assert "owned_order_shadow" not in src
    for verb in ("classify_fill", "CANDIDATE", "http_get"):
        assert verb not in src, verb


def test_manifest_marks_both_defective_runs_invalid():
    man = json.load(open(os.path.join(
        HERE, "analysis", "l5", "shadow", "RUN_MANIFEST.json")))
    bad = [r for r in man["runs"]
           if str(r.get("validity", "")).startswith("INVALID")]
    assert {r["run_id"] for r in bad} == {37399000910, 37400932571}


def test_a_missing_bid_on_one_outcome_is_counted_separately_not_hidden():
    """Both books read but one side has an empty bid ladder: that is still
    a valid two-sided OBSERVATION, and the shortfall is reported rather
    than silently failing the market."""
    M = _mod()
    rows = _rows(polls=50, reads=50, both_obs=0, sides=("up", "down"))
    rows.insert(0, {"kind": "shadow_snapshot", "market": "m1", "t": 1.0,
                    "up_bid": 0.5, "down_bid": None})
    mkts, _ = M.summarise(rows)
    assert mkts[0]["observations_with_both_token_books"] == 1
    assert mkts[0]["observations_with_a_bid_on_both_outcomes"] == 0
    assert mkts[0]["measurement_gate"] == "VALID_MEASUREMENT"


def test_an_unreadable_order_outcome_fails_gate5():
    M = _mod()
    rows = _rows(polls=50, reads=50, both_obs=10, sides=())
    rows.insert(0, {"kind": "shadow_order_placed", "market": "m1",
                    "t": 1.0, "order": None})
    mkts, _ = M.summarise(rows)
    assert mkts[0]["measurement_gate"] == "GATE5_ORDER_OUTCOME_UNREADABLE"


def test_the_window_identity_records_the_outcome_names():
    M = _mod()
    mkts, _ = M.summarise(_rows(polls=9, reads=9, both_obs=3,
                                sides=("up", "down")))
    assert mkts[0]["window"]["outcomes"] == ["down", "up"]
    assert mkts[0]["window"]["first_t"] == 100.0
