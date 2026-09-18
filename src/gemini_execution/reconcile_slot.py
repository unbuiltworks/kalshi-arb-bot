"""Reconcile ONE historical ledger slot against authoritative venue state.

WHY THIS EXISTS. CONTINUOUS-3 reached the mutation boundary with every gate
open and then refused, because slot 2 of CONTINUOUS-2 -- a CANCEL the venue
rejected 400/-1013 -- was still recorded UNKNOWN. The ledger is committed to
the repository, so that row blocks every future window until it is closed
against the venue. It cost an authorization to discover.

WHAT IT IS ALLOWED TO DO. Read. It opens no order path, imports no session and
never calls a mutation method; a structural test asserts that. It answers one
question -- did this attempt change anything at the venue? -- and writes the
answer to an artifact.

WHAT IT IS NOT ALLOWED TO DO. Weaken UNKNOWN handling. It closes ONE named
slot of ONE named authorization on positive evidence, and refuses on anything
else. UNREADABLE IS NOT EMPTY: a read that did not succeed is never scored as
"nothing there", because those are different facts and only one is safe.

TWO PHASES ON PURPOSE. Gathering needs credentials and runs in CI. APPLYING
writes a row that clears a safety block, so it is a separate invocation over
the committed evidence -- reviewable, and never an automatic consequence of a
read.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "GEMINI_SLOT_RECONCILE_V1"

#: the only verdict that may close a slot
NO_MUTATION = "NO_VENUE_MUTATION"
UNREADABLE = "UNREADABLE"
MUTATED = "VENUE_MUTATION_POSSIBLE"

#: outcome written to the ledger. `REJECTED` already exists in ws_continuous
#: and is neither ATTEMPTED nor UNKNOWN, so closing the slot needs no change
#: to the authorized driver.
RESOLVED_OUTCOME = "REJECTED"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_ledger(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def slot_state(rows: Sequence[Mapping[str, Any]], slot: int) -> Dict[str, Any]:
    """The attempt row, its resolutions, and whether it is still open."""
    attempt = None
    resolves: List[Mapping[str, Any]] = []
    for r in rows:
        if r.get("slot") != slot:
            continue
        if r.get("kind") == "RESOLVE":
            resolves.append(r)
        elif r.get("kind") in ("PLACE", "CANCEL"):
            attempt = r
    outcome = resolves[-1].get("outcome") if resolves else (
        attempt or {}).get("outcome")
    return {"attempt": attempt, "resolves": list(resolves),
            "effective_outcome": outcome,
            "still_unresolved": outcome in ("ATTEMPTED", "UNKNOWN")}


def place_row_for(rows: Sequence[Mapping[str, Any]],
                  client_order_id: str) -> Optional[Mapping[str, Any]]:
    for r in rows:
        if (r.get("kind") == "PLACE"
                and str(r.get("client_order_id")) == str(client_order_id)):
            return r
    return None


def definitive_rejection(resolves: Sequence[Mapping[str, Any]]
                         ) -> Dict[str, Any]:
    """Did the venue REFUSE the request outright, rather than go quiet?

    A 4xx carrying an error code is the venue stating it validated the request
    and declined it. A timeout, a dropped socket or a missing ack is the
    opposite: no information. Only the first can support NO_VENUE_MUTATION.
    """
    for r in resolves:
        ack = r.get("ack") or {}
        status = ack.get("status") if isinstance(ack, Mapping) else None
        err = ack.get("error") if isinstance(ack, Mapping) else None
        code = err.get("code") if isinstance(err, Mapping) else None
        msg = err.get("msg") if isinstance(err, Mapping) else None
        if isinstance(status, int) and 400 <= status < 500 and code is not None:
            return {"definitive": True, "status": status, "code": code,
                    "msg": msg,
                    "why": ("the venue validated the request and refused it; "
                            "a %s rejection is a decision, not silence"
                            % code)}
    return {"definitive": False,
            "why": ("no 4xx-with-error-code resolution found; an attempt "
                    "without a definitive rejection stays UNKNOWN")}


def post_attempt_observation(artifact: str, order_id: Any,
                             after_t: float) -> Dict[str, Any]:
    """Was the targeted order still OPEN after the attempt was rejected?

    This is the decisive corroboration and it is why a recorded artifact is
    kept: the venue's own reply two seconds later showing the order still
    resting proves the rejected cancel did not cancel it.
    """
    try:
        with open(artifact, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"readable": False,
                "why": "%s: %s" % (type(exc).__name__, exc)}
    captured = doc.get("captured_utc")
    rows = doc.get("open_orders")
    if rows is None:
        return {"readable": False, "why": "artifact carries no open_orders"}
    for o in rows:
        if str(o.get("orderId")) == str(order_id):
            return {"readable": True, "found": True,
                    "captured_utc": captured,
                    "status": o.get("status"),
                    "filledQuantity": o.get("filledQuantity"),
                    "still_open_after_attempt": o.get("status") == "open",
                    "attempt_epoch": after_t,
                    "why": ("the venue reported this order still OPEN after "
                            "the attempt was rejected, so the attempt did not "
                            "cancel it")}
    return {"readable": True, "found": False,
            "captured_utc": captured,
            "why": "the targeted order is not in the recorded open set"}


async def venue_evidence(symbol: str, client_order_id: str,
                         order_id: Any) -> Dict[str, Any]:
    """Every authoritative read that bears on the question. READ-ONLY."""
    from src.gemini_execution import metadata as MD
    from src.gemini_execution import transport as T

    out: Dict[str, Any] = {"symbol": symbol, "read_utc": _utc()}

    try:
        act = await T.active_orders(symbol)
        out["active_ok"] = bool(act.get("ok"))
        out["active_orders"] = T._orders_of(act) if act.get("ok") else None
    except Exception as exc:                                   # noqa: BLE001
        out["active_ok"] = False
        out["active_orders"] = None
        out["active_error"] = "%s: %s" % (type(exc).__name__, exc)

    for key, kwargs in (("by_client_order_id",
                         {"client_order_id": client_order_id}),
                        ("by_order_id", {"order_id": order_id})):
        try:
            out[key] = await T.find_order(symbol=symbol, **kwargs)
        except Exception as exc:                               # noqa: BLE001
            out[key] = {"resolved": False,
                        "why": "%s: %s" % (type(exc).__name__, exc)}

    try:
        hist = await MD.order_history(symbol=symbol,
                                      statuses=("filled", "cancelled"),
                                      limit=100)
        out["history_readable"] = hist.get("readable") is not False
        out["history_verdict"] = hist.get("verdict")
        out["history_total_rows"] = hist.get("total_rows")
        # RECORD EVERY ROW, THEN FILTER -- never the other way round. The
        # first version stored only the matched rows, so an empty result could
        # not be told apart from a BROKEN MATCHER, and the "no non-zero fill"
        # clause would have been vacuous rather than satisfied. That is the
        # same shape as every fixture-kinder-than-the-venue defect here.
        # THE RAW STRUCTURE, VERBATIM. `order_history` aggregates its rows
        # into counts and samples and DISCARDS the list -- there is no
        # per_status[].orders key at all. Iterating one produced an empty set
        # on every call, which read as "the venue returned nothing" when it
        # actually meant "this code read a key that does not exist". Recording
        # the structure is what makes that distinguishable.
        out["history_per_status_raw"] = hist.get("per_status")
        allrows: List[Dict[str, Any]] = []
        for p in (hist.get("per_status") or []):
            for key in ("orders", "results", "data", "btc_samples"):
                got = p.get(key)
                if isinstance(got, list):
                    allrows.extend(x for x in got if isinstance(x, dict))
            if isinstance(p.get("sample"), dict):
                allrows.append(p["sample"])
        out["history_rows_symbol"] = [
            {k: o.get(k) for k in
             ("orderId", "clientOrderId", "globalOrderId", "symbol", "status",
              "side", "outcome", "price", "quantity", "filledQuantity",
              "remainingQuantity", "createdAt", "updatedAt")}
            for o in allrows]
        out["history_row_keys_seen"] = sorted(
            {k for o in allrows for k in o.keys()})

        def _matches(o: Mapping[str, Any]) -> bool:
            wanted_c = str(client_order_id)
            wanted_o = str(order_id)
            for key in ("clientOrderId", "client_order_id", "clientOrderID"):
                if str(o.get(key)) == wanted_c:
                    return True
            for key in ("orderId", "order_id", "orderID", "globalOrderId"):
                if str(o.get(key)) == wanted_o:
                    return True
            return False

        rows = [o for o in allrows if _matches(o)]
        out["history_rows_for_target"] = rows
        out["history_match_diagnostic"] = {
            "symbol_rows": len(allrows), "matched": len(rows),
            "why": ("every row returned for this symbol is recorded above, so "
                    "an empty match is auditable rather than assumed")}
    except Exception as exc:                                   # noqa: BLE001
        out["history_readable"] = False
        out["history_error"] = "%s: %s" % (type(exc).__name__, exc)

    try:
        out["resolution"] = MD.resolution_for(symbol)
    except Exception as exc:                                   # noqa: BLE001
        out["resolution"] = {"readable": False,
                             "why": "%s: %s" % (type(exc).__name__, exc)}
    return out


def verdict_for(rejection: Mapping[str, Any], observed: Mapping[str, Any],
                venue: Mapping[str, Any]) -> Dict[str, Any]:
    """Conservative by construction: every clause must hold, or no close."""
    reasons: List[str] = []

    if not rejection.get("definitive"):
        reasons.append(rejection.get("why") or "no definitive rejection")
    if not observed.get("readable"):
        reasons.append("post-attempt observation unreadable: %s"
                       % observed.get("why"))
    elif not observed.get("still_open_after_attempt"):
        reasons.append("the targeted order was NOT observed still open after "
                       "the attempt, so the attempt is not excluded as its "
                       "cause")

    if venue.get("active_orders") is None:
        reasons.append("active-orders read did not succeed; UNREADABLE is not "
                       "empty")
        return {"verdict": UNREADABLE, "blocking": reasons}
    if not venue.get("history_readable"):
        reasons.append("order-history read did not succeed; UNREADABLE is not "
                       "empty")
        return {"verdict": UNREADABLE, "blocking": reasons}

    if venue.get("active_orders"):
        reasons.append("%d order(s) still ACTIVE on this symbol; nothing is "
                       "closed while something is still resting"
                       % len(venue["active_orders"]))

    for row in (venue.get("history_rows_for_target") or []):
        if str(row.get("filledQuantity") or "0") not in ("0", "0.0", ""):
            reasons.append("the targeted order has a non-zero filledQuantity "
                           "in history; this is not a no-op")

    # Any row on this symbol with a fill is disqualifying even if it did not
    # match the target, because an unmatched row means either it is not ours
    # or the matcher missed it -- and only one of those is safe.
    for row in (venue.get("history_rows_symbol") or []):
        if str(row.get("filledQuantity") or "0") not in ("0", "0.0", ""):
            reasons.append("a row on this symbol carries a non-zero "
                           "filledQuantity (orderId %s); the account is not "
                           "flat here and nothing is closed on a guess"
                           % row.get("orderId"))

    if reasons:
        return {"verdict": MUTATED if not any("UNREADABLE" in r
                                              for r in reasons)
                else UNREADABLE, "blocking": reasons}
    return {"verdict": NO_MUTATION, "blocking": [],
            "why": ("the venue refused the request with %s, and reported the "
                    "targeted order still open afterwards; no fill and no "
                    "resting order remain" % rejection.get("code"))}


def proposed_row(slot: int, auth_id: str, attempt: Mapping[str, Any],
                 rejection: Mapping[str, Any], observed: Mapping[str, Any],
                 venue: Mapping[str, Any], evidence_path: str
                 ) -> Dict[str, Any]:
    """The RESOLVE row, carrying WHY it was written and by what."""
    return {
        "kind": "RESOLVE", "slot": slot, "t": time.time(),
        "outcome": RESOLVED_OUTCOME,
        "reconciled": True,
        "reconciled_utc": _utc(),
        "reconciled_by": LABEL,
        "reconciles_auth_id": auth_id,
        "reconciles_kind": attempt.get("kind"),
        "reconciles_client_order_id": attempt.get("client_order_id"),
        "reconciles_symbol": attempt.get("symbol"),
        "venue_order_id": venue.get("target_order_id"),
        "venue_status": rejection.get("status"),
        "venue_error_code": rejection.get("code"),
        "venue_error_msg": rejection.get("msg"),
        "evidence_artifact": evidence_path,
        "evidence_read_utc": venue.get("read_utc"),
        "post_attempt_observation_utc": observed.get("captured_utc"),
        "post_attempt_order_status": observed.get("status"),
        "why": ("HISTORICAL RECONCILIATION, not a live outcome. The venue "
                "refused this request with %s %s and reported the targeted "
                "order still OPEN afterwards, so the attempt changed nothing "
                "at the venue. Closes ONLY this slot."
                % (rejection.get("status"), rejection.get("code"))),
        "labels": ("UNKNOWN handling is unchanged for every other attempt; "
                   "this row is positive evidence about one named slot."),
    }


async def gather(args: argparse.Namespace) -> Dict[str, Any]:
    rows = read_ledger(args.ledger)
    st = slot_state(rows, args.slot)
    attempt = st["attempt"]
    if attempt is None:
        return {"label": LABEL, "verdict": MUTATED,
                "why": "no attempt row for slot %d" % args.slot}

    coid = str(attempt.get("client_order_id") or "")
    symbol = str(attempt.get("symbol") or "")
    rejection = definitive_rejection(st["resolves"])
    observed = post_attempt_observation(args.shapes, args.order_id,
                                        float(attempt.get("t") or 0))
    venue = await venue_evidence(symbol, coid, args.order_id)
    venue["target_order_id"] = args.order_id
    v = verdict_for(rejection, observed, venue)

    rep: Dict[str, Any] = {
        "label": LABEL, "utc": _utc(),
        "target": {"slot": args.slot, "auth_id": attempt.get("auth_id"),
                   "kind": attempt.get("kind"), "symbol": symbol,
                   "client_order_id": coid, "order_id": args.order_id},
        "ledger_state_before": st,
        "originating_place_row": place_row_for(rows, coid),
        "definitive_rejection": rejection,
        "post_attempt_observation": observed,
        "venue_evidence": venue,
        "verdict": v["verdict"], "blocking": v.get("blocking"),
        "why": v.get("why"),
        "labels": ("READ-ONLY. No order path was opened. This artifact is "
                   "evidence; applying it is a separate, reviewable step."),
    }
    if v["verdict"] == NO_MUTATION:
        rep["proposed_resolve_row"] = proposed_row(
            args.slot, str(attempt.get("auth_id")), attempt, rejection,
            observed, venue, args.out or "")
    return rep


def apply_from(evidence_path: str, ledger_path: str) -> Dict[str, Any]:
    """Append the proposed row. Refuses anything that is not a clean close."""
    with open(evidence_path, "r", encoding="utf-8") as fh:
        rep = json.load(fh)
    if rep.get("verdict") != NO_MUTATION:
        return {"applied": False,
                "why": "verdict is %s, not %s" % (rep.get("verdict"),
                                                  NO_MUTATION)}
    row = rep.get("proposed_resolve_row")
    if not row:
        return {"applied": False, "why": "no proposed row in the evidence"}

    slot = row.get("slot")
    before = slot_state(read_ledger(ledger_path), slot)
    if not before["still_unresolved"]:
        return {"applied": False,
                "why": "slot %s is already %s; nothing to close"
                       % (slot, before["effective_outcome"])}

    row = dict(row)
    row["t"] = time.time()
    row["applied_utc"] = _utc()
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())

    after = slot_state(read_ledger(ledger_path), slot)
    return {"applied": True, "slot": slot,
            "outcome_before": before["effective_outcome"],
            "outcome_after": after["effective_outcome"],
            "still_unresolved_after": after["still_unresolved"],
            "appended_row": row}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger",
                    default="analysis/gemini/continuous_ledger.jsonl")
    ap.add_argument("--slot", type=int, default=2)
    ap.add_argument("--order-id", default="145828836242288478")
    ap.add_argument("--shapes",
                    default="analysis/gemini/venue_shapes_20260918.json")
    ap.add_argument("--out")
    ap.add_argument("--apply-from",
                    help="apply a previously gathered evidence artifact")
    a = ap.parse_args(argv)

    if a.apply_from:
        res = apply_from(a.apply_from, a.ledger)
        print("GEMINI_SLOT_RECONCILE_APPLY " + json.dumps(res, default=str))
        return 0 if res.get("applied") else 1

    rep = asyncio.run(gather(a))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2, default=str)
    print("GEMINI_SLOT_RECONCILE " + json.dumps(
        {k: rep.get(k) for k in ("verdict", "why", "blocking", "target")},
        default=str)[:4000], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
