"""The execution driver for the frozen L5 continuous controller.

WHAT THIS IS. `continuous_probe` decides a surface and renders frames; it has
never been able to send one. This is the half that talks to the venue, and it
contributes NO strategy: every side, price, size and timing decision is
`continuous_probe.plan` through `WindowGuard.step`, used as found. What this
adds is the order of operations, the durable accounting, and the working-order
memory -- which is where a bounded probe is actually made safe.

IT CANNOT BE ARMED BY CONFIGURATION. No environment variable, no argument and
no caller can make `authorized()` true on its own: `AUTHORIZED_SHA` must name
the sha256 of THIS FILE's executable body, and `AUTHORIZED_ENVELOPE` the hash
of the bounds in force, so editing either invalidates the authorization
automatically. Installing one is a human editing three constants in a reviewed
commit, and the dual key's live half is never in this repository.

AUTHORIZATION HISTORY, because a spent one must never look reusable:
  OWNER-2026-09-17-GEMINI-CANARY-1      SPENT, latch REJECTED
  OWNER-2026-09-18-GEMINI-CONTINUOUS-1  SPENT, latch INTENT_PERSISTED, no order
  OWNER-2026-09-18-GEMINI-CONTINUOUS-2  SPENT, 2 orders placed and accepted,
                                        both expired UNFILLED, zero position

THE TWO SEAM DEFECTS THIS EXISTS TO CLOSE, both measured before it was written
(analysis/gemini/CONTINUOUS_CONTROLLER_WALK_FINDINGS.md):

  1. THE BUDGET WAS CALLER-ENFORCED. `WindowGuard.step()` reads actions_taken
     and never raises it; only `record()` does, and the frozen artifact never
     calls it. A driver that forgot would run unbounded while the guard
     reported 0 taken.

     Closed by making the EXECUTION LAYER the accountant. `MutationLedger` is
     an append-only, fsynced record of every venue mutation ATTEMPT, written
     BEFORE the frame goes out. A slot is spent by the attempt, not by its
     success: a send that times out has already cost one, because the order
     may be resting. On restart the budget is rebuilt as the MAXIMUM of the
     durable ledger and the venue's own order history for this window --
     never zero, and never the smaller of two disagreeing sources.

  2. THE BUDGET COUNTED RE-DECISIONS. `plan()` emits the DESIRED SURFACE every
     turn with no memory of what is resting, so the same rung at the same
     price is decided again on every cycle. Recording each decided action
     spent 12 slots in 6 cycles across TWO distinct prices.

     Closed by `diff_surface`. The controller keeps re-deciding -- that is
     correct and unchanged -- and the driver compares the decision to
     authoritative working-order state. An already-correct resting rung is
     KEPT and costs nothing. Only a genuine venue mutation costs a slot. This
     is also why queue position survives: re-placing a standing rung every
     cycle would send it to the back of the queue every cycle, which is the
     one thing a passive surface cannot afford.

WHAT ONE OF THE 12 SLOTS BUYS, stated exactly: one `order.place` attempt, or
one `order.cancel` attempt. KEEP costs nothing. A rejected order still costs
its slot -- the venue was asked. A cancel/replace of one rung costs TWO. This
is the conservative reading and it is deliberate: the alternative, counting
only fills or only accepted places, lets a rejection loop run forever inside a
budget that was meant to bound how many times we touch the venue.

AMBIGUITY IS NEVER "NO ORDER". A timeout, a dropped socket or an unparseable
ack is SUBMISSION_UNKNOWN_RECONCILE_REQUIRED: the driver STOPS and the order
is resolved against the venue's own record. Reading it as a failure is how a
probe places a second order on top of one it already has.

TWO IDENTIFIERS, AND THEY ARE NOT INTERCHANGEABLE. `clientOrderId` is ours and
is what reconciliation MATCHES on; `orderId` is the venue's and is what
`order.cancel` TAKES. Confusing them earned -1013 "invalid orderId format" on
the first live cancel and left the order resting. The venue id is read from
`ack["result"]["orderId"]` -- under `result`, not at the top level -- and a
cancel with no venue id is refused as reconciliation work rather than sent
malformed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from decimal import Decimal as D
from typing import (Any, Callable, Dict, List, Mapping, Optional, Sequence,
                    Tuple)

from src.gemini_execution import arming, continuous_probe as CP, spec
from src.gemini_execution import executions as EX
from src.gemini_execution import continuous_preflight as PF

LABEL = "GEMINI_L5_WS_CONTINUOUS_V1"

# --------------------------------------------------------------- AUTHORIZATION

#: The authorization identifier this build will honour.
#: None means NO AUTHORIZATION HAS BEEN ISSUED FOR THIS DRIVER. It is not a
#: default to be overridden at runtime: nothing reads an environment variable
#: to fill it in. A human edits it in a reviewed commit, or the driver cannot
#: mutate anything, ever.
REQUIRED_AUTH_ID: Optional[str] = None

#: The sha256 of THIS FILE as reviewed under that authorization. An
#: authorization names the exact executable it was granted against, so editing
#: the driver afterwards invalidates it automatically rather than silently
#: carrying it forward onto code nobody reviewed.
AUTHORIZED_SHA: Optional[str] = None

#: The sha256 of the frozen risk envelope the authorization was granted
#: against. Changing any bound changes this, and the authorization stops
#: applying -- so an envelope cannot be widened under an old approval.
AUTHORIZED_ENVELOPE: Optional[str] = "b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17"  # noqa: E501


class NotAuthorized(RuntimeError):
    """Refused before anything reached the venue. Always safe to see."""


class ReconcileRequired(RuntimeError):
    """The venue's state is unknown to us. STOP and read it back."""


#: the three authorization assignments, matched at line start and nowhere else
_AUTH_LINES = re.compile(
    r"^(REQUIRED_AUTH_ID|AUTHORIZED_SHA|AUTHORIZED_ENVELOPE)"
    r": Optional\[str\] = .*$", re.MULTILINE)

#: what those lines are replaced with before hashing
_AUTH_PLACEHOLDER = r"\1: Optional[str] = <AUTHORIZATION>"


class NormalizationFailed(RuntimeError):
    """The authorization block is not the shape the hash assumes."""


def executable_body() -> str:
    """This module's source with the AUTHORIZATION BLOCK normalized away.

    WHY THIS EXCLUSION EXISTS, and why it is not a loophole. The first version
    hashed the whole file, including the three authorization constants -- so
    an authorization naming `executable_sha()` could never be satisfied:
    binding the constants changes the file, which changes the hash, which
    invalidates the very authorization being installed. Satisfying it would
    have meant finding a 64-hex string X such that embedding X in this file
    yields sha256 == X, a self-referential preimage of SHA-256. The driver was
    therefore not "unarmed pending a commit"; it was UNARMABLE, and no commit
    could have fixed it.

    This is the standard detached-signature shape: the signature field is
    excluded from the payload it signs. Everything else in the file is still
    hashed, so any change to LOGIC still invalidates an authorization -- which
    is the property the check exists to provide. Exactly three lines may
    normalize, and if that is not true the hash is refused rather than
    guessed, because a normalization that silently matched more than intended
    would be a way to hide code from review.
    """
    with open(__file__, "r", encoding="utf-8") as fh:
        src = fh.read()
    body, n = _AUTH_LINES.subn(_AUTH_PLACEHOLDER, src)
    if n != 3:
        raise NormalizationFailed(
            "expected exactly 3 authorization lines, normalized %d; the hash "
            "is refused rather than computed over an unknown shape" % n)
    return body


def executable_sha() -> str:
    """sha256 of the executable body, independent of which authorization
    (if any) is installed in it."""
    return hashlib.sha256(executable_body().encode("utf-8")).hexdigest()


def envelope() -> Dict[str, str]:
    """The frozen envelope, read from the controller rather than restated.

    A second copy of a limit is a second place for it to drift, so these are
    read from `continuous_probe` every time instead of being duplicated here.
    """
    return {
        "max_actions": str(CP.MAX_ACTIONS),
        "action_size": str(CP.ACTION_SIZE),
        "max_total_spend_usd": str(CP.MAX_TOTAL_SPEND),
        "max_residual": str(CP.MAX_RESIDUAL),
        "max_imbalance": str(CP.MAX_IMBALANCE),
        "max_yes": str(CP.MAX_YES),
        "max_no": str(CP.MAX_NO),
        "no_new_open_extend_tte_s": str(CP.NO_NEW_OPEN_EXTEND_TTE_S),
        "withdraw_all_tte_s": str(CP.WITHDRAW_ALL_TTE_S),
        "cohort_prefix": CP.COHORT_PREFIX,
        "maker_tif": spec.WS_TIF_MAKER_ONLY,
    }


def envelope_sha() -> str:
    return hashlib.sha256(
        json.dumps(envelope(), sort_keys=True).encode("utf-8")).hexdigest()


def authorization_state(now: Optional[float] = None) -> Dict[str, Any]:
    """Why mutation is or is not possible. Never reveals a credential."""
    arm = arming.state(now)
    reasons: List[str] = []
    if REQUIRED_AUTH_ID is None:
        reasons.append(
            "REQUIRED_AUTH_ID is None: no owner authorization has been issued "
            "for this driver. No environment variable can supply one.")
    if AUTHORIZED_SHA is None:
        reasons.append(
            "AUTHORIZED_SHA is None: no reviewed executable has been named.")
    elif AUTHORIZED_SHA != executable_sha():
        reasons.append(
            "AUTHORIZED_SHA names a different executable than the one "
            "running; this file changed after it was reviewed.")
    if AUTHORIZED_ENVELOPE is None:
        reasons.append("AUTHORIZED_ENVELOPE is None: no envelope was named.")
    elif AUTHORIZED_ENVELOPE != envelope_sha():
        reasons.append(
            "AUTHORIZED_ENVELOPE names a different envelope than the one in "
            "force; a bound changed after it was reviewed.")
    if REQUIRED_AUTH_ID is not None and arm.get("auth_id_present"):
        if arming.auth_id() != REQUIRED_AUTH_ID:
            reasons.append("the supplied authorization identifier is not the "
                           "one this driver requires")
    if not arm.get("armed"):
        reasons.append("transport arm: %s" % arm.get("why_not"))
    return {
        "authorized": not reasons,
        "required_auth_id": REQUIRED_AUTH_ID,
        "authorized_sha": AUTHORIZED_SHA,
        "executable_sha": executable_sha(),
        "authorized_envelope": AUTHORIZED_ENVELOPE,
        "envelope_sha": envelope_sha(),
        "arming": arm,
        "why_not": reasons,
        "labels": ("an authorization names an EXECUTABLE and an ENVELOPE. "
                   "Editing either after review invalidates it, which is the "
                   "point: approval attaches to what was read, not to a name."),
    }


def authorized(now: Optional[float] = None) -> bool:
    return bool(authorization_state(now)["authorized"])


def require_authorization(now: Optional[float] = None) -> None:
    st = authorization_state(now)
    if not st["authorized"]:
        raise NotAuthorized("; ".join(st["why_not"]))


# ------------------------------------------------------- DURABLE MUTATION LEDGER

PLACE, CANCEL = "PLACE", "CANCEL"
#: a SAFETY mutation, counted apart from the strategy budget. It can
#: only cancel, so it can never create exposure; `consumed()` ignores it
#: deliberately, and it carries its own hard bound instead.
CLEANUP_CANCEL = "CLEANUP_CANCEL"
ATTEMPTED, ACCEPTED, REJECTED, UNKNOWN = ("ATTEMPTED", "ACCEPTED", "REJECTED",
                                          "UNKNOWN")

#: How many orderUpdate frames to record per cycle. A bound, not a budget:
#: these are OBSERVED and never decided upon, and an unbounded drain would let
#: a chatty account stall the loop it is meant to inform.
_MAX_EVENTS_PER_CYCLE = 200


class MutationLedger:
    """Append-only, fsynced record of every venue mutation ATTEMPT.

    THE SLOT IS SPENT BY THE ATTEMPT. A row is written and flushed to disk
    BEFORE the frame goes out, so a process that dies mid-send still shows the
    slot consumed on restart. The opposite order -- record on success -- loses
    exactly the orders that matter, the ones whose outcome we never learned.
    """

    def __init__(self, path: str, *, auth_id: str, symbol: str) -> None:
        self.path = path
        self.auth_id = str(auth_id)
        self.symbol = str(symbol)
        self.rows: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        """Replay the log: attempts become slots, resolutions settle them.

        A RESOLVE row is not a slot -- it is the outcome of one, and it has to
        be APPLIED on load or every clean restart reads its own finished work
        as unresolved and refuses to continue. That failure is safe but wrong,
        and it would make restart recovery impossible in exactly the ordinary
        case.
        """
        resolved: Dict[int, Dict[str, Any]] = {}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        # a torn line is a CONSUMED slot of unknown outcome,
                        # never a skipped one
                        self.rows.append({"kind": PLACE, "slot": len(self.rows),
                                          "outcome": UNKNOWN,
                                          "why": "unparseable ledger row"})
                        continue
                    if not isinstance(r, dict):
                        continue
                    if r.get("kind") == "RESOLVE":
                        slot = r.get("slot")
                        if isinstance(slot, int):
                            resolved[slot] = r
                        continue
                    self.rows.append(r)
        except OSError:
            pass
        for row in self.rows:
            res = resolved.get(row.get("slot"))
            if res is not None and row.get("kind") in (PLACE, CANCEL):
                row["outcome"] = res.get("outcome", UNKNOWN)

    def _append(self, row: Mapping[str, Any]) -> None:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def attempt(self, kind: str, detail: Mapping[str, Any]) -> int:
        """Spend one slot and return its number. Written before the send."""
        slot = len(self.rows)
        row = {"kind": kind, "slot": slot, "t": time.time(),
               "auth_id": self.auth_id, "symbol": self.symbol,
               "outcome": ATTEMPTED, **dict(detail)}
        self.rows.append(row)
        self._append(row)
        return slot

    def resolve(self, slot: int, outcome: str,
                detail: Optional[Mapping[str, Any]] = None) -> None:
        """Record what became of an attempt. NEVER frees the slot."""
        row = {"kind": "RESOLVE", "slot": slot, "t": time.time(),
               "outcome": outcome, **dict(detail or {})}
        self._append(row)
        for r in self.rows:
            if r.get("slot") == slot and r.get("kind") in (PLACE, CANCEL):
                r["outcome"] = outcome

    def consumed(self) -> int:
        return sum(1 for r in self.rows if r.get("kind") in (PLACE, CANCEL))

    def unresolved(self) -> List[Dict[str, Any]]:
        return [r for r in self.rows
                if r.get("kind") in (PLACE, CANCEL)
                and r.get("outcome") in (ATTEMPTED, UNKNOWN)]

    def client_order_ids(self) -> List[str]:
        return [str(r.get("client_order_id")) for r in self.rows
                if r.get("kind") == PLACE and r.get("client_order_id")]


def reconstruct_budget(ledger: MutationLedger,
                       venue_orders: Optional[Sequence[Mapping[str, Any]]]
                       ) -> Tuple[Optional[int], str]:
    """Consumed budget after a restart. NEVER zero by default.

    Two independent sources, and the LARGER wins: the durable ledger (what we
    know we attempted) and the venue's own order count for this window (what
    it actually received). They disagree exactly when one of them is missing
    something, and the safe reading of a disagreement is always the higher
    count. An unreadable venue is UNREADABLE -- it returns None and stops the
    probe, because "the venue did not answer" and "the venue has no orders"
    are different facts and only one of them is safe.
    """
    if venue_orders is None:
        return None, ("venue order history unreadable on restart; consumed "
                      "budget cannot be established and a restart never "
                      "assumes an empty one")
    local = ledger.consumed()
    remote = len(venue_orders)
    return max(local, remote), (
        "max(ledger %d, venue %d): a disagreement is always read upward"
        % (local, remote))


# ------------------------------------------------------- WORKING-ORDER MEMORY

KEEP = "KEEP"


def canonical_order_id(value: Any) -> str:
    """The venue's orderId in ONE representation: a string.

    WHY THIS EXISTS. Gemini publishes the same orderId in two types.
    `order.place`'s ack returns it as a STRING; the open-orders LISTING
    returns it as an INT. CONTINUOUS-5 placed from the ack (string), then a
    mid-window refresh rebuilt the working set from the listing (int), and
    the cancel sent the int. The venue answered 400 / -1013 "Invalid
    parameters" and the window failed closed with the order still resting.

    REPRESENTATION ONLY. The digits are never touched: an int is rendered,
    a string is returned as it came apart from surrounding whitespace, so a
    leading zero or any future non-numeric id survives intact. Nothing is
    parsed and re-emitted, because re-emitting is how a float turns
    145828836250441342 into 1.4582883625044134e+17.

    UNUSABLE IS REFUSED, NEVER INVENTED. A missing, empty, boolean, float or
    structured id cannot address an order, and guessing one would send a
    cancel at whatever it guessed.
    """
    # bool BEFORE int: True is an int in Python, and "orderId: true" is a
    # malformed payload, not order number one.
    if isinstance(value, bool):
        raise ReconcileRequired(
            "venue orderId arrived as a boolean (%r), which addresses no "
            "order; refusing rather than inventing one" % (value,))
    if isinstance(value, int):
        if value <= 0:
            raise ReconcileRequired(
                "venue orderId %d is not a usable identifier" % value)
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ReconcileRequired(
                "venue orderId was empty or blank; an unread id is never a "
                "usable one")
        try:
            if int(text) <= 0:
                raise ReconcileRequired(
                    "venue orderId %r is not a usable identifier" % text)
        except ValueError:
            # not numeric: kept VERBATIM. The venue's id is the venue's to
            # choose, and refusing a shape we have not seen would be this
            # package inventing a rule rather than reading one.
            pass
        return text
    raise ReconcileRequired(
        "venue orderId arrived as %s (%r), which is not an identifier this "
        "package can address an order with" % (type(value).__name__, value))


class WorkingOrders:
    """Authoritative view of what is resting, keyed by clientOrderId.

    EVERY WRITE PATH CANONICALIZES. `add` and `reconcile` are the only two,
    and both put the venue orderId through `canonical_order_id`, so the
    working set cannot hold an id in a shape `order.cancel` will reject. The
    invariant lives here rather than at the cancel, because a cancel that
    repaired its own argument would leave the wrong shape in the set for
    everything else to read.
    """

    def __init__(self) -> None:
        self.by_id: Dict[str, Dict[str, Any]] = {}

    def add(self, client_order_id: str, *, leg: str, price: str, qty: str,
            now: float, venue_order_id: Any = None) -> None:
        # None is allowed HERE and only here: a place ack that carried no
        # orderId leaves the order resting with its id unknown, and `cancel`
        # already refuses that case by sending the operator to the venue's
        # own open-order list. Turning it into an exception would remove a
        # recovery path that works.
        self.by_id[str(client_order_id)] = {
            "client_order_id": str(client_order_id), "leg": str(leg),
            "price": str(price), "qty": str(qty), "resting_since": float(now),
            "venue_order_id": (None if venue_order_id is None
                               else canonical_order_id(venue_order_id))}

    def drop(self, client_order_id: str) -> None:
        self.by_id.pop(str(client_order_id), None)

    def rungs(self) -> List[Dict[str, Any]]:
        return list(self.by_id.values())

    def reconcile(self, venue_open: Optional[Sequence[Mapping[str, Any]]],
                  now: float) -> str:
        """Replace our view with the VENUE's. Unreadable is not empty."""
        if venue_open is None:
            raise ReconcileRequired(
                "open orders unreadable; the working set cannot be "
                "established and an unread venue is never an empty one")
        fresh: Dict[str, Dict[str, Any]] = {}
        for o in venue_open:
            coid = o.get("clientOrderId") or o.get("client_order_id")
            if not coid:
                continue
            # PRESENCE, NOT TRUTHINESS. `a or b` fell through to the second
            # key whenever the first was 0 or "", which is how an unusable id
            # becomes a plausible-looking one.
            raw = (o.get(spec.ORDER_ID_FIELD) if spec.ORDER_ID_FIELD in o
                   else o.get("order_id"))
            fresh[str(coid)] = {
                "client_order_id": str(coid),
                "leg": ("YES" if str(o.get("outcome", o.get("eventOutcome", "")
                                          )).lower() == "yes" else "NO"),
                "price": str(o.get("price")),
                "qty": str(o.get("quantity", o.get("qty"))),
                "resting_since": float(o.get("resting_since") or now),
                # THE FIX. An order the venue says is resting MUST be
                # addressable, so an unusable id here stops the window
                # rather than entering the working set.
                "venue_order_id": canonical_order_id(raw),
            }
        before, self.by_id = set(self.by_id), fresh
        gone = sorted(before - set(fresh))
        added = sorted(set(fresh) - before)
        return ("venue authoritative: %d resting (+%s / -%s)"
                % (len(fresh), added or "none", gone or "none"))


def _ids_of(rows: Any) -> set:
    """Every identifier a venue row might carry for one order, canonicalized.

    Both forms are collected because the venue is not consistent about which
    it returns: order history keys on `orderId`, /v1/mytrades on `order_id`
    and `client_order_id`. Matching on only one of them is how a fill goes
    unrecognised.
    """
    out: set = set()
    for r in (rows or []):
        if not isinstance(r, Mapping):
            continue
        for k in ("orderId", "order_id", "globalOrderId"):
            v = r.get(k)
            if v not in (None, "", 0):
                try:
                    out.add(canonical_order_id(v))
                except ReconcileRequired:
                    pass
        for k in ("clientOrderId", "client_order_id"):
            v = r.get(k)
            if v not in (None, ""):
                out.add(str(v))
    return out


def unexplained_disappearances(known: Mapping[str, Mapping[str, Any]],
                               st: Mapping[str, Any]
                               ) -> List[Dict[str, Any]]:
    """Orders that left `active_orders` with nothing to say why.

    CONTINUOUS-6, to the millisecond: a real fill landed at 22:12:44.125, the
    order was gone from `active_orders` by 22:12:44.392, and
    `order_history(filled)` had still not published it. Inventory therefore
    read FLAT and the controller emitted OPEN 985ms after the fill.

    The two halves of the authoritative read do not share a clock. This
    computes the disagreement rather than papering over it, and it does NOT
    treat disappearance as a fill: disappearance is a QUESTION, and the
    answer has to come from the venue's own fill or terminal record.
    """
    active = _ids_of(st.get("open_orders"))
    # THE SAME VIEW INVENTORY IS BUILT FROM. It used to be
    # `_ids_of(orders_seen) | _ids_of(trades)` -- a looser set than the one
    # `recover` consumed, which is exactly how C8B produced `coherent: True`
    # beside an inventory that omitted the execution making it coherent.
    view = st.get("executions")
    if isinstance(view, Mapping):
        filled = EX.identifiers(view)
    else:
        filled = _ids_of(st.get("orders_seen")) | _ids_of(st.get("trades"))
    terminal = _ids_of(st.get("terminal_seen"))
    out: List[Dict[str, Any]] = []
    for coid, row in (known or {}).items():
        oid = row.get("venue_order_id")
        ids = {str(coid)} | ({str(oid)} if oid else set())
        if ids & active:
            continue                      # still resting: nothing to explain
        if ids & filled:
            continue                      # explained: the venue filled it
        if ids & terminal:
            continue                      # explained: cancelled or expired
        out.append({"client_order_id": str(coid), "venue_order_id": oid,
                    "leg": row.get("leg"), "price": row.get("price"),
                    "why": ("left active_orders with no fill row and no "
                            "terminal row; the venue has not said whether it "
                            "filled")})
    return out


def diff_surface(desired: Sequence[Mapping[str, Any]],
                 working: WorkingOrders) -> Dict[str, List[Dict[str, Any]]]:
    """Controller's desired surface vs what is actually resting.

    THE WHOLE POINT: a rung the venue already holds at the right price and
    size is KEPT and costs nothing, however many times the controller
    re-decides it. Matching is exact on (leg, price, qty) -- a near match is a
    different order, and treating it as the same one would leave the venue
    holding something we stopped intending.
    """
    want: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for a in desired:
        want[(str(a["leg"]), str(a["price"]), str(a["qty"]))] = dict(a)

    keep, cancel = [], []
    matched: set = set()
    for o in working.rungs():
        key = (o["leg"], o["price"], o["qty"])
        if key in want and key not in matched:
            matched.add(key)
            keep.append({"action": KEEP, "order": o,
                         "why": "already resting at the desired price and "
                                "size; re-placing it would cost a slot AND "
                                "surrender queue position"})
        else:
            cancel.append({"action": CANCEL, "order": o,
                           "why": "resting rung is not in the desired "
                                  "surface"})
    place = [{"action": PLACE, "rung": v}
             for k, v in want.items() if k not in matched]
    return {"keep": keep, "place": place, "cancel": cancel,
            "mutations": len(place) + len(cancel),
            "labels": ("KEEP costs no slot. PLACE and CANCEL each cost one. A "
                       "changed price is a CANCEL plus a PLACE, so it costs "
                       "two -- stated plainly because that is the case people "
                       "forget when they size a budget.")}


# ------------------------------------------------------------- THE STATE MACHINE

INIT = "INIT"
PREFLIGHT_OK = "PREFLIGHT_OK"
AUTHORIZED = "AUTHORIZED"
RECOVERED = "RECOVERED"
BOUND = "BOUND"
RUNNING = "RUNNING"
WITHDRAWING = "WITHDRAWING"
STOPPED = "STOPPED"
DO_NOT_TRADE = "DO_NOT_TRADE"
RECONCILE = "SUBMISSION_UNKNOWN_RECONCILE_REQUIRED"
#: an order left the book and the venue has not yet said why. NOT terminal on
#: its own: it is the state the driver sits in WHILE re-reading.
PENDING_RECONCILIATION = "PENDING_RECONCILIATION"
#: the disappearance was never explained inside the bound. Terminal.
UNKNOWN_TERMINAL = "UNKNOWN_TERMINAL"
#: cleanup ran and the venue confirms nothing of ours is resting
STOPPED_FLAT_ORDERS = "STOPPED_FLAT_ORDERS"
#: cleanup could not establish that. NEVER describe this as inert.
STOPPED_WITH_LIVE_ORDERS = "STOPPED_WITH_LIVE_ORDERS"
CLEANUP_UNCONFIRMED = "CLEANUP_UNCONFIRMED"

TERMINAL = (STOPPED, DO_NOT_TRADE, RECONCILE, UNKNOWN_TERMINAL,
            STOPPED_FLAT_ORDERS, STOPPED_WITH_LIVE_ORDERS)

#: How many authoritative re-reads a single unexplained disappearance may
#: cost, and how long it may take. A DEADMAN, not a sleep: each attempt is a
#: fresh venue read, and the bound exists so an unexplained order stops the
#: window instead of stalling it forever.
MAX_RECONCILE_ATTEMPTS = 6
MAX_RECONCILE_SECONDS = 30.0

#: Cleanup mutations are SAFETY mutations and are counted SEPARATELY from the
#: strategy budget. They may only cancel; they can never place, sell, cross or
#: flatten, so they cannot create exposure. Their own hard bound.
MAX_CLEANUP_MUTATIONS = 8
MAX_CLEANUP_SECONDS = 45.0


class Driver:
    """One BTC5m window, and then STOPPED. STOPPED is terminal."""

    def __init__(self, auth_id: str, symbol: str, *, ledger_path: str,
                 now_fn: Callable[[], float] = time.time,
                 max_actions: Optional[int] = None) -> None:
        if not str(symbol).startswith(CP.COHORT_PREFIX):
            raise CP.ProbeStop("%s is not a %s contract"
                               % (symbol, CP.COHORT_PREFIX))
        # IMMUTABLE BEFORE THE FIRST MUTATION. Bound in the constructor and
        # never reassigned; WindowGuard re-checks it on every single turn.
        self._symbol = str(symbol)
        self._auth_id = str(auth_id)
        self.now_fn = now_fn
        self.state = INIT
        self.why: Optional[str] = None
        self.ledger = MutationLedger(ledger_path, auth_id=auth_id,
                                     symbol=self._symbol)
        self.working = WorkingOrders()
        self.inv: Optional[CP.Inventory] = None
        self.guard: Optional[CP.WindowGuard] = None
        self.budget_consumed: Optional[int] = None
        #: SAFETY mutations, bounded separately from the strategy
        #: budget so cleanup can still run when strategy slots are 0.
        self.cleanup_consumed = 0
        #: client/venue order id -> "YES"/"NO", from OUR OWN placements.
        #: /v1/mytrades states no outcome, so this is what sides a
        #: trades-only execution before filled-history catches up.
        self._legs: Dict[str, str] = {}
        self.log: List[Dict[str, Any]] = []
        # A COHORT MAY TIGHTEN THE ENVELOPE, NEVER EXTEND IT. A request above
        # CP.MAX_ACTIONS is REFUSED rather than clamped: silently granting
        # less than was asked for leaves the caller believing it holds budget
        # it does not hold, which is the failure this bound exists to prevent.
        if max_actions is None:
            self._max_actions = int(CP.MAX_ACTIONS)
        else:
            if int(max_actions) < 1:
                raise CP.ProbeStop("a window with no mutation slots cannot act")
            if int(max_actions) > int(CP.MAX_ACTIONS):
                raise CP.ProbeStop(
                    "max_actions %d exceeds the envelope's %d; the envelope is "
                    "the ceiling" % (int(max_actions), int(CP.MAX_ACTIONS)))
            self._max_actions = int(max_actions)

    @property
    def max_actions(self) -> int:
        return self._max_actions

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def auth_id(self) -> str:
        return self._auth_id

    def stop(self, state: str, why: str) -> None:
        if self.state not in TERMINAL:
            self.state = state
            self.why = why
            self.log.append({"event": state, "why": why, "t": self.now_fn()})

    def budget_left(self) -> int:
        used = self.budget_consumed if self.budget_consumed is not None else 0
        return max(0, self._max_actions - used)

    # -------------------------------------------------- fail-closed preamble

    def preflight(self, *, readers: Optional[Mapping[str, Callable]] = None
                  ) -> Dict[str, Any]:
        pf = PF.preflight_now(self._symbol, readers=readers)
        ok, bad = PF.check_frozen(pf)
        if not ok:
            self.stop(DO_NOT_TRADE,
                      "fresh preflight differs from the frozen assumptions "
                      "the envelope was sized against: %s"
                      % json.dumps(bad, default=str))
        else:
            self.state = PREFLIGHT_OK
        return {"preflight": pf, "agrees": ok, "discrepancies": bad}

    def authorize(self) -> Dict[str, Any]:
        st = authorization_state(self.now_fn())
        if not st["authorized"]:
            self.stop(DO_NOT_TRADE, "not authorized: %s"
                      % "; ".join(st["why_not"]))
        else:
            self.state = AUTHORIZED
        return st

    def recover(self, *, venue_fills: Optional[List[Mapping[str, Any]]],
                venue_open: Optional[Sequence[Mapping[str, Any]]],
                venue_orders: Optional[Sequence[Mapping[str, Any]]]
                ) -> Dict[str, Any]:
        """Rebuild EVERYTHING authoritative before a surface is actionable.

        Inventory, working orders and consumed budget are each rebuilt from
        the venue plus the durable ledger. Any one of them unreadable stops
        the probe: a restart never assumes flat, never assumes nothing is
        resting, and never assumes the budget is untouched.
        """
        inv, why = CP.recover(self._auth_id, self._symbol, venue_fills)
        if inv is None:
            self.stop(DO_NOT_TRADE, why or "inventory unreadable")
            return {"recovered": False, "why": self.why}
        try:
            note = self.working.reconcile(venue_open, self.now_fn())
        except ReconcileRequired as exc:
            self.stop(DO_NOT_TRADE, str(exc))
            return {"recovered": False, "why": self.why}
        used, budget_note = reconstruct_budget(self.ledger, venue_orders)
        if used is None:
            self.stop(DO_NOT_TRADE, budget_note)
            return {"recovered": False, "why": self.why}
        unresolved = self.ledger.unresolved()
        if unresolved:
            self.stop(RECONCILE,
                      "%d mutation attempt(s) have no recorded outcome; they "
                      "must be resolved against the venue by clientOrderId "
                      "before anything else is sent" % len(unresolved))
            return {"recovered": False, "why": self.why,
                    "unresolved": unresolved}
        self.inv = inv
        self.budget_consumed = used
        self.state = RECOVERED
        return {"recovered": True, "inventory": inv.report(),
                "working": note, "budget_consumed": used,
                "budget_note": budget_note,
                "budget_left": self.budget_left()}

    def bind(self) -> Dict[str, Any]:
        if self.state != RECOVERED:
            self.stop(DO_NOT_TRADE,
                      "bind() before recovery: the window may not be bound "
                      "until authoritative state is established")
            return {"bound": False, "why": self.why}
        self.guard = CP.WindowGuard(self._symbol, auth_id=self._auth_id)
        self.guard.actions_taken = int(self.budget_consumed or 0)
        self.state = BOUND
        return {"bound": True, "symbol": self._symbol,
                "actions_taken": self.guard.actions_taken}

    # --------------------------------------------------------------- one turn

    def decide(self, tte_s: float, *,
               book: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """Decide and DIFF. Sends nothing; this is the whole dry run.

        RETURNS THE MARKET STATE IT DECIDED ON. The book was always read here
        -- `state_from_venue` fetches it when the caller passes none -- used,
        and then DISCARDED, so the record could say what we quoted but never
        what we were quoting against. CONTINUOUS-7 filled nothing and the only
        way to say why was to infer the book backwards from our own prices and
        `ticks_behind_bid`. Handing the caller the state that was actually
        used replaces that inference with the thing itself.

        EVIDENCE ONLY, and additive. The same state object is returned as was
        passed to the guard; no decision, bound, price, size or ordering
        depends on this key existing, and nothing reads it back.
        """
        if self.state in TERMINAL:
            return {"state": self.state, "why": self.why, "plan": None,
                    "market_state": None,
                    "market_state_why": "terminal before a state was built"}
        if self.guard is None or self.inv is None:
            self.stop(DO_NOT_TRADE, "decide() before bind()")
            return {"state": self.state, "why": self.why, "plan": None,
                    "market_state": None,
                    "market_state_why": "decide() before bind()"}

        now = self.now_fn()
        st = CP.state_from_venue(self._symbol, tte_s, self.inv, now=now,
                                 book=book)
        turn = self.guard.step(st, self.inv, now=now)

        phase = turn.get("window_phase")
        if phase == CP.WINDOW_WITHDRAWN or turn.get("stop"):
            # withdraw: cancel everything, keep nothing, then STOP
            self.state = WITHDRAWING
            plan = {"keep": [], "place": [],
                    "cancel": [{"action": CANCEL, "order": o,
                                "why": "window withdrawn at tte %s" % tte_s}
                               for o in self.working.rungs()],
                    "mutations": len(self.working.rungs())}
            return {"state": self.state, "turn": turn, "plan": plan,
                    "market_state": st,
                    "why": turn.get("stop") or "window withdrawn"}

        plan = diff_surface(turn.get("actions") or [], self.working)
        if plan["mutations"] > self.budget_left():
            self.stop(STOPPED,
                      "budget: %d mutation(s) required, %d slot(s) left of %d"
                      % (plan["mutations"], self.budget_left(),
                         self._max_actions))
            return {"state": self.state, "turn": turn, "plan": plan,
                    "market_state": st, "why": self.why}
        return {"state": self.state, "turn": turn, "plan": plan,
                "market_state": st,
                "window_phase": phase, "budget_left": self.budget_left()}

    def apply_fill(self, client_order_id: str, qty: Any, price: Any,
                   *, partial: bool) -> Dict[str, Any]:
        """Authoritative inventory update, BEFORE the next decision.

        A partial leaves the order resting with its remaining size, because it
        is still in the queue; a full fill retires it. Either way inventory
        moves first, so the next surface is computed against what we actually
        hold rather than what we held a cycle ago.
        """
        if self.inv is None:
            raise ReconcileRequired("no inventory to update")
        o = self.working.by_id.get(str(client_order_id))
        if o is None:
            return {"applied": False,
                    "why": ("a fill for an order this window does not hold; "
                            "reported for reconciliation, NOT applied")}
        self.inv.apply_fill(o["leg"], qty, price, source="venue_ws")
        if partial:
            o["qty"] = str(D(str(o["qty"])) - D(str(qty)))
        else:
            self.working.drop(str(client_order_id))
        return {"applied": True, "leg": o["leg"], "qty": str(qty),
                "partial": partial, "inventory": self.inv.report()}

    # ------------------------------------------------------- venue mutations

    def _client_order_id(self, leg: str, price: str) -> str:
        """Ties an order to THIS window and THIS authorization.

        Reconciliation matches on clientOrderId, so an id that did not name
        the window could match an order from another one -- which is how a
        probe would apply someone else's fill to its own inventory.
        """
        stem = "l5cp-%s-%s-%s" % (self._auth_id.lower()[:10], leg.lower(),
                                  uuid.uuid4().hex[:8])
        return stem[:spec.WS_CLIENT_ID_MAX]

    async def place(self, session: Any, rung: Mapping[str, Any]
                    ) -> Dict[str, Any]:
        """One order.place. The slot is spent BEFORE the frame goes out."""
        require_authorization(self.now_fn())
        if self.budget_left() < 1:
            self.stop(STOPPED, "no mutation slots left")
            raise CP.ProbeStop(self.why or "budget exhausted")
        if self.state in TERMINAL:
            raise CP.ProbeStop(self.why or "terminal")

        leg, price, qty = str(rung["leg"]), str(rung["price"]), str(rung["qty"])
        breach = CP.would_breach(leg, price, self.inv)
        if breach:
            self.stop(STOPPED, "risk gate refused the rung: %s" % breach)
            raise CP.ProbeStop(self.why or breach)

        from src.gemini_execution import payload as P
        coid = self._client_order_id(leg, price)
        params = P.build_ws_order(
            symbol=self._symbol, price=price, quantity=qty,
            client_order_id=coid,
            outcome=(spec.WS_OUTCOME_YES if leg == "YES"
                     else spec.WS_OUTCOME_NO))

        slot = self.ledger.attempt(PLACE, {
            "client_order_id": coid, "leg": leg, "price": price, "qty": qty})
        # BEFORE THE FRAME. An ack that never comes back still leaves an
        # order that may rest and fill, and its leg has to be answerable.
        self._remember_leg(coid, leg)
        self.budget_consumed = (self.budget_consumed or 0) + 1
        if self.guard is not None:
            self.guard.actions_taken = self.budget_consumed

        try:
            ack = await session.request(spec.WS_PLACE_METHOD, params,
                                        timeout=5.0)
        except Exception as exc:                               # noqa: BLE001
            self.ledger.resolve(slot, UNKNOWN,
                                {"error": "%s: %s" % (type(exc).__name__, exc)})
            self.stop(RECONCILE,
                      "order.place %s did not return a readable ack; it may "
                      "be resting and MUST be resolved against the venue by "
                      "clientOrderId before anything else is sent" % coid)
            raise ReconcileRequired(self.why or "submission unknown")

        status = (ack or {}).get("status")
        if status is None:
            self.ledger.resolve(slot, UNKNOWN, {"ack": ack})
            self.stop(RECONCILE, "ack for %s carried no status" % coid)
            raise ReconcileRequired(self.why or "submission unknown")
        if int(status) != spec.WS_ACK_OK:
            self.ledger.resolve(slot, REJECTED, {"status": status,
                                                 "ack": ack})
            return {"placed": False, "rejected": True, "status": status,
                    "client_order_id": coid, "slot": slot,
                    "labels": ("the slot is still spent: the venue was asked. "
                               "Counting only accepted orders would let a "
                               "rejection loop run forever inside a budget "
                               "meant to bound how often we touch it.")}

        # THE ORDER ID LIVES UNDER `result`, NOT AT THE TOP LEVEL. Reading it
        # from the top level silently produced None, and a None order id is
        # what made the first live cancel malformed. ws_canary already parsed
        # it correctly; this now matches it exactly.
        result = (ack or {}).get("result") or (ack or {}).get("data") or {}
        oid = (result.get(spec.ORDER_ID_FIELD)
               if isinstance(result, dict) else None)
        self.ledger.resolve(slot, ACCEPTED, {"status": status,
                                             spec.ORDER_ID_FIELD: oid})
        self._remember_leg(coid, leg, oid)
        self.working.add(coid, leg=leg, price=price, qty=qty,
                         now=self.now_fn(), venue_order_id=oid)
        return {"placed": True, "client_order_id": coid, "slot": slot,
                "status": status, "venue_order_id": oid, "ack": ack}

    async def cancel(self, session: Any, order: Mapping[str, Any]
                     ) -> Dict[str, Any]:
        """One order.cancel, by id. NEVER a blanket cancel."""
        require_authorization(self.now_fn())
        coid = str(order.get("client_order_id"))
        if self.budget_left() < 1:
            self.stop(STOPPED, "no mutation slots left to cancel with")
            raise CP.ProbeStop(self.why or "budget exhausted")

        # order.cancel TAKES orderId. It is written in spec.py -- "cancel, by
        # orderId" -- spec.ORDER_ID_FIELD exists for it, and transport.py
        # already cancels with {"orderId": ...}. Sending clientOrderId instead
        # earned -1013 "invalid orderId format" on the first live cancel and
        # left the order resting. The package had the answer in three places.
        oid = order.get("venue_order_id")
        if oid in (None, "", 0):
            # NEVER send a malformed cancel. An unknown venue id is
            # reconciliation work: the venue's own open-order list carries it.
            self.stop(RECONCILE,
                      "no venue orderId for %s, so a cancel cannot be formed; "
                      "re-read open orders to recover it rather than sending "
                      "a cancel the venue will reject" % coid)
            raise ReconcileRequired(self.why or "missing venue order id")

        slot = self.ledger.attempt(CANCEL, {"client_order_id": coid,
                                            spec.ORDER_ID_FIELD: oid})
        self.budget_consumed = (self.budget_consumed or 0) + 1
        if self.guard is not None:
            self.guard.actions_taken = self.budget_consumed

        try:
            ack = await session.request(spec.WS_CANCEL_METHOD,
                                        {spec.ORDER_ID_FIELD: oid},
                                        timeout=5.0)
        except Exception as exc:                               # noqa: BLE001
            self.ledger.resolve(slot, UNKNOWN,
                                {"error": "%s: %s" % (type(exc).__name__, exc)})
            self.stop(RECONCILE,
                      "order.cancel %s is unconfirmed; the order may still be "
                      "resting" % coid)
            raise ReconcileRequired(self.why or "cancel unknown")

        status = (ack or {}).get("status")
        if status is not None and int(status) == spec.WS_ACK_OK:
            # A 200 MEANS THE REQUEST WAS ACCEPTED, NOT THAT AN ORDER WAS
            # CANCELLED. Measured 2026-09-18: every cancel in CONTINUOUS-4
            # returned 200 with result {"orderId":"","symbol":"","status":""}
            # -- empty -- and TWO of the four orders it "cancelled" had already
            # FILLED. Dropping them from the working set on that ack is how the
            # driver came to believe it held nothing while the venue held 0.02.
            #
            # An empty or id-less result is therefore UNCONFIRMED. The slot is
            # still spent, because the frame went out. But the order stays in
            # the working set and the next authoritative refresh decides
            # whether it is really gone. The venue is the authority; an ack is
            # a receipt.
            result = (ack or {}).get("result")
            confirmed = bool(isinstance(result, dict)
                             and str(result.get(spec.ORDER_ID_FIELD) or ""))
            self.ledger.resolve(slot, ACCEPTED,
                                {"status": status,
                                 "cancel_confirmed": confirmed})
            if confirmed:
                self.working.drop(coid)
            return {"cancelled": confirmed, "client_order_id": coid,
                    "slot": slot, "cancel_confirmed": confirmed,
                    "why": (None if confirmed else
                            "ack 200 carried no orderId; this order may have "
                            "FILLED rather than cancelled, so it stays in the "
                            "working set for authoritative reconciliation")}
        self.ledger.resolve(slot, UNKNOWN, {"status": status, "ack": ack})
        self.stop(RECONCILE, "cancel of %s was not acknowledged OK" % coid)
        raise ReconcileRequired(self.why or "cancel unknown")

    # --------------------------------------------------------- terminal cleanup

    async def withdraw_orders(self, session: Any,
                              refresh_fn: Optional[Callable] = None
                              ) -> Dict[str, Any]:
        """Cancel what this strategy still has resting, then PROVE it is gone.

        WHY THIS EXISTS. STOP stopped the controller and nothing else.
        CONTINUOUS-5 stopped fail-closed on an ambiguous cancel and one of its
        resting maker orders FILLED 57 seconds later, on a contract that then
        resolved against it. CONTINUOUS-6 stopped on budget with NO 0.69 still
        live for a further 2m13s. A stopped window is not a flat one, and
        saying "STOPPED" while an order is still executable is a claim we had
        no right to make.

        BOUNDED AND SAFETY-ONLY. It cancels; it never places, sells, crosses,
        flattens inventory or touches the surface, so it cannot create
        exposure. Its mutations are counted SEPARATELY from the strategy
        budget -- C6 ended with zero strategy slots left, and a cleanup that
        borrowed from that budget could never run when it is most needed.

        IT ENDS IN A DEFINITE STATE. Either the venue confirms nothing of ours
        is resting, or the result says so loudly and stays visible.
        """
        started = self.now_fn()
        rep: Dict[str, Any] = {
            "label": "TERMINAL_WITHDRAWAL",
            "started": started,
            "cleanup_budget": MAX_CLEANUP_MUTATIONS,
            "cancelled": [], "failed": [], "rounds": 0,
            "labels": ("cleanup mutations are SAFETY mutations, bounded "
                       "separately from the strategy budget. This path can "
                       "only cancel: no place, no SELL, no crossing, no "
                       "forced flatten, no change to inventory or strategy."),
        }
        while True:
            rep["rounds"] += 1
            resting = list(self.working.rungs())
            if not resting:
                break
            if self.cleanup_consumed >= MAX_CLEANUP_MUTATIONS:
                rep["why"] = ("cleanup budget of %d exhausted with %d order(s) "
                              "still known resting"
                              % (MAX_CLEANUP_MUTATIONS, len(resting)))
                break
            if (self.now_fn() - started) >= MAX_CLEANUP_SECONDS:
                rep["why"] = ("cleanup deadman at %.0fs with %d order(s) still "
                              "known resting"
                              % (MAX_CLEANUP_SECONDS, len(resting)))
                break
            for o in resting:
                if self.cleanup_consumed >= MAX_CLEANUP_MUTATIONS:
                    break
                try:
                    r = await self._cleanup_cancel(session, o)
                    rep["cancelled"].append(r)
                except Exception as exc:                       # noqa: BLE001
                    rep["failed"].append(
                        {"client_order_id": o.get("client_order_id"),
                         "error": "%s: %s" % (type(exc).__name__, exc)})
            # THE VENUE DECIDES WHETHER THEY ARE GONE, not the acks. C4's
            # cancels returned 200 over orders that had already filled.
            if refresh_fn is None:
                rep["why"] = ("no authoritative reader available, so the "
                              "disappearance of these orders cannot be "
                              "confirmed")
                break
            try:
                st = await refresh_fn()
            except Exception as exc:                           # noqa: BLE001
                rep["why"] = "authoritative re-read failed: %s" % exc
                break
            if st.get("open_orders") is None:
                rep["why"] = ("open orders unreadable, so nothing can be "
                              "confirmed gone")
                break
            try:
                self.working.reconcile(st.get("open_orders"), self.now_fn())
            except ReconcileRequired as exc:
                rep["why"] = str(exc)
                break

        remaining = [dict(o) for o in self.working.rungs()]
        rep["remaining"] = remaining
        rep["cleanup_consumed"] = self.cleanup_consumed
        rep["elapsed_s"] = round(self.now_fn() - started, 3)
        if remaining or rep["failed"]:
            rep["verdict"] = CLEANUP_UNCONFIRMED
            rep["state"] = STOPPED_WITH_LIVE_ORDERS
            rep["ALARM"] = (
                "%d strategy-owned order(s) may still be EXECUTABLE. This "
                "window is STOPPED but NOT inert; a resting order can still "
                "fill and settle against us." % len(remaining))
        else:
            rep["verdict"] = "CLEANUP_CONFIRMED"
            rep["state"] = STOPPED_FLAT_ORDERS
            rep["note"] = ("the venue's own open-order list is empty of this "
                           "strategy's orders; FLAT ORDERS is not flat "
                           "INVENTORY and says nothing about fills already "
                           "taken")
        self.state = rep["state"]
        self.why = rep.get("ALARM") or rep.get("note")
        return rep

    async def _cleanup_cancel(self, session: Any, order: Mapping[str, Any]
                              ) -> Dict[str, Any]:
        """One safety cancel. Counted apart; identical wire shape otherwise."""
        require_authorization(self.now_fn())
        coid = str(order.get("client_order_id"))
        oid = order.get("venue_order_id")
        if oid in (None, "", 0):
            raise ReconcileRequired(
                "no venue orderId for %s; a cancel cannot be formed" % coid)
        slot = self.ledger.attempt(CLEANUP_CANCEL,
                                   {"client_order_id": coid,
                                    spec.ORDER_ID_FIELD: oid})
        self.cleanup_consumed += 1
        try:
            ack = await session.request(spec.WS_CANCEL_METHOD,
                                        {spec.ORDER_ID_FIELD: oid},
                                        timeout=5.0)
        except Exception as exc:                               # noqa: BLE001
            self.ledger.resolve(slot, UNKNOWN,
                                {"error": "%s: %s" % (type(exc).__name__, exc)})
            raise
        status = (ack or {}).get("status")
        ok = status is not None and int(status) == spec.WS_ACK_OK
        self.ledger.resolve(slot, ACCEPTED if ok else REJECTED,
                            {"status": status})
        return {"client_order_id": coid, spec.ORDER_ID_FIELD: oid,
                "status": status, "accepted": ok,
                "note": ("an ack is a receipt. Whether the order is gone is "
                         "decided by the authoritative re-read below.")}

    # ------------------------------------------------------------- the window

    def _drain_events(self, session: Any, report: Dict[str, Any]) -> None:
        """Record raw orderUpdate frames. OBSERVATION ONLY.

        The session subscribes to orders@account and NOTHING has ever read that
        queue, so the orderUpdate shape has never been seen. Nothing here
        decides on them: a parser written against an unobserved shape is
        exactly what produced the last several defects in this package. They
        are recorded so the shape becomes evidence, while inventory comes from
        an authoritative re-read whose shape IS observed.
        """
        q = getattr(session, "events", None)
        if q is None or not hasattr(q, "get_nowait"):
            return
        seen = report.setdefault("order_events", [])
        for _ in range(_MAX_EVENTS_PER_CYCLE):
            try:
                seen.append(q.get_nowait())
            except Exception:                                  # noqa: BLE001
                return

    def _remember_leg(self, client_order_id: Any, leg: Any,
                      venue_order_id: Any = None) -> None:
        """OUR OWN record of what we asked for, keyed every way it comes back.

        /v1/mytrades states price, quantity, fee and aggressor -- and NOT the
        outcome. So a trades-only execution cannot be sided from the venue
        alone until the slower filled-history row arrives, which is precisely
        the window C8B's read 13 sat in. This is the authoritative answer to
        "which leg did we buy": we placed it.
        """
        leg = str(leg or "").upper()
        if leg not in ("YES", "NO"):
            return
        for k in (client_order_id, venue_order_id):
            if k not in (None, "", 0):
                self._legs[str(k)] = leg

    async def _refresh(self, refresh_fn: Callable) -> Optional[Dict[str, Any]]:
        """Re-establish inventory and the working set from the VENUE.

        WHY THIS RUNS BEFORE EVERY DECISION. `apply_fill` existed but had no
        caller in the live loop, so inventory stayed frozen at the recovery
        snapshot for a whole window. CONTINUOUS-4 completed a pair at $1.06
        believing it held nothing while the venue held 0.02, and
        `CP.would_breach` was therefore testing every rung against ZERO
        inventory -- the residual, imbalance, per-leg and total-spend bounds
        were advisory rather than enforced.

        RE-READ RATHER THAN PARSE EVENTS, because the orderUpdate shape has
        never been observed. This path uses only reads already proven against
        the venue.

        IDEMPOTENT BY CONSTRUCTION, not by bookkeeping. `CP.recover` builds a
        FRESH Inventory from the authoritative fill list on every call and
        `WorkingOrders.reconcile` REPLACES its view, so each fill is applied
        exactly once no matter how often this runs, and a restart lands on the
        same state. There is no "already seen" set to drift.

        UNREADABLE STOPS THE WINDOW. Mid-window is no more entitled to assume a
        flat account than a restart is.
        """
        known = {r["client_order_id"]: r for r in self.working.rungs()}
        pending: List[Dict[str, Any]] = []
        st: Dict[str, Any] = {}
        t0 = self.now_fn()
        attempts = 0

        # RE-READ UNTIL THE VENUE EXPLAINS ITSELF. Each pass is a fresh
        # authoritative read; nothing sleeps and nothing is assumed.
        while True:
            attempts += 1
            st = await refresh_fn()
            if st.get("fills") is None or st.get("open_orders") is None:
                self.stop(DO_NOT_TRADE,
                          "authoritative state unreadable mid-window; an "
                          "unread venue is never an empty one")
                return None
            # ONE VIEW, BUILT ONCE, READ BY BOTH. The gate below and the
            # inventory rebuild further down now answer "did this execute?"
            # from the same object, so they cannot disagree.
            st = dict(st)
            st["executions"] = EX.canonical_executions(
                st.get("orders_seen"), st.get("trades"),
                leg_of=self._legs, symbol=self._symbol,
                fills=st.get("fills"))
            if st["executions"].get("contradictions"):
                self.stop(DO_NOT_TRADE,
                          "the venue's execution sources contradict each "
                          "other: %s. The state is not established and "
                          "nothing is decided on it."
                          % json.dumps(st["executions"]["contradictions"],
                                       default=str)[:400])
                return None
            pending = unexplained_disappearances(known, st)
            if not pending:
                break
            if (attempts >= MAX_RECONCILE_ATTEMPTS
                    or (self.now_fn() - t0) >= MAX_RECONCILE_SECONDS):
                # THE DEADMAN. An order left the book, the venue never said
                # why, and the one thing we must not do is call the
                # controller with an inventory we know may be wrong.
                self.stop(UNKNOWN_TERMINAL,
                          "%d working order(s) left active_orders and no "
                          "authoritative fill or terminal evidence explained "
                          "them within %d read(s): %s. Deciding now would "
                          "hand the controller an inventory that may already "
                          "be stale."
                          % (len(pending), attempts,
                             ", ".join(sorted(p["client_order_id"]
                                              for p in pending))))
                return None

        inv, why = CP.recover(self._auth_id, self._symbol,
                              EX.as_fills(st.get("executions"))
                              if isinstance(st.get("executions"), Mapping)
                              else st.get("fills"))
        if inv is None:
            self.stop(DO_NOT_TRADE, why or "inventory unreadable mid-window")
            return None
        try:
            note = self.working.reconcile(st.get("open_orders"), self.now_fn())
        except ReconcileRequired as exc:
            self.stop(DO_NOT_TRADE, str(exc))
            return None
        before = self.inv.report() if self.inv is not None else None
        self.inv = inv
        after = inv.report()
        ev = st.get("executions") if isinstance(st.get("executions"),
                                                 Mapping) else {}
        return {"working": note, "changed": before != after,
                "coherent": True, "reconcile_attempts": attempts,
                # THE VIEW BOTH CONSUMERS READ, reported so a later reader can
                # see that `coherent` and the inventory came from one object.
                "executions": {
                    "n": ev.get("n_executions"),
                    "degraded": ev.get("degraded"),
                    "sources_readable": ev.get("sources_readable"),
                    "by_order": ev.get("by_order")},
                "n_fills": after.get("n_fills"),
                "yes_qty": after.get("yes_qty"),
                "no_qty": after.get("no_qty"),
                "matched_qty": after.get("matched_qty"),
                "directional_residual": after.get("directional_residual"),
                "inventory_imbalance": after.get("inventory_imbalance"),
                "total_spend_usd": after.get("total_spend_usd"),
                "resting": len(self.working.rungs())}

    async def run_window(self, session: Any, *, tte_of: Callable[[], float],
                         venue_fills: Optional[List[Mapping[str, Any]]] = None,
                         venue_open: Optional[Sequence[Mapping[str, Any]]] = None,
                         venue_orders: Optional[Sequence[Mapping[str, Any]]] = None,
                         readers: Optional[Mapping[str, Callable]] = None,
                         refresh_fn: Optional[Callable] = None,
                         interval_s: float = 5.0,
                         max_cycles: int = 400) -> Dict[str, Any]:
        """Drive ONE window. Every exit is terminal; nothing re-arms.

        The preamble runs IN ORDER and each step can only stop the probe:
        preflight, authorization, authoritative recovery, window binding. Only
        then is a surface actionable.
        """
        report: Dict[str, Any] = {"label": LABEL, "symbol": self._symbol,
                                  "authorization_id": self._auth_id,
                                  "started": self.now_fn(), "cycles": 0,
                                  "turns": []}
        report["preflight"] = self.preflight(readers=readers)
        if self.state in TERMINAL:
            return self._finish(report)
        report["authorization"] = self.authorize()
        if self.state in TERMINAL:
            return self._finish(report)
        report["recovery"] = self.recover(venue_fills=venue_fills,
                                          venue_open=venue_open,
                                          venue_orders=venue_orders)
        if self.state in TERMINAL:
            return self._finish(report)
        report["binding"] = self.bind()
        if self.state in TERMINAL:
            return self._finish(report)

        self.state = RUNNING
        try:
            while self.state == RUNNING and report["cycles"] < max_cycles:
                report["cycles"] += 1
                # THE VENUE IS THE AUTHORITY ON WHAT WE HOLD, and it is asked
                # BEFORE the decision rather than after the window.
                self._drain_events(session, report)
                if refresh_fn is not None:
                    ref = await self._refresh(refresh_fn)
                    report.setdefault("refreshes", []).append(
                        {"cycle": report["cycles"], "when": "pre_decide",
                         **(ref or {"failed": True})})
                    if self.state in TERMINAL:
                        break
                out = self.decide(float(tte_of()))
                report["turns"].append({
                    "cycle": report["cycles"], "state": out.get("state"),
                    "window_phase": out.get("window_phase"),
                    "plan": _plan_summary(out.get("plan")),
                    "inventory": (self.inv.report() if self.inv else None),
                    "budget_left": self.budget_left()})
                plan = out.get("plan") or {}
                for c in plan.get("cancel", []):
                    await self.cancel(session, c["order"])
                # A FILL CAN ARRIVE BETWEEN THE CANCEL AND THE REPLACEMENT.
                # Deciding on inventory read before the cancels and then
                # placing afterwards is precisely how a replacement gets sized
                # against stale zero inventory, so the state is re-established
                # once more before anything new goes out. Cheap: it only runs
                # when this cycle actually intends to place.
                if refresh_fn is not None and plan.get("place"):
                    ref2 = await self._refresh(refresh_fn)
                    report.setdefault("refreshes", []).append(
                        {"cycle": report["cycles"], "when": "post_cancel",
                         **(ref2 or {"failed": True})})
                    if self.state in TERMINAL:
                        break
                for p in plan.get("place", []):
                    await self.place(session, p["rung"])
                if self.state == WITHDRAWING:
                    self.stop(STOPPED, "window withdrawn; one contract only, "
                                       "no continuation into the next")
                    break
                await asyncio.sleep(max(0.0, interval_s))
        except (CP.ProbeStop, ReconcileRequired, NotAuthorized) as exc:
            if self.state not in TERMINAL:
                self.stop(STOPPED, str(exc))
        finally:
            if self.state == RUNNING:
                self.stop(STOPPED, "cycle budget exhausted")
            # WHY THE CONTROLLER STOPPED, kept before cleanup rewrites the
            # state: the two facts are different and both matter.
            report["stop_state"] = self.state
            report["stop_why"] = self.why
            # TERMINAL CLEANUP. The controller has stopped; the ORDERS have
            # not. Every exit runs this, including the fail-closed ones,
            # because a fail-closed exit is exactly when an order is most
            # likely to be left resting.
            try:
                report["withdrawal"] = await self.withdraw_orders(
                    session, refresh_fn)
            except Exception as exc:                           # noqa: BLE001
                report["withdrawal"] = {
                    "label": "TERMINAL_WITHDRAWAL",
                    "verdict": CLEANUP_UNCONFIRMED,
                    "state": STOPPED_WITH_LIVE_ORDERS,
                    "error": "%s: %s" % (type(exc).__name__, exc),
                    "remaining": [dict(o) for o in self.working.rungs()],
                    "ALARM": ("cleanup itself failed, so whether anything of "
                              "this strategy is still executable is UNKNOWN"),
                }
                self.state = STOPPED_WITH_LIVE_ORDERS
                self.why = report["withdrawal"]["ALARM"]
        return self._finish(report)

    def _finish(self, report: Dict[str, Any]) -> Dict[str, Any]:
        report["state"] = self.state
        report["why"] = self.why
        report["budget_consumed"] = self.budget_consumed
        report["budget_left"] = self.budget_left()
        report["max_actions"] = self._max_actions
        report["envelope_max_actions"] = CP.MAX_ACTIONS
        report["working_orders"] = self.working.rungs()
        report["inventory"] = self.inv.report() if self.inv else None
        report["residual_note"] = (
            "any residual is CARRIED directional exposure, reconciled through "
            "settlement. No taker cross was introduced to flatten it.")
        w = report.get("withdrawal") or {}
        report["orders_live_after_stop"] = len(w.get("remaining") or [])
        report["inert"] = (w.get("state") == STOPPED_FLAT_ORDERS)
        report["inert_note"] = (
            "FLAT ORDERS is not flat INVENTORY. inert:false means at least "
            "one strategy-owned order may still be executable; inert:true "
            "means the venue showed none resting and says nothing about "
            "fills already taken.")
        report["realized"] = None
        report["realized_note"] = (
            "REALIZED stays null until a venue-confirmed cash movement is "
            "read back. settlement_report keeps matched-pair and "
            "directional-residual apart.")
        return report


def _plan_summary(plan: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not plan:
        return None
    return {"keep": len(plan.get("keep") or []),
            "place": len(plan.get("place") or []),
            "cancel": len(plan.get("cancel") or []),
            "mutations": plan.get("mutations")}
