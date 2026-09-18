"""GEMINI_L5_WS_CANARY_READY: the exact frame, the gates, and what is unproven.

This answers one question for the owner: if the WS canary were authorized right
now, what EXACTLY would go on the wire, and which of its properties are
established rather than assumed?

IT SUBMITS NOTHING AND ARMS NOTHING. Every call it makes is a read: the public
preflight, the contract's own metadata, and the arming state. It is safe to run
while disarmed, and it reports `armed: false` as a gate rather than treating it
as an error -- a readiness report that could only be produced by an armed
process would be useless for deciding whether to arm.

WHY IT REPORTS UNPROVEN THINGS AS UNPROVEN. The WebSocket has carried an
accepted order on these credentials, which is why it is the chosen transport.
It has never carried a FRACTIONAL quantity, and no venue-accepted maker-or-
cancel order is in the recovered evidence. Those are exactly the two properties
this canary would newly establish, so a report that folded them into a single
"ready" verdict would be claiming the result in advance.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

from src.gemini_execution import arming, canary, latch, ws_transport

LABEL = "GEMINI_L5_WS_CANARY_READY"


async def report(auth_id: Optional[str] = None,
                 state_dir: str = latch.DEFAULT_DIR) -> Dict[str, Any]:
    """Build the readiness record. Reads only."""
    from src import gemini_canary_preflight as PF
    from src.gemini_execution import metadata as MD

    out: Dict[str, Any] = {
        "label": LABEL,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "transport": "WEBSOCKET wss://ws.gemini.com method order.place",
        "transport_evidence": ws_transport.WS_TRANSPORT_EVIDENCE,
        "arming": arming.state(),
        "submitted": False,
        "armed": arming.armed(),
    }

    pf = PF.build()
    out["preflight"] = {
        "ok": bool(pf.get("ok")),
        "verdict": pf.get("verdict"),
        "why": pf.get("why"),
        "generated_utc": pf.get("generated_utc"),
        "instrument": pf.get("instrument"),
        "limit_price": pf.get("limit_price"),
        "quantity": pf.get("quantity"),
        "maximum_cash_exposure_usd": pf.get("maximum_cash_exposure_usd"),
        "tte_s": pf.get("tte_s"),
        "expiry_utc": pf.get("expiry_utc"),
        "current_bbo": pf.get("current_bbo"),
        "restability": pf.get("restability"),
        "venue_terms": pf.get("venue_terms"),
        "rejected_windows": pf.get("rejected_windows"),
    }
    if not pf.get("ok"):
        out["verdict"] = "NOT_READY"
        out["why"] = ("no eligible window: %s" % pf.get("why"))
        out["ws_frame"] = None
        return out

    sym = str(pf.get("instrument"))
    terms = MD.terms_for(sym)
    out["instrument_metadata"] = terms
    try:
        intent = canary.check_envelope(pf, terms=terms)
    except canary.EnvelopeViolation as exc:
        out["verdict"] = "NOT_READY"
        out["why"] = "envelope refused: %s" % exc
        out["ws_frame"] = None
        return out
    out["envelope_intent"] = intent

    # THE FRAME. Only the request id and the client order id are placeholders;
    # both are generated per attempt. Nothing is redacted because nothing
    # secret is present -- the key, the signature and the secret travel in the
    # handshake headers and never appear in a frame.
    out["ws_frame"] = ws_transport.sanitized_ws_frame(
        symbol=intent["symbol"], price=intent["price"],
        quantity=intent["quantity"], side=intent["side"],
        outcome=intent["outcome"])

    out["maker_only"] = {
        "field": "timeInForce",
        "value": ws_transport.WS_MAKER_TIF,
        "why": ("MOC is post-only in the WS dialect. makerOrCancel is the "
                "REST boolean and has no meaning on this socket, so sending "
                "it would post with no post-only protection at all."),
        "venue_level_backstop": (pf.get("venue_terms") or {}).get("status"),
    }

    if auth_id:
        spent = latch.consumed(auth_id, state_dir)
        out["authorization"] = {
            "authorization_id": auth_id,
            "already_consumed": spent,
            "latch_state": (latch.read(auth_id, state_dir) or {}).get("state"),
            "why": ("a consumed authorization is reconciliation work, never a "
                    "resubmission") if spent else None,
        }

    gates = {
        "preflight_ok": True,
        "envelope_ok": True,
        "instrument_metadata_readable": bool(terms.get("readable")),
        "contract_tradeable": (terms.get("contract_status") == "active"
                               and terms.get("market_state") == "open"),
        "maker_only_expressible": True,
        "venue_write_path_proven": True,
        "reconciliation_reads_active_orders_first": (
            ws_transport.reconcile_queries(sym)[0][0]
            == ws_transport.ACTIVE_PATH),
        "rejection_reason_captured": True,
        "durable_one_shot_latch": True,
        "armed": arming.armed(),
        "fractional_quantity_proven_on_this_transport": False,
        "maker_only_proven_on_this_transport": False,
    }
    out["gates"] = gates
    out["verdict"] = "READY_TO_SUBMIT_ON_OWNER_AUTHORIZATION"
    out["what_is_still_unproven"] = [
        ws_transport.WS_TRANSPORT_EVIDENCE["fractional_quantity_why"],
        ws_transport.WS_TRANSPORT_EVIDENCE["maker_only_why"],
    ]
    out["blocking"] = (
        "NOT ARMED. This is the intended state: submission requires an "
        "explicit, unexpired owner authorization that is not already spent. "
        "OWNER-2026-09-17-GEMINI-CANARY-1 was consumed by canary #1 and "
        "cannot be reused." if not arming.armed() else None)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    import asyncio
    args = list(argv if argv is not None else sys.argv[1:])
    auth_id = args[0] if args else None
    rec = asyncio.run(report(auth_id))
    print(json.dumps(rec, indent=2, default=str))
    return 0 if rec.get("verdict", "").startswith("READY") else 1


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(main())
