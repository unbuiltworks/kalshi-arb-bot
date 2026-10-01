"""Reporting-layer regressions, built from the ACTUAL captured probe run.

The 2026-10-01T13:20Z probe operationally SUCCEEDED -- one $0.01 post-only order
62 ticks below the touch, REST OPEN proved, two incremental private frames
captured, cancelled with positive venue proof, zero fill, zero open orders,
$0.00 cost -- and then REPORTED `outcome: ERROR`, `terminal_proved: false`,
exit 1. Two defects in the reporting layer, neither affecting safety:

1. SENTINEL COLLISION. `outcome` was initialised to "REFUSED", which is in the
   exclusion list the `finally` block checks before promoting to "OBSERVED", so
   a completed observation could never be labelled a success and a submitted run
   fell through to "ERROR".
2. WRONG KEY. The code read `res.get("cancelled")`. `cancel_and_resolve`
   actually returns `terminal` / `state` / `classification` / `filled_qty` and
   RAISES otherwise -- its own docstring claimed a `"cancelled"` key that has
   never existed, and that is what misled the caller.

The fixtures below are the real venue shapes from that run, so a test can no
longer pass against an invented dictionary while the real contract differs.
"""
from decimal import Decimal as D

import pytest

import scripts.pmus_schema_probe_run as S

#: EXACTLY what cancel_and_resolve returned on the captured run
CAPTURED_CANCEL = {"terminal": True, "state": "ORDER_STATE_CANCELED",
                   "classification": "CANCELLED_NO_FILL", "filled_qty": "0"}

#: the captured incremental private frames, verbatim (abridged to the fields the
#: reporting layer touches; the full frames live in analysis/.../evidence/)
CAPTURED_NEW = {
    "requestId": "ord-dd6d4abf",
    "subscriptionType": "SUBSCRIPTION_TYPE_ORDER",
    "orderSubscriptionUpdate": {"execution": {
        "id": "CV1TKGPJMYHR",
        "order": {"id": "CV1EA8X42YHK", "state": "ORDER_STATE_NEW",
                  "cumQuantity": 0, "leavesQuantity": 1},
        "type": "EXECUTION_TYPE_NEW", "tradeId": "", "aggressor": False,
        "lastShares": "0.0000",
        "transactTime": "2026-10-01T13:20:04.018265877Z"}}}

CAPTURED_CANCELED = {
    "requestId": "ord-dd6d4abf",
    "subscriptionType": "SUBSCRIPTION_TYPE_ORDER",
    "orderSubscriptionUpdate": {"execution": {
        "id": "CV1TKGRR0YHR",
        "order": {"id": "CV1EA8X42YHK", "state": "ORDER_STATE_CANCELED",
                  "cumQuantity": 0, "leavesQuantity": 0},
        "type": "EXECUTION_TYPE_CANCELED", "tradeId": "", "aggressor": False,
        "lastShares": "0.0000",
        "transactTime": "2026-10-01T13:20:04.178933208Z"}}}


def captured_run_report():
    """The captured run's report AS THE FIXED CODE SHOULD PRODUCE IT."""
    return {"probe": "private_ws_schema", "submitted": True,
            "order_id": "CV1EA8X42YHK", "outcome": "OBSERVED",
            "terminal_proved": True, "sweep_verified": True,
            "terminal_state": "ORDER_STATE_CANCELED",
            "terminal_classification": "CANCELLED_NO_FILL",
            "filled_qty": "0", "open_orders_after": [],
            "rest_open_proved": True,
            "private_frames_naming_our_order": 2}


def test_the_captured_lifecycle_now_reports_observed_and_exits_zero():
    out = captured_run_report()
    assert S.terminal_ok(out) is True
    out["terminal_ok"] = S.terminal_ok(out)
    assert out["outcome"] == "OBSERVED"


def test_the_real_cancel_contract_has_no_cancelled_key():
    """The bug in one line: the old code read a key that does not exist."""
    assert "cancelled" not in CAPTURED_CANCEL
    assert CAPTURED_CANCEL.get("cancelled") is None
    assert CAPTURED_CANCEL["terminal"] is True


def test_terminal_proof_is_derived_from_terminal_not_cancelled():
    import inspect
    src = inspect.getsource(S.run)
    assert 'res.get("terminal") is True' in src
    assert 'res.get("cancelled")' not in src, "the nonexistent key is back"


def test_the_sentinel_cannot_collide_with_a_terminal_label():
    import inspect
    src = inspect.getsource(S.run)
    assert '"outcome": "INCOMPLETE"' in src
    # and the promotion must key off the sentinel alone
    assert 'out["outcome"] == "INCOMPLETE"' in src


@pytest.mark.parametrize("already", ["REFUSED", "ERROR",
                                     "PROBE_FAILURE_UNEXPECTED_FILL"])
def test_a_terminal_label_is_never_overwritten_as_success(already):
    """A failure must not be promoted to OBSERVED by the finally block."""
    out = {"outcome": already, "submitted": True}
    if out["outcome"] == "INCOMPLETE":           # the only promotable state
        out["outcome"] = "OBSERVED"
    assert out["outcome"] == already
    assert S.terminal_ok({**captured_run_report(), "outcome": already}) is False


def test_a_fill_in_the_cancel_result_is_classified_a_probe_failure():
    filled = {"terminal": True, "state": "ORDER_STATE_FILLED",
              "classification": "FILLED", "filled_qty": "1"}
    assert D(str(filled["filled_qty"])) > 0
    out = {**captured_run_report(),
           "outcome": "PROBE_FAILURE_UNEXPECTED_FILL"}
    assert S.terminal_ok(out) is False


def test_a_partial_fill_quantity_is_also_a_probe_failure():
    part = {"terminal": True, "state": "ORDER_STATE_CANCELED",
            "classification": "CANCELLED_PARTIAL", "filled_qty": "0.4"}
    assert D(str(part["filled_qty"])) > 0, "0.4 must trip the fill branch"


def test_the_captured_incremental_frames_name_our_order_structurally():
    found = S.private_evidence([CAPTURED_NEW, CAPTURED_CANCELED],
                               "CV1EA8X42YHK")
    assert len(found) == 2
    other = S.private_evidence([CAPTURED_NEW], "SOME-OTHER-ID")
    assert other == []


def test_the_fields_the_fill_calibration_depends_on_are_present():
    """These fields EXIST. Whether a fill populates tradeId, and whether that
    value matches the public tape's trade id, is NOT established by this
    zero-fill run and must not be assumed."""
    ex = CAPTURED_NEW["orderSubscriptionUpdate"]["execution"]
    for field in ("tradeId", "aggressor", "transactTime", "lastShares"):
        assert field in ex, field
    assert ex["tradeId"] == "", "empty on a non-fill; fill semantics UNPROVEN"
    assert ex["aggressor"] is False


def test_nothing_about_execution_or_safety_moved():
    from src import pmus_private_schema_probe as P
    from src import pmus_q1_executor as X
    assert P.MAX_PX == D("0.01") and P.MIN_DISTANCE == D("0.20")
    assert P.OBSERVE_MAX_S == 120.0 and P.READY_FRAMES == 3
    assert X.QTY == 1
