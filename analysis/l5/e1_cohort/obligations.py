"""THE DURABLE OBLIGATION LEDGER: our own record outranks the venue's view.

WHY THIS EXISTS.

E1's one-outstanding-first-leg bound was repaired once already, by reading
`our_outstanding_orders` from authoritative venue state instead of the
literal `0` that caused the 2026-10-08 1215z burst. That repair is correct
and is not replaced here. It is, however, READ-YOUR-WRITES DEPENDENT, and
`test_e1_burst_1215z.py` demonstrates the consequence: a venue that
acknowledges a POST and then denies the order exists -- omitted from
`/v1/orders/open` AND 404 on `/v1/order/{id}`, both endpoints agreeing, so
nothing is inconsistent and nothing fails closed -- gets a second first leg
placed, and more as the denial persists, bounded above only by the attempt
latch at twenty. `read_order_state` files such an id under
`ids_absent_at_venue_404` and does not count it, because a successful read
that omits an id is normally authoritative. Immediately after a POST it is
not.

THE PRINCIPLE, AND IT IS A REVERSAL OF PRECEDENCE.

An OBLIGATION is created the moment E1 decides to submit, and it lives on
disk. The venue may CONFIRM an obligation, ADVANCE it, and RECONCILE it. The
venue may never ERASE one. An order the venue has temporarily forgotten is
still our obligation, because we are the ones who sent it.

OBSERVATION IS NOT RELEASE.

Seeing the order is not enough to authorize another first leg: an observed
order may be resting, or partially filled, and either way it can still
fill. An obligation is released only when it reaches a VERIFIED TERMINAL
state AND its fills have been incorporated into the inventory the
eligibility gate actually reads. Those are separate conditions and both are
checked:

    SUBMITTED   -> we sent it; no successful read has shown it yet.
                   BLOCKING. Charged at the frozen unobserved ceiling,
                   because an order we cannot see could be resting at any
                   size up to what we sent.
    OBSERVED    -> a successful read showed it live or partially filled.
                   BLOCKING, and now charged at its real own-side cost.
    TERMINAL_FILL_UNACCOUNTED
                -> the venue says it is finished and it filled, but the
                   inventory view does not yet account for that fill.
                   BLOCKING: releasing here is how an unhedged position
                   becomes invisible.
    RECONCILED  -> terminal, and either it never filled or its fill is
                   present in the inventory the gate reads. RELEASED --
                   and from this point the RESIDUAL gate is what blocks a
                   new first leg, which is its job and not this module's.

WHAT THIS MODULE DOES NOT DO.

It never cancels, submits, amends or reads the venue. It holds no
credentials and opens no socket. It is a record and a set of predicates
over that record, so a bad read can make it REFUSE but can never make it
permit.

AND IT ONLY EVER RAISES THE FLOOR. `merge_into_snapshot` takes the maximum
of the venue's count and ours, and the maximum of the venue's exposure and
ours. Every existing refusal keeps firing on exactly the input it fired on
before; nothing that used to refuse can now permit.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional

from analysis.l5.e1_cohort.order_state import FIRST_LEG, LOCAL_INTENT

SCHEMA = "e1_obligation_ledger/1"

#: the directory name under E1's own state directory.
DIR_NAME = "obligations"

#: WHAT AN UNSEEN SUBMISSION COSTS. The same frozen ceiling
#: `order_state` already charges an unconfirmed pending id, reused rather
#: than re-chosen: an order we cannot observe must be priced at the worst
#: it could be, not at nothing.
UNOBSERVED_CEILING_USD = D("0.60")

SUBMITTED = "SUBMITTED"
OBSERVED = "OBSERVED"
TERMINAL_FILL_UNACCOUNTED = "TERMINAL_FILL_UNACCOUNTED"
RECONCILED = "RECONCILED"
RETIRED_NEVER_SENT = "RETIRED_NEVER_SENT"

#: every state that forbids opening another first leg.
BLOCKING_STATES = frozenset({SUBMITTED, OBSERVED, TERMINAL_FILL_UNACCOUNTED})

#: states that are finished and block nothing.
RELEASED_STATES = frozenset({RECONCILED, RETIRED_NEVER_SENT})


class ReservationHeld(RuntimeError):
    """Another worker already holds the single first-leg reservation.

    Not an error condition -- it is the other caller's answer, exactly as
    `FileExistsError` is for the attempt latch.
    """

    def __init__(self, holder: Mapping[str, Any]) -> None:
        super().__init__("the first-leg reservation is held by %s"
                         % (holder.get("intent_id"),))
        self.holder = dict(holder)


class LedgerUnavailable(RuntimeError):
    """The durable record cannot be established, so nothing may be sent.

    FAIL CLOSED, for the same reason the latch does: an in-memory
    substitute is the weaker guarantee this exists to replace.
    """


def _fin(x: Any) -> Optional[D]:
    if x is None or x == "":
        return None
    try:
        v = D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return v if v.is_finite() else None


def _stem(session_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-._" else "-"
                   for c in str(session_id))[:40]
    return "e1ob.%s.%s" % (safe,
                           hashlib.sha256(str(session_id).encode()
                                          ).hexdigest()[:16])


def ledger_dir(state_dir: str) -> str:
    return os.path.join(state_dir, DIR_NAME)


class ObligationLedger:
    """One durable row per first-leg submission obligation.

    Append-only journal for audit, plus one atomically-written file per
    obligation so a restart reads the current state without replaying.
    """

    def __init__(self, state_dir: str, *, session_id: str,
                 now_fn: Any = time.time) -> None:
        if not str(session_id or "").strip():
            raise LedgerUnavailable(
                "the obligation ledger has no session to scope itself to")
        self.state_dir = str(state_dir)
        self.session_id = str(session_id)
        self.now_fn = now_fn
        self._stem = _stem(self.session_id)
        self.rows: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------ durable
    @property
    def dir(self) -> str:
        return ledger_dir(self.state_dir)

    def _path(self, key: str) -> str:
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return os.path.join(self.dir, "%s.%s.json" % (self._stem, digest))

    def _reservation(self) -> str:
        """THE SINGLE FIRST-LEG TOKEN, as one file.

        WHY A FILE AND NOT THE BLOCKING COUNT. The count is a predicate over
        rows this process has loaded, and two processes that start before
        either writes both load zero rows and both decide they may proceed.
        A count is read-then-write, which bounds nothing under a race -- the
        same reasoning the attempt latch records for refusing to be a
        counter. `O_CREAT|O_EXCL` is one operation that exactly one caller
        can win, however many start at once.
        """
        return os.path.join(self.dir, "%s.first_leg.reservation"
                            % self._stem)

    def _journal(self) -> str:
        return os.path.join(self.dir, "%s.journal.jsonl" % self._stem)

    def _ensure_dir(self) -> None:
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError as exc:
            raise LedgerUnavailable(
                "the obligation directory %s is not writable: %s"
                % (self.dir, type(exc).__name__)) from exc

    def _write(self, row: Mapping[str, Any]) -> None:
        """Atomic: temp file in the same directory, fsynced, then renamed."""
        self._ensure_dir()
        path = self._path(str(row["key"]))
        tmp = path + ".tmp.%d" % os.getpid()
        try:
            with open(tmp, "w", encoding="utf8") as fh:
                json.dump(dict(row), fh, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            with open(self._journal(), "a", encoding="utf8") as fh:
                fh.write(json.dumps(dict(row), sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise LedgerUnavailable(
                "an obligation could not be durably recorded (%s); refusing "
                "to submit" % type(exc).__name__) from exc

    def _load(self) -> None:
        if not os.path.isdir(self.dir):
            return
        try:
            names = os.listdir(self.dir)
        except OSError as exc:
            raise LedgerUnavailable(
                "the obligation directory %s is unreadable: %s"
                % (self.dir, type(exc).__name__)) from exc
        for name in sorted(names):
            if not name.startswith(self._stem) or not name.endswith(".json"):
                continue
            p = os.path.join(self.dir, name)
            try:
                with open(p, encoding="utf8") as fh:
                    row = json.load(fh)
            except (OSError, ValueError) as exc:
                # AN UNREADABLE OBLIGATION IS NOT AN ABSENT ONE. We know a
                # file is there and cannot read it, which is exactly the
                # state in which nothing may be sent.
                raise LedgerUnavailable(
                    "obligation file %s is unreadable (%s); a submission "
                    "record we cannot read is not a record we may ignore"
                    % (name, type(exc).__name__)) from exc
            if not isinstance(row, Mapping) or not row.get("key"):
                raise LedgerUnavailable(
                    "obligation file %s has no key" % name)
            self.rows[str(row["key"])] = dict(row)

    # ------------------------------------------------------------ mutation
    def reserve_first_leg(self, intent_id: str) -> Dict[str, Any]:
        """Claim the single first-leg reservation, or raise.

        Called BEFORE anything else on the submission path. Released only
        when the obligation it belongs to reaches a released state, so a
        crash leaves it held -- which is the safe direction, and which the
        startup reconciliation and the operator cancel tool exist to
        resolve.
        """
        self._ensure_dir()
        path = self._reservation()
        rec = {"intent_id": str(intent_id), "pid": os.getpid(),
               "unix": float(self.now_fn()),
               "session_id": self.session_id,
               "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID")}
        # WRITE THE CONTENT FIRST, THEN CREATE THE NAME, because the
        # obvious ordering has a window. `O_EXCL` on the final name gives
        # exclusivity, but between the create and the fsync the file is
        # EMPTY -- and a five-process race found exactly that: a loser read
        # the reservation mid-write and could only report its holder as
        # UNREADABLE. It refused, so nothing was unsafe, but a lock whose
        # holder cannot be named is a lock that cannot be audited.
        #
        # `os.link` fixes the ordering without giving up atomicity: the
        # content is complete and fsynced in a temp file, and the link
        # either creates the final name or fails with `FileExistsError`,
        # which is still one operation exactly one caller can win.
        # THE TEMP NAME IS UNIQUE PER ATTEMPT, AND CREATED O_EXCL.
        #
        # A CRASH TEST FOUND WHY. `os.link` makes the temp name a SECOND
        # NAME FOR THE SAME INODE, so a temp file left behind by a killed
        # process is a hard link to the live reservation. A later attempt
        # that reused that name and opened it with "w" TRUNCATED THE
        # RESERVATION THROUGH THE LINK, rewrote it with its own intent id,
        # and was then told by `reservation_holder` that it already owned
        # the token -- two workers, one reservation, no error. A unique
        # name plus `O_EXCL` makes a stale temp file inert garbage instead
        # of a back door.
        tmp = "%s.new.%d.%s" % (path, os.getpid(), uuid.uuid4().hex[:12])
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf8") as fh:
                json.dump(rec, fh, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise LedgerUnavailable(
                "the first-leg reservation could not be written (%s); "
                "refusing to submit" % type(exc).__name__) from exc
        try:
            os.link(tmp, path)
        except FileExistsError:
            holder = self.reservation_holder() or {"intent_id": None}
            if str(holder.get("intent_id")) == str(intent_id):
                # our own, re-entered: idempotent, not a second reservation
                return dict(holder)
            raise ReservationHeld(holder) from None
        except OSError as exc:
            raise LedgerUnavailable(
                "the first-leg reservation could not be created (%s); "
                "refusing to submit" % type(exc).__name__) from exc
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return rec

    def reservation_holder(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self._reservation(), encoding="utf8") as fh:
                row = json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            # UNREADABLE IS HELD. We know a reservation exists and cannot
            # say whose, which is not a state in which to take another.
            return {"intent_id": "UNREADABLE", "unreadable": True}
        return dict(row) if isinstance(row, Mapping) else {
            "intent_id": "UNREADABLE", "unreadable": True}

    def _release_reservation_if(self, intent_id: str) -> bool:
        """Release only when the holder is this obligation and it is done."""
        holder = self.reservation_holder()
        if holder is None or str(holder.get("intent_id")) != str(intent_id):
            return False
        try:
            os.unlink(self._reservation())
        except OSError:
            return False
        return True

    def record_submission(self, *, intent_id: str, window: Any, slot: Any,
                          slug: str, side: str, price: Any, qty: Any,
                          role: str = FIRST_LEG) -> Dict[str, Any]:
        """Called BEFORE the POST, next to the latch slot.

        The obligation exists from the moment we decide to send, so a
        crash, a lost acknowledgement or a venue that denies the order all
        leave the same durable record behind.
        """
        key = str(intent_id)
        if key in self.rows:
            return dict(self.rows[key])
        # THE RESERVATION IS THE MUTUAL EXCLUSION, and it is taken here
        # rather than trusted from a count. `ReservationHeld` propagates:
        # the caller must refuse, not queue.
        self.reserve_first_leg(key)
        row = {
            "schema": SCHEMA, "key": key, "intent_id": key,
            "order_id": None, "role": role,
            "window": window, "slot": slot, "slug": slug, "side": side,
            "price": str(price), "qty": str(qty),
            "state": SUBMITTED,
            "submitted_unix": float(self.now_fn()),
            "observed_unix": None, "terminal_state": None,
            "filled_qty": "0", "released_unix": None,
            "session_id": self.session_id,
            "pid": os.getpid(),
            "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID"),
            "MEANING": ("E1 undertook to send one first leg. The venue may "
                        "confirm, advance or reconcile this; it may never "
                        "erase it."),
        }
        self.rows[key] = row
        self._write(row)
        return dict(row)

    def bind_venue_id(self, intent_id: str, order_id: Any) -> None:
        """Attach the acknowledged venue id. Does NOT change the state:
        an acknowledgement is not an observation."""
        row = self.rows.get(str(intent_id))
        if row is None or not str(order_id or ""):
            return
        row["order_id"] = str(order_id)
        row["acknowledged_unix"] = float(self.now_fn())
        self._write(row)

    def retire_never_sent(self, intent_id: str, *, why: str) -> None:
        """Only for a refusal STRICTLY BEFORE the POST -- the latch
        refusing, for instance. Never for an ambiguous submission, where
        an order may exist."""
        row = self.rows.get(str(intent_id))
        if row is None:
            return
        if row["state"] != SUBMITTED or row.get("order_id"):
            return
        row["state"] = RETIRED_NEVER_SENT
        row["released_unix"] = float(self.now_fn())
        row["retired_because"] = str(why)
        self._release_reservation_if(intent_id)
        self._write(row)

    # --------------------------------------------------------- reconcile
    def reconcile(self, view: Mapping[str, Any]) -> Dict[str, Any]:
        """Advance obligations from an order-state view. Never releases on
        absence, and never releases on observation alone.

        A view that is not `ok` advances NOTHING: an unreadable venue is
        not evidence about our obligations, in either direction.
        """
        out: Dict[str, Any] = {"advanced": [], "released": [],
                               "unobserved": [], "view_ok": bool(
                                   view and view.get("ok"))}
        if not view or not view.get("ok"):
            out["because"] = "VIEW_NOT_OK_NOTHING_ADVANCED"
            out["blocking"] = [r["key"] for r in self.blocking()]
            return out
        rows_by_id = {}
        for r in (view.get("orders") or []):
            if isinstance(r, Mapping) and r.get("id") is not None:
                rows_by_id[str(r["id"])] = r

        # PASS ONE: record each obligation's OWN cumulative fill from its
        # OWN per-id row. Order-specific, and idempotent -- the quantity is
        # SET from the row, never incremented, so replaying the same view
        # (or a duplicated event) cannot inflate it.
        for key, row in list(self.rows.items()):
            if row["state"] in RELEASED_STATES:
                continue
            oid = row.get("order_id")
            seen = rows_by_id.get(str(oid)) if oid else None
            if seen is None:
                continue
            filled = _fin(seen.get("cum_quantity")) or D("0")
            prior = _fin(row.get("filled_qty")) or D("0")
            row["filled_qty"] = str(filled)
            if filled > 0 and (prior <= 0
                               or row.get("fill_observed_unix") is None):
                # WHEN WE FIRST SAW IT, which is the instant an inventory
                # snapshot has to postdate to be evidence of it.
                row["fill_observed_unix"] = float(self.now_fn())

        # THE TOTAL WE BELIEVE WE HOLD AND HAVE NOT MATCHED. An independent
        # snapshot must cover ALL of this, which is what stops one order's
        # inventory releasing another order's obligation.
        total_unmatched = D("0")
        for row in self.rows.values():
            if row["state"] in RELEASED_STATES and not row.get(
                    "fills_incorporated"):
                continue
            if row["state"] == RECONCILED:
                continue
            total_unmatched += _fin(row.get("filled_qty")) or D("0")
        out["total_unmatched_filled_qty"] = str(total_unmatched)

        # PASS TWO: advance and release.
        for key, row in list(self.rows.items()):
            if row["state"] in RELEASED_STATES:
                continue
            oid = row.get("order_id")
            seen = rows_by_id.get(str(oid)) if oid else None
            if seen is None:
                # ABSENCE IS NOT RELEASE. This is the whole point.
                out["unobserved"].append(key)
                continue
            row["observed_unix"] = float(self.now_fn())
            filled = _fin(row.get("filled_qty")) or D("0")
            if not seen.get("terminal"):
                if row["state"] != OBSERVED:
                    row["state"] = OBSERVED
                    out["advanced"].append(key)
                self._write(row)
                continue
            row["terminal_state"] = str(seen.get("state"))
            decision = _fills_accounted(
                view, filled, total_unmatched=total_unmatched,
                fill_observed_unix=row.get("fill_observed_unix"))
            row["accounting"] = decision
            if decision["accounted"]:
                row["state"] = RECONCILED
                row["released_unix"] = float(self.now_fn())
                row["fills_incorporated"] = filled > 0
                self._release_reservation_if(key)
                out["released"].append(key)
            else:
                if row["state"] != TERMINAL_FILL_UNACCOUNTED:
                    out["advanced"].append(key)
                row["state"] = TERMINAL_FILL_UNACCOUNTED
            self._write(row)
        out["blocking"] = [r["key"] for r in self.blocking()]
        return out

    # -------------------------------------------------------- predicates
    def blocking(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.rows.values()
                if r.get("role", FIRST_LEG) == FIRST_LEG
                and r["state"] in BLOCKING_STATES]

    def observed_ids(self) -> frozenset:
        """Venue ids a successful read has shown at least once.

        Handed to `read_order_state` so it stops resolving a pending id it
        has never actually seen.
        """
        return frozenset(str(r["order_id"]) for r in self.rows.values()
                         if r.get("order_id") and r.get("observed_unix"))

    def worst_case_usd(self) -> D:
        """What our own record says the blocking obligations could cost.

        An unobserved submission is charged at the frozen ceiling; an
        observed one at its own-side price. Quantities come from what WE
        sent, never from a venue row we cannot see.
        """
        total = D("0")
        for r in self.blocking():
            qty = _fin(r.get("qty")) or D("0")
            if r["state"] == SUBMITTED:
                total += qty * UNOBSERVED_CEILING_USD
                continue
            px = _fin(r.get("price")) or UNOBSERVED_CEILING_USD
            total += qty * px
        return total

    def report(self) -> Dict[str, Any]:
        by_state: Dict[str, int] = {}
        for r in self.rows.values():
            by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        return {
            "schema": SCHEMA, "dir": self.dir,
            "n": len(self.rows), "by_state": by_state,
            "blocking_keys": [r["key"] for r in self.blocking()],
            "n_blocking_first_legs": len(self.blocking()),
            "worst_case_usd": str(self.worst_case_usd()),
            "observed_ids": sorted(self.observed_ids()),
            "reservation_holder": self.reservation_holder(),
            "RESERVATION_IS_O_EXCL_NOT_A_COUNTER": True,
            "OUR_RECORD_OUTRANKS_THE_VENUE_VIEW": True,
            "ABSENCE_IS_NEVER_RELEASE": True,
            "OBSERVATION_IS_NEVER_RELEASE": True,
            "RELEASE_REQUIRES_TERMINAL_AND_ACCOUNTED_FILLS": True,
        }


def _fills_accounted(view: Mapping[str, Any], filled: D, *,
                     total_unmatched: D,
                     fill_observed_unix: Optional[float] = None
                     ) -> Dict[str, Any]:
    """Is THIS ORDER'S fill accounted for -- not merely a matching total?

    TWO THINGS THE FIRST VERSION GOT WRONG, IN ORDER.

    1. IT WAS CIRCULAR. It accepted `derived_net_qty`, which
       `read_order_state` computes from the same per-id `cumQuantity` the
       fill was read from. "The fill is accounted because the fill exists"
       made `TERMINAL_FILL_UNACCOUNTED` unreachable, so the state was
       decoration. `derived_net_qty` is not consulted here at all.

    2. IT COMPARED AGAINST THE WRONG QUANTITY. Even using an independent
       source, `independent >= filled` can be satisfied BY COINCIDENCE: a
       different order's 0.13 fill makes the net position 0.13 and would
       have released an obligation whose own 0.13 nobody had accounted for.
       Aggregate inventory cannot attribute, so it is never asked to.

    SO THE RULE IS, in this order:

      * ORDER-SPECIFIC AUTHORITY FIRST. The obligation's own per-id venue
        row must be terminal and its own `cumQuantity` is the fill. That
        is order-specific by construction -- it is a statement about this
        id and no other.

      * A ZERO FILL NEEDS NOTHING MORE. There is no inventory to account
        for.

      * OTHERWISE AN INDEPENDENT SNAPSHOT MUST COVER THE WHOLE OF WHAT WE
        BELIEVE WE HOLD, not just this order: `independent >=
        total_unmatched`, summed across every obligation whose fill is not
        yet matched. One order's fill can no longer be released by another
        order's inventory, because the comparison is against the sum.

      * AND THE SNAPSHOT MUST BE FRESH ENOUGH TO CONTAIN THE FILL. A
        positions read taken BEFORE we saw the fill cannot evidence it,
        however large the number is. `view["as_of"]` is when the read's
        first GET went out, and it must not predate the fill observation.

    Returns the decision and its reasons, so the ledger can record WHY an
    obligation did or did not release rather than only that it did.
    """
    out: Dict[str, Any] = {
        "accounted": False,
        "filled": str(filled),
        "total_unmatched": str(total_unmatched),
        "AGGREGATE_ALONE_IS_NEVER_PROOF": True,
        "derived_net_qty_is_never_consulted": True,
    }
    if filled <= 0:
        out.update({"accounted": True, "because": "NO_FILL_TO_ACCOUNT_FOR"})
        return out
    # FRESHNESS BOUNDARY. The snapshot must not predate the fill.
    as_of = _fin(view.get("as_of"))
    if fill_observed_unix is not None:
        if as_of is None:
            out["because"] = "INVENTORY_SNAPSHOT_HAS_NO_AS_OF"
            return out
        if float(as_of) < float(fill_observed_unix):
            out.update({
                "because": "INVENTORY_SNAPSHOT_PREDATES_THE_FILL",
                "as_of": str(as_of),
                "fill_observed_unix": str(fill_observed_unix)})
            return out
    # INDEPENDENT SOURCES ONLY, each compared against the TOTAL.
    candidates = {
        "residual": _fin((view.get("residual") or {}).get("qty")),
        "venue_net_qty": _fin(view.get("venue_net_qty")),
        "historical_resolved_filled_qty": _fin(
            view.get("historical_resolved_filled_qty")),
    }
    out["independent_sources"] = {k: (None if v is None else str(v))
                                  for k, v in candidates.items()}
    for name, value in candidates.items():
        if value is not None and value >= total_unmatched:
            out.update({"accounted": True, "because": "COVERED_BY_%s" % name,
                        "covering_value": str(value)})
            return out
    out["because"] = "NO_INDEPENDENT_SOURCE_COVERS_THE_TOTAL_UNMATCHED_FILLS"
    return out


def merge_into_snapshot(snap: Dict[str, Any], *,
                        ledger: Optional[ObligationLedger]) -> Dict[str, Any]:
    """Raise the snapshot's floors from our own durable record.

    ONLY EVER RAISES. `our_outstanding_orders` becomes the maximum of the
    venue's count and ours, and `aggregate_exposure_usd` likewise, so every
    existing refusal still fires on at least the input it fired on before.
    Nothing that previously refused can now permit.

    The truthy fail-closed sentinel is preserved untouched: if the venue
    view could not be established, that value stays exactly as it was and
    keeps refusing on its own.
    """
    if ledger is None:
        return snap
    rep = ledger.report()
    snap["obligations"] = rep
    n_local = int(rep["n_blocking_first_legs"])
    cur = snap.get("our_outstanding_orders")
    if isinstance(cur, int):
        snap["our_outstanding_orders"] = max(cur, n_local)
    elif n_local and not cur:
        # a non-int, falsy value cannot be compared, so our own count is
        # published on its own rather than being lost to it
        snap["our_outstanding_orders"] = n_local
    local_exposure = ledger.worst_case_usd()
    venue_exposure = _fin(snap.get("aggregate_exposure_usd"))
    if venue_exposure is None or local_exposure > venue_exposure:
        snap["aggregate_exposure_usd"] = str(local_exposure)
        snap["exposure_floor_from_obligations"] = True
    return snap
