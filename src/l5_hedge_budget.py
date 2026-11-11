"""Durable, reserved, idempotent accounting for defensive-hedge locked loss.

WHY A RESERVATION AND NOT A COUNTER. Two hedge candidates evaluated in the
same window would each see the same remaining budget and each pass, so a
$0.60 cap could authorise $1.20 of locked loss. Capacity is therefore
RESERVED before submission and only converted to consumption by a
venue-confirmed fill. The ledger is the single source of truth for both.

WHY DURABLE. An arming variable write restarts the container. A budget held
in memory resets to zero on exactly the event most likely to happen during
a campaign, so the cap would silently stop existing. This writes to the
same `/data` root the window ledger uses, keyed by the same campaign nonce,
atomically and fsynced before returning.

MIRRORS `l5_campaign_approval.WindowLedger` DELIBERATELY, including the rule
that matters most: A READ FAILURE RAISES. "I could not tell" and "nothing
spent" differ by the whole budget, so a missing, corrupt or malformed ledger
must never be reported as an empty one.

IDEMPOTENCY IS KEYED ON THE CLIENT ORDER ID. Fill events duplicate, arrive
out of order, and are replayed by reconciliation. So a confirmation records
the CUMULATIVE filled quantity and takes the MAXIMUM ever seen: a duplicate
is a no-op, an out-of-order earlier event cannot erase a later one, and
repeated reconciliation converges instead of accumulating.

WHAT THIS MODULE DOES NOT DO. It holds no limit of its own -- the caps are
passed in from the owner-approved figures on every call. It never cancels,
never submits, and never touches the watchdog or reconciliation paths.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Mapping, Optional

#: Beside `campaign_windows`, under the same durable root.
CONSUMED_DIR = "campaign_hedge_budget"
ROOT_ENV = "L5_LIVE_ROOT"

#: Reservation lifecycle.
OPEN = "OPEN"              # submitted or about to be; capacity held
CONSUMED = "CONSUMED"      # venue-confirmed fill, actual amount booked
RELEASED = "RELEASED"      # authoritative terminal/cancel, capacity returned


class HedgeBudgetUnavailable(RuntimeError):
    """The ledger could not be read or is inconsistent. FAIL CLOSED."""


def budget_root(root: Optional[str] = None) -> str:
    base = root or os.getenv(ROOT_ENV, "").strip() or "."
    return os.path.join(base, CONSUMED_DIR)


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    if isinstance(v, D):
        return v
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _q(v: D) -> D:
    return v.quantize(D("0.000001"))


class HedgeBudgetLedger:
    """Per-campaign-nonce record of reserved and consumed locked loss."""

    def __init__(self, root: Optional[str] = None) -> None:
        self.dir = budget_root(root)

    def _path(self, nonce_sid: str) -> str:
        safe = "".join(c for c in str(nonce_sid) if c.isalnum()
                       or c in ".-_")
        if not safe:
            raise ValueError("empty nonce id")
        return os.path.join(self.dir, "%s.json" % safe)

    # ------------------------------------------------------------- reading

    def read(self, nonce_sid: str) -> Dict[str, Any]:
        """The ledger, or RAISE. Never an empty dict standing in for one."""
        p = self._path(nonce_sid)
        try:
            with open(p, "r") as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            return {"nonce_sid": str(nonce_sid), "orders": {}}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise HedgeBudgetUnavailable(
                "hedge budget ledger unreadable: %s"
                % type(exc).__name__) from exc
        if not isinstance(doc, dict) or not isinstance(
                doc.get("orders"), Mapping):
            raise HedgeBudgetUnavailable("hedge budget ledger malformed")
        for coid, row in doc["orders"].items():
            if not isinstance(row, Mapping):
                raise HedgeBudgetUnavailable(
                    "hedge budget row malformed: %s" % str(coid)[:40])
            if row.get("state") not in (OPEN, CONSUMED, RELEASED):
                raise HedgeBudgetUnavailable(
                    "hedge budget row has unknown state: %s"
                    % str(row.get("state"))[:40])
            if _d(row.get("locked_loss_usd")) is None:
                raise HedgeBudgetUnavailable(
                    "hedge budget row has unreadable amount")
        return doc

    def committed(self, nonce_sid: str) -> Dict[str, Any]:
        """What counts against the cap right now.

        RESERVED PLUS CONSUMED. An open reservation is as binding as a
        confirmed fill for the purpose of admitting the NEXT hedge --
        otherwise two candidates both pass against the same capacity.
        Released rows count for nothing.
        """
        doc = self.read(nonce_sid)
        reserved = D(0)
        consumed = D(0)
        n_open = n_consumed = n_released = 0
        for row in doc["orders"].values():
            amt = _d(row.get("locked_loss_usd")) or D(0)
            st = row.get("state")
            if st == OPEN:
                reserved += amt
                n_open += 1
            elif st == CONSUMED:
                consumed += amt
                n_consumed += 1
            else:
                n_released += 1
        return {"nonce_sid": str(nonce_sid),
                "reserved_usd": _q(reserved),
                "consumed_usd": _q(consumed),
                "committed_usd": _q(reserved + consumed),
                "open_orders": n_open, "consumed_orders": n_consumed,
                "released_orders": n_released,
                "labels": ("committed = reserved + consumed. An OPEN "
                           "reservation binds the next candidate exactly as "
                           "a confirmed fill does, so two hedges cannot pass "
                           "against the same remaining capacity.")}

    # ------------------------------------------------------------- writing

    def _write(self, nonce_sid: str, doc: Dict[str, Any],
               now_iso: str) -> None:
        out = dict(doc, nonce_sid=str(nonce_sid), last_utc=now_iso)
        out.setdefault("first_utc", now_iso)
        os.makedirs(self.dir, exist_ok=True)
        p = self._path(nonce_sid)
        tmp = p + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(out, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
        try:
            dfd = os.open(self.dir, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass

    def reserve(self, nonce_sid: str, coid: str, *,
                locked_loss_usd: Any, max_cumulative_usd: Any,
                now_iso: str, market: Any = None,
                qty: Any = None, own_price: Any = None) -> Dict[str, Any]:
        """Hold capacity for one defensive hedge, durably and idempotently.

        Re-reserving the SAME coid is a no-op returning the existing row, so
        a retry after an ambiguous submission cannot double-charge.
        """
        amt = _d(locked_loss_usd)
        cap = _d(max_cumulative_usd)
        if amt is None or amt < 0:
            return {"ok": False, "reason": "LOCKED_LOSS_UNREADABLE"}
        if cap is None or cap < 0:
            # NO SILENT DEFAULT. The owner-approved cap is required.
            return {"ok": False, "reason": "CUMULATIVE_CAP_NOT_SUPPLIED"}
        key = str(coid)
        if not key:
            return {"ok": False, "reason": "COID_MISSING"}
        doc = self.read(nonce_sid)
        orders = dict(doc.get("orders") or {})
        existing = orders.get(key)
        if isinstance(existing, Mapping):
            cur = self.committed(nonce_sid)
            return {"ok": existing.get("state") != RELEASED,
                    "reason": (None if existing.get("state") != RELEASED
                               else "RESERVATION_ALREADY_RELEASED"),
                    "reserved_now": False, "idempotent": True,
                    "coid": key, "state": existing.get("state"),
                    "locked_loss_usd": str(existing.get("locked_loss_usd")),
                    "committed_usd": str(cur["committed_usd"])}
        before = self.committed(nonce_sid)
        after = before["committed_usd"] + amt
        if after > cap:
            return {"ok": False, "reason": "CUMULATIVE_BUDGET_EXHAUSTED",
                    "coid": key, "locked_loss_usd": str(_q(amt)),
                    "committed_usd": str(before["committed_usd"]),
                    "would_commit_usd": str(_q(after)),
                    "max_cumulative_usd": str(cap),
                    "remaining_usd": str(_q(cap - before["committed_usd"]))}
        orders[key] = {"state": OPEN, "locked_loss_usd": str(_q(amt)),
                       "reserved_utc": now_iso,
                       "market": None if market is None else str(market),
                       "qty_reserved": None if qty is None else str(qty),
                       "own_price": (None if own_price is None
                                     else str(own_price)),
                       "filled_qty_seen": "0"}
        self._write(nonce_sid, dict(doc, orders=orders), now_iso)
        now = self.committed(nonce_sid)
        return {"ok": True, "reserved_now": True, "idempotent": False,
                "coid": key, "locked_loss_usd": str(_q(amt)),
                "committed_usd": str(now["committed_usd"]),
                "max_cumulative_usd": str(cap),
                "remaining_usd": str(_q(cap - now["committed_usd"]))}

    def confirm_fill(self, nonce_sid: str, coid: str, *,
                     cumulative_filled_qty: Any,
                     actual_locked_loss_usd: Any,
                     now_iso: str) -> Dict[str, Any]:
        """Book a VENUE-CONFIRMED fill against its reservation.

        IDEMPOTENT AND ORDER-INSENSITIVE. `cumulative_filled_qty` is the
        total the venue reports for this order, and the ledger keeps the
        MAXIMUM it has ever seen. A duplicate event is a no-op; an
        out-of-order earlier event cannot reduce a later one; repeated
        reconciliation converges rather than accumulating.

        The booked amount is the ACTUAL locked loss at the quantities,
        prices and fees the venue confirmed -- never the reserved estimate.
        """
        key = str(coid)
        q_new = _d(cumulative_filled_qty)
        amt = _d(actual_locked_loss_usd)
        if q_new is None or q_new < 0:
            return {"ok": False, "reason": "FILLED_QTY_UNREADABLE"}
        if amt is None or amt < 0:
            return {"ok": False, "reason": "ACTUAL_LOCKED_LOSS_UNREADABLE"}
        doc = self.read(nonce_sid)
        orders = dict(doc.get("orders") or {})
        row = orders.get(key)
        if not isinstance(row, Mapping):
            # A FILL WITH NO RESERVATION IS AN INCONSISTENCY, not a free
            # hedge. It is booked so the consumption is never lost, and
            # flagged so the caller fails closed on new hedges.
            orders[key] = {"state": CONSUMED,
                           "locked_loss_usd": str(_q(amt)),
                           "filled_qty_seen": str(q_new),
                           "reserved_utc": None,
                           "consumed_utc": now_iso,
                           "unreserved_fill": True}
            self._write(nonce_sid, dict(doc, orders=orders), now_iso)
            return {"ok": True, "booked": True, "unreserved_fill": True,
                    "reason": "FILL_WITHOUT_RESERVATION",
                    "coid": key,
                    "committed_usd": str(
                        self.committed(nonce_sid)["committed_usd"])}
        seen = _d(row.get("filled_qty_seen")) or D(0)
        if q_new <= seen and row.get("state") == CONSUMED:
            return {"ok": True, "booked": False, "idempotent": True,
                    "coid": key, "filled_qty_seen": str(seen),
                    "locked_loss_usd": str(row.get("locked_loss_usd")),
                    "committed_usd": str(
                        self.committed(nonce_sid)["committed_usd"])}
        keep_q = max(seen, q_new)
        prev_amt = _d(row.get("locked_loss_usd")) or D(0)
        # MONOTONE IN THE QUANTITY SEEN. If this event carries no more fill
        # than we already booked, the amount does not move either.
        keep_amt = amt if q_new >= seen else prev_amt
        orders[key] = dict(row, state=CONSUMED,
                           locked_loss_usd=str(_q(keep_amt)),
                           filled_qty_seen=str(keep_q),
                           consumed_utc=now_iso)
        self._write(nonce_sid, dict(doc, orders=orders), now_iso)
        return {"ok": True, "booked": True, "idempotent": False,
                "coid": key, "filled_qty_seen": str(keep_q),
                "locked_loss_usd": str(_q(keep_amt)),
                "committed_usd": str(
                    self.committed(nonce_sid)["committed_usd"])}

    def release(self, nonce_sid: str, coid: str, *, now_iso: str,
                authoritative: bool, reason: str) -> Dict[str, Any]:
        """Return unused capacity -- ONLY on authoritative terminal state.

        `authoritative` must be True, and the caller may only set it from a
        venue re-read that reported a terminal state or a verified
        cancellation. A cancel call that merely RETURNED is not terminal,
        and releasing on it would hand back capacity an order might still
        consume.

        A CONSUMED row is never released: the loss has already happened.
        """
        key = str(coid)
        if not authoritative:
            return {"ok": False, "reason": "RELEASE_REQUIRES_AUTHORITATIVE"}
        doc = self.read(nonce_sid)
        orders = dict(doc.get("orders") or {})
        row = orders.get(key)
        if not isinstance(row, Mapping):
            return {"ok": True, "released": False, "idempotent": True,
                    "reason": "NO_SUCH_RESERVATION", "coid": key}
        if row.get("state") == CONSUMED:
            return {"ok": False, "released": False,
                    "reason": "ALREADY_CONSUMED", "coid": key,
                    "locked_loss_usd": str(row.get("locked_loss_usd"))}
        if row.get("state") == RELEASED:
            return {"ok": True, "released": False, "idempotent": True,
                    "coid": key}
        orders[key] = dict(row, state=RELEASED, released_utc=now_iso,
                           release_reason=str(reason)[:80])
        self._write(nonce_sid, dict(doc, orders=orders), now_iso)
        return {"ok": True, "released": True, "idempotent": False,
                "coid": key,
                "committed_usd": str(
                    self.committed(nonce_sid)["committed_usd"])}

    # ----------------------------------------------------------- admission

    def may_reserve(self, nonce_sid: str, *, locked_loss_usd: Any,
                    max_per_hedge_usd: Any,
                    max_cumulative_usd: Any) -> Dict[str, Any]:
        """READ-ONLY verdict for one candidate. Writes nothing.

        Both caps are REQUIRED. A missing cap refuses rather than falling
        back to a permissive default, because a default the owner did not
        approve is not a limit.
        """
        amt = _d(locked_loss_usd)
        per = _d(max_per_hedge_usd)
        cum = _d(max_cumulative_usd)
        if amt is None or amt < 0:
            return {"ok": False, "reason": "LOCKED_LOSS_UNREADABLE"}
        if per is None or per < 0:
            return {"ok": False, "reason": "PER_HEDGE_CAP_NOT_SUPPLIED"}
        if cum is None or cum < 0:
            return {"ok": False, "reason": "CUMULATIVE_CAP_NOT_SUPPLIED"}
        if amt > per:
            return {"ok": False, "reason": "LOCKED_LOSS_EXCEEDS_PER_HEDGE",
                    "locked_loss_usd": str(_q(amt)),
                    "max_per_hedge_usd": str(per)}
        cur = self.committed(nonce_sid)          # may RAISE -> fail closed
        after = cur["committed_usd"] + amt
        if after > cum:
            return {"ok": False, "reason": "CUMULATIVE_BUDGET_EXHAUSTED",
                    "committed_usd": str(cur["committed_usd"]),
                    "would_commit_usd": str(_q(after)),
                    "max_cumulative_usd": str(cum),
                    "remaining_usd": str(_q(cum - cur["committed_usd"]))}
        return {"ok": True, "committed_usd": str(cur["committed_usd"]),
                "would_commit_usd": str(_q(after)),
                "max_cumulative_usd": str(cum),
                "remaining_usd": str(_q(cum - after)),
                "open_orders": cur["open_orders"]}

    def consistency(self, nonce_sid: str,
                    venue_open_coids: Any = None) -> Dict[str, Any]:
        """Is the ledger consistent with venue state? FAIL CLOSED if not.

        An OPEN reservation whose order the venue does not report is
        unresolved: it may have filled, so its capacity must NOT be
        released and new hedges must stop until reconciliation settles it.
        An unreserved fill is likewise an inconsistency.
        """
        doc = self.read(nonce_sid)
        open_here = [c for c, r in doc["orders"].items()
                     if isinstance(r, Mapping) and r.get("state") == OPEN]
        unreserved = [c for c, r in doc["orders"].items()
                      if isinstance(r, Mapping) and r.get("unreserved_fill")]
        problems = []
        if unreserved:
            problems.append("UNRESERVED_FILLS:%d" % len(unreserved))
        if venue_open_coids is not None:
            known = {str(c) for c in venue_open_coids}
            missing = [c for c in open_here if c not in known]
            if missing:
                problems.append("OPEN_RESERVATION_NOT_AT_VENUE:%d"
                                % len(missing))
        return {"ok": not problems, "problems": problems,
                "open_reservations": open_here,
                "unreserved_fills": unreserved,
                "labels": ("not ok means new defensive hedges must be "
                           "refused. It does NOT disable cancellation, the "
                           "watchdog or reconciliation.")}
