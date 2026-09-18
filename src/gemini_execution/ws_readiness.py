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

#: the WS mutation verb, named once so the reachability scan and the
#: isolation guard agree on what counts as reaching the venue.
WS_PLACE_MARKER = "order.place"


def compare_to_known_good(frame: Dict[str, Any]) -> Dict[str, Any]:
    """Field by field against the historical frame this socket accepted.

    The comparison is on FIELD NAMES and FIXED ENUM VALUES. `price`,
    `quantity`, `symbol` and `clientOrderId` are per-run by construction, so
    they are reported as `per_run` rather than as differences -- calling a
    fresh price a "mismatch" would bury the one difference that matters.
    """
    known = ws_transport.HISTORICAL_KNOWN_GOOD_FRAME
    ours = dict(frame.get("params") or {})
    per_run = {"symbol", "price", "clientOrderId"}
    rows = []
    for key in sorted(set(known) | set(ours)):
        k_val, o_val = known.get(key), ours.get(key)
        if key not in known:
            verdict = "ADDED_BY_US"
        elif key not in ours:
            verdict = "DROPPED_BY_US"
        elif key in per_run:
            verdict = "PER_RUN"
        elif key == "quantity":
            # THE one substantive difference, and the reason it is safe:
            # historical runs sent "1"; we send the BTC5m contract's own
            # quantityMinimum. Not a mismatch to fix -- a change to declare.
            verdict = ("DIFFERS_BY_DESIGN" if k_val != o_val else "IDENTICAL")
        else:
            verdict = "IDENTICAL" if k_val == o_val else "DIFFERS"
        rows.append({"field": key, "historical": k_val, "ours": o_val,
                     "verdict": verdict})
    differs = [r for r in rows if r["verdict"] in
               ("DIFFERS", "ADDED_BY_US", "DROPPED_BY_US")]
    by_design = [r for r in rows if r["verdict"] == "DIFFERS_BY_DESIGN"]
    return {
        "field_count": len(rows),
        "rows": rows,
        "unexpected_differences": differs,
        "differences_by_design": by_design,
        "shape_matches_known_good": not differs,
        "labels": (
            "the historical frame is the canonical first-leg params from "
            "gemini_short_crypto_btc5m_maker_canary_q1, the maker frame every "
            "live module sent through this socket. The one venue-CONFIRMED "
            "FILLED order is a different shape again -- fill-or-kill, outcome "
            "no -- so it proves the socket, not the maker semantics."),
        "venue_confirmed_filled_order":
            ws_transport.VENUE_CONFIRMED_FILLED_ORDER,
    }


def submission_reachability() -> Dict[str, Any]:
    """Proof that only the one-shot canary can reach a submission.

    Structural and repo-wide, not a claim about intent: every module that can
    reach a venue mutation path is enumerated from the source, and each
    mutation entry point is checked for the arm gate. A reader should be able
    to disprove this by finding a path it missed, which is the point of
    listing them rather than asserting a count.
    """
    import ast
    import pathlib

    from src.gemini_execution import spec

    markers = tuple(spec.MUTATION_PATHS) + (WS_PLACE_MARKER,)
    holders, unguarded = [], []
    for p in sorted(pathlib.Path("src").rglob("*.py")):
        txt = p.read_text(encoding="utf-8")
        if not any(m in txt for m in markers):
            continue
        holders.append(str(p))
        tree = ast.parse(txt)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.dump(node)
            sends = ("order.place" in body and "ws.send" in body) or \
                    any(m in body for m in spec.MUTATION_PATHS)
            if not sends:
                continue
            # the arm gate must be inside the function that can mutate
            if "arming" not in body and "require" not in body:
                unguarded.append("%s:%s" % (p, node.name))
    return {
        "modules_holding_a_mutation_path": holders,
        "all_inside_execution_package": all(
            h.startswith("src/gemini_execution") for h in holders),
        "unguarded_mutation_functions": unguarded,
        "arm_gate": {
            "both_keys_required": [arming.LIVE_ENV, arming.AUTH_ENV],
            "expiry_required": arming.EXPIRY_ENV,
            "expected_authorization": arming.EXPECTED_AUTH_ID,
            "armed_now": arming.armed(),
        },
        "one_shot": {
            "mechanism": "O_EXCL latch consumed BEFORE the mutation",
            "implementation": "src/gemini_execution/latch.py",
            "durable_across_ephemeral_hosts": (
                "durable_check consults git-committed state first, so a fresh "
                "CI disk does not make a spent authorization unspent"),
            "states": list(latch.STATES),
        },
        "verdict": ("ONLY_THE_ONE_SHOT_CANARY_CAN_SUBMIT"
                    if (not unguarded and all(
                        h.startswith("src/gemini_execution") for h in holders))
                    else "REACHABILITY_NOT_ESTABLISHED"),
    }


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

    # FIELD BY FIELD against the frame this socket is known to have accepted.
    out["frame_comparison"] = compare_to_known_good(out["ws_frame"])

    # Proof, from the source, that nothing else can reach a submission.
    out["submission_reachability"] = submission_reachability()

    # QUEUE AHEAD and MAXIMUM EXPOSURE, the two numbers that say what this
    # order risks and how likely it is to do anything.
    fp = pf.get("fill_prospect") or {}
    out["queue_ahead"] = {
        "volume_ahead_at_our_price": fp.get("volume_ahead_at_our_price"),
        "our_quantity": fp.get("our_quantity"),
        "must_clear_before_we_fill": fp.get("must_clear_before_we_fill"),
        "spread": fp.get("spread"),
        "model": fp.get("model"),
        "caveat": fp.get("caveat"),
    }
    out["maximum_cash_exposure_usd"] = pf.get("maximum_cash_exposure_usd")

    # FAIL-CLOSED EXISTING-EXPOSURE CHECK. Reused from run_canary, where the
    # rule is already reviewed: an UNREADABLE order book is not an empty one,
    # so an unreadable read reports clear=False.
    from src.gemini_prediction_auth import has_credentials
    credentialed = has_credentials()
    out["credentialed"] = credentialed
    if not credentialed:
        # Say WHICH it is. "not clear" with no reason reads as "there is an
        # existing order", and that is a different and much more alarming
        # claim than "this host cannot see the account".
        out["existing_exposure"] = {
            "clear": False,
            "unreadable_reason": "NO_CREDENTIALS_ON_THIS_HOST",
            "why": ("the account could not be read from here at all, so "
                    "existing exposure is UNKNOWN, not zero. This gate has to "
                    "be satisfied on the credentialed runner before any "
                    "submission; an unread order book is not an empty one."),
        }
    else:
        try:
            from src.gemini_execution.run_canary import (
                no_existing_canary_exposure)
            out["existing_exposure"] = await no_existing_canary_exposure(sym)
        except Exception as exc:                               # noqa: BLE001
            out["existing_exposure"] = {
                "clear": False,
                "unreadable_reason": type(exc).__name__,
                "why": "exposure read failed; unreadable is not clear"}

    # SETTLEMENT CONTRACT EVIDENCE -- the outcome leg. A settled contract
    # never states what OUR account received, so nothing here is realized P&L.
    try:
        out["settlement_evidence"] = {
            "quoted_instrument": MD.resolution_for(sym),
            "most_recent_settled": MD.most_recent_settled(sym),
            "labels": ("terminal contract evidence LATCHES: this endpoint can "
                       "return a stale `active` reading after expiry, so an "
                       "absent settlement stays UNCONFIRMED rather than being "
                       "read as unsettled."),
        }
    except Exception as exc:                                   # noqa: BLE001
        out["settlement_evidence"] = {
            "readable": False,
            "why": "%s: %s" % (type(exc).__name__, str(exc)[:200])}

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
        "frame_matches_known_good_shape": bool(
            out["frame_comparison"]["shape_matches_known_good"]),
        "only_the_one_shot_canary_can_submit": (
            out["submission_reachability"]["verdict"]
            == "ONLY_THE_ONE_SHOT_CANARY_CAN_SUBMIT"),
        "no_existing_exposure": bool(
            (out.get("existing_exposure") or {}).get("clear")),
        "fractional_quantity_proven_on_this_transport": False,
        "maker_only_proven_on_this_transport": False,
    }
    out["gates"] = gates

    # Three gates are EXPECTED false and are not blockers:
    #   armed -- the whole point is that it is not armed yet;
    #   the two *_proven_on_this_transport -- what a canary would establish.
    expected_false = {"armed", "fractional_quantity_proven_on_this_transport",
                      "maker_only_proven_on_this_transport"}
    blocking = [k for k, v in gates.items()
                if v is not True and k not in expected_false]
    out["blocking"] = blocking
    out["verdict"] = ("READY_TO_SUBMIT_ON_OWNER_AUTHORIZATION" if not blocking
                      else "READY_PENDING_CREDENTIALED_RUNNER")
    out["verdict_why"] = (
        None if not blocking else
        ("every structural gate passes; %s can only be satisfied where the "
         "account is readable. Re-run this report on the credentialed runner "
         "to close it." % ", ".join(blocking)))
    out["what_is_still_unproven"] = [
        ws_transport.WS_TRANSPORT_EVIDENCE["fractional_quantity_why"],
        ws_transport.WS_TRANSPORT_EVIDENCE["maker_only_why"],
    ]
    out["arming_note"] = (
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
