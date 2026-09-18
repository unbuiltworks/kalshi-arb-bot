"""The L5 surface engine on the PROVEN WebSocket transport.

THE SMALLEST ADAPTER. It adds no policy. The instrument, the side, the price
and the size all come out of the existing preflight and the existing
`canary.check_envelope`, unchanged -- the L5 surface model is not touched
here and must not be. What this file does is render that envelope into the
recovered WS dialect and put it through the same one-shot machinery the REST
canary already had.

WHY THIS TRANSPORT. Every order this account has ever had accepted was placed
with `order.place` over the authenticated socket. The REST path has exactly
one attempt against it and it was rejected. Between a transport with venue
evidence and a transport with a 400, the evidence wins.

WHAT IS PRESERVED FROM THE REST WORK, because none of it was transport-
specific: the durable one-shot latch consumed BEFORE the mutation, the fresh
preflight immediately before submission, the fail-closed existing-exposure
check, one BTC5m window, one order, no complement, no retry, no repeat, full
rejection/error capture, authoritative reconciliation, and the settlement
contract evidence.

WHAT IS NOT CLAIMED. That the venue will accept quantity 0.01 or MOC on a
BTC5m contract. The contract's own metadata admits 0.01, and MOC is what the
maker path was written for, but this account's recovered history is MLB and
fill-or-kill. Only a submission settles it, and that needs an authorization
this file does not have.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, Optional

from src.gemini_execution import (arming, canary as REST, latch, payload,
                                  spec, ws_session)

#: one BTC5m window, YES side, exactly as the REST envelope
COHORT_PREFIX = REST.COHORT_PREFIX
COHORT_SUFFIX = REST.COHORT_SUFFIX

ACCOUNTING = ("SUBMITTED", "FILLED", "SETTLED", "RECONCILED", "REALIZED")


def client_order_id(intent_id: str) -> str:
    """A venue-side handle, truncated where the recovered path truncated it.

    This is what makes reconciliation authoritative rather than positional:
    the venue stores it and returns it on our own order rows.
    """
    return ("l5c-%s" % intent_id)[:spec.WS_CLIENT_ID_MAX]


def build_frame(intent: Dict[str, Any], *, request_id: Optional[str] = None
                ) -> Dict[str, Any]:
    """Envelope -> WS frame. The only translation step in this file."""
    params = payload.build_ws_order(
        symbol=intent["symbol"], price=str(intent["price"]),
        quantity=str(intent["quantity"]),
        client_order_id=intent["client_order_id"],
        outcome=(spec.WS_OUTCOME_YES
                 if str(intent.get("outcome", "yes")).lower() == "yes"
                 else spec.WS_OUTCOME_NO))
    rid = request_id or "l5-canary-%s" % uuid.uuid4().hex
    return payload.sanitized_ws_frame(params, request_id=rid)


async def dry_run(*, now: Optional[float] = None,
                  request_id: str = "<uuid4 hex, per request>"
                  ) -> Dict[str, Any]:
    """Everything up to the socket, and then stop. Connects to NOTHING.

    It refuses to be anything else: it never constructs a session, so there
    is no path from here to a frame on a wire even if the process were armed.
    """
    from src import gemini_canary_preflight as pf_mod
    from src.gemini_execution import metadata as MD

    pf = await pf_mod.preflight_async(now)
    sym = pf.get("instrument")
    terms = MD.terms_for(sym) if sym else {"readable": False,
                                           "why": "no eligible instrument"}
    try:
        intent = REST.check_envelope(pf, now=now, terms=terms)
    except REST.EnvelopeViolation as exc:
        return {"ok": False, "state": "ENVELOPE_REFUSED", "why": str(exc)}
    intent["intent_id"] = "canary-%s" % uuid.uuid4().hex[:16]
    intent["client_order_id"] = client_order_id(intent["intent_id"])
    rendered = build_frame(intent, request_id=request_id)
    return {
        "ok": True,
        "intent": intent,
        "transport": "WEBSOCKET",
        "instrument_metadata": terms,
        "resolution_evidence": MD.resolution_for(sym) if sym else None,
        "bbo": pf.get("current_bbo"),
        "queue_ahead": (pf.get("fill_prospect") or {}).get(
            "volume_ahead_at_our_price"),
        "max_cash_exposure_usd": MD.max_cash_exposure(intent["quantity"],
                                                      intent["price"]),
        "armed": arming.armed(now),
        "sent": False,
        **rendered,
    }


async def submit_once_ws(auth_id: str, *, state_dir: str = latch.DEFAULT_DIR,
                         now: Optional[float] = None,
                         durable_check: Optional[Callable] = None,
                         make_durable: Optional[Callable] = None,
                         session: Any = None,
                         exposure_check: Optional[Callable] = None
                         ) -> Dict[str, Any]:
    """ONE order, over the proven transport. Every gate the REST path had."""
    from src import gemini_canary_preflight as pf_mod
    from src.gemini_execution import metadata as MD

    # 1 arm. Structural: without it nothing below can reach a frame either,
    # because the session refuses mutation methods while disarmed.
    try:
        arming.require(now)
    except arming.NotArmed as exc:
        return {"submitted": False, "state": "NOT_ARMED", "why": str(exc)}

    # 2 a prior latch is reconciliation work, never a resubmission
    if durable_check is not None and durable_check(auth_id):
        return {"submitted": False, "state": "ALREADY_CONSUMED",
                "durable": True,
                "why": ("recorded consumed in durable storage. Reconcile; "
                        "there is no retry.")}
    if latch.consumed(auth_id, state_dir):
        rec = latch.read(auth_id, state_dir) or {}
        return {"submitted": False, "state": "ALREADY_CONSUMED",
                "latch_state": rec.get("state"),
                "venue_order_id": rec.get("venue_order_id"), "record": rec,
                "why": "already spent. Reconcile; there is no retry."}

    # 3 fresh preflight
    pf = await pf_mod.preflight_async(now)
    sym = pf.get("instrument")
    terms = MD.terms_for(sym) if sym else {"readable": False,
                                           "why": "no instrument"}

    # 4 envelope, unchanged: the L5 surface decides, this file does not
    try:
        intent = REST.check_envelope(pf, now=now, terms=terms)
    except REST.EnvelopeViolation as exc:
        return {"submitted": False, "state": "ENVELOPE_REFUSED",
                "why": str(exc), "preflight_ok": pf.get("ok")}

    # 5 EXISTING EXPOSURE, fail closed. An unreadable book is not an empty
    # one, and the check returns clear=False when it cannot be established.
    check = exposure_check
    if check is None:
        from src.gemini_execution.run_canary import no_existing_canary_exposure
        check = no_existing_canary_exposure
    clear = await check(intent["symbol"])
    if not clear.get("clear"):
        return {"submitted": False, "state": "PREFLIGHT_REFUSED",
                "why": "existing order or residual: %s" % clear.get("why"),
                "existing_exposure": clear}

    intent["intent_id"] = "canary-%s" % uuid.uuid4().hex[:16]
    intent["client_order_id"] = client_order_id(intent["intent_id"])
    intent["amount"] = intent["quantity"]
    intent["transport"] = "WEBSOCKET"

    # 6 consume BEFORE the mutation, and make it durable before the venue
    # sees anything. A failed durable write means nothing was sent, so a
    # later attempt still places exactly one order.
    try:
        rec0 = latch.consume(auth_id, intent, state_dir,
                             durable_check=durable_check)
    except latch.AlreadyConsumed as exc:
        return {"submitted": False, "state": "ALREADY_CONSUMED",
                "why": str(exc)}
    if make_durable is not None:
        try:
            make_durable(auth_id, rec0)
        except Exception as exc:                               # noqa: BLE001
            return {"submitted": False, "state": "DURABILITY_REFUSED",
                    "sent": False,
                    "why": ("not recorded durably, so nothing was sent: "
                            "%s: %s" % (type(exc).__name__, str(exc)[:200]))}

    rendered = build_frame(intent)
    frame = rendered["frame"]

    # 7 connect and send, exactly once. No retry: a second frame is a second
    # order, and the authorization is for one.
    sess = session or ws_session.GeminiMakerSession()
    opened = False
    try:
        if session is None:
            await sess.connect()
        opened = True
        ack = await sess.request(spec.WS_PLACE_METHOD, frame["params"],
                                 timeout=4.0, now=now)
    except Exception as exc:                                   # noqa: BLE001
        # AMBIGUOUS. We do not know whether the venue saw it, and a failure
        # is never evidence that no order exists.
        rec = latch.advance(auth_id, latch.SUBMISSION_UNKNOWN,
                            detail={"error": type(exc).__name__,
                                    "detail": str(exc)[:200],
                                    "request_sent": frame},
                            directory=state_dir)
        if make_durable is not None:
            try:
                make_durable(auth_id, rec)
            except Exception:                                  # noqa: BLE001
                pass
        return {"submitted": None, "state": latch.SUBMISSION_UNKNOWN,
                "why": ("the send did not complete cleanly. Reconcile by "
                        "clientOrderId through authoritative reads; a "
                        "failure is not evidence that no order exists."),
                "client_order_id": intent["client_order_id"],
                "request_sent": frame, "record": rec}
    finally:
        if opened and session is None:
            try:
                await sess.close()
            except Exception:                                  # noqa: BLE001
                pass

    status = int((ack or {}).get("status", 0))
    if status == spec.WS_ACK_OK:
        result = (ack or {}).get("result") or (ack or {}).get("data") or {}
        oid = (result.get("orderId") if isinstance(result, dict) else None)
        rec = latch.advance(auth_id, latch.SUBMITTED, venue_order_id=oid,
                            detail={"ack_status": status,
                                    "client_order_id":
                                        intent["client_order_id"]},
                            directory=state_dir)
        if make_durable is not None:
            try:
                make_durable(auth_id, rec)
            except Exception:                                  # noqa: BLE001
                pass
        return {"submitted": True, "state": latch.SUBMITTED,
                "venue_order_id": oid, "ack_status": status, "ack": ack,
                "client_order_id": intent["client_order_id"],
                "intent_id": intent["intent_id"], "intent": intent,
                "request_sent": frame, "record": rec,
                "accounting": ("SUBMITTED only. Not filled, not settled, not "
                               "realized, and no complement or second order "
                               "follows from this.")}

    # REJECTED. Capture everything the ack carries; a rejection whose cause
    # was not recorded teaches nothing, which is the defect canary #1 left.
    reason = {k: (ack or {}).get(k) for k in
              ("status", "result", "reason", "message", "error", "data")
              if (ack or {}).get(k) is not None}
    rec = latch.advance(auth_id, latch.REJECTED,
                        detail={"ack_status": status, "venue_reason": reason,
                                "request_sent": frame},
                        directory=state_dir)
    if make_durable is not None:
        try:
            make_durable(auth_id, rec)
        except Exception:                                      # noqa: BLE001
            pass
    return {"submitted": False, "state": latch.REJECTED, "ack_status": status,
            "venue_reason": reason, "ack": ack, "request_sent": frame,
            "client_order_id": intent["client_order_id"], "record": rec,
            "why": ("the venue refused the order. The authorization is still "
                    "spent; that is deliberate.")}


async def reconcile(auth_id: str, *, state_dir: str = latch.DEFAULT_DIR
                    ) -> Dict[str, Any]:
    """Authoritative reads only. A latch is never un-consumed by reconciling.

    It matches on clientOrderId, which the venue stores and returns, so the
    answer does not depend on being the only order on the symbol.
    """
    from src.gemini_execution import metadata as MD, transport as TR
    rec = latch.read(auth_id, state_dir)
    if rec is None:
        return {"resolved": False, "why": "no latch for this authorization"}
    cid = ((rec.get("intent") or {}).get("client_order_id")
           or (rec.get("detail") or {}).get("client_order_id"))
    sym = rec.get("symbol")
    active = await TR.find_order(symbol=sym, client_order_id=cid,
                                 order_id=rec.get("venue_order_id"))
    history = await MD.order_history(symbol=sym)
    resolution = MD.resolution_for(sym) if sym else None
    return {
        "resolved": bool(active.get("resolved")) and history.get("readable"),
        "latch_state": rec.get("state"),
        "client_order_id": cid,
        "active": active,
        "order_history": history,
        "settlement_contract": resolution,
        "accounting": {
            "ladder": list(ACCOUNTING),
            "labels": ("SUBMITTED is not FILLED; a settled contract is not a "
                       "credited cashflow; and nothing is REALIZED until the "
                       "account's own cash is reconciled. A resolutionSide "
                       "states the outcome leg only."),
        },
    }


def main(argv: Optional[list] = None) -> int:      # pragma: no cover - entry
    """Render the frame and stop. There is no --submit, by construction."""
    import argparse
    import asyncio
    import json
    ap = argparse.ArgumentParser(description="L5 WS canary DRY RUN")
    ap.add_argument("--request-id", default="<uuid4 hex, per request>")
    a = ap.parse_args(argv)
    out = asyncio.run(dry_run(request_id=a.request_id))
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":                         # pragma: no cover
    raise SystemExit(main())
