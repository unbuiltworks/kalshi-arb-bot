"""The historical-slot reconciler, and the ordering change it exists to serve.

CONTINUOUS-3 is the whole reason this file exists. Every gate opened, the
authorization was spent, the socket connected -- and then the driver refused,
correctly, because a CANCEL from CONTINUOUS-2 was still recorded UNKNOWN in a
ledger that is committed to the repository. Nothing was ever sent.

Two properties are under test and they pull in opposite directions:

  A KNOWN-STALE REFUSAL MUST NOT COST AN AUTHORIZATION. Deterministic,
  read-only recovery now runs before the latch.

  UNKNOWN HANDLING MUST NOT GET WEAKER. The reconciler closes ONE named slot
  of ONE named authorization on positive evidence and refuses everything else,
  including -- especially -- an unreadable venue.

Fixtures use the REAL recorded payloads. A fixture kinder than the venue is
what hid the last several defects.
"""
from __future__ import annotations

import json
import os

import pytest

from src.gemini_execution import reconcile_slot as RS

SHAPES = os.path.join("analysis", "gemini", "venue_shapes_20260918.json")
TARGET_ORDER_ID = 145828836242288478
COID = "l5cp-owner-2026-yes-774c492b"
SYMBOL = "GEMI-BTC05M2609181435-UP"

#: the REAL rows from analysis/gemini/continuous_ledger.jsonl
REAL_CANCEL = {"kind": "CANCEL", "slot": 2, "t": 1789741869.5275366,
               "auth_id": "OWNER-2026-09-18-GEMINI-CONTINUOUS-2",
               "symbol": SYMBOL, "outcome": "ATTEMPTED",
               "client_order_id": COID}
REAL_RESOLVE = {"kind": "RESOLVE", "slot": 2, "t": 1789741869.5562732,
                "outcome": "UNKNOWN", "status": 400,
                "ack": {"id": "l5cp-81cf6a9a212542dc8db243198c4258c6",
                        "status": 400,
                        "error": {"code": -1013,
                                  "msg": "invalid orderId format"}}}
REAL_PLACE = {"kind": "PLACE", "slot": 0, "t": 1789741860.462346,
              "auth_id": "OWNER-2026-09-18-GEMINI-CONTINUOUS-2",
              "symbol": SYMBOL, "outcome": "ACCEPTED",
              "client_order_id": COID, "leg": "YES",
              "price": "0.03", "qty": "0.01"}

CLEAN_VENUE = {"symbol": SYMBOL, "read_utc": "2026-09-18T16:40:00+00:00",
               "active_ok": True, "active_orders": [],
               "history_readable": True, "history_verdict": "NO_ORDERS",
               "history_rows_for_target": [],
               "target_order_id": TARGET_ORDER_ID}


def ledger_file(tmp_path, rows):
    p = tmp_path / "led.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return str(p)


# ------------------------------------------------------- READING THE LEDGER

def test_the_real_slot_reads_as_unresolved():
    rows = RS.read_ledger("analysis/gemini/continuous_ledger.jsonl")
    st = RS.slot_state(rows, 2)
    assert st["attempt"]["kind"] == "CANCEL"
    assert st["attempt"]["client_order_id"] == COID


def test_a_definitive_rejection_is_recognised_from_the_real_ack():
    d = RS.definitive_rejection([REAL_RESOLVE])
    assert d["definitive"] is True
    assert d["status"] == 400 and d["code"] == -1013
    assert d["msg"] == "invalid orderId format"


def test_silence_is_not_a_definitive_rejection():
    """A timeout or a dropped socket carries NO information. Only a venue that
    validated the request and said no can support NO_VENUE_MUTATION."""
    for r in ({"outcome": "UNKNOWN", "ack": {}},
              {"outcome": "UNKNOWN", "ack": {"status": 200}},
              {"outcome": "UNKNOWN", "error": "timeout"},
              {"outcome": "UNKNOWN", "ack": {"status": 400}}):
        assert RS.definitive_rejection([r])["definitive"] is False, r


def test_a_500_is_not_a_definitive_rejection():
    """5xx is the venue failing, not the venue deciding."""
    r = {"ack": {"status": 503, "error": {"code": -1, "msg": "upstream"}}}
    assert RS.definitive_rejection([r])["definitive"] is False


# ------------------------------------------- THE DECISIVE CORROBORATION

def test_the_recorded_artifact_shows_the_order_still_open_after_the_attempt():
    obs = RS.post_attempt_observation(SHAPES, TARGET_ORDER_ID,
                                      REAL_CANCEL["t"])
    assert obs["readable"] is True and obs["found"] is True
    assert obs["status"] == "open"
    assert obs["still_open_after_attempt"] is True
    assert obs["captured_utc"] == "2026-09-18T14:31:11Z"


def test_a_missing_artifact_is_unreadable_not_absent():
    obs = RS.post_attempt_observation("does/not/exist.json", 1, 0.0)
    assert obs["readable"] is False


# --------------------------------------------------------------- VERDICTS

def test_the_real_case_reaches_no_venue_mutation():
    d = RS.definitive_rejection([REAL_RESOLVE])
    o = RS.post_attempt_observation(SHAPES, TARGET_ORDER_ID, REAL_CANCEL["t"])
    v = RS.verdict_for(d, o, CLEAN_VENUE)
    assert v["verdict"] == RS.NO_MUTATION, v.get("blocking")


def test_an_unreadable_active_orders_read_refuses():
    """UNREADABLE IS NOT EMPTY. This is the substitution that would let a
    silent venue look like a clean one."""
    d = RS.definitive_rejection([REAL_RESOLVE])
    o = RS.post_attempt_observation(SHAPES, TARGET_ORDER_ID, REAL_CANCEL["t"])
    venue = dict(CLEAN_VENUE, active_orders=None)
    assert RS.verdict_for(d, o, venue)["verdict"] == RS.UNREADABLE


def test_an_unreadable_history_refuses():
    d = RS.definitive_rejection([REAL_RESOLVE])
    o = RS.post_attempt_observation(SHAPES, TARGET_ORDER_ID, REAL_CANCEL["t"])
    venue = dict(CLEAN_VENUE, history_readable=False)
    assert RS.verdict_for(d, o, venue)["verdict"] == RS.UNREADABLE


def test_a_still_resting_order_refuses():
    d = RS.definitive_rejection([REAL_RESOLVE])
    o = RS.post_attempt_observation(SHAPES, TARGET_ORDER_ID, REAL_CANCEL["t"])
    venue = dict(CLEAN_VENUE, active_orders=[{"orderId": TARGET_ORDER_ID}])
    v = RS.verdict_for(d, o, venue)
    assert v["verdict"] == RS.MUTATED


def test_a_nonzero_fill_refuses():
    d = RS.definitive_rejection([REAL_RESOLVE])
    o = RS.post_attempt_observation(SHAPES, TARGET_ORDER_ID, REAL_CANCEL["t"])
    venue = dict(CLEAN_VENUE,
                 history_rows_for_target=[{"filledQuantity": "0.01"}])
    assert RS.verdict_for(d, o, venue)["verdict"] == RS.MUTATED


def test_no_definitive_rejection_refuses():
    o = RS.post_attempt_observation(SHAPES, TARGET_ORDER_ID, REAL_CANCEL["t"])
    v = RS.verdict_for({"definitive": False, "why": "silence"}, o, CLEAN_VENUE)
    assert v["verdict"] == RS.MUTATED


# ------------------------------------------------------------- APPLYING

def _evidence(tmp_path, verdict=RS.NO_MUTATION, slot=2):
    d = RS.definitive_rejection([REAL_RESOLVE])
    o = RS.post_attempt_observation(SHAPES, TARGET_ORDER_ID, REAL_CANCEL["t"])
    rep = {"verdict": verdict,
           "proposed_resolve_row": RS.proposed_row(
               slot, REAL_CANCEL["auth_id"], REAL_CANCEL, d, o, CLEAN_VENUE,
               "evidence.json")}
    p = tmp_path / "ev.json"
    p.write_text(json.dumps(rep), encoding="utf-8")
    return str(p)


def test_applying_closes_only_that_slot_and_leaves_the_old_row(tmp_path):
    led = ledger_file(tmp_path, [REAL_PLACE, REAL_CANCEL, REAL_RESOLVE])
    before = open(led, encoding="utf-8").read()
    res = RS.apply_from(_evidence(tmp_path), led)
    assert res["applied"] is True
    assert res["still_unresolved_after"] is False
    after = open(led, encoding="utf-8").read()
    assert after.startswith(before), (
        "the reconciler must APPEND; the original rows stay byte-for-byte")
    assert json.dumps(REAL_RESOLVE) in after.replace("\n", "\n")


def test_the_appended_row_carries_its_provenance(tmp_path):
    led = ledger_file(tmp_path, [REAL_PLACE, REAL_CANCEL, REAL_RESOLVE])
    row = RS.apply_from(_evidence(tmp_path), led)["appended_row"]
    for key in ("reconciled", "reconciled_by", "reconciles_auth_id",
                "reconciles_client_order_id", "venue_status",
                "venue_error_code", "evidence_artifact", "why", "labels"):
        assert key in row, key
    assert row["reconciled"] is True
    assert row["venue_error_code"] == -1013
    assert row["reconciles_auth_id"] == "OWNER-2026-09-18-GEMINI-CONTINUOUS-2"
    assert "HISTORICAL RECONCILIATION" in row["why"]


def test_applying_refuses_a_verdict_that_is_not_a_clean_close(tmp_path):
    led = ledger_file(tmp_path, [REAL_PLACE, REAL_CANCEL, REAL_RESOLVE])
    for verdict in (RS.UNREADABLE, RS.MUTATED):
        res = RS.apply_from(_evidence(tmp_path, verdict=verdict), led)
        assert res["applied"] is False, verdict


def test_applying_twice_is_refused(tmp_path):
    led = ledger_file(tmp_path, [REAL_PLACE, REAL_CANCEL, REAL_RESOLVE])
    assert RS.apply_from(_evidence(tmp_path), led)["applied"] is True
    second = RS.apply_from(_evidence(tmp_path), led)
    assert second["applied"] is False
    assert "already" in second["why"]


def test_a_slot_that_is_not_unresolved_is_never_touched(tmp_path):
    """Closing an already-ACCEPTED slot would be rewriting history."""
    led = ledger_file(tmp_path, [REAL_PLACE])
    res = RS.apply_from(_evidence(tmp_path, slot=0), led)
    assert res["applied"] is False


def test_the_written_outcome_clears_unresolved_in_the_real_ledger_reader(
        tmp_path):
    """END TO END through the DRIVER's own ledger reader, not a reimplementation
    of it. The whole point is that ws_continuous stops refusing."""
    from src.gemini_execution import ws_continuous as WC
    led = ledger_file(tmp_path, [REAL_PLACE, REAL_CANCEL, REAL_RESOLVE])
    before = WC.MutationLedger(led, auth_id="x", symbol=SYMBOL)
    assert len(before.unresolved()) == 1
    RS.apply_from(_evidence(tmp_path), led)
    after = WC.MutationLedger(led, auth_id="x", symbol=SYMBOL)
    assert after.unresolved() == []
    assert after.consumed() == 2, "the slots stay SPENT; only the outcome moved"


def test_unknown_handling_is_not_weakened_for_other_slots(tmp_path):
    """A second, untouched UNKNOWN must still block."""
    from src.gemini_execution import ws_continuous as WC
    other = dict(REAL_CANCEL, slot=3, client_order_id="other-coid")
    led = ledger_file(tmp_path,
                      [REAL_PLACE, REAL_CANCEL, REAL_RESOLVE, other])
    RS.apply_from(_evidence(tmp_path), led)
    after = WC.MutationLedger(led, auth_id="x", symbol=SYMBOL)
    assert [r["slot"] for r in after.unresolved()] == [3]


# ------------------------------------------------------------- STRUCTURAL

def test_the_reconciler_cannot_reach_an_order_path():
    import ast
    tree = ast.parse(open(RS.__file__, encoding="utf-8").read())
    consts = {n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for m in ("order.place", "order.cancel", "order.cancel_session"):
        assert m not in consts, m
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for forbidden in ("place", "cancel", "cancel_and_confirm", "request"):
        assert forbidden not in names, forbidden
    src = open(RS.__file__, encoding="utf-8").read()
    assert "ws_session" not in src and "GeminiMakerSession" not in src
