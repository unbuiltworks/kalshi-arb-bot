"""The cancel seam, driven with the VENUE'S OWN recorded payloads.

WHY FIXTURES FROM THE WIRE. Three defects in a row survived a large suite
because the fixtures were kinder than the venue: a lambda where production is
async, a default ack with no `result`, a hand-written order row. Every payload
here is copied verbatim out of the first live window
(analysis/gemini/venue_shapes_20260918.json) rather than invented.

WHAT IS AND IS NOT VERIFIED HERE:

  VERIFIED   the cancel sends the venue's numeric orderId, not clientOrderId
  VERIFIED   the id survives into the durable ledger, so a restart can cancel
             what it placed
  VERIFIED   an unchanged surface KEEPs and spends nothing
  VERIFIED   a moved price CANCELs by orderId and REPLACEs
  VERIFIED   a place ack that yields NO id refuses the cancel instead of
             sending a malformed one -- checked against the REAL -1013 reject
  NOT VERIFIED, AND NOT VERIFIABLE UNARMED: the shape of a SUCCESSFUL
             order.place ack. The code that placed the first live orders did
             not record its ack, so `result.orderId` comes from ws_canary's
             recovered known-good parser and is INFERRED. Exercising it for
             real requires placing an order, which requires authorization.
"""
import json
import pathlib

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import spec
from src.gemini_execution import ws_continuous as WC

from tests.test_gemini_ws_continuous import (FakeSession, _authorized,  # noqa
                                             _ready, _rung, _run, AUTH)

SHAPES = json.loads(
    pathlib.Path("analysis/gemini/venue_shapes_20260918.json").read_text())
YES_ROW = next(o for o in SHAPES["open_orders"] if o["outcome"] == "yes")
NO_ROW = next(o for o in SHAPES["open_orders"] if o["outcome"] == "no")
REJECT = SHAPES["cancel_reject_ack"]


def _place_ack(order_id):
    """The INFERRED successful shape. Labelled so nobody reads it as observed."""
    return {"id": "l5cp-<uuid4>", "status": 200, "result": {"orderId": order_id}}


# ---------------------------------------------------------- the seam itself

def test_the_venue_order_id_is_captured_from_the_place_ack(tmp_path,
                                                            _authorized):
    d = _ready(tmp_path)
    s = FakeSession(default=_place_ack(YES_ROW["orderId"]))
    out = _run(d.place(s, _rung("YES", "0.03")))
    assert out["venue_order_id"] == YES_ROW["orderId"] == 145828836242288478


def test_the_cancel_sends_the_venue_orderId_and_not_the_client_id(tmp_path,
                                                                   _authorized):
    """THE DEFECT THE FIRST LIVE WINDOW FOUND."""
    d = _ready(tmp_path)
    s = FakeSession(default=_place_ack(YES_ROW["orderId"]))
    _run(d.place(s, _rung("YES", "0.03")))
    _run(d.cancel(s, d.working.rungs()[0]))

    sent = [m for m in s.sent if m["method"] == spec.WS_CANCEL_METHOD]
    assert len(sent) == 1
    assert sent[0]["params"] == {"orderId": 145828836242288478}
    assert "clientOrderId" not in sent[0]["params"], (
        "clientOrderId is OURS and is what reconciliation matches on; orderId "
        "is the VENUE'S and is what order.cancel takes"
    )


def test_the_venue_id_is_durable_so_a_restart_can_cancel_what_it_placed(
        tmp_path, _authorized):
    d = _ready(tmp_path)
    _run(d.place(FakeSession(default=_place_ack(NO_ROW["orderId"])),
                 _rung("NO", "0.68")))
    rows = [json.loads(l) for l in
            open(d.ledger.path, encoding="utf-8").read().splitlines() if l]
    assert any(r.get("orderId") == NO_ROW["orderId"] for r in rows)

    again = WC.MutationLedger(d.ledger.path, auth_id=AUTH,
                              symbol=YES_ROW["symbol"])
    assert again.consumed() == 1
    assert again.unresolved() == []


# ------------------------------------------------ KEEP / CANCEL / REPLACE

def test_an_unchanged_surface_KEEPS_and_spends_nothing(tmp_path, _authorized):
    d = _ready(tmp_path)
    s = FakeSession(default=_place_ack(YES_ROW["orderId"]))
    want = [_rung("YES", "0.03")]
    for p in WC.diff_surface(want, d.working)["place"]:
        _run(d.place(s, p["rung"]))
    spent = d.budget_consumed
    for _ in range(25):
        plan = WC.diff_surface(want, d.working)
        assert plan["mutations"] == 0 and len(plan["keep"]) == 1
    assert d.budget_consumed == spent


def test_a_moved_price_CANCELS_by_orderId_then_REPLACES(tmp_path, _authorized):
    """The exact sequence that failed live: KEEP 1 / PLACE 1 / CANCEL 1."""
    d = _ready(tmp_path)
    s = FakeSession(default=_place_ack(YES_ROW["orderId"]))
    _run(d.place(s, _rung("YES", "0.03")))
    _run(d.place(FakeSession(default=_place_ack(NO_ROW["orderId"])),
                 _rung("NO", "0.68")))
    assert d.budget_consumed == 2

    # the YES rung moves, the NO rung does not
    plan = WC.diff_surface([_rung("YES", "0.04"), _rung("NO", "0.68")],
                           d.working)
    assert len(plan["keep"]) == 1 and plan["keep"][0]["order"]["leg"] == "NO"
    assert len(plan["cancel"]) == 1 and len(plan["place"]) == 1
    assert plan["mutations"] == 2

    s2 = FakeSession(default=_place_ack(999))
    for c in plan["cancel"]:
        _run(d.cancel(s2, c["order"]))
    for p in plan["place"]:
        _run(d.place(s2, p["rung"]))
    assert d.budget_consumed == 4

    cancels = [m for m in s2.sent if m["method"] == spec.WS_CANCEL_METHOD]
    assert cancels[0]["params"] == {"orderId": YES_ROW["orderId"]}
    # the KEPT rung was never touched
    assert NO_ROW["orderId"] not in [
        m["params"].get("orderId") for m in cancels]


# ------------------------------------------- the fail-safe, on the REAL reject

def test_a_place_ack_without_an_id_refuses_the_cancel_rather_than_malforming_it(
        tmp_path, _authorized):
    d = _ready(tmp_path)
    s = FakeSession(default={"id": "x", "status": 200})     # no result
    out = _run(d.place(s, _rung("YES", "0.03")))
    assert out["venue_order_id"] is None
    before = d.budget_consumed

    with pytest.raises(WC.ReconcileRequired):
        _run(d.cancel(s, d.working.rungs()[0]))
    assert d.state == WC.RECONCILE
    assert d.budget_consumed == before, "an unformed cancel spends no slot"
    assert not [m for m in s.sent if m["method"] == spec.WS_CANCEL_METHOD]
    assert d.working.by_id, "the order is still resting and stays tracked"


def test_the_real_minus_1013_reject_is_treated_as_unknown_not_as_cancelled(
        tmp_path, _authorized):
    """The venue's actual rejection, verbatim. An unacknowledged cancel must
    leave the order tracked as RESTING -- it was not cancelled."""
    d = _ready(tmp_path)
    s = FakeSession(default=_place_ack(YES_ROW["orderId"]))
    _run(d.place(s, _rung("YES", "0.03")))
    o = d.working.rungs()[0]

    with pytest.raises(WC.ReconcileRequired):
        _run(d.cancel(FakeSession(script=[REJECT]), o))
    assert d.state == WC.RECONCILE
    assert d.working.by_id, "a rejected cancel does NOT retire the order"
    resolved = [json.loads(l) for l in
                open(d.ledger.path, encoding="utf-8").read().splitlines() if l]
    assert any(r.get("outcome") == WC.UNKNOWN for r in resolved)


# -------------------------------------- the venue's own rows reconcile cleanly

def test_the_recorded_open_orders_reconcile_into_working_state(tmp_path):
    d = WC.Driver(AUTH, YES_ROW["symbol"],
                  ledger_path=str(tmp_path / "m.jsonl"))
    note = d.working.reconcile(SHAPES["open_orders"], now=0.0)
    assert "2 resting" in note
    by_leg = {o["leg"]: o for o in d.working.rungs()}
    assert by_leg["YES"]["venue_order_id"] == YES_ROW["orderId"]
    assert by_leg["NO"]["venue_order_id"] == NO_ROW["orderId"]
    assert by_leg["YES"]["price"] == "0.03"
    assert by_leg["NO"]["price"] == "0.68"


def test_a_reconciled_order_can_be_cancelled_by_its_venue_id(tmp_path,
                                                              _authorized):
    """After a restart the ids come from the VENUE, not from our acks -- which
    is the path that does not depend on the unobserved place-ack shape."""
    d = _ready(tmp_path)
    d.working.reconcile(SHAPES["open_orders"], now=0.0)
    s = FakeSession(default={"status": 200})
    _run(d.cancel(s, d.working.by_id[YES_ROW["clientOrderId"]]))
    sent = [m for m in s.sent if m["method"] == spec.WS_CANCEL_METHOD]
    assert sent[0]["params"] == {"orderId": YES_ROW["orderId"]}


def test_the_place_ack_shape_is_labelled_as_unobserved():
    assert SHAPES["place_ack"]["CAPTURED"] is False
    assert "INFERRED" in SHAPES["place_ack"]["why"]
