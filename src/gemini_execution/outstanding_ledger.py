"""Durable record of every order that MIGHT still be live at the venue.

THE BLOCKER THIS CLOSES. `MutationLedger` records that a venue mutation was
ATTEMPTED, which is what bounds how many times we touch the venue. It does not
record WHAT IS STILL OUT THERE. So across

    rest -> partial fill -> second rest -> cancel ambiguous -> process dies

a restart could rebuild inventory and the action budget correctly and still
have no durable answer to "how much cash is committed to orders that may be
resting right now". `WorkingOrders` knew, and `WorkingOrders` lives in memory.
The $2.50 committed cap and the $2.00 residual cap were therefore bounds
within one process, not across a restart.

THE RULE THAT GENERATES EVERYTHING HERE. **Only an authoritative venue read
may release committed cash.** Not an ack, not a confirmed cancel receipt, not
our own belief that an order filled. Gemini's own cancel acks are the reason:
measured 2026-09-18, every cancel in CONTINUOUS-4 returned 200 with an empty
result and TWO of the four "cancelled" orders had already FILLED. An ack is a
receipt; the venue is the authority.

  potentially live   INTENT, ACCEPTED, PARTIAL, FILLED, SUBMISSION_UNKNOWN,
                     CANCEL_REQUESTED, CANCEL_ACKED, CANCEL_UNKNOWN
  terminal           REJECTED (the venue said it refused to create it),
                     VENUE_ABSENT, VENUE_TERMINAL (an authoritative read)

TWO DIFFERENT QUESTIONS, DELIBERATELY SEPARATED.

  `blocks_restart()`   is this order proven gone? A FILLED order commits no
                       more cash but is still not proven gone, so it blocks a
                       new window until a read clears it -- and clearing it is
                       one read, which the restart has to do anyway.
  `remaining_committed_usd()`  how much cash could still leave? A filled order
                       contributes nothing; an order of unknown fate
                       contributes its WHOLE original notional, because
                       unknown filled quantity means unknown remaining.

UNREADABLE IS NEVER EMPTY, AND IT IS NEVER ZERO. A reconcile against an
unreadable active-order endpoint returns UNREADABLE and releases nothing.

DURABILITY. Append-only JSONL, written and fsynced before the frame goes out,
exactly as strong as `MutationLedger` and for the same reason: a process that
dies mid-send must still show the order as possibly resting. A torn final line
is replayed as an order of UNKNOWN fate rather than skipped.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

LABEL = "GEMINI_OUTSTANDING_ORDER_LEDGER_V1"

# ---- submission states ------------------------------------------------
INTENT = "INTENT"                       # written BEFORE the frame
ACCEPTED = "ACCEPTED"                   # venue acked the placement
REJECTED = "REJECTED"                   # venue refused to create it
SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
PARTIAL = "PARTIAL"
FILLED = "FILLED"
CANCEL_REQUESTED = "CANCEL_REQUESTED"
CANCEL_ACKED = "CANCEL_ACKED"           # a RECEIPT, not a release
CANCEL_UNKNOWN = "CANCEL_UNKNOWN"
VENUE_ABSENT = "VENUE_ABSENT"           # authoritative read: not resting
VENUE_TERMINAL = "VENUE_TERMINAL"       # authoritative read: filled/cancelled

#: the ONLY states in which an order is proven not to be resting. Everything
#: else is exposure. REJECTED is here because a non-OK placement ack is the
#: venue stating it did not create the order -- that is the venue speaking,
#: not us inferring.
TERMINAL = (REJECTED, VENUE_ABSENT, VENUE_TERMINAL)

# ---- reconcile verdicts ----------------------------------------------
CLEAR = "CLEAR"                 # nothing potentially live; a window may start
BLOCKED = "BLOCKED"             # something may still be resting
UNREADABLE = "UNREADABLE"       # the venue was not read; release nothing


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


class OutstandingLedger:
    """Append-only, fsynced, replayed on load. One file per window."""

    def __init__(self, path: str, *, auth_id: str, symbol: str) -> None:
        self.path = path
        self.auth_id = str(auth_id)
        self.symbol = str(symbol)
        #: client_order_id -> the latest known shape of that order
        self.orders: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------ durability

    def _append(self, row: Mapping[str, Any]) -> None:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _load(self) -> None:
        torn = 0
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        # A TORN LINE IS AN ORDER OF UNKNOWN FATE. It cannot
                        # be skipped: the write that tore is exactly the one
                        # that happened as the process died, which is the
                        # order most likely to be resting right now.
                        torn += 1
                        coid = "TORN-ROW-%d" % torn
                        self.orders[coid] = {
                            "client_order_id": coid, "symbol": self.symbol,
                            "state": SUBMISSION_UNKNOWN,
                            "leg": None, "price": None,
                            "original_qty": None, "filled_qty": None,
                            "why": "unparseable ledger row; fate unknown",
                        }
                        continue
                    if not isinstance(r, dict):
                        continue
                    coid = str(r.get("client_order_id") or "")
                    if not coid:
                        continue
                    cur = self.orders.setdefault(coid, {})
                    cur.update({k: v for k, v in r.items() if k != "t"})
        except OSError:
            pass

    # ------------------------------------------------------------ the writes

    def _write(self, coid: str, **fields: Any) -> Dict[str, Any]:
        row = {"label": LABEL, "client_order_id": str(coid),
               "symbol": self.symbol, "auth_id": self.auth_id,
               "t": time.time(), **fields}
        self._append(row)
        cur = self.orders.setdefault(str(coid), {})
        cur.update({k: v for k, v in row.items() if k != "t"})
        return cur

    def intent(self, coid: str, *, leg: str, price: Any, qty: Any
               ) -> Dict[str, Any]:
        """Written BEFORE the frame goes out. A crash here leaves an order
        that must be treated as potentially live."""
        return self._write(coid, state=INTENT, leg=str(leg).upper(),
                           price=str(price), original_qty=str(qty),
                           filled_qty="0", venue_order_id=None)

    def accepted(self, coid: str, venue_order_id: Any) -> Dict[str, Any]:
        return self._write(coid, state=ACCEPTED,
                           venue_order_id=(None if venue_order_id in
                                           (None, "", 0)
                                           else str(venue_order_id)))

    def rejected(self, coid: str, why: str = "") -> Dict[str, Any]:
        return self._write(coid, state=REJECTED, why=why)

    def submission_unknown(self, coid: str, why: str = "") -> Dict[str, Any]:
        return self._write(coid, state=SUBMISSION_UNKNOWN, why=why)

    def fill(self, coid: str, qty: Any, *, partial: bool) -> Dict[str, Any]:
        cur = self.orders.get(str(coid), {})
        prior = _dec(cur.get("filled_qty")) or D("0")
        add = _dec(qty) or D("0")
        return self._write(coid, state=(PARTIAL if partial else FILLED),
                           filled_qty=str(prior + add))

    def cancel_requested(self, coid: str, *, leg: Any = None,
                         price: Any = None, qty: Any = None
                         ) -> Dict[str, Any]:
        """Cancelling an order this ledger has never seen still has to leave a
        COMPUTABLE record.

        An order recovered from the venue after a restart was never placed by
        this process, so there is no INTENT row for it. Writing a bare
        CANCEL_REQUESTED would create a potentially-live order with no size
        and no price, and `committed_usd` would then -- correctly -- call the
        whole total unreadable and refuse everything. Carrying what the caller
        already knows keeps the total computable without ever inventing a
        number: fields already on record are never overwritten.
        """
        cur = self.orders.get(str(coid), {})
        fill: Dict[str, Any] = {}
        if leg is not None and not cur.get("leg"):
            fill["leg"] = str(leg).upper()
        if price is not None and not cur.get("price"):
            fill["price"] = str(price)
        if qty is not None and not cur.get("original_qty"):
            fill["original_qty"] = str(qty)
            fill.setdefault("filled_qty", cur.get("filled_qty") or "0")
        return self._write(coid, state=CANCEL_REQUESTED, **fill)

    def cancel_acked(self, coid: str, *, confirmed: bool) -> Dict[str, Any]:
        """A RECEIPT, NOT A RELEASE. Even a confirmed cancel ack leaves the
        order potentially live until an authoritative read says otherwise."""
        return self._write(coid, state=CANCEL_ACKED,
                           cancel_ack_confirmed=bool(confirmed))

    def cancel_unknown(self, coid: str, why: str = "") -> Dict[str, Any]:
        return self._write(coid, state=CANCEL_UNKNOWN, why=why)

    # ------------------------------------------------------------- the reads

    def remaining_qty(self, o: Mapping[str, Any]) -> Optional[D]:
        """Unknown original or unknown fill means UNKNOWN, not zero."""
        orig = _dec(o.get("original_qty"))
        if orig is None:
            return None
        state = o.get("state")
        if state in (INTENT, SUBMISSION_UNKNOWN):
            # the venue may have filled ANY part of it, including none: the
            # whole notional is still at risk of leaving the account
            return orig
        filled = _dec(o.get("filled_qty"))
        if filled is None:
            return orig
        return max(D("0"), orig - filled)

    def remaining_committed_usd(self, o: Mapping[str, Any]) -> Optional[D]:
        q = self.remaining_qty(o)
        p = _dec(o.get("price"))
        if q is None or p is None:
            return None
        return q * p

    def potentially_live(self) -> List[Dict[str, Any]]:
        return [dict(o) for o in self.orders.values()
                if o.get("state") not in TERMINAL]

    def blocks_restart(self) -> List[Dict[str, Any]]:
        """Orders not PROVEN gone by an authoritative read."""
        return self.potentially_live()

    def committed_usd(self, *, exclude: Optional[Set[str]] = None
                      ) -> Dict[str, Any]:
        """Cash that could still leave, over potentially-live orders.

        `exclude` drops client order ids the caller is already counting from
        another source (the in-process working set), so the same order is
        never charged twice.

        An order whose remaining commitment cannot be computed makes the WHOLE
        total unreadable. A partially computable total is the worst answer
        available: it looks like a number and is not one.
        """
        skip = {str(x) for x in (exclude or set())}
        total = D("0")
        counted: List[str] = []
        for coid, o in self.orders.items():
            if o.get("state") in TERMINAL or coid in skip:
                continue
            c = self.remaining_committed_usd(o)
            if c is None:
                return {"readable": False, "usd": None, "counted": counted,
                        "why": ("order %s has no computable remaining "
                                "commitment (state %s, qty %r, price %r); a "
                                "partial total is not a total"
                                % (coid, o.get("state"),
                                   o.get("original_qty"), o.get("price")))}
            total += c
            counted.append(coid)
        return {"readable": True, "usd": total, "counted": counted,
                "why": None}

    # -------------------------------------------------------- reconciliation

    @staticmethod
    def _keys_of(row: Mapping[str, Any]) -> Set[str]:
        out: Set[str] = set()
        for k in ("clientOrderId", "client_order_id"):
            v = row.get(k)
            if v not in (None, ""):
                out.add(str(v))
        for k in ("orderId", "order_id", "venue_order_id"):
            v = row.get(k)
            if v not in (None, "", 0):
                out.add(str(v))
        return out

    def reconcile(self, active: Optional[Sequence[Mapping[str, Any]]]
                  ) -> Dict[str, Any]:
        """Durable ledger against the venue's AUTHORITATIVE active orders.

        This is the only thing that may release committed cash, and it fails
        closed in every direction:

          active is None                  -> UNREADABLE
          a row that is not a mapping     -> UNREADABLE
          an active row we do not know    -> BLOCKED, and its notional counts
          one of ours still active        -> BLOCKED, commitment refreshed
          one of ours absent              -> VENUE_ABSENT, released
        """
        if active is None:
            return {"verdict": UNREADABLE, "tradeable": False,
                    "committed_usd": None, "blocking": None,
                    "why": ("active orders were not readable; an unread venue "
                            "is never an empty one and nothing is released")}
        rows = list(active)
        for r in rows:
            if not isinstance(r, Mapping):
                return {"verdict": UNREADABLE, "tradeable": False,
                        "committed_usd": None, "blocking": None,
                        "why": ("the active-order payload contained a row "
                                "that is not a mapping (%s); the read is "
                                "malformed and releases nothing"
                                % type(r).__name__)}

        by_key: Dict[str, Mapping[str, Any]] = {}
        for r in rows:
            for k in self._keys_of(r):
                by_key[k] = r

        matched_rows: Set[int] = set()
        blocking: List[Dict[str, Any]] = []
        released: List[str] = []

        for coid, o in list(self.orders.items()):
            if o.get("state") in TERMINAL:
                continue
            keys = {coid}
            if o.get("venue_order_id") not in (None, "", 0):
                keys.add(str(o.get("venue_order_id")))
            hit = None
            for k in keys:
                if k in by_key:
                    hit = by_key[k]
                    break
            if hit is None:
                # THE VENUE DOES NOT LIST IT. That is the authoritative read
                # this ledger has been waiting for, and the only thing that
                # releases the cash.
                self._write(coid, state=VENUE_ABSENT,
                            why="authoritative active-order read does not "
                                "list this order")
                released.append(coid)
                continue
            matched_rows.add(id(hit))
            fq = hit.get("filledQuantity", hit.get("filled_quantity"))
            upd: Dict[str, Any] = {"state": PARTIAL if _dec(fq) and
                                   (_dec(fq) or D("0")) > 0 else ACCEPTED}
            if fq is not None and _dec(fq) is not None:
                upd["filled_qty"] = str(_dec(fq))
            oid = hit.get("orderId", hit.get("order_id"))
            if oid not in (None, "", 0):
                upd["venue_order_id"] = str(oid)
            px = hit.get("price")
            if _dec(px) is not None:
                upd["price"] = str(_dec(px))
            oq = hit.get("quantity", hit.get("qty"))
            if _dec(oq) is not None:
                upd["original_qty"] = str(_dec(oq))
            upd["why"] = "still active at the venue"
            self._write(coid, **upd)
            blocking.append(dict(self.orders[coid]))

        # AN ACTIVE ORDER WE HAVE NO RECORD OF IS STILL OUR EXPOSURE. It is
        # on this account and this symbol; not recognising it is a reason to
        # stop, never a reason to ignore it.
        foreign: List[Dict[str, Any]] = []
        for r in rows:
            if id(r) in matched_rows:
                continue
            coid = (r.get("clientOrderId") or r.get("client_order_id")
                    or r.get("orderId") or r.get("order_id"))
            q = _dec(r.get("quantity", r.get("qty")))
            f = _dec(r.get("filledQuantity", r.get("filled_quantity"))) or D("0")
            p = _dec(r.get("price"))
            rem = None if q is None else max(D("0"), q - f)
            foreign.append({
                "client_order_id": (None if coid is None else str(coid)),
                "remaining_qty": (None if rem is None else str(rem)),
                "price": (None if p is None else str(p)),
                "remaining_committed_usd": (
                    None if (rem is None or p is None) else str(rem * p)),
                "why": "active at the venue and NOT in our durable ledger"})

        own = self.committed_usd()
        if not own["readable"]:
            return {"verdict": UNREADABLE, "tradeable": False,
                    "committed_usd": None,
                    "blocking": blocking, "foreign": foreign,
                    "released": released, "why": own["why"]}
        total = own["usd"]
        for f in foreign:
            c = _dec(f.get("remaining_committed_usd"))
            if c is None:
                return {"verdict": UNREADABLE, "tradeable": False,
                        "committed_usd": None, "blocking": blocking,
                        "foreign": foreign, "released": released,
                        "why": ("an active order we do not recognise has no "
                                "computable notional (%s); exposure is not "
                                "established" % f.get("client_order_id"))}
            total += c

        blocked = bool(blocking or foreign)
        return {
            "verdict": (BLOCKED if blocked else CLEAR),
            "tradeable": not blocked,
            "committed_usd": str(total),
            "blocking": blocking, "foreign": foreign, "released": released,
            "why": (None if not blocked else
                    "%d of our order(s) and %d unrecognised order(s) are "
                    "active at the venue; a new window may not begin while "
                    "anything may still be resting"
                    % (len(blocking), len(foreign))),
            "labels": ("only this read releases committed cash. A cancel "
                       "ack is a receipt, not a release."),
        }
