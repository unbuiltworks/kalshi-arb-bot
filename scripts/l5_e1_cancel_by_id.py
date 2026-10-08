"""OPERATOR EMERGENCY CANCELLATION, BY EXACT ORDER ID. Cancels nothing else.

WHY IT EXISTS. If the E1 probe's process dies with an order resting at the
venue, its own handover deadman never runs and the order sits there. The
only operator tool that existed, `l5_e1_cancel_outstanding.py`, cancels by
MARKET -- `POST /v1/orders/open/cancel {"slugs": [slug]}` -- and this PMUS
account is SHARED, so on a market another desk is also quoting that would
cancel their resting orders too. That is the exact mechanism that got
fingerprint 0c12d361 superseded out of the engine, and it is not an
acceptable recovery path.

So this is the narrow replacement: named ids, one POST each, to
`/v1/order/{id}/cancel`, through the already-frozen
`live_exec2.orders.cancel_exact`. There is no market-scoped route in this
file and no account-wide route anywhere near it.

NAMING AN ID IS NOT AUTHORIZATION. Every id must also appear in E1's OWN
DURABLE RECORDS -- the append-only tape on the volume, read through
`order_registry.load_from_tape`. An id the tape has never seen is refused,
because "the operator typed it" is exactly the evidence a shared account
cannot accept: a typo or a copied row from someone else's blotter would
otherwise cancel a stranger's order. The venue's own answer for the id is
cross-checked too: if `GET /v1/order/{id}` reports a different market slug
than the one given, that is uncertain identity and the id is refused.

FAIL CLOSED, AND ALL-OR-NOTHING ON OWNERSHIP. If the tape cannot be read,
or if ANY supplied id fails ownership, NOTHING is cancelled -- not even the
ids that passed. A recovery tool running against a half-understood input is
worse than one that refuses.

A CANCEL RESPONSE IS NOT PROOF. Every id is re-read after the POST and
reported by its VENUE-CONFIRMED terminal state. An ambiguous or timed-out
acknowledgement is labelled as such and verified by re-reading, never
assumed to have worked and never retried. Filled quantity is reported as
residual inventory to reconcile: cancelling an order does not unwind a
fill, and this tool never tries to -- there is no taker rescue here at any
price.

DRY RUN BY DEFAULT. Without `E1_CANCEL_EXECUTE=YES` it does every read,
every ownership check and every classification, and sends no POST. That is
how the operator procedure is rehearsed without cancelling a real order.

IT DOES NOT NEED E1 TO BE ARMED. Refusing to clean up because an arming
flag is unset is the dangerous direction, as `live_exec2.orders` says
itself. It reads no arming key and it can place nothing: there is no order
body, no preview, no latch and no submit route in this file.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import order_registry as ORG
from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.live_exec2 import orders as O
from src import pmus_auth as AUTH

IDS_ENV = "E1_CANCEL_ORDER_IDS"
SLUG_ENV = "E1_CANCEL_SLUG"
EXECUTE_ENV = "E1_CANCEL_EXECUTE"
SESSION_ENV = "E1_CANCEL_SESSION_ID"

#: the venue's order ids are short uppercase alphanumerics (e.g. CZGW4QWKMZ73).
#: Anything else is malformed input and is refused before any lookup.
ID_RE = re.compile(r"^[A-Z0-9]{8,24}$")

#: a BTC15m slug, checked for shape only; the authority on the slug is the
#: venue's own answer for each id, cross-checked below.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{8,80}$")

TERMINAL = {"ORDER_STATE_CANCELED", "ORDER_STATE_CANCELLED",
            "ORDER_STATE_FILLED", "ORDER_STATE_REJECTED",
            "ORDER_STATE_EXPIRED"}


def _say(tag: str, payload: Mapping[str, Any]) -> None:
    print("%s %s" % (tag, json.dumps(payload, sort_keys=True, default=str)),
          flush=True)


class CancelOnlyTransport(object):
    """`BoundedOrderTransport`, narrowed to ONE mutating route.

    The bounded transport permits preview, submit, the market deadman and
    the per-id cancel. This recovery tool is allowed exactly the last of
    those, so the other three are refused HERE, in the only transport this
    script constructs -- a tool that cannot form a submit or a market-sweep
    request cannot be talked into one by a mistaken environment variable.
    """

    def __init__(self) -> None:
        self._inner = O.BoundedOrderTransport()
        self.posts: List[str] = []

    def request(self, method: str, path: str, body: Any = None,
                base: Optional[str] = None) -> Any:
        m = str(method).upper()
        if m == "GET":
            return self._inner.request(m, path, body)
        if m != "POST" or not (path.startswith("/v1/order/")
                               and path.endswith("/cancel")):
            raise O.MutationRefused(
                "%s %s refused: this tool may only POST /v1/order/{id}/"
                "cancel. Market-scoped and account-wide cancellation are "
                "not reachable from here." % (m, path))
        self.posts.append(path)
        return self._inner.request(m, path, body)


def parse_ids(raw: str) -> Dict[str, Any]:
    """Explicit ids only, and malformed input is a refusal, not a filter."""
    parts = [p.strip() for p in (raw or "").replace(";", ",").split(",")]
    parts = [p for p in parts if p]
    bad = sorted({p for p in parts if not ID_RE.match(p)})
    seen: List[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return {"ids": seen, "malformed": bad, "n": len(seen)}


def ownership(ids: List[str], *, state_dir: str,
              session_id: Optional[str] = None) -> Dict[str, Any]:
    """E1's DURABLE records decide, not the operator's typing."""
    try:
        reg = ORG.load_from_tape(state_dir, session_id=session_id)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False,
                "because": "TAPE_UNREADABLE:%s" % type(exc).__name__,
                "FAILS_CLOSED": True}
    known = {str(i) for i in (reg.get("known_ids") or [])}
    roles = dict(reg.get("order_roles") or {})
    owned = [i for i in ids if i in known]
    foreign = [i for i in ids if i not in known]
    return {"ok": not foreign and bool(ids),
            "because": (None if (not foreign and ids) else
                        ("NO_IDS" if not ids else
                         "NOT_IN_E1_DURABLE_RECORDS:%s"
                         % ",".join(sorted(foreign)))),
            "tape": reg.get("tape"), "rows_scanned": reg.get("rows_scanned"),
            "n_known_on_tape": len(known),
            "owned": owned, "foreign": foreign,
            "roles": {i: roles.get(i) for i in owned},
            "naming_an_id_is_not_authorization": True,
            "all_or_nothing": True}


def classify(row: Mapping[str, Any]) -> Dict[str, Any]:
    state = str(row.get("state") or "")
    cum = D(str(row.get("cumQuantity") or "0"))
    leaves = D(str(row.get("leavesQuantity") or "0"))
    return {"state": state, "cum_quantity": str(cum),
            "leaves_quantity": str(leaves),
            "is_terminal": state in TERMINAL,
            "is_partially_filled": cum > 0 and leaves > 0,
            "has_inventory_to_reconcile": cum > 0}


def read_order(reader: Any, order_id: str) -> Dict[str, Any]:
    try:
        row = reader(OS.ORDER_PATH % order_id)
    except Exception as exc:                                   # noqa: BLE001
        code = getattr(exc, "code", None) or getattr(exc, "status", None)
        return {"ok": False, "why": "%s%s" % (type(exc).__name__,
                                              ":%s" % code if code else "")}
    if isinstance(row, Mapping) and isinstance(row.get("order"), Mapping):
        row = row["order"]
    if not isinstance(row, Mapping):
        return {"ok": False, "why": "UNPARSEABLE_ROW"}
    return {"ok": True, "row": dict(row)}


def cancel_one(*, order_id: str, slug: str, reader: Any, transport: Any,
               execute: bool) -> Dict[str, Any]:
    """One id. Read, decide, cancel at most once, verify, report."""
    out: Dict[str, Any] = {"order_id": order_id, "slug": slug,
                           "cancel_posted": False, "dry_run": not execute}
    pre = read_order(reader, order_id)
    if not pre["ok"]:
        out["outcome"] = "PRE_READ_FAILED_FAIL_CLOSED"
        out["why"] = pre["why"]
        out["venue_confirmed"] = False
        return out
    out["before"] = classify(pre["row"])
    venue_slug = OS.slug_of(pre["row"])
    out["venue_slug"] = venue_slug
    if venue_slug and str(venue_slug) != str(slug):
        # UNCERTAIN IDENTITY. The tape says this id is ours; the venue says
        # it lives in a different market than the operator named. Refuse,
        # rather than cancelling something nobody has correctly identified.
        out["outcome"] = "REFUSED_SLUG_MISMATCH"
        out["venue_confirmed"] = False
        return out
    if out["before"]["is_terminal"]:
        out["outcome"] = "ALREADY_TERMINAL_NOTHING_TO_CANCEL"
        out["venue_confirmed"] = True
        out["residual_inventory_qty"] = out["before"]["cum_quantity"]
        return out
    if not execute:
        out["outcome"] = "DRY_RUN_WOULD_CANCEL"
        out["would_post"] = "/v1/order/%s/cancel" % order_id
        out["venue_confirmed"] = False
        return out
    # ------------------------------------------------- the one POST
    ack: Dict[str, Any]
    try:
        ack = {"ok": True,
               "response": O.cancel_exact(transport, order_id=order_id,
                                          slug=slug)}
    except Exception as exc:                                   # noqa: BLE001
        # NEVER RETRIED. The request may have been applied, so the only
        # honest next step is to re-read the venue and let it answer.
        ack = {"ok": False, "error": type(exc).__name__,
               "AMBIGUOUS": True, "never_retried": True}
    out["cancel_posted"] = True
    out["ack"] = ack
    post = read_order(reader, order_id)
    if not post["ok"]:
        out["outcome"] = ("UNVERIFIED_AFTER_AMBIGUOUS_ACK"
                          if not ack["ok"] else "UNVERIFIED_POST_READ_FAILED")
        out["why"] = post["why"]
        out["venue_confirmed"] = False
        out["NEEDS_OPERATOR"] = True
        return out
    out["after"] = classify(post["row"])
    out["residual_inventory_qty"] = out["after"]["cum_quantity"]
    if out["after"]["is_terminal"]:
        out["outcome"] = ("CANCELLED_CONFIRMED_AFTER_AMBIGUOUS_ACK"
                          if not ack["ok"] else "CANCELLED_CONFIRMED")
        out["venue_confirmed"] = True
    else:
        out["outcome"] = "STILL_OPEN_AFTER_CANCEL_NEEDS_OPERATOR"
        out["venue_confirmed"] = False
        out["NEEDS_OPERATOR"] = True
    if D(out["after"]["cum_quantity"]) > 0:
        out["inventory_note"] = (
            "a cancel does not unwind a fill. This filled quantity rides "
            "to settlement and is reconciled by the settlement audit; no "
            "taker rescue is attempted at any price")
    return out


def run(*, ids_raw: str, slug: str, execute: bool,
        state_dir: Optional[str] = None,
        session_id: Optional[str] = None,
        reader: Any = None, transport: Any = None) -> Dict[str, Any]:
    state_dir = state_dir or L.LATCH_DIR
    rep: Dict[str, Any] = {
        "tool": "l5_e1_cancel_by_id", "unix": time.time(),
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mechanism": "analysis.l5.live_exec2.orders:cancel_exact",
        "route": "POST /v1/order/{id}/cancel",
        "market_scoped_cancellation_used": False,
        "account_wide_cancellation_used": False,
        "execute": execute, "dry_run": not execute}
    parsed = parse_ids(ids_raw)
    rep["input"] = parsed
    if not parsed["ids"]:
        rep["REFUSED"] = "NO_ORDER_IDS: set %s. This tool cancels only ids " \
                         "it is given." % IDS_ENV
        return rep
    if parsed["malformed"]:
        rep["REFUSED"] = "MALFORMED_ORDER_IDS:%s" % ",".join(
            parsed["malformed"])
        return rep
    if not slug or not SLUG_RE.match(slug):
        rep["REFUSED"] = "MISSING_OR_MALFORMED_SLUG: set %s" % SLUG_ENV
        return rep
    own = ownership(parsed["ids"], state_dir=state_dir,
                    session_id=session_id)
    rep["ownership"] = own
    if not own["ok"]:
        rep["REFUSED"] = "OWNERSHIP_NOT_ESTABLISHED:%s" % own["because"]
        rep["nothing_was_cancelled"] = True
        return rep
    if reader is None:
        # READS GO THROUGH THE GET-ONLY TRANSPORT, not the cancel one, so a
        # read can never be mistaken for a mutation.
        _ro = AUTH.ReadOnlyTransport()

        def reader(path: str) -> Any:                          # noqa: E306
            return _ro.request("GET", path)
    transport = transport if transport is not None else CancelOnlyTransport()
    rep["results"] = [cancel_one(order_id=i, slug=slug, reader=reader,
                                 transport=transport, execute=execute)
                      for i in own["owned"]]
    rep["summary"] = {
        "confirmed_cancelled": [r["order_id"] for r in rep["results"]
                                if r["outcome"].startswith(
                                    "CANCELLED_CONFIRMED")],
        "already_terminal": [r["order_id"] for r in rep["results"]
                             if r["outcome"]
                             == "ALREADY_TERMINAL_NOTHING_TO_CANCEL"],
        "needs_operator": [r["order_id"] for r in rep["results"]
                           if r.get("NEEDS_OPERATOR")],
        "would_cancel_dry_run": [r["order_id"] for r in rep["results"]
                                 if r["outcome"] == "DRY_RUN_WOULD_CANCEL"],
        "residual_inventory": {r["order_id"]: r["residual_inventory_qty"]
                               for r in rep["results"]
                               if D(str(r.get("residual_inventory_qty")
                                        or "0")) > 0},
        "a_cancel_response_is_not_proof": True,
        "every_outcome_is_venue_confirmed_or_labelled_unconfirmed": True}
    return rep


def main(argv: Optional[List[str]] = None) -> int:
    execute = (os.getenv(EXECUTE_ENV) or "").strip().upper() == "YES"
    rep = run(ids_raw=os.getenv(IDS_ENV) or "",
              slug=(os.getenv(SLUG_ENV) or "").strip(),
              execute=execute,
              session_id=(os.getenv(SESSION_ENV) or "").strip() or None)
    _say("E1_CANCEL_BY_ID", rep)
    if rep.get("REFUSED"):
        return 2
    if rep.get("summary", {}).get("needs_operator"):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
