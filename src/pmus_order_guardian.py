"""Order ownership that survives the process, because R6 proved it must.

THE INCIDENT THIS EXISTS FOR. On 2026-09-29 at 18:17:27 the R6 probe exited
on `probe_error` with order `CSWG9ZB98YCA` (DOWN 10 @ own 0.69) resting at the
venue. Nothing cancelled it. It FILLED unsupervised, and a separate read-only
recovery 26 minutes later found it already terminal and booked it. The book
was `incoherent` and `uncertainty_cleared` was false until then.

WHAT IS ACTUALLY AVAILABLE, stated plainly because the brief rightly insists.
No in-process mechanism protects against a hard kill:

  SIGTERM / SIGINT     a handler RUNS. Container stops and orchestrator
                       restarts send SIGTERM first, so this covers the
                       ordinary case. `install_signal_handlers` wires it.
  unhandled exception  a `try/finally` RUNS. This is the R6 hole and it was
                       closed separately in commit 6683b0b3f, AFTER R6 ran.
  SIGKILL / OOM kill   NOTHING RUNS. No handler, no finally, no atexit. The
  power loss           process is gone between one instruction and the next.
  container hard-stop

  So for a hard kill the only real protections are OUTSIDE the process:
    1. durable ownership on disk, written BEFORE the order exists, so the
       next process knows what to ask the venue about (`OwnershipStore`);
    2. mandatory startup reconciliation that BLOCKS new submission until the
       venue's truth is read (`startup_reconcile`);
    3. an independent reconciler that does not share the trading process's
       fate (any scheduled read-only runner can call `startup_reconcile`);
    4. bounding size so an orphan is survivable -- at the venue minimum of
       0.01 contracts an orphan can lose at most $0.01;
    5. the venue's own clock: these markets expire, and R5's orphan went
       ORDER_STATE_EXPIRED by itself at window end.

  `atexit` is deliberately NOT used. It does not run on SIGKILL and its
  presence invites exactly the false confidence the brief warns against.

IDEMPOTENCE. Every recovery operation is keyed by client order id and is safe
to run repeatedly: booking a fill twice is refused by the store, and a cancel
against an already-terminal order is classified, not retried.

FAIL CLOSED. When the venue cannot be read, or a cancel is neither confirmed
nor refused, the store is marked UNCERTAIN and `may_submit()` returns False
until a later reconciliation clears it. Uncertainty never decays on its own.
"""
from __future__ import annotations

import json
import os
import pathlib
import signal
import tempfile
import time
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

SCHEMA = "pmus_order_guardian/1"

#: venue states that mean the order can no longer trade
TERMINAL = ("ORDER_STATE_FILLED", "ORDER_STATE_CANCELLED",
            "ORDER_STATE_REJECTED", "ORDER_STATE_EXPIRED")

OPEN_STATES = ("INTENT", "LIVE_AT_VENUE", "CANCEL_REQUESTED")


def _d(x: Any) -> D:
    try:
        return D(str(x))
    except Exception:                                          # noqa: BLE001
        return D(0)


class OwnershipStore:
    """Durable record of every order we may own, and whether we are certain.

    An INTENT row is written BEFORE submission, so an order that exists at the
    venue but whose acknowledgement we never saw is still recoverable: the
    client order id is ours and we can ask by it. That is the case R6 could
    not handle.

    Writes are atomic (temp file + `os.replace`) and the journal is
    append-only, so a kill mid-write cannot corrupt the snapshot.
    """

    def __init__(self, path: str) -> None:
        self.path = pathlib.Path(path)
        self.journal = self.path.with_suffix(".journal.jsonl")
        self.orders: Dict[str, dict] = {}
        self.uncertain: List[str] = []
        self._load()

    # ------------------------------------------------------------ storage ---
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except Exception:                                      # noqa: BLE001
            return
        if str(raw.get("schema") or "") != SCHEMA:
            # an unreadable or foreign snapshot is NOT an empty one
            self.uncertain.append("SNAPSHOT_SCHEMA_UNKNOWN")
            return
        self.orders = dict(raw.get("orders") or {})
        self.uncertain = list(raw.get("uncertain") or [])

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"schema": SCHEMA, "orders": self.orders,
                           "uncertain": self.uncertain,
                           "written_t": time.time()}, default=str)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _append(self, kind: str, row: Mapping[str, Any]) -> None:
        try:
            with open(self.journal, "a") as fh:
                fh.write(json.dumps({"t": time.time(), "kind": kind,
                                     **row}, default=str) + "\n")
        except Exception:                                      # noqa: BLE001
            self.mark_uncertain("JOURNAL_APPEND_FAILED:%s" % kind)

    # ------------------------------------------------------- the lifecycle ---
    def intend(self, coid: str, **fields: Any) -> dict:
        """Record an order BEFORE it is submitted. Never overwrites a fill."""
        cur = self.orders.get(coid)
        if cur and cur.get("state") in TERMINAL:
            return cur
        row = {"coid": coid, "state": "INTENT", "venue_order_id": None,
               "filled_qty": "0", "booked": False, **fields}
        self.orders[coid] = {**(cur or {}), **row}
        self._append("intend", row)
        self._flush()
        return self.orders[coid]

    def acknowledged(self, coid: str, venue_order_id: str) -> None:
        o = self.orders.setdefault(coid, {"coid": coid, "filled_qty": "0",
                                          "booked": False})
        if o.get("state") not in TERMINAL:
            o["state"] = "LIVE_AT_VENUE"
        o["venue_order_id"] = venue_order_id
        self._append("acknowledged", {"coid": coid,
                                      "venue_order_id": venue_order_id})
        self._flush()

    def observe(self, coid: str, venue_state: str, filled_qty: Any) -> dict:
        """Apply the venue's truth. Idempotent: the same read twice is a no-op.

        A fill is PERMANENT. Quantity never decreases, because a later read
        that reports less than we already booked is a read we do not trust.
        """
        o = self.orders.setdefault(coid, {"coid": coid, "booked": False})
        prev = _d(o.get("filled_qty"))
        now = _d(filled_qty)
        o["filled_qty"] = str(max(prev, now))
        if now < prev:
            self.mark_uncertain(
                "FILL_QTY_DECREASED:%s %s->%s" % (coid, prev, now))
        o["venue_state"] = venue_state
        if venue_state in TERMINAL:
            o["state"] = venue_state
        self._append("observe", {"coid": coid, "venue_state": venue_state,
                                 "filled_qty": o["filled_qty"]})
        self._flush()
        return o

    def book_fill(self, coid: str) -> Optional[dict]:
        """Book a fill exactly once. Returns the row if THIS call booked it."""
        o = self.orders.get(coid)
        if not o or _d(o.get("filled_qty")) <= 0 or o.get("booked"):
            return None
        o["booked"] = True
        self._append("book_fill", {"coid": coid,
                                   "filled_qty": o.get("filled_qty")})
        self._flush()
        return o

    def mark_uncertain(self, why: str) -> None:
        if why not in self.uncertain:
            self.uncertain.append(why)
            try:
                self._flush()
            except Exception:                                  # noqa: BLE001
                pass

    def clear_uncertainty(self, why: str) -> None:
        self.uncertain = [u for u in self.uncertain if u != why]
        self._flush()

    # -------------------------------------------------------- the questions ---
    def outstanding(self) -> List[dict]:
        return [o for o in self.orders.values()
                if str(o.get("state")) in OPEN_STATES]

    def may_submit(self) -> dict:
        """Fail closed. Nothing new while anything is uncertain or open."""
        out = self.outstanding()
        if self.uncertain:
            return {"may_submit": False, "why": "UNCERTAIN",
                    "detail": list(self.uncertain)}
        if out:
            return {"may_submit": False, "why": "ORDERS_OUTSTANDING",
                    "detail": [o.get("coid") for o in out]}
        return {"may_submit": True, "why": None, "detail": []}


# ------------------------------------------------------------- shutdown ---
def cancel_outstanding(store: OwnershipStore,
                       cancel: Callable[[str], Any],
                       read_order: Callable[[str], Mapping[str, Any]],
                       ) -> dict:
    """Try to cancel everything we may own, then VERIFY against the venue.

    A cancel call that returns without raising is NOT evidence. Every order is
    re-read, because `CANCEL_FAILED` is exactly what the venue returns for an
    order that already FILLED -- which is how R4's sweep looked, and it was
    correct there. Only the re-read distinguishes the two.
    """
    results: List[dict] = []
    for o in list(store.outstanding()):
        coid = str(o.get("coid"))
        oid = o.get("venue_order_id") or coid
        row: Dict[str, Any] = {"coid": coid, "venue_order_id": oid}
        try:
            cancel(oid)
            row["cancel_call"] = "RETURNED"
        except Exception as exc:                               # noqa: BLE001
            row["cancel_call"] = "RAISED:%s" % type(exc).__name__
        # VERIFY, always, whether or not the call raised
        try:
            st = read_order(oid) or {}
            vs = str(st.get("state") or st.get("venue_state") or "")
            fq = st.get("filled_qty", st.get("cumQuantity", 0))
            store.observe(coid, vs, fq)
            if vs in TERMINAL:
                row["verdict"] = ("FILLED_NOT_CANCELLED"
                                  if vs == "ORDER_STATE_FILLED"
                                  else "TERMINAL:%s" % vs)
            else:
                row["verdict"] = "STILL_OPEN_AFTER_CANCEL"
                store.mark_uncertain("CANCEL_UNCONFIRMED:%s" % coid)
            row["venue_state"] = vs
        except Exception as exc:                               # noqa: BLE001
            row["verdict"] = "UNVERIFIABLE:%s" % type(exc).__name__
            store.mark_uncertain("CANCEL_UNVERIFIABLE:%s" % coid)
        results.append(row)
    return {"attempted": len(results), "results": results,
            "uncertain": list(store.uncertain),
            "LABEL": "a returned cancel call is not a cancellation; only the "
                     "re-read is evidence"}


def install_signal_handlers(on_signal: Callable[[int], None],
                            signals: Sequence[int] = (signal.SIGTERM,
                                                      signal.SIGINT),
                            ) -> dict:
    """Wire graceful-stop signals only, and say what is NOT covered."""
    wired: List[str] = []
    for s in signals:
        try:
            signal.signal(s, lambda n, _f: on_signal(n))
            wired.append(getattr(s, "name", str(s)))
        except Exception:                                      # noqa: BLE001
            continue
    return {"wired": wired,
            "NOT_COVERED": ["SIGKILL", "OOM_KILL", "POWER_LOSS",
                            "CONTAINER_HARD_STOP"],
            "WHY": "no user code runs on those; durable ownership plus "
                   "startup reconciliation is the only protection",
            "atexit_used": False}


# ----------------------------------------------------------- startup path ---
def startup_reconcile(store: OwnershipStore,
                      list_open: Callable[[], Sequence[Mapping[str, Any]]],
                      read_order: Callable[[str], Mapping[str, Any]],
                      ) -> dict:
    """Read the venue BEFORE anything may be submitted.

    Three populations, all handled:
      ours-and-open       we knew about it and it is still live -> adopt
      ours-and-terminal   it went terminal while we were gone, possibly
                          FILLED -- the R6 case. Observed and booked once.
      venue-only          an order at the venue we have no record of. This is
                          never ignored: it marks uncertainty, because an
                          unknown live order is an unbounded obligation.
    """
    report: Dict[str, Any] = {"adopted": [], "late_terminal": [],
                              "late_fills_booked": [], "venue_only": [],
                              "unbound_intents": [], "errors": []}
    try:
        open_rows = list(list_open() or [])
    except Exception as exc:                                   # noqa: BLE001
        store.mark_uncertain("OPEN_ORDERS_UNREADABLE:%s"
                             % type(exc).__name__)
        return {**report, "errors": ["OPEN_ORDERS_UNREADABLE"],
                "may_submit": store.may_submit()}

    live_ids = set()
    for r in open_rows:
        oid = str(r.get("id") or r.get("order_id") or r.get("venue_order_id")
                  or "")
        if oid:
            live_ids.add(oid)

    known = {str(o.get("venue_order_id") or o.get("coid")): o
             for o in store.orders.values()}

    # our own records first
    for coid, o in list(store.orders.items()):
        if str(o.get("state")) in TERMINAL and o.get("booked"):
            continue
        # AN ID-LESS INTENT IS NOT ASKABLE. Using our local key as an order id
        # produces a nonsense venue call whose empty answer then looks like
        # "neither open nor terminal". Binding it is reconcile_unbound's job;
        # it marks its own uncertainty, so submission is still blocked.
        if str(o.get("state")) == "INTENT" and not o.get("venue_order_id"):
            report["unbound_intents"] = report.get("unbound_intents", [])
            report["unbound_intents"].append(coid)
            continue
        oid = str(o.get("venue_order_id") or coid)
        if oid in live_ids:
            o["state"] = "LIVE_AT_VENUE"
            store._flush()
            report["adopted"].append(coid)
            continue
        # absent from open orders means terminal there -- including FILLED
        try:
            st = read_order(oid) or {}
        except Exception as exc:                               # noqa: BLE001
            store.mark_uncertain("ORDER_UNREADABLE:%s" % coid)
            report["errors"].append("ORDER_UNREADABLE:%s:%s"
                                    % (coid, type(exc).__name__))
            continue
        vs = str(st.get("state") or "")
        fq = st.get("filled_qty", st.get("cumQuantity", 0))
        store.observe(coid, vs, fq)
        if vs in TERMINAL:
            report["late_terminal"].append({"coid": coid, "state": vs,
                                            "filled_qty": str(fq)})
            if store.book_fill(coid):
                report["late_fills_booked"].append(coid)
        else:
            store.mark_uncertain("NEITHER_OPEN_NOR_TERMINAL:%s" % coid)

    # anything at the venue we do not recognise
    for oid in sorted(live_ids):
        if oid not in known:
            report["venue_only"].append(oid)
            store.mark_uncertain("UNKNOWN_LIVE_ORDER:%s" % oid)

    report["may_submit"] = store.may_submit()
    report["LABEL"] = ("startup reconciliation is mandatory; may_submit is "
                       "False until the venue's truth is read and clean")
    return report


def unbound_intents(store: "OwnershipStore") -> List[dict]:
    """INTENT rows that never received a venue order id.

    THE VENUE GIVES US NO CLIENT ORDER ID. `OrderRequest` in the captured
    spec carries symbol, orderType, side, quantity, price, outcome,
    stopPrice, timeInForce and makerOrCancel -- and nothing of ours. The id
    is assigned by PMUS (R4's `CSDKAWYZCYBQ`). So a process that dies between
    submitting and reading the acknowledgement holds an intent it cannot look
    up by key, and identity has to be inferred from attributes instead.
    """
    return [o for o in store.orders.values()
            if str(o.get("state")) == "INTENT"
            and not o.get("venue_order_id")]


def reconcile_unbound(store: "OwnershipStore",
                      list_recent: Callable[[], Sequence[Mapping[str, Any]]],
                      *, window_s: float = 120.0) -> dict:
    """Match id-less intents against venue orders by attribute, or fail.

    Matching is on (market, side, price, qty) inside `window_s` of the
    intent. This is a HEURISTIC and is labelled as one: it cannot be an
    identity claim when the venue never accepted an id from us.

      exactly one candidate   -> bind it, and say the binding was inferred
      several candidates      -> AMBIGUOUS, fail closed
      none                    -> UNMATCHED_INTENT, fail closed. An intent
                                 with no candidate may mean the order never
                                 reached the venue, or that it did and has
                                 since gone terminal. Those are not the same
                                 and the difference is not observable here.
    """
    report: Dict[str, Any] = {"bound": [], "ambiguous": [], "unmatched": [],
                              "errors": []}
    pend = unbound_intents(store)
    if not pend:
        return {**report, "may_submit": store.may_submit(),
                "LABEL": "no id-less intents"}
    try:
        rows = list(list_recent() or [])
    except Exception as exc:                                   # noqa: BLE001
        store.mark_uncertain("RECENT_ORDERS_UNREADABLE:%s"
                             % type(exc).__name__)
        return {**report, "errors": ["RECENT_ORDERS_UNREADABLE"],
                "may_submit": store.may_submit()}

    for o in pend:
        coid = str(o.get("coid"))
        cands = []
        for r in rows:
            if str(r.get("market") or r.get("symbol") or "") != \
                    str(o.get("market") or ""):
                continue
            if str(r.get("side") or "") != str(o.get("side") or ""):
                continue
            if _d(r.get("price")) != _d(o.get("price")):
                continue
            if _d(r.get("quantity", r.get("qty"))) != _d(o.get("qty")):
                continue
            t_o, t_r = o.get("submitted_t"), r.get("created_t")
            if t_o is not None and t_r is not None:
                try:
                    if abs(float(t_r) - float(t_o)) > window_s:
                        continue
                except (TypeError, ValueError):
                    pass
            cands.append(r)
        if len(cands) == 1:
            oid = str(cands[0].get("id") or cands[0].get("order_id") or "")
            if oid:
                store.acknowledged(coid, oid)
                o["binding"] = "INFERRED_FROM_ATTRIBUTES"
                store._flush()
                report["bound"].append({"coid": coid, "venue_order_id": oid,
                                        "binding": "INFERRED"})
                continue
        if len(cands) > 1:
            report["ambiguous"].append(coid)
            store.mark_uncertain("AMBIGUOUS_INTENT_BINDING:%s" % coid)
        else:
            report["unmatched"].append(coid)
            store.mark_uncertain("UNMATCHED_INTENT:%s" % coid)
    report["may_submit"] = store.may_submit()
    report["LABEL"] = ("attribute matching is a heuristic, never an identity "
                       "claim: this venue accepts no client order id")
    return report


def available_protections() -> dict:
    """What actually protects us, by failure mode. No false comfort."""
    return {
        "graceful_stop_SIGTERM": "signal handler runs; cancel + verify",
        "unhandled_exception": "try/finally runs; cancel + verify",
        "SIGKILL_OOM_POWER_LOSS": "NOTHING IN-PROCESS RUNS",
        "out_of_process_protections": [
            "durable INTENT written before submission",
            "mandatory startup reconciliation that blocks submission",
            "independent read-only reconciler, separate process",
            "order size at the venue minimum (0.01) bounds an orphan to $0.01",
            "market expiry retires an orphan by itself (observed in R5)",
        ],
        "atexit_used": False,
        "atexit_why_not": "does not run on SIGKILL; invites false confidence",
        "no_client_order_id": (
            "PMUS OrderRequest accepts no client order id, so an intent whose "
            "ack was lost can only be matched by attribute. See "
            "reconcile_unbound; ambiguity fails closed."),
    }
