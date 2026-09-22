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
import weakref
from decimal import Decimal as D
from typing import (Any, Callable, Dict, List, Mapping, Optional, Sequence,
                    Tuple)

from src.gemini_execution import arming, continuous_probe as CP, spec
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import outstanding_ledger as OL
from src.gemini_execution import position_ledger as PL
from src.gemini_execution import executions as EX
from src.gemini_execution import continuous_preflight as PF

LABEL = "GEMINI_L5_WS_CONTINUOUS_V1"

# --------------------------------------------------------------- AUTHORIZATION

#: The authorization identifier this build will honour.
#: None means NO AUTHORIZATION HAS BEEN ISSUED FOR THIS DRIVER. It is not a
#: default to be overridden at runtime: nothing reads an environment variable
#: to fill it in. A human edits it in a reviewed commit, or the driver cannot
#: mutate anything, ever.
REQUIRED_AUTH_ID: Optional[str] = "OWNER-2026-09-22-GEMINI-BTC15M-Q1-CAP4-COHORT-2"

#: The sha256 of THIS FILE as reviewed under that authorization. An
#: authorization names the exact executable it was granted against, so editing
#: the driver afterwards invalidates it automatically rather than silently
#: carrying it forward onto code nobody reviewed.
AUTHORIZED_SHA: Optional[str] = "f90b84c91d2e171e6805503e71dd523ecb7380b4d0d6067b0325a76fd73e0903"

#: The sha256 of the frozen risk envelope the authorization was granted
#: against. Changing any bound changes this, and the authorization stops
#: applying -- so an envelope cannot be widened under an old approval.
#
# RETIRED 2026-09-21. It named the 0.01 envelope; the live q=1 bounds are now
# part of the hash, so that value describes an envelope that no longer exists.
# It is set to None rather than re-pointed at the new hash: re-pointing it
# would be PRE-AUTHORIZING an envelope nobody has approved, which is the exact
# thing this constant exists to prevent. Installing one is a human editing
# this line in a reviewed commit.
AUTHORIZED_ENVELOPE: Optional[str] = "be8b544b35929149c9ecea41e6a2461824426a59debb3a6e5d4e275f9250445d"


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
        # THE FAMILIES THIS DRIVER MAY BE BOUND TO, not one hard-coded name.
        # This said `CP.COHORT_PREFIX` -- the literal BTC5m prefix -- so an
        # authorization's envelope hash asserted BTC5m no matter which
        # envelope the driver was actually constructed with. The family now
        # comes from the ENVELOPE (see Driver.__init__), so what the hash
        # must pin is the set of families that are selectable at all.
        "cohort_prefixes": ",".join(CP.COHORT_PREFIXES),
        "default_cohort_prefix": CP.COHORT_PREFIX,
        "maker_tif": spec.WS_TIF_MAKER_ONLY,
        # THE BOUNDS THAT ACTUALLY HOLD ON THE ORDER PATH. Leaving these out
        # would let an authorization be granted against an envelope hash that
        # said nothing about the caps the live gate enforces -- which is the
        # same defect as a bound that exists only in a document.
        # BOTH SIZES ARE NAMED. An authorization hash that covered only the
        # envelope a driver happened to be constructed with would let the
        # OTHER one change unreviewed, and the choice between them is one
        # constructor argument.
        # EVERY DECLARED ENVELOPE, NOT A HAND-KEPT PAIR. This named
        # SHADOW_Q001 and LIVE_Q1 only, so LIVE_Q1_BTC15M -- the envelope a
        # BTC15m authorization would actually run under -- sat OUTSIDE the
        # hash and could have changed unreviewed. LE.ALL is the declaration;
        # adding an envelope moves this hash, which is correct.
        "envelopes": json.dumps(
            {e.name: e.as_dict() for e in LE.ALL}, sort_keys=True),
        "envelope_shas": json.dumps(
            {e.name: e.sha() for e in LE.ALL}, sort_keys=True),
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

    def resolve_by_client_order_id(self, coid: str, outcome: str,
                                   why: str) -> int:
        """Settle every dangling attempt naming ONE client order id.

        THE ONLY LEGITIMATE CALLER is an AUTHORITATIVE venue read that has
        just proven what became of that exact order. The slot is NOT freed --
        `consumed()` is unchanged and the budget never re-opens -- this only
        records the outcome the venue has now stated, so that a restart is not
        blocked forever by an attempt whose answer has since arrived.

        Resolving by anything weaker than an authoritative read would turn the
        unresolved-attempt gate into a formality, which is why this takes a
        client order id rather than a slot: a caller cannot use it to wave
        through attempts it has not actually resolved.
        """
        n = 0
        for row in self.rows:
            if (row.get("kind") in (PLACE, CANCEL)
                    and str(row.get("client_order_id") or "") == str(coid)
                    and row.get("outcome") in (ATTEMPTED, UNKNOWN)):
                self.resolve(int(row["slot"]), outcome,
                             {"client_order_id": str(coid), "why": why})
                row["outcome"] = outcome
                n += 1
        return n

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
#: RETAINED is a KEEP the desired surface did NOT ask for: a still-valid
#: resting maker order younger than the frozen minimum_rest_time, held
#: because cancelling it would surrender queue position for nothing.
RETAIN = "RETAIN"
#: ADD is PLACE in the lifecycle vocabulary. Same action, named for the
#: RETAIN / ADD / CANCEL contract rather than for the transport.
ADD = PLACE
#: HOLD is a desired rung that did NOT go on the book, because its leg
#: already holds every order the frozen policy authorises there. The same
#: word `continuous_probe` uses when the OSCILLATOR or the ENVELOPE withholds
#: a quote, for the same reason: the report must say which control withheld
#: it, and "the rung is simply absent" says nothing.
HOLD = CP.HOLD

#: THE ONLY REASONS A CANCEL MAY BYPASS THE REST FLOOR.
#:
#: Ordinary desired-price movement is NOT one of them, and that omission is
#: the entire point: LIVE-2 cancelled three of five NO rungs at 9s, 19s and
#: 11s because a moved quote looked like a reason, while the frozen lane's own
#: minimum_rest_time is 30.0s and the code enforcing it
#: (continuous_probe.stale_orders) is called nowhere in production.
#:
#: Each of these is a state in which continuing to rest is itself unsafe, so
#: queue position is worth nothing and the floor must not protect the order.
SAFETY_CANCEL_REASONS = (
    "TERMINAL_WITHDRAWAL",      # window withdrawn / end-of-window teardown
    "ENVELOPE_BREACH",          # a bound would be or has been exceeded
    "MARKET_INVALIDATED",       # symbol/window no longer the bound one
    "POSITION_CONFLICT",        # authoritative position disagrees with ours
    "RECOVERY_SAFETY",          # restart/recovery cannot vouch for the order
    "FAIL_CLOSED",              # any existing fail-closed stop
)


class SafetyCancelUnknown(ValueError):
    """A cancel claimed a safety reason that is not an enumerated one.

    Refused rather than honoured: an unrecognised reason string would become
    a way to bypass the rest floor by inventing a label.
    """


def _leg_slots(cohort_prefix: Optional[str]) -> int:
    """How many same-side maker levels this family may expose AT ONCE.

    EVIDENCE, NOT PREFERENCE (EP006):

      BTC15m  3,434 maker transactions consumed >=2 distinct L5 prices at
              adjacent ticks in ONE taker action, so >=2 orders were resting
              simultaneously. One transaction required 3.
      BTC5m   ZERO such transactions in 6,229. At BTC15m's rate ~356 were
              expected, so this is a measured negative, not a thin sample --
              and BTC5m therefore KEEPS ITS EXISTING SINGLE LEVEL. BTC15m's
              geometry is not inherited without its own evidence.

    Three is representable and deliberately NOT returned: one historical
    transaction is not an authorization.
    """
    return 2 if str(cohort_prefix or "").startswith("GEMI-BTC15M") else 1


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


def reconcile_surface(desired: Sequence[Mapping[str, Any]],
                      working: WorkingOrders, *,
                      now: Optional[float] = None,
                      min_rest_s: float = CP.STALE_ORDER_S,
                      cohort_prefix: Optional[str] = None,
                      safety: Optional[str] = None,
                      ) -> Dict[str, List[Dict[str, Any]]]:
    """desired surface -> RETAIN / ADD / CANCEL -> venue. The whole lifecycle.

    THE DEFECT THIS REPLACES. The old reconciliation matched exactly on
    (leg, price, qty) and cancelled everything else. Any quote movement
    therefore destroyed a resting maker order and its queue position, which is
    what LIVE-2 did at 9s, 19s and 11s -- all three below the frozen lane's own
    minimum_rest_time of 30.0s. The discipline existed
    (continuous_probe.stale_orders) and was called nowhere in production.

    THE RULE. A resting order that is still VALID -- right leg, right size,
    inside the envelope -- and YOUNGER than the floor is RETAINED even when
    the newly computed desired price has moved away from it. Its slot is then
    considered occupied, so the moved quote does NOT also go on as a second
    order; that would manufacture concurrency this family may not have
    evidence for.

    WHAT MAY STILL CANCEL IMMEDIATELY: `safety`, one of
    SAFETY_CANCEL_REASONS. In those states continuing to rest is itself
    unsafe, so queue position is worth nothing. An unrecognised reason is
    REFUSED rather than honoured, because a free-text reason would be a way to
    bypass the floor by inventing a label.

    HOW MANY LEVELS PER SIDE. Two bounds, and the TIGHTER one wins:

      `_leg_slots`        CAPACITY. BTC15m can represent two adjacent
                          same-side levels because EP006 measured L5 with two
                          resting; BTC5m keeps one, because the same test
                          measured zero of 6,229.
      the DESIRED surface AUTHORITY. However many rungs the frozen policy
                          asks for on that leg right now -- `rung_count`,
                          which is 1 with `tick_offsets` [0].

    CAPACITY IS NOT AUTHORITY, and reading only `_leg_slots` conflated them.
    On BTC15m a RETAINed order left a second slot free, so the moved quote
    went on BESIDE it: three orders resting after one price move, four if
    both legs moved. Nothing in the desired surface asked for that. EP006
    established that L5 HAD two adjacent levels; it did not establish WHEN
    the second is created, and no frozen rule says -- so creating one here
    was an execution rule nobody wrote, inferred from a spare slot.

    The paragraph above ("Its slot is then considered occupied, so the moved
    quote does NOT also go on as a second order") described the intended
    behaviour all along; on BTC15m the code did the opposite. It now does
    what it says.

    A desired rung that cannot go on because its leg is at the cap is HOLD,
    reported with the order holding the slot -- not silently dropped. Once
    the retained order passes the floor it CANCELs and the replacement ADDs
    on the same turn: the existing cancel/replace lifecycle, with the working
    set never temporarily wider than the policy authorises.

    `now` is required in practice. It defaults to None only so that callers
    that never had a clock keep working, and in that case the floor cannot be
    evaluated and NOTHING is retained on age -- the conservative direction,
    matching the previous behaviour exactly.
    """
    if safety is not None and safety not in SAFETY_CANCEL_REASONS:
        raise SafetyCancelUnknown(
            "%r is not an enumerated safety reason; the rest floor is not "
            "bypassed by an unrecognised label. Known: %s"
            % (safety, ", ".join(SAFETY_CANCEL_REASONS)))

    slots = _leg_slots(cohort_prefix)
    want: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    #: how many rungs the POLICY is asking for on each leg, this turn. This
    #: is `rung_count` as the frozen surface actually emitted it, read off
    #: the request rather than re-derived from the lane.
    wanted_per_leg: Dict[str, int] = {}
    for a in desired:
        want[(str(a["leg"]), str(a["price"]), str(a["qty"]))] = dict(a)
        wanted_per_leg[str(a["leg"])] = wanted_per_leg.get(str(a["leg"]), 0) + 1

    keep: List[Dict[str, Any]] = []
    retain: List[Dict[str, Any]] = []
    cancel: List[Dict[str, Any]] = []
    matched: set = set()
    occupied: Dict[str, int] = {}

    for o in working.rungs():
        key = (o["leg"], o["price"], o["qty"])
        if key in want and key not in matched:
            matched.add(key)
            occupied[o["leg"]] = occupied.get(o["leg"], 0) + 1
            keep.append({"action": KEEP, "order": o,
                         "why": "already resting at the desired price and "
                                "size; re-placing it would cost a slot AND "
                                "surrender queue position"})
            continue
        if safety is not None:
            cancel.append({"action": CANCEL, "order": o, "safety": safety,
                           "why": "safety cancel (%s): continuing to rest is "
                                  "itself unsafe, so the rest floor does not "
                                  "protect this order" % safety})
            continue
        age = (None if now is None
               else float(now) - float(o.get("resting_since") or now))
        if age is not None and age < float(min_rest_s):
            occupied[o["leg"]] = occupied.get(o["leg"], 0) + 1
            retain.append({"action": RETAIN, "order": o, "age_s": age,
                           "why": "valid resting maker order %.1fs old, below "
                                  "the frozen minimum_rest_time of %.1fs; a "
                                  "moved desired price is NOT a reason to "
                                  "surrender queue position"
                                  % (age, float(min_rest_s))})
            continue
        cancel.append({"action": CANCEL, "order": o,
                       "age_s": age,
                       "why": "resting rung is not in the desired surface and "
                              "has rested at least the minimum %.1fs"
                              % float(min_rest_s)})

    add: List[Dict[str, Any]] = []
    hold: List[Dict[str, Any]] = []
    #: CAPACITY capped by AUTHORITY, per leg. `slots` is what the family can
    #: represent; `wanted_per_leg` is what the frozen policy asked for. The
    #: tighter one binds, so a free slot can never become permission.
    caps = {leg: min(slots, n) for leg, n in wanted_per_leg.items()}
    for k, v in want.items():
        if k in matched:
            continue
        leg = str(v["leg"])
        cap = caps.get(leg, 0)
        if occupied.get(leg, 0) >= cap:
            # HELD, NOT DROPPED. The slot is taken by a KEEP or a RETAIN, and
            # the policy authorises no second level on this leg. Reported
            # with the order holding it so the report says WHY the desired
            # price is not on the book.
            holder = next((r["order"] for r in retain
                           if str(r["order"]["leg"]) == leg), None) \
                or next((r["order"] for r in keep
                         if str(r["order"]["leg"]) == leg), None)
            hold.append({"action": HOLD, "rung": v, "held_by": "LIFECYCLE",
                         "holder": holder, "leg_cap": cap,
                         "leg_slots": slots,
                         "why": ("leg %s already holds %d of %d authorised "
                                 "order(s); the frozen surface asks for %d "
                                 "rung(s) on this leg, so a free capacity "
                                 "slot is NOT permission to add a second "
                                 "price level. The replacement goes on when "
                                 "the retained order passes the %.1fs floor "
                                 "and cancels."
                                 % (leg, occupied.get(leg, 0), cap,
                                    wanted_per_leg.get(leg, 0),
                                    float(min_rest_s)))})
            continue
        occupied[leg] = occupied.get(leg, 0) + 1
        add.append({"action": ADD, "rung": v})

    return {"keep": keep, "retain": retain, "place": add, "add": add,
            "cancel": cancel, "hold": hold,
            "mutations": len(add) + len(cancel),
            "leg_slots": slots, "leg_caps": caps,
            "labels": ("RETAIN is not KEEP: KEEP means the desired surface "
                       "still asks for this order, RETAIN means it does not "
                       "and the order is held anyway because it is valid and "
                       "young. Neither costs a mutation. HOLD is neither: it "
                       "is a desired rung that did NOT go on, because the "
                       "leg already holds every order the frozen policy "
                       "authorises there.")}


def diff_surface(desired: Sequence[Mapping[str, Any]],
                 working: WorkingOrders) -> Dict[str, List[Dict[str, Any]]]:
    """The clockless façade over `reconcile_surface`. ONE implementation.

    Without a clock the rest floor cannot be evaluated, so nothing is retained
    on age and every unmatched resting rung is cancelled -- which is exactly
    the behaviour this function had before the floor existed. Callers that
    care about queue position must pass a clock, which means calling
    `reconcile_surface` directly; the driver does.

    Kept because several call sites and tests describe budget arithmetic in
    terms of it: KEEP costs no slot, PLACE and CANCEL each cost one, and a
    changed price is therefore two -- the fact people forget when they size a
    budget.
    """
    return reconcile_surface(desired, working, now=None)


# ------------------------------------------------------ ONE MARKET AT A TIME

#: symbol -> weakref to the Driver currently holding that market in THIS
#: PROCESS.
#:
#: WHY A MODULE-LEVEL REGISTRY AND NOT A FLAG ON THE DRIVER. "one concurrent
#: market" is a property of the ACCOUNT, and a second Driver object cannot see
#: a field on the first one. This is the smallest thing that can actually
#: refuse. It is a PROCESS bound and it says so: exclusion across processes is
#: supplied by the deployment -- one service, one replica, restartPolicyType
#: NEVER -- and that is stated in the manifest rather than implied here.
#:
#: WHY WEAK REFERENCES. A lease belongs to a LIVE driver. A driver that was
#: dropped without stopping -- abandoned mid-window, or simply garbage after a
#: test -- is holding nothing, and a lease it never gave back would refuse the
#: next authorized window forever for no reason. A strong dict turns a bound
#: meant to stop CONCURRENCY into a bound that stops the SECOND window ever.
_BOUND_MARKETS: "weakref.WeakValueDictionary[str, Any]" = (
    weakref.WeakValueDictionary())


def bound_markets() -> Dict[str, str]:
    """Markets held by a LIVE driver in this process, symbol -> auth_id."""
    return {sym: getattr(d, "_auth_id", "")
            for sym, d in list(_BOUND_MARKETS.items()) if d is not None}


def _release_market(symbol: str) -> None:
    _BOUND_MARKETS.pop(str(symbol), None)


# ------------------------------------------------------------- THE STATE MACHINE

INIT = "INIT"
PREFLIGHT_OK = "PREFLIGHT_OK"
AUTHORIZED = "AUTHORIZED"
RECOVERED = "RECOVERED"
BOUND = "BOUND"
RUNNING = "RUNNING"
WITHDRAWING = "WITHDRAWING"

#: WHY AN UNREADABLE SNAPSHOT IS A HOLD AND NOT A FUNERAL.
#:
#: EP013 ended a live BTC15m window on ONE crossed tick at tte 332.4s -- 32
#: seconds before the frozen surface's [0, 300] quoting window would have
#: opened. Zero orders, zero evidence, a whole authorized window spent. The
#: refusal itself was right: a book we cannot read is not one to quote into,
#: and that is not negotiable below. What was wrong was the BLAST RADIUS --
#: one bad snapshot terminated the window instead of costing one cycle.
#:
#: THE BOUND IS MEASURED, NOT INVENTED. scripts/gemini_unreadable_census.py
#: walks every per-cycle recording in the tree -- 1,543 cycles, 225
#: unreadable, 40 distinct runs -- and asks how long an unreadable run lasts
#: when it recovers at all:
#:
#:     recovered in 1 cycle   7 runs   63.6%
#:     recovered in 2 cycles  2 runs   81.8%
#:     recovered in 5 cycles  2 runs  100.0%
#:     longer than 5 cycles   0 runs  NEVER RECOVERED, not once in 40
#:
#: Every run that came back came back within five cycles (41.9s of wall clock
#: at the longest). Every run that exceeded five cycles ran to the end of its
#: contract in the dark -- those are the last minutes of an expiring market,
#: not a transient. So five is not a compromise between recall and safety: it
#: is the entire observed recovery distribution with nothing outside it.
UNREADABLE_MAX_CONSEC = 5

#: The same bound in wall clock, because a loop that stalls could sit inside
#: five cycles for a very long time. The longest RECOVERED run spanned 41.9s;
#: five cycles at the observed ~9.4s cadence is ~47s. 60s covers both with a
#: margin and still fails closed long before the frozen surface could care.
#: Whichever bound trips first ends the hold.
UNREADABLE_MAX_S = 60.0

#: who is holding a rung off the book, in the plan's own vocabulary
HELD_UNREADABLE = "UNREADABLE_STATE"
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
    """ONE window on ONE contract, and then STOPPED. STOPPED is terminal.

    THE FAMILY COMES FROM THE ENVELOPE, NOT FROM A MODULE CONSTANT. This
    class used to open with `startswith(CP.COHORT_PREFIX)`, which is the
    literal "GEMI-BTC05M" -- so discovery, the family selector, the envelope
    and the entry band could all resolve BTC15m correctly and the DRIVER
    would still refuse the contract:

        ProbeStop: GEMI-BTC15M2609211430-UP is not a GEMI-BTC05M contract

    That is run 35609233642, and it is the tenth instance of the same class:
    a selector threaded everywhere except the one place that decides. The
    envelope already carries `cohort_prefix` -- it is the field that says
    which market these bounds were derived for -- so that is what the symbol
    is checked against, and what the guard is bound to. An envelope naming a
    family the module does not know is still refused by WindowGuard.

    Nothing is loosened: a symbol outside the envelope's own family is
    refused exactly as before, and the ONLY way to reach BTC15m is to hand
    over an envelope that was sized for BTC15m.
    """

    def __init__(self, auth_id: str, symbol: str, *, ledger_path: str,
                 now_fn: Callable[[], float] = time.time,
                 max_actions: Optional[int] = None,
                 quote_band: Optional[Sequence[float]] = None,
                 envelope: "LE.Envelope" = LE.DEFAULT) -> None:
        want_prefix = str(getattr(envelope, "cohort_prefix", "")
                          or CP.COHORT_PREFIX)
        if not str(symbol).startswith(want_prefix):
            raise CP.ProbeStop("%s is not a %s contract"
                               % (symbol, want_prefix))
        #: the family this driver is bound to, decided ONCE, in the
        #: constructor, from the envelope -- never re-derived from the symbol
        #: and never read from a module default afterwards.
        self._family_prefix = want_prefix
        # WHICH TTE INTERVAL THE FROZEN SURFACE MAY QUOTE IN, built ONCE.
        #
        # None is the standing behaviour and the lane's own
        # quote_between_tte_s is used untouched -- byte-identical to every
        # window this programme has run. A band arrives only from a declared
        # LiveProbeSpec, whose `spec_sha` covers it, so the interval cannot
        # move without the hash readiness compares moving with it.
        #
        # Built here rather than per cycle because probe_params reads the
        # frozen artifact off disk; one object, used every turn, also means
        # the surface the controller sees cannot differ between cycles.
        self._quote_band = (None if quote_band is None
                            else (float(quote_band[0]), float(quote_band[1])))
        self._sp = (None if self._quote_band is None
                    else CP.probe_params(action_size=envelope.action_size,
                                         quote_band=self._quote_band))
        # IMMUTABLE BEFORE THE FIRST MUTATION. Bound in the constructor and
        # never reassigned; WindowGuard re-checks it on every single turn.
        self._symbol = str(symbol)
        self._auth_id = str(auth_id)
        # WHICH BOUNDS THIS DRIVER RUNS UNDER, chosen by the caller and never
        # by a default that could drift upward. LE.DEFAULT is the SHADOW size
        # (0.01); the q=1 manifest is obtained only by handing over
        # LE.LIVE_Q1 explicitly, in a reviewed commit. A bigger size is a
        # decision, not an upgrade.
        self.env = envelope
        self.now_fn = now_fn
        self.state = INIT
        self.why: Optional[str] = None
        self.ledger = MutationLedger(ledger_path, auth_id=auth_id,
                                     symbol=self._symbol)
        # WHAT MIGHT STILL BE RESTING, across a restart. MutationLedger says
        # how many times we touched the venue; this says what is still out
        # there, which is what the committed-cash cap is a bound on.
        self.outstanding = OL.OutstandingLedger(
            ledger_path + ".orders.jsonl", auth_id=auth_id,
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
        # ---- BOUNDED RECOVERY FROM AN UNREADABLE AUTHORITATIVE SNAPSHOT ----
        #: how many CONSECUTIVE cycles have refused to yield a coherent
        #: market state. Reset to 0 by a fresh coherent read, and by nothing
        #: else -- in particular not by the passage of time.
        self._unreadable_n = 0
        #: when the current run of unreadable cycles began, for the wall-clock
        #: half of the bound.
        self._unreadable_since: Optional[float] = None
        #: the venue timestamp of the most recent UNREADABLE snapshot. Resume
        #: requires a coherent snapshot at least this new, so a cached or
        #: replayed read cannot end a hold.
        self._unreadable_last_t: Optional[float] = None
        self._unreadable_whys: List[str] = []
        #: PROVENANCE ONLY. The last snapshot that WAS coherent, kept so a
        #: report can say what we last legitimately saw. It is never handed to
        #: the controller, never priced against and never treated as current:
        #: a remembered book is not an authoritative one.
        self._last_coherent: Optional[Dict[str, Any]] = None
        #: the most recent mid-hold position re-read, for the report.
        self._position_recheck: Optional[Dict[str, Any]] = None
        # A COHORT MAY TIGHTEN THE ENVELOPE, NEVER EXTEND IT. A request above
        # CP.MAX_ACTIONS is REFUSED rather than clamped: silently granting
        # less than was asked for leaves the caller believing it holds budget
        # it does not hold, which is the failure this bound exists to prevent.
        ceiling = min(int(CP.MAX_ACTIONS), int(self.env.max_actions))
        if max_actions is None:
            self._max_actions = ceiling
        else:
            if int(max_actions) < 1:
                raise CP.ProbeStop("a window with no mutation slots cannot act")
            if int(max_actions) > ceiling:
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
            # THE MARKET LEASE IS RELEASED ON THE WAY INTO A TERMINAL STATE,
            # not in a finally somewhere: a Driver that stopped is holding
            # nothing, and a lease it never gave back would block the NEXT
            # authorized window for no reason. Releasing here does not
            # re-arm anything -- STOPPED is still terminal for this object.
            _release_market(self._symbol)

    def _cohort_prefix(self) -> str:
        """Which contract family this driver is bound to.

        Taken from the GUARD, which refused any symbol outside a known family
        when it was constructed, rather than re-parsed from the symbol here.
        Before bind() there is no family and the conservative single-level
        answer applies.
        """
        g = getattr(self, "guard", None)
        # The guard is authoritative once bound; before bind() the family the
        # CONSTRUCTOR already fixed is the answer, because it came from the
        # envelope and cannot change. Falling back to "" here would have made
        # a pre-bind reconcile silently single-level.
        return str(getattr(g, "cohort_prefix", "") or ""
                   or getattr(self, "_family_prefix", "") or "")

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
        # ---- STEP ZERO: WHAT MIGHT STILL BE RESTING FROM BEFORE.
        #
        # This runs FIRST and fails closed, because every other recovery step
        # answers a question that is only meaningful once we know nothing is
        # still live. An unreadable active-order read releases nothing; an
        # order still active blocks the window outright. A cancel may be
        # attempted by the operator under the cleanup budget, but only
        # ANOTHER authoritative read can clear it -- an ack is a receipt.
        out_rec = self.outstanding.reconcile(venue_open)
        if out_rec["verdict"] == OL.UNREADABLE:
            self.stop(DO_NOT_TRADE, out_rec["why"] or
                      "active orders unreadable; nothing is released")
            return {"recovered": False, "why": self.why,
                    "outstanding": out_rec}
        if out_rec["verdict"] == OL.BLOCKED:
            self.stop(DO_NOT_TRADE, out_rec["why"] or
                      "orders may still be resting at the venue")
            return {"recovered": False, "why": self.why,
                    "outstanding": out_rec}

        # THE READ THAT PROVED AN ORDER GONE ALSO ANSWERS THE ATTEMPT THAT
        # CREATED IT. Without this a window is blocked forever by an
        # ambiguous cancel whose answer has since arrived -- safe, but wrong,
        # and it would make restart recovery impossible in the ordinary case.
        # The slot stays spent; only the OUTCOME is recorded.
        for coid in out_rec.get("released") or ():
            self.ledger.resolve_by_client_order_id(
                coid, ACCEPTED,
                "authoritative active-order read shows this order is no "
                "longer resting")

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
                "budget_note": budget_note, "outstanding": out_rec,
                "budget_left": self.budget_left()}

    def bind(self) -> Dict[str, Any]:
        if self.state != RECOVERED:
            self.stop(DO_NOT_TRADE,
                      "bind() before recovery: the window may not be bound "
                      "until authoritative state is established")
            return {"bound": False, "why": self.why}
        # NOTHING MAY STILL BE RESTING. recover() already refuses on this,
        # and it is re-asserted here because bind() is the last gate before a
        # window becomes eligible and it must not depend on a caller having
        # honoured an earlier refusal.
        still = self.outstanding.blocks_restart()
        if still:
            self.stop(DO_NOT_TRADE,
                      "%d order(s) are not proven terminal at the venue (%s); "
                      "a new window may not begin until an authoritative "
                      "active-order read clears them"
                      % (len(still), ", ".join(sorted(
                          str(o.get("client_order_id")) for o in still))))
            return {"bound": False, "why": self.why}
        # ONE MARKET AT A TIME, REFUSED RATHER THAN QUEUED. A second symbol
        # bound in this process is how a one-market probe becomes a portfolio.
        other = {k: v for k, v in bound_markets().items()
                 if k != self._symbol}
        if len(other) + 1 > self.env.max_concurrent_markets:
            self.stop(DO_NOT_TRADE,
                      "%d market(s) already bound in this process (%s); "
                      "MAX_CONCURRENT_MARKETS is %d and this envelope does "
                      "not queue, it refuses"
                      % (len(other), ", ".join(sorted(other)),
                         self.env.max_concurrent_markets))
            return {"bound": False, "why": self.why}
        _BOUND_MARKETS[self._symbol] = self
        # AND THE GUARD IS BOUND TO THE SAME FAMILY. It already accepted a
        # cohort_prefix and was never given one, so every window -- BTC15m
        # included -- guarded as BTC5m. That is not only a wrong label:
        # `_cohort_prefix` reads the guard, so `_leg_slots` returned 1 on a
        # BTC15m contract and the two-level geometry was unreachable even
        # with the constructor's check fixed.
        self.guard = CP.WindowGuard(self._symbol, auth_id=self._auth_id,
                                    cohort_prefix=self._family_prefix)
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

        # AN UNREADABLE SNAPSHOT NEVER REACHES THE POLICY.
        #
        # This branch sits ABOVE guard.step deliberately. The safety invariant
        # is not "the policy declines to act on a bad book" -- it is that the
        # policy is NEVER SHOWN one. Nothing below this line runs: no surface,
        # no pricing, no sizing, no inventory-dependent classification. The
        # only thing this snapshot is allowed to do is be counted.
        if st.get("inventory_known") is False:
            return self._hold_unreadable(st, tte_s, now)

        # RESUMING TAKES A FRESH COHERENT READ, not merely a coherent one.
        # `state_from_venue` stamps the snapshot with the clock it was built
        # against, so a cached or replayed object cannot end a hold: the
        # resume read must be at least as new as the last refusal.
        if self._unreadable_n:
            t = st.get("t_utc")
            if (t is not None and self._unreadable_last_t is not None
                    and float(t) < float(self._unreadable_last_t)):
                return self._hold_unreadable(
                    dict(st, inventory_known=False,
                         unreadable_why=(
                             "a coherent-looking snapshot older than the last "
                             "refusal (%.3f < %.3f) is a stale read, not a "
                             "recovery" % (float(t),
                                           float(self._unreadable_last_t)))),
                    tte_s, now)
            self.log.append({"event": "UNREADABLE_RECOVERED",
                             "after_cycles": self._unreadable_n,
                             "state_id": st.get("state_id"),
                             "t": now})
        # A coherent read clears the run and becomes the provenance record.
        self._unreadable_n = 0
        self._unreadable_since = None
        self._unreadable_last_t = None
        self._unreadable_whys = []
        self._last_coherent = {"state_id": st.get("state_id"),
                               "t_utc": st.get("t_utc"), "tte_s": tte_s}

        # THE AUTHORIZED q REACHES THE DECISION. The policy chooses side,
        # price, ordering and classification; q is stamped by the execution
        # layer and is not a behavioural output. Passing the envelope's own
        # action_size is what makes an explicitly authorized LIVE_Q1 run at
        # q=1 instead of silently at the shadow constant.
        turn = self.guard.step(st, self.inv, now=now,
                                action_size=self.env.action_size,
                                **({} if self._sp is None
                                   else {"sp": self._sp}))

        # AND IT CANNOT SILENTLY REVERT. LE.would_breach permits a rung
        # SMALLER than action_size, because a partial remainder is legitimately
        # smaller -- which means it would NOT catch a freshly decided rung that
        # came back at 0.01 under a q=1 authorization. That is precisely the
        # failure this whole change exists to close, so it is checked here on
        # the decision itself, where every rung is fresh.
        for a in (turn.get("actions") or []):
            if str(a.get("qty")) != str(self.env.action_size):
                self.stop(DO_NOT_TRADE,
                          "decided rung carries qty %s but the authorized "
                          "envelope %s is q=%s; a silently downsized action "
                          "is refused rather than sent"
                          % (a.get("qty"), self.env.name,
                             self.env.action_size))
                return {"state": self.state, "turn": turn, "plan": None,
                        "market_state": st, "why": self.why}

        phase = turn.get("window_phase")
        if phase == CP.WINDOW_WITHDRAWN or turn.get("stop"):
            # withdraw: cancel everything, keep nothing, then STOP
            self.state = WITHDRAWING
            # THE REST FLOOR DOES NOT PROTECT A WITHDRAWAL. This is an
            # enumerated safety cancel: at the end of the window a resting
            # order is exposure with no time left to be complemented, so
            # queue position is worth nothing.
            plan = reconcile_surface([], self.working, now=now,
                                     cohort_prefix=self._cohort_prefix(),
                                     safety="TERMINAL_WITHDRAWAL")
            for c in plan["cancel"]:
                c["why"] = ("window withdrawn at tte %s; %s"
                            % (tte_s, c["why"]))
            return {"state": self.state, "turn": turn, "plan": plan,
                    "market_state": st,
                    "why": (turn.get("stop")
                            or ("window withdrawn at tte %.1f: the frozen "
                                "surface's phase is WINDOW_WITHDRAWN. One "
                                "contract only, no continuation into the "
                                "next." % float(tte_s)))}

        # THE PRODUCTION LIFECYCLE. Ordinary desired-price movement may no
        # longer cancel a valid resting maker order younger than the frozen
        # minimum_rest_time; it is RETAINED and its slot counted as occupied.
        plan = reconcile_surface(turn.get("actions") or [], self.working,
                                 now=now,
                                 cohort_prefix=self._cohort_prefix())
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

    # ------------------------------------------- UNREADABLE, BOUNDED

    def _hold_plan(self, why: str) -> Dict[str, Any]:
        """The plan for a cycle we are not allowed to think during.

        Every resting order is RETAINED exactly as it is -- not re-priced, not
        replaced, not cancelled, not forgotten. Zero mutations, so the
        strategy budget is untouched and `run_window` has nothing to send.
        """
        retain = [{"action": RETAIN, "order": o, "held_by": HELD_UNREADABLE,
                   "why": ("authoritative state is UNREADABLE (%s). A resting "
                           "order is left EXACTLY where it is: replacing it "
                           "would be a decision taken on a snapshot we just "
                           "refused to read, and cancelling it would be a "
                           "mutation nothing asked for." % why)}
                  for o in self.working.rungs()]
        return {"keep": [], "retain": retain, "place": [], "add": [],
                "cancel": [], "hold": [], "mutations": 0,
                "held_by": HELD_UNREADABLE,
                "labels": ("a HOLD is not a withdrawal and not a surface. "
                           "Nothing goes on, nothing comes off, and the "
                           "next cycle re-reads the venue from scratch.")}

    def _hold_unreadable(self, st: Mapping[str, Any], tte_s: float,
                         now: float) -> Dict[str, Any]:
        """One unreadable cycle: HOLD, or -- past the bound -- withdraw.

        WHAT IS PRESERVED, and it is the whole point:

          * zero placements and zero strategy decisions from this snapshot;
          * inventory is NEVER assumed flat -- `self.inv` is whatever the last
            authoritative fill reconciliation established and is not touched
            here, and `_refresh` keeps re-establishing it every cycle;
          * the last coherent state is kept as PROVENANCE and is not consulted;
          * resting orders stay accounted for and stay exactly as they are;
          * the withdrawal path stays available and runs on every exit.

        Past `UNREADABLE_MAX_CONSEC` cycles or `UNREADABLE_MAX_S` seconds, a
        transient is no longer a transient and terminal withdrawal is the
        correct answer -- which is what the census says too: no run longer
        than five cycles has ever recovered.
        """
        why = str(st.get("unreadable_why")
                  or "authoritative market state is not readable")
        self._unreadable_n += 1
        if self._unreadable_since is None:
            self._unreadable_since = now
        # MONOTONE, deliberately. A stale snapshot arrives here too (the
        # resume check re-enters this method with it), and letting its older
        # clock overwrite the watermark would make the NEXT replay of the
        # original look fresh. The high-water mark only ever rises.
        t = st.get("t_utc")
        newt = float(t) if t is not None else float(now)
        self._unreadable_last_t = (
            newt if self._unreadable_last_t is None
            else max(float(self._unreadable_last_t), newt))
        self._unreadable_whys.append(why)
        elapsed = float(now) - float(self._unreadable_since)

        hold: Dict[str, Any] = {
            "label": "UNREADABLE_HOLD_V1",
            "why": why,
            "consecutive": self._unreadable_n,
            "elapsed_s": round(elapsed, 3),
            "bound": {"max_consecutive": UNREADABLE_MAX_CONSEC,
                      "max_s": UNREADABLE_MAX_S},
            "tte_s": tte_s,
            "resting": len(self.working.rungs()),
            "inventory": (self.inv.report() if self.inv is not None else None),
            "inventory_note": (
                "carried from the last AUTHORITATIVE fill reconciliation and "
                "re-established every cycle by _refresh. An unreadable book "
                "says nothing about what we hold, and flat is never assumed."),
            "last_coherent": self._last_coherent,
            "last_coherent_note": (
                "PROVENANCE ONLY. Recorded so a reader can see what we last "
                "legitimately saw. It is not passed to the controller, not "
                "priced against, and cannot end this hold."),
            "position_recheck": self._position_recheck,
            "whys": list(self._unreadable_whys),
        }

        if (self._unreadable_n > UNREADABLE_MAX_CONSEC
                or elapsed > UNREADABLE_MAX_S):
            hold["verdict"] = "BOUND_EXCEEDED"
            stop_why = (
                "authoritative state unreadable for %d consecutive cycle(s) "
                "over %.1fs, past the bound of %d cycle(s) / %.1fs: %s. No "
                "unreadable run longer than %d cycles has ever recovered, so "
                "this is withdrawn rather than waited out."
                % (self._unreadable_n, elapsed, UNREADABLE_MAX_CONSEC,
                   UNREADABLE_MAX_S, why, UNREADABLE_MAX_CONSEC))
            self.state = WITHDRAWING
            plan = reconcile_surface([], self.working, now=now,
                                     cohort_prefix=self._cohort_prefix(),
                                     safety="TERMINAL_WITHDRAWAL")
            for c in plan["cancel"]:
                c["why"] = ("unreadable past the recovery bound at tte %s; %s"
                            % (tte_s, c["why"]))
            return {"state": self.state, "turn": None, "plan": plan,
                    "market_state": st, "unreadable_hold": hold,
                    "why": stop_why}

        hold["verdict"] = "HOLD"
        return {"state": self.state, "turn": None,
                "plan": self._hold_plan(why),
                "market_state": st, "unreadable_hold": hold,
                "window_phase": None, "budget_left": self.budget_left(),
                "why": ("HOLD %d/%d: %s. Nothing is placed, replaced or "
                        "cancelled on a state we cannot read."
                        % (self._unreadable_n, UNREADABLE_MAX_CONSEC, why))}

    async def _recheck_position(self, position_fn: Callable
                                ) -> Dict[str, Any]:
        """Re-read the venue's own position DURING a hold.

        The book is re-read every cycle by the loop; this is the other
        authoritative read, and it is the one that could say our inventory is
        not what we think. A DISAGREEMENT is terminal here exactly as it is at
        startup. An UNREADABLE position is NOT terminal and is NOT flat: it
        leaves the hold running under the same bound.
        """
        try:
            pos = await position_fn()
        except Exception as exc:                              # noqa: BLE001
            rec = {"verdict": PL.UNREADABLE, "tradeable": False,
                   "why": "%s: %s" % (type(exc).__name__, exc)}
        else:
            rec = PL.reconcile(
                self.inv if self.inv is not None else CP.Inventory(),
                (pos or {}).get("rows"), self._symbol)
        out = {"verdict": rec.get("verdict"), "why": rec.get("why"),
               "our_signed": rec.get("our_signed"),
               "venue_signed": rec.get("venue_signed"),
               "venue_representation": rec.get("venue_representation"),
               "terminal": rec.get("verdict") == PL.DISAGREE,
               "note": ("UNREADABLE keeps the hold and is never read as flat; "
                        "DISAGREE is terminal, because an account whose "
                        "position contradicts our ledger is not one to trade "
                        "on however readable its book becomes.")}
        self._position_recheck = out
        if out["terminal"]:
            self.stop(DO_NOT_TRADE,
                      "position disagreement during an unreadable hold: %s"
                      % (rec.get("why") or "venue and ledger disagree"))
        return out

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
        # the filled part is inventory; the unfilled part is still committed.
        # Both are durable, and both are inside the envelope together.
        self.outstanding.fill(str(client_order_id), qty, partial=partial)
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
        # THE LIVE ENVELOPE, NOT THE SHADOW ONE. `CP.would_breach` is sized in
        # units of 0.01 and measures the prospective fill with its own module
        # constant rather than the qty being sent, and it bounds FILLED spend
        # only -- resting orders are cash the venue may take without asking
        # again. `LE.would_breach` is handed the rung's ACTUAL qty and the
        # ACTUAL resting set, and it names every bound it breaks rather than
        # the first. It replaces the shadow check here rather than adding to
        # it: at q=1 every rung breaches the 0.01-sized bounds, so ANDing the
        # two would refuse the manifest it is meant to authorise.
        # DE-DUPLICATED BY CONSTRUCTION: anything in the in-process working
        # set is already counted by `resting`, so the durable ledger is asked
        # only for what that set does not know about.
        durable = self.outstanding.committed_usd(
            exclude={r["client_order_id"] for r in self.working.rungs()})
        if not durable["readable"]:
            self.stop(DO_NOT_TRADE, durable["why"] or
                      "durable outstanding commitment is unreadable")
            raise CP.ProbeStop(self.why or "outstanding commitment unreadable")
        breach = LE.would_breach(leg, price, self.inv, qty=qty,
                                 resting=self.working.rungs(),
                                 extra_committed_usd=durable["usd"],
                                 env=self.env)
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
        # BEFORE THE FRAME, AND FSYNCED. A crash between here and the ack
        # leaves an order that must be treated as potentially live.
        self.outstanding.intent(coid, leg=leg, price=price, qty=qty)
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
            self.outstanding.submission_unknown(
                coid, "place raised %s" % type(exc).__name__)
            self.stop(RECONCILE,
                      "order.place %s did not return a readable ack; it may "
                      "be resting and MUST be resolved against the venue by "
                      "clientOrderId before anything else is sent" % coid)
            raise ReconcileRequired(self.why or "submission unknown")

        status = (ack or {}).get("status")
        if status is None:
            self.ledger.resolve(slot, UNKNOWN, {"ack": ack})
            self.outstanding.submission_unknown(coid, "ack carried no status")
            self.stop(RECONCILE, "ack for %s carried no status" % coid)
            raise ReconcileRequired(self.why or "submission unknown")
        if int(status) != spec.WS_ACK_OK:
            self.ledger.resolve(slot, REJECTED, {"status": status,
                                                 "ack": ack})
            # THE VENUE SAID IT DID NOT CREATE THE ORDER. That is the venue
            # speaking, not us inferring, so it releases the commitment.
            self.outstanding.rejected(coid, "venue status %s" % status)
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
        self.outstanding.accepted(coid, oid)
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
        # CARRY WHAT WE KNOW. An order recovered from the venue has no
        # INTENT row of ours, and a record with no size or price would make
        # total committed cash unreadable and refuse everything after it.
        self.outstanding.cancel_requested(
            coid, leg=order.get("leg"), price=order.get("price"),
            qty=order.get("qty"))
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
            self.outstanding.cancel_unknown(
                coid, "cancel raised %s" % type(exc).__name__)
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
            # A RECEIPT, NOT A RELEASE, even when confirmed. Only an
            # authoritative active-order read may release the commitment --
            # two of CONTINUOUS-4's four "cancelled" orders had already
            # FILLED.
            self.outstanding.cancel_acked(coid, confirmed=confirmed)
            if confirmed:
                self.working.drop(coid)
            return {"cancelled": confirmed, "client_order_id": coid,
                    "slot": slot, "cancel_confirmed": confirmed,
                    "why": (None if confirmed else
                            "ack 200 carried no orderId; this order may have "
                            "FILLED rather than cancelled, so it stays in the "
                            "working set for authoritative reconciliation")}
        self.ledger.resolve(slot, UNKNOWN, {"status": status, "ack": ack})
        self.outstanding.cancel_unknown(coid, "cancel ack not OK")
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

        # AND THE DURABLE LEDGER AGAINST THE SAME AUTHORITATIVE READ.
        #
        # THE DEFECT THIS CLOSES. `reconcile` is the only thing that may
        # release committed cash, and it ran ONCE, at recovery. The live loop
        # rebuilds inventory and the working set from the venue every cycle
        # but never touched the durable ledger, so an order stayed at its
        # PLACEMENT state -- ACCEPTED, filled_qty 0 -- for the rest of the
        # window no matter what the venue did with it. Its full notional went
        # on being charged as "potentially live" while the same cash was ALSO
        # counted as filled inventory by `inv.spend()`.
        #
        # Run 35641166387 stopped on exactly that: "durable outstanding 2.16"
        # was 0.19 + 0.72 + 0.56 (three orders the venue had FILLED, already
        # inside the 1.47 of filled spend) plus 0.69 (an order the venue had
        # CANCELED under maker-or-cancel). None of it was live. The one order
        # that WAS live, YES 0.11, was counted correctly and separately as
        # `resting`.
        #
        # `reconcile` is used here exactly as at recovery and keeps every
        # fail-closed property: an unreadable read releases NOTHING, an order
        # the venue still lists is refreshed and stays charged (with its
        # filled_qty taken from the venue, so a PARTIAL keeps only its
        # remainder), and an active order we do not recognise still counts.
        # The VERDICT is not a mid-window stop the way it is at recovery:
        # BLOCKED there means "do not start", and here it is the ordinary
        # state of a window with an order resting.
        out_rec = self.outstanding.reconcile(st.get("open_orders"))
        if out_rec["verdict"] == OL.UNREADABLE:
            self.stop(DO_NOT_TRADE, out_rec["why"] or
                      "durable outstanding unreadable mid-window; an unread "
                      "venue is never an empty one and nothing is released")
            return None

        before = self.inv.report() if self.inv is not None else None
        self.inv = inv
        after = inv.report()
        ev = st.get("executions") if isinstance(st.get("executions"),
                                                 Mapping) else {}
        return {"working": note, "changed": before != after,
                "coherent": True, "reconcile_attempts": attempts,
                # the durable ledger's own reconciliation, so a reader can
                # see WHICH reservations the venue released and when
                "outstanding": {"verdict": out_rec.get("verdict"),
                                "committed_usd": str(out_rec.get("committed_usd")),
                                "released": out_rec.get("released"),
                                "foreign": out_rec.get("foreign")},
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
                         position_fn: Optional[Callable] = None,
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
                    "unreadable_hold": (out.get("unreadable_hold") or {}
                                        ).get("verdict"),
                    "inventory": (self.inv.report() if self.inv else None),
                    "budget_left": self.budget_left()})
                # THE OTHER AUTHORITATIVE READ, and only while held. The book
                # is re-read every cycle anyway; the position is what could
                # say our inventory is not what we think, and a hold is
                # exactly when that matters. It runs on a HOLD and on the
                # bound-exceeded cycle alike: the withdrawal below still
                # needs to know whether the account agrees with us.
                hold_rec = out.get("unreadable_hold")
                if hold_rec is not None:
                    if position_fn is not None:
                        hold_rec["position_recheck"] = \
                            await self._recheck_position(position_fn)
                    report.setdefault("unreadable_holds", []).append(
                        dict(hold_rec, cycle=report["cycles"]))
                    if self.state in TERMINAL:
                        break
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
                    # THE REASON SURVIVES THE WITHDRAWAL. This used to
                    # overwrite every cause with one sentence about contract
                    # rollover, so EP013's report said the contract rolled
                    # when what actually happened was a crossed book 32
                    # seconds before the quoting window opened. `decide`
                    # already knows why it withdrew; the withdrawal is the
                    # consequence, not the explanation.
                    self.stop(STOPPED, out.get("why")
                              or ("window withdrawn; one contract only, no "
                                  "continuation into the next"))
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
        # THE UNREADABLE-RECOVERY LEDGER, so a window that held and came
        # back says so rather than looking like a quiet one.
        holds = report.get("unreadable_holds") or []
        report["unreadable"] = {
            "holds": len(holds),
            "bound": {"max_consecutive": UNREADABLE_MAX_CONSEC,
                      "max_s": UNREADABLE_MAX_S},
            "longest_run": max([h.get("consecutive") or 0
                                for h in holds] or [0]),
            "bound_exceeded": any(h.get("verdict") == "BOUND_EXCEEDED"
                                  for h in holds),
            "recoveries": len([e for e in self.log
                               if e.get("event") == "UNREADABLE_RECOVERED"]),
            "note": ("a HOLD costs one cycle and no mutation. Nothing was "
                     "placed, replaced or cancelled on an unreadable "
                     "snapshot, and inventory was never assumed flat."),
        }
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
