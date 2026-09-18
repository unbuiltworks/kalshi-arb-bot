"""The execution driver for the frozen L5 continuous controller.

WHAT THIS IS. `continuous_probe` decides a surface and renders frames; it has
never been able to send one. This is the half that talks to the venue, and it
contributes NO strategy: every side, price, size and timing decision is
`continuous_probe.plan` through `WindowGuard.step`, used as found. What this
adds is the order of operations, the durable accounting, and the working-order
memory -- which is where a bounded probe is actually made safe.

IT IS NOT ARMED AND CANNOT BE ARMED BY CONFIGURATION. `REQUIRED_AUTH_ID` and
`AUTHORIZED_SHA` are both None. No environment variable, no argument and no
caller can make `authorized()` true while they are; a human must edit two
constants in a reviewed commit, and `AUTHORIZED_SHA` must name the sha256 of
THIS FILE as reviewed, so editing the driver after authorization invalidates
the authorization automatically. The Probe #1 authorization is unspent and is
NOT authority for this code: it named a different artifact.

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
is resolved against the venue's own record by clientOrderId. Reading it as a
failure is how a probe places a second order on top of one it already has.
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
from src.gemini_execution import continuous_preflight as PF

LABEL = "GEMINI_L5_WS_CONTINUOUS_V1"

# --------------------------------------------------------------- AUTHORIZATION

#: The authorization identifier this build will honour.
#: None means NO AUTHORIZATION HAS BEEN ISSUED FOR THIS DRIVER. It is not a
#: default to be overridden at runtime: nothing reads an environment variable
#: to fill it in. A human edits it in a reviewed commit, or the driver cannot
#: mutate anything, ever.
REQUIRED_AUTH_ID: Optional[str] = "OWNER-2026-09-18-GEMINI-CONTINUOUS-2"

#: The sha256 of THIS FILE as reviewed under that authorization. An
#: authorization names the exact executable it was granted against, so editing
#: the driver afterwards invalidates it automatically rather than silently
#: carrying it forward onto code nobody reviewed.
AUTHORIZED_SHA: Optional[str] = "2793c4cda4577debb00b3c3a04120138d8f78c8b38f057497105024a4cdc8d5d"  # noqa: E501

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
ATTEMPTED, ACCEPTED, REJECTED, UNKNOWN = ("ATTEMPTED", "ACCEPTED", "REJECTED",
                                          "UNKNOWN")


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


class WorkingOrders:
    """Authoritative view of what is resting, keyed by clientOrderId."""

    def __init__(self) -> None:
        self.by_id: Dict[str, Dict[str, Any]] = {}

    def add(self, client_order_id: str, *, leg: str, price: str, qty: str,
            now: float, venue_order_id: Any = None) -> None:
        self.by_id[str(client_order_id)] = {
            "client_order_id": str(client_order_id), "leg": str(leg),
            "price": str(price), "qty": str(qty), "resting_since": float(now),
            "venue_order_id": venue_order_id}

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
            fresh[str(coid)] = {
                "client_order_id": str(coid),
                "leg": ("YES" if str(o.get("outcome", o.get("eventOutcome", "")
                                          )).lower() == "yes" else "NO"),
                "price": str(o.get("price")),
                "qty": str(o.get("quantity", o.get("qty"))),
                "resting_since": float(o.get("resting_since") or now),
                "venue_order_id": o.get("orderId") or o.get("order_id"),
            }
        before, self.by_id = set(self.by_id), fresh
        gone = sorted(before - set(fresh))
        added = sorted(set(fresh) - before)
        return ("venue authoritative: %d resting (+%s / -%s)"
                % (len(fresh), added or "none", gone or "none"))


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

TERMINAL = (STOPPED, DO_NOT_TRADE, RECONCILE)


class Driver:
    """One BTC5m window, and then STOPPED. STOPPED is terminal."""

    def __init__(self, auth_id: str, symbol: str, *, ledger_path: str,
                 now_fn: Callable[[], float] = time.time) -> None:
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
        self.log: List[Dict[str, Any]] = []

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
        return max(0, CP.MAX_ACTIONS - used)

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
        """Decide and DIFF. Sends nothing; this is the whole dry run."""
        if self.state in TERMINAL:
            return {"state": self.state, "why": self.why, "plan": None}
        if self.guard is None or self.inv is None:
            self.stop(DO_NOT_TRADE, "decide() before bind()")
            return {"state": self.state, "why": self.why, "plan": None}

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
                    "why": turn.get("stop") or "window withdrawn"}

        plan = diff_surface(turn.get("actions") or [], self.working)
        if plan["mutations"] > self.budget_left():
            self.stop(STOPPED,
                      "budget: %d mutation(s) required, %d slot(s) left of %d"
                      % (plan["mutations"], self.budget_left(),
                         CP.MAX_ACTIONS))
            return {"state": self.state, "turn": turn, "plan": plan,
                    "why": self.why}
        return {"state": self.state, "turn": turn, "plan": plan,
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

        self.ledger.resolve(slot, ACCEPTED, {"status": status})
        self.working.add(coid, leg=leg, price=price, qty=qty,
                         now=self.now_fn(),
                         venue_order_id=(ack or {}).get("orderId"))
        return {"placed": True, "client_order_id": coid, "slot": slot,
                "status": status}

    async def cancel(self, session: Any, order: Mapping[str, Any]
                     ) -> Dict[str, Any]:
        """One order.cancel, by id. NEVER a blanket cancel."""
        require_authorization(self.now_fn())
        coid = str(order.get("client_order_id"))
        if self.budget_left() < 1:
            self.stop(STOPPED, "no mutation slots left to cancel with")
            raise CP.ProbeStop(self.why or "budget exhausted")

        slot = self.ledger.attempt(CANCEL, {"client_order_id": coid})
        self.budget_consumed = (self.budget_consumed or 0) + 1
        if self.guard is not None:
            self.guard.actions_taken = self.budget_consumed

        try:
            ack = await session.request(spec.WS_CANCEL_METHOD,
                                        {"clientOrderId": coid}, timeout=5.0)
        except Exception as exc:                               # noqa: BLE001
            self.ledger.resolve(slot, UNKNOWN,
                                {"error": "%s: %s" % (type(exc).__name__, exc)})
            self.stop(RECONCILE,
                      "order.cancel %s is unconfirmed; the order may still be "
                      "resting" % coid)
            raise ReconcileRequired(self.why or "cancel unknown")

        status = (ack or {}).get("status")
        if status is not None and int(status) == spec.WS_ACK_OK:
            self.ledger.resolve(slot, ACCEPTED, {"status": status})
            self.working.drop(coid)
            return {"cancelled": True, "client_order_id": coid, "slot": slot}
        self.ledger.resolve(slot, UNKNOWN, {"status": status, "ack": ack})
        self.stop(RECONCILE, "cancel of %s was not acknowledged OK" % coid)
        raise ReconcileRequired(self.why or "cancel unknown")

    # ------------------------------------------------------------- the window

    async def run_window(self, session: Any, *, tte_of: Callable[[], float],
                         venue_fills: Optional[List[Mapping[str, Any]]] = None,
                         venue_open: Optional[Sequence[Mapping[str, Any]]] = None,
                         venue_orders: Optional[Sequence[Mapping[str, Any]]] = None,
                         readers: Optional[Mapping[str, Callable]] = None,
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
                out = self.decide(float(tte_of()))
                report["turns"].append({
                    "cycle": report["cycles"], "state": out.get("state"),
                    "window_phase": out.get("window_phase"),
                    "plan": _plan_summary(out.get("plan")),
                    "budget_left": self.budget_left()})
                plan = out.get("plan") or {}
                for c in plan.get("cancel", []):
                    await self.cancel(session, c["order"])
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
        return self._finish(report)

    def _finish(self, report: Dict[str, Any]) -> Dict[str, Any]:
        report["state"] = self.state
        report["why"] = self.why
        report["budget_consumed"] = self.budget_consumed
        report["budget_left"] = self.budget_left()
        report["max_actions"] = CP.MAX_ACTIONS
        report["working_orders"] = self.working.rungs()
        report["inventory"] = self.inv.report() if self.inv else None
        report["residual_note"] = (
            "any residual is CARRIED directional exposure, reconciled through "
            "settlement. No taker cross was introduced to flatten it.")
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
