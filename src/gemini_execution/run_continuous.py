"""Invoker for the reviewed continuous driver. Thin by design.

THIS FILE HOLDS NO SAFETY LOGIC AND MUST NOT ACQUIRE ANY. Every bound --
the 12-slot mutation budget, the $0.15 exposure ceiling, the 0.04 residual and
imbalance caps, the 0.10 per-leg caps, the end-of-window floors, the terminal
one-window binding, the ambiguity handling -- lives inside
`ws_continuous.Driver` and is covered by the authorized `executable_sha`. What
happens here is only: pick the eligible contract, read authoritative venue
state, hand both to the Driver, and write down what it did.

THE ONE THING THIS FILE DECIDES THAT THE DRIVER CANNOT CHECK is WHICH SYMBOL
and WHAT ITS CLOCK SAYS. A wrong symbol or a wrong time-to-expiry would make
the end-of-window floors fire at the wrong moment, so `tte_of` is derived from
the venue's own published expiry on every call and returns a NEGATIVE number
rather than a guess when the expiry cannot be read -- which drives the Driver
straight to WITHDRAWN, the fail-closed direction.

It is NOT covered by the authorized executable_sha, which covers the driver.
That is stated plainly in the run report rather than left to be discovered.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import (Any, Callable, Dict, List, Mapping,
                    Optional, Sequence)

from src.gemini_execution import bundle as BUNDLE
from src.gemini_execution import continuous_preflight as PF
from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import habitat as HB
from src.gemini_execution import metadata as MD
from src.gemini_execution import spec
from src.gemini_execution import ws_continuous as WC
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import position_ledger as PL

LABEL = "GEMINI_L5_CONTINUOUS_RUN_V1"

#: reached the boundary, refused only by the authorization gate. Defined
#: HERE and not in ws_continuous because adding a name there would change
#: the authorized executable_sha, and the authorization names that hash.
READY_BUT_UNAUTHORIZED = "READY_TO_MUTATE_BUT_UNAUTHORIZED"

#: a window is worth entering only if the normal surface can still act, i.e.
#: at least one full maker-rest cycle above the OPEN/EXTEND floor
MIN_ENTRY_TTE_S = CP.NO_NEW_OPEN_EXTEND_TTE_S + CP.STALE_ORDER_S   # 90.0
MAX_ENTRY_TTE_S = 300.0


# --------------------------------------------------------------- WIRE CAPTURE
# The successful `order.place` acknowledgement shape has never been observed.
# CONTINUOUS-2 placed two accepted orders, but the driver parsed them with code
# recovered from the canary and the shape went into the record as INFERRED. The
# cost of inferring was already paid once: the first live cancel sent
# `clientOrderId` and the venue rejected it -1013, because the id is nested
# under `result`. This captures the answer instead of guessing it again.
#
# WHY IT IS HERE AND NOT IN THE DRIVER. `ws_continuous.Driver.place` already
# returns the ack, but `run_window` discards that return value. Capturing it
# there would mean editing the driver, and the owner authorization names the
# driver's exact `executable_sha`. Evidence capture is not worth invalidating
# the hash that makes the authorization meaningful, so the capture is taken at
# the session seam THIS FILE owns and the reviewed executable stays byte-for-
# byte what was authorized.

#: Keys whose VALUE is never written to an artifact. The key and the value's
#: TYPE survive, because the point of this capture is the venue's response
#: SHAPE -- redacting the shape would defeat the whole exercise.
_SENSITIVE = ("apikey", "secret", "signature", "nonce", "password", "token",
              "authorization", "cookie", "session", "account", "acct",
              "payload")
_MAX_STR = 512
_MAX_ITEMS = 200
#: recursion caps for the two walkers. Deliberately NOT envelope-shaped
#: numbers: a loose 12 here reads as MAX_ACTIONS to the next person, and
#: the invoker restating an envelope bound is the drift this file forbids.
_MAX_DEPTH = 16
_MAX_SHAPE_DEPTH = 9
_MAX_SEQ = 5


def _is_sensitive(key: Any) -> bool:
    k = str(key).lower().replace("-", "").replace("_", "")
    return any(s.replace("_", "") in k for s in _SENSITIVE)


def sanitize(obj: Any, _depth: int = 0) -> Any:
    """Redact credential-shaped VALUES; keep the structure around them."""
    if _depth > _MAX_DEPTH:
        return "<truncated:depth>"
    if isinstance(obj, Mapping):
        out: Dict[str, Any] = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= _MAX_ITEMS:
                out["<truncated>"] = "%d more keys" % (len(obj) - i)
                break
            out[str(k)] = ("<redacted:%s>" % type(v).__name__
                           if _is_sensitive(k) else sanitize(v, _depth + 1))
        return out
    if isinstance(obj, (list, tuple)):
        return [sanitize(v, _depth + 1) for v in list(obj)[:_MAX_ITEMS]]
    if isinstance(obj, str):
        return obj if len(obj) <= _MAX_STR else obj[:_MAX_STR] + "<truncated>"
    if isinstance(obj, (bool, int, float)) or obj is None:
        return obj
    return str(obj)[:_MAX_STR]


def shape_of(obj: Any, _depth: int = 0) -> Any:
    """Types only. `orderId` arriving as int vs str is the kind of difference
    that produced a -1013, so the type is recorded next to the value."""
    if _depth > _MAX_SHAPE_DEPTH:
        return "..."
    if isinstance(obj, Mapping):
        return {str(k): shape_of(v, _depth + 1)
                for k, v in list(obj.items())[:_MAX_ITEMS]}
    if isinstance(obj, (list, tuple)):
        return [shape_of(v, _depth + 1) for v in list(obj)[:_MAX_SEQ]]
    return type(obj).__name__


def order_id_paths(obj: Any, field: Optional[str] = None) -> List[Dict[str, Any]]:
    """WHERE the venue actually put the order id, as a dotted path.

    This is the exact question that cost a live cancel: the driver read
    `ack[orderId]` while the venue nests it under `ack[result][orderId]`.
    """
    f = field or spec.ORDER_ID_FIELD
    found: List[Dict[str, Any]] = []

    def walk(o: Any, path: str) -> None:
        if isinstance(o, Mapping):
            for k, v in o.items():
                pth = "%s.%s" % (path, k) if path else str(k)
                if str(k) == f:
                    found.append({"path": pth, "value": v,
                                  "type": type(v).__name__})
                walk(v, pth)
        elif isinstance(o, (list, tuple)):
            for i, v in enumerate(o):
                walk(v, "%s[%d]" % (path, i))

    walk(obj, "")
    return found


class RecordingSession:
    """A TRANSPARENT observer around the real session. It adds no behaviour.

    Every safety property still lives where it lived: the arm gate runs inside
    the real session's `request`, and every bound inside the Driver. This only
    writes down what crossed the wire.

    IT MUST NOT CHANGE EXECUTION, so: `request` returns the venue's own object
    unchanged, every exception propagates untouched (the driver's
    UNKNOWN/RECONCILE path depends on seeing them), and a failure to RECORD is
    noted in the row rather than raised.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.wire: List[Dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        if name == "_inner":                       # never recurse pre-__init__
            raise AttributeError(name)
        return getattr(self._inner, name)

    def _record(self, method: str, params: Any, ack: Any, t0: float,
                error: Optional[str] = None) -> None:
        row: Dict[str, Any] = {
            "utc": datetime.now(timezone.utc).isoformat(),
            "method": method, "elapsed_s": round(time.time() - t0, 4)}
        try:
            row["params"] = sanitize(params)
            row["ack"] = None if ack is None else sanitize(ack)
            row["ack_types"] = None if ack is None else shape_of(ack)
            if error is not None:
                row["error"] = error
        except Exception as exc:                               # noqa: BLE001
            row["record_error"] = "%s: %s" % (type(exc).__name__, exc)
        self.wire.append(row)

    async def request(self, method: str, params: Any, **kw: Any) -> Any:
        t0 = time.time()
        try:
            ack = await self._inner.request(method, params, **kw)
        except BaseException as exc:                           # noqa: BLE001
            self._record(method, params, None, t0,
                         error="%s: %s" % (type(exc).__name__, exc))
            raise
        self._record(method, params, ack, t0)
        return ack


def observed_place_acks(wire: Sequence[Mapping[str, Any]]
                        ) -> List[Dict[str, Any]]:
    """The `order.place` acknowledgements, accepted or not, as OBSERVED."""
    out: List[Dict[str, Any]] = []
    for row in wire:
        if row.get("method") != spec.WS_PLACE_METHOD:
            continue
        ack = row.get("ack")
        status = ack.get("status") if isinstance(ack, Mapping) else None
        out.append({
            "utc": row.get("utc"),
            "status": status,
            "accepted": status == spec.WS_ACK_OK,
            "client_order_id": (row.get("params") or {}).get(
                "clientOrderId") if isinstance(row.get("params"), Mapping)
            else None,
            "order_id_paths": order_id_paths(ack) if ack is not None else [],
            "ack": ack,
            "ack_types": row.get("ack_types"),
            "error": row.get("error"),
            "evidence": ("OBSERVED: this is the venue's own reply, recorded at "
                         "the socket seam" if ack is not None else
                         "NO ACK: the request did not return a reply"),
        })
    return out


# ------------------------------------------------------------ DECISION CAPTURE
# WHAT WAS MISSING. The research question CONTINUOUS-5 exists to answer is
# whether a real fill changes the L5 ACTION: OPEN -> COMPLETE. The driver's
# report recorded `_plan_summary(out["plan"])` -- four integers -- and dropped
# `out["turn"]`, which is where the classification lives. `WindowGuard.record`,
# which would have logged each decided action, has no caller in the live path.
# So the one field the experiment turns on was not written down anywhere, and
# the reasons a cycle did nothing (OSCILLATOR, ENVELOPE with its literal
# `would_breach` string, END_OF_WINDOW) were lost entirely.
#
# WHY IT IS CAPTURED HERE. `decide()` already RETURNS the turn. Recording an
# already-returned value at the seam this file owns leaves the authorized
# driver byte-for-byte what was reviewed -- executable_sha and envelope_sha do
# not move -- which is the same resolution that captured the place acks.
def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _spread(bid: Any, ask: Any) -> Optional[str]:
    b, a = _dec(bid), _dec(ask)
    return None if (b is None or a is None) else str(a - b)


def presented_book(ms: Any) -> Dict[str, Any]:
    """The book the CONTROLLER decided on, as it decided on it.

    Not a re-read and not a reconstruction: this is the `state_from_venue`
    object the driver handed to `WindowGuard.step`, which is the only state
    any rung in this cycle can have come from.

    Unreadable is reported as unreadable. `state_from_venue` returns
    `state_id == "unreadable"` with `inventory_known False` when the venue's
    book is missing or fails its own complement-identity check, and the
    driver then quotes nothing -- so "no book" is a real and reportable
    outcome, never an empty one.
    """
    if not isinstance(ms, Mapping):
        return {"present": False,
                "why": ("the driver returned no market state for this "
                        "decision; it returned before building one")}
    b = ms.get("book") if isinstance(ms.get("book"), Mapping) else {}
    out: Dict[str, Any] = {
        "present": True,
        "t_utc": ms.get("t_utc"),
        "tte_s": ms.get("tte_s"),
        "state_id": ms.get("state_id"),
        "inventory_known": ms.get("inventory_known"),
        "source": b.get("source"),
        "readable": bool(b.get("readable")),
        "identity_consistent": b.get("identity_consistent"),
        "complement_identity": sanitize(b.get("complement_identity")),
    }
    # THE STATE'S VERDICT WINS OVER THE RAW READ'S. A crossed book parses
    # perfectly -- the venue served it, `readable` is True and both sides
    # have prices -- and `state_from_venue` still refuses it, because a bid
    # at or above its own ask is not a book we can quote into. Soak #2 showed
    # 20 of 87 cycles in that state, correctly quoting nothing, while this
    # record said `readable: True` beside a -0.11 spread. A later reader
    # scanning `readable` would have concluded the controller had a usable
    # book. The state's own verdict is authoritative here.
    if ms.get("inventory_known") is False or ms.get("state_id") == "unreadable":
        out["readable"] = False
        out["raw_book_parsed"] = bool(b.get("readable"))
        # THE VENUE'S OWN REASON WINS. "the contract does not publish both
        # outcomes' prices" says more than "book not readable", and the
        # state's generic verdict is carried alongside rather than over it.
        out["why"] = (b.get("why") or ms.get("unreadable_why")
                      or "the venue's book was refused by state_from_venue")
        out["state_verdict_why"] = ms.get("unreadable_why")
        out["refused_prices"] = {
            "yes": {"bid": (b.get("yes") or {}).get("bid"),
                    "ask": (b.get("yes") or {}).get("ask")},
            "no": {"bid": (b.get("no") or {}).get("bid"),
                   "ask": (b.get("no") or {}).get("ask")},
            "why_kept": ("the numbers the venue published are kept so the "
                         "refusal can be audited; they are NOT a book and no "
                         "spread is derived from them")}
        return out
    if not out["readable"] or not b.get("identity_consistent"):
        out["why"] = (b.get("why")
                      or "the venue's book failed its complement-identity "
                         "check; nothing was quoted against it")
        return out
    yes, no = b.get("yes") or {}, b.get("no") or {}
    outcomes = ms.get("outcomes") or {}
    out.update({
        "yes": {"bid": yes.get("bid"), "ask": yes.get("ask"),
                "spread": _spread(yes.get("bid"), yes.get("ask")),
                "qty": None},
        "no": {"bid": no.get("bid"), "ask": no.get("ask"),
               "spread": _spread(no.get("bid"), no.get("ask")),
               "qty": None},
        "qty_why": ("this source publishes PRICES ONLY -- the contract object "
                    "carries no depth. Size is reported under `depth`, from a "
                    "separate read, and is NOT inferred here"),
        "passive_pair_cost": b.get("passive_pair_cost"),
        "crossing_pair_cost": b.get("crossing_pair_cost"),
        "last_trade_price": b.get("last_trade_price"),
        "tick": ((outcomes.get("Up") or {}).get("tick")),
    })
    return out


def book_agreement(presented: Mapping[str, Any],
                   depth: Any) -> Dict[str, Any]:
    """Do the two independent reads describe the same touch?

    CHECKED PER CYCLE, NEVER ASSUMED. The contract ladder and the contract
    object are different endpoints with different clocks, and the claim that
    `bids[0]`/`asks[0]` ARE the YES bid/ask is exactly the kind of mapping
    that deserves to be asserted every time rather than once in a comment. A
    disagreement is reported; it is not smoothed and it stops nothing, because
    this is evidence and the decision has already been made.
    """
    if not isinstance(depth, Mapping) or not depth.get("readable"):
        return {"checked": False,
                "why": "no readable depth read to compare against"}
    if not presented.get("readable"):
        return {"checked": False,
                "why": "no readable presented book to compare against"}
    d_bid = (depth.get("bids") or {}).get("best")
    d_ask = (depth.get("asks") or {}).get("best")
    p_bid = (presented.get("yes") or {}).get("bid")
    p_ask = (presented.get("yes") or {}).get("ask")
    bid_ok = (_dec(d_bid) is not None and _dec(d_bid) == _dec(p_bid))
    ask_ok = (_dec(d_ask) is not None and _dec(d_ask) == _dec(p_ask))
    return {"checked": True, "bid_agrees": bid_ok, "ask_agrees": ask_ok,
            "agrees": bool(bid_ok and ask_ok),
            "ladder_best_bid": d_bid, "presented_yes_bid": p_bid,
            "ladder_best_ask": d_ask, "presented_yes_ask": p_ask,
            "labels": ("agreement means the ladder's touch IS the presented "
                       "YES touch, so the ladder's sizes describe the "
                       "presented book. A disagreement means the two reads "
                       "caught different instants and the sizes must not be "
                       "read as the presented book's sizes.")}


def book_state(ms: Any, depth: Any, flow: Any = None) -> Dict[str, Any]:
    """Everything needed to reconstruct WHY a rung was chosen this cycle.

    `flow` was added after the segmented census found the venue's PUBLIC print
    tape to be the binding constraint -- 87.9% of measured adjacent-cycle
    pairs saw zero prints -- and found that not one live fill could be
    segmented by it, because live windows never recorded the tape. It is a
    public, unauthenticated read taken beside the decision and never inside
    it.
    """
    p = presented_book(ms)
    return {"presented": p, "depth": sanitize(depth),
            "flow": sanitize(flow),
            "agreement": book_agreement(p, depth),
            "labels": ("`presented` is the state the controller decided on. "
                       "`depth` and `flow` are separate PUBLIC reads taken "
                       "next to it, for size and for prints. None is derived "
                       "from our own order prices or from ticks_behind_bid.")}


#
# EVIDENCE ONLY. Each wrapper makes the SAME call with the SAME arguments,
# returns the SAME object, propagates every exception untouched, and adds no
# decision and no mutation of its own. A failure to RECORD is written into the
# row instead of raised, because evidence capture must never be able to change
# what the window does.


class DecisionObserver:
    """Transparent per-cycle recorder for the continuous window.

    It wraps three seams and changes none of them:

      `refresh`        this file's own authoritative re-read -- gives the
                       newly reconciled fills and the read that precedes a
                       place;
      `driver.decide`  the decision itself -- inventory it saw, the desired
                       rungs with their OPEN/EXTEND/COMPLETE labels, every
                       HOLD with its literal reason, and the resulting plan;
      `driver.place` / `driver.cancel`
                       the inventory in force AT the mutation and what the
                       call returned.

    Nothing here holds a credential, opens a socket or builds a frame. The
    mutation still happens inside the driver, under the driver's own bounds.
    """

    def __init__(self, depth_fn: Optional[Callable[[], Any]] = None,
                 flow_fn: Optional[Callable[[], Any]] = None) -> None:
        self.cycles: List[Dict[str, Any]] = []
        self.reads: List[Dict[str, Any]] = []
        self.mutations: List[Dict[str, Any]] = []
        self.decide_calls = 0
        self.errors: List[str] = []
        self._prev_fills: Optional[List[Dict[str, Any]]] = None
        self._prev_order_ids: set = set()
        #: OPTIONAL public size read, taken beside each decision. None means
        #: no depth is captured, which is a smaller record and never a
        #: different window: nothing the driver does can depend on it.
        self.depth_fn = depth_fn
        #: OPTIONAL public PRINT TAPE read, taken beside each decision. Same
        #: contract as `depth_fn`: None means a smaller record, never a
        #: different window.
        self.flow_fn = flow_fn

    # ------------------------------------------------------- safe accessors
    # Every one of these is wrapped: a recorder that can raise is a recorder
    # that can stop a live window, which is worse than no recorder at all.

    def _safe(self, fn: Callable, default: Any = None) -> Any:
        try:
            return fn()
        except Exception as exc:                               # noqa: BLE001
            self.errors.append("%s: %s" % (type(exc).__name__, exc))
            return default

    def _inventory(self, driver: Any) -> Optional[Dict[str, Any]]:
        return self._safe(
            lambda: sanitize(driver.inv.report()) if driver.inv else None)

    def _working(self, driver: Any) -> Optional[List[Dict[str, Any]]]:
        return self._safe(lambda: sanitize(driver.working.rungs()))

    def _budget(self, driver: Any) -> Dict[str, Any]:
        return self._safe(lambda: {
            "consumed": driver.budget_consumed,
            "remaining": driver.budget_left(),
            "cohort_budget": getattr(driver, "_max_actions", None)}, {})

    def _depth(self) -> Any:
        """A size read taken next to the decision, and never in its way.

        Wrapped like every other accessor: a recorder that can raise is a
        recorder that can stop a live window. The elapsed time is measured and
        kept, because the cost of this read comes out of maker resting time
        and an unmeasured cost is one nobody can weigh.
        """
        if self.depth_fn is None:
            return {"captured": False,
                    "why": "no depth reader was configured for this run"}
        t0 = time.time()
        got = self._safe(self.depth_fn, None)
        if got is None:
            return {"captured": True, "readable": False,
                    "elapsed_s": round(time.time() - t0, 4),
                    "why": "the depth reader raised; see record_errors"}
        if isinstance(got, dict):
            got = dict(got)
            got["elapsed_s"] = round(time.time() - t0, 4)
            got["captured"] = True
        return got

    def _flow(self) -> Any:
        """The PUBLIC print tape, taken next to the decision and never in its
        way. Identical contract to `_depth`, including the measured cost: this
        read comes out of maker resting time too."""
        if self.flow_fn is None:
            return {"captured": False,
                    "why": "no flow reader was configured for this run"}
        t0 = time.time()
        got = self._safe(self.flow_fn, None)
        if got is None:
            return {"captured": True, "readable": False,
                    "elapsed_s": round(time.time() - t0, 4),
                    "why": "the flow reader raised; see record_errors"}
        if isinstance(got, dict):
            got = dict(got)
            got["elapsed_s"] = round(time.time() - t0, 4)
            got["captured"] = True
        return got

    # ------------------------------------------------------------- recorders

    def _record_read(self, driver: Any, st: Any,
                     inventory_before: Optional[Dict[str, Any]]) -> None:
        row: Dict[str, Any] = {
            "read_seq": len(self.reads) + 1,
            "utc": datetime.now(timezone.utc).isoformat(),
            "decide_calls_so_far": self.decide_calls,
            "inventory_before_this_read": inventory_before,
        }
        try:
            fills = (st or {}).get("fills")
            seen = (st or {}).get("orders_seen")
            openo = (st or {}).get("open_orders")
            # UNREADABLE IS NOT EMPTY, and the row must say which it was:
            # None means the read failed, [] means the venue said nothing.
            row["fills_readable"] = fills is not None
            row["open_orders_readable"] = openo is not None
            row["fills"] = None if fills is None else sanitize(fills)
            row["n_fills"] = None if fills is None else len(fills)
            row["open_order_ids"] = None if openo is None else sanitize(
                [o.get("clientOrderId") or o.get("client_order_id")
                 for o in openo])
            ids = set()
            if isinstance(seen, list):
                ids = {str(o.get("orderId") or o.get("order_id"))
                       for o in seen if isinstance(o, Mapping)}
            row["new_fill_order_ids"] = sorted(ids - self._prev_order_ids)
            row["new_fills"] = self._new_fills(fills)
            if fills is not None:
                self._prev_fills = list(fills)
                self._prev_order_ids |= ids
        except Exception as exc:                               # noqa: BLE001
            row["record_error"] = "%s: %s" % (type(exc).__name__, exc)
        self.reads.append(row)

    def _new_fills(self, fills: Any) -> Optional[List[Dict[str, Any]]]:
        """Fills present now that were not present at the previous read.

        A MULTISET difference, not a set one: two identical 0.01 @ 0.14 fills
        are two fills, and collapsing them would hide exactly the second fill
        this experiment is watching for.
        """
        if fills is None:
            return None
        if self._prev_fills is None:
            return sanitize(list(fills))
        rest = [dict(f) for f in self._prev_fills]
        new = []
        for f in fills:
            match = next((i for i, p in enumerate(rest) if p == f), None)
            if match is None:
                new.append(f)
            else:
                rest.pop(match)
        return sanitize(new)

    def _record_decision(self, driver: Any, pre: Dict[str, Any],
                         out: Any, depth: Any = None,
                         flow: Any = None) -> None:
        row = dict(pre)
        # OUTSIDE the try below, so a book that cannot be summarised still
        # leaves a row saying so rather than vanishing into record_error.
        row["book_state"] = self._safe(
            lambda: book_state((out or {}).get("market_state"), depth, flow),
            {"presented": {"present": False,
                           "why": "book_state raised; see record_errors"}})
        try:
            turn = (out or {}).get("turn") or {}
            plan = (out or {}).get("plan") or {}
            actions = list(turn.get("actions") or [])
            holds = list(turn.get("holds") or [])
            row.update({
                "state": (out or {}).get("state"),
                "window_phase": ((out or {}).get("window_phase")
                                 or turn.get("window_phase")),
                "why": (out or {}).get("why"),
                # THE FIELD THE EXPERIMENT TURNS ON. Each entry carries the
                # leg, the price, the quantity and the L5 label.
                "actions": sanitize(actions),
                "classification": [str(a.get("action")) for a in actions],
                # and every rung that was NOT acted on, with its literal
                # reason: OSCILLATOR, ENVELOPE (the would_breach string) or
                # END_OF_WINDOW.
                "holds": sanitize(holds),
                "held_by": [str(h.get("held_by")) for h in holds],
                "plan": {
                    "keep": sanitize([k.get("order") for k in
                                      (plan.get("keep") or [])]),
                    "place": sanitize([p.get("rung") for p in
                                       (plan.get("place") or [])]),
                    "cancel": sanitize([c.get("order") for c in
                                        (plan.get("cancel") or [])]),
                    "mutations": plan.get("mutations"),
                },
                "turn": {k: sanitize(turn.get(k)) for k in
                         ("actions_remaining", "probe_state", "stop",
                          "why_no_surface", "blocked_by")},
                "budget_after_decision": self._budget(driver),
            })
        except Exception as exc:                               # noqa: BLE001
            row["record_error"] = "%s: %s" % (type(exc).__name__, exc)
        self.cycles.append(row)

    def _record_mutation(self, kind: str, driver: Any, target: Any,
                         at_call: Dict[str, Any], result: Any,
                         error: Optional[str]) -> None:
        """`at_call` is taken BEFORE the inner call and passed in.

        It has to be. The driver spends the slot before the frame goes out,
        so a budget read after the call is the budget AFTER the spend, and
        the inventory can move under a fill. Both readings are kept: the one
        the mutation was made against, and the one it left behind.
        """
        row: Dict[str, Any] = {
            "mutation_seq": len(self.mutations) + 1,
            "utc": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "cycle": self.decide_calls,
        }
        try:
            row["target"] = sanitize(target)
            row.update(at_call)
            row["budget_after_call"] = self._budget(driver)
            row["result"] = None if result is None else sanitize(result)
            if error is not None:
                row["error"] = error
        except Exception as exc:                               # noqa: BLE001
            row["record_error"] = "%s: %s" % (type(exc).__name__, exc)
        self.mutations.append(row)

    def _at_call(self, driver: Any) -> Dict[str, Any]:
        """THE STATE IN FORCE AT THE MUTATION -- not at the decision.

        A post-cancel refresh sits between the two, and CONTINUOUS-4's $1.06
        pair is exactly what a stale answer to this question looks like.
        """
        return {"inventory_at_call": self._inventory(driver),
                "budget_at_call": self._budget(driver)}

    # ------------------------------------------------------------- the seams

    def attach(self, driver: Any, refresh_fn: Callable) -> Callable:
        """Install the wrappers and return the observed `refresh`.

        The inner callables are bound BEFORE the attributes are replaced, so
        each wrapper calls the driver's own original method exactly once.
        """
        inner_decide = driver.decide
        inner_place = driver.place
        inner_cancel = driver.cancel

        async def refresh(*a: Any, **kw: Any) -> Any:
            before = self._inventory(driver)
            st = await refresh_fn(*a, **kw)
            self._record_read(driver, st, before)
            return st

        def decide(*a: Any, **kw: Any) -> Any:
            self.decide_calls += 1
            pre = {
                "cycle": self.decide_calls,
                "utc": datetime.now(timezone.utc).isoformat(),
                "tte_s": a[0] if a else kw.get("tte_s"),
                "inventory_before_decision": self._inventory(driver),
                "working_orders_before": self._working(driver),
                "budget_before_decision": self._budget(driver),
            }
            # BEFORE the decision, so the size read describes the book the
            # decision is about to be made against -- and OUTSIDE it, because
            # it is not passed in and the driver cannot see it.
            depth = self._depth()
            flow = self._flow()
            out = inner_decide(*a, **kw)
            self._record_decision(driver, pre, out, depth, flow)
            return out

        async def place(*a: Any, **kw: Any) -> Any:
            target = a[1] if len(a) > 1 else kw.get("rung")
            at = self._at_call(driver)
            try:
                res = await inner_place(*a, **kw)
            except BaseException as exc:                       # noqa: BLE001
                self._record_mutation("PLACE", driver, target, at, None,
                                      "%s: %s" % (type(exc).__name__, exc))
                raise
            self._record_mutation("PLACE", driver, target, at, res, None)
            return res

        async def cancel(*a: Any, **kw: Any) -> Any:
            target = a[1] if len(a) > 1 else kw.get("order")
            at = self._at_call(driver)
            try:
                res = await inner_cancel(*a, **kw)
            except BaseException as exc:                       # noqa: BLE001
                self._record_mutation("CANCEL", driver, target, at, None,
                                      "%s: %s" % (type(exc).__name__, exc))
                raise
            self._record_mutation("CANCEL", driver, target, at, res, None)
            return res

        driver.decide = decide
        driver.place = place
        driver.cancel = cancel
        return refresh

    def link_refreshes(self, refreshes: Any) -> Dict[str, Any]:
        """Copy the driver's OWN `when` label onto each observed read.

        The phase could be inferred from call order, but the driver already
        states it. Joining the authoritative label beats inferring one, and
        when the two lists do not line up that is itself recorded rather than
        papered over with a guess.
        """
        rows = list(refreshes or [])
        # ONE REFRESH IS NO LONGER ONE READ. The coherence gate re-reads until
        # the venue explains a disappearance, so a refresh reports how many
        # reads it cost; the terminal withdrawal then reads again after the
        # last refresh. The join consumes exactly that many per refresh and
        # labels whatever is left as cleanup.
        i = 0
        for ref in rows:
            n = int(ref.get("reconcile_attempts") or 1)
            for _ in range(max(1, n)):
                if i >= len(self.reads):
                    return {"linked": False,
                            "why": ("driver refreshes account for more reads "
                                    "than the observer saw (%d); phases left "
                                    "unlabelled rather than guessed"
                                    % len(self.reads))}
                self.reads[i]["when"] = ref.get("when")
                self.reads[i]["driver_cycle"] = ref.get("cycle")
                i += 1
        if i == 0 and self.reads:
            # no refresh to attribute anything to: calling every read
            # "cleanup" would be a guess, which is the one thing this is for
            return {"linked": False,
                    "why": ("observer saw %d read(s) and the driver recorded "
                            "no refresh; phases left unlabelled rather than "
                            "guessed" % len(self.reads))}
        trailing = 0
        for read in self.reads[i:]:
            read["when"] = "terminal_withdrawal"
            read["driver_cycle"] = None
            trailing += 1
        return {"linked": True, "reads": len(self.reads),
                "refreshes": len(rows), "cleanup_reads": trailing,
                "note": ("reads are attributed by each refresh's own "
                         "reconcile_attempts; any read after the last refresh "
                         "belongs to terminal withdrawal")}

    def summary(self) -> Dict[str, Any]:
        labels: Dict[str, int] = {}
        for c in self.cycles:
            for lab in (c.get("classification") or []):
                labels[lab] = labels.get(lab, 0) + 1
            for lab in (c.get("held_by") or []):
                labels["HOLD:" + lab] = labels.get("HOLD:" + lab, 0) + 1
        return {
            "decide_calls": self.decide_calls,
            "cycles_recorded": len(self.cycles),
            "authoritative_reads": len(self.reads),
            "mutations_observed": len(self.mutations),
            "action_labels": labels,
            "book_capture": self.book_capture(),
            "record_errors": self.errors[:20],
            "note": ("EVIDENCE ONLY. Each wrapper makes the same call with "
                     "the same arguments, returns the same object and "
                     "propagates every exception. It adds no decision, no "
                     "mutation and no bound; every limit is still enforced "
                     "inside ws_continuous.Driver. The driver now RETURNS the "
                     "market state it decided on, which moved its "
                     "executable_sha -- an additive key nothing reads back, "
                     "not a change to what it decides."),
        }

    def book_capture(self) -> Dict[str, Any]:
        """Coverage of the book record, so a silent gap is visible HERE.

        A capture that quietly stopped working would otherwise show up as a
        window nobody can explain afterwards -- which is the position
        CONTINUOUS-7 left us in, and the reason this block exists.
        """
        n = len(self.cycles)
        pres = dep = agree = dis = 0
        elapsed: List[float] = []
        why: List[str] = []
        for c in self.cycles:
            bs = c.get("book_state") or {}
            p = bs.get("presented") or {}
            d = bs.get("depth") or {}
            a = bs.get("agreement") or {}
            if p.get("readable"):
                pres += 1
            elif p.get("why"):
                why.append(str(p.get("why")))
            if isinstance(d, Mapping):
                if d.get("readable"):
                    dep += 1
                elif d.get("why"):
                    why.append(str(d.get("why")))
                if isinstance(d.get("elapsed_s"), (int, float)):
                    elapsed.append(float(d["elapsed_s"]))
            if a.get("checked"):
                agree += 1 if a.get("agrees") else 0
                dis += 0 if a.get("agrees") else 1
        return {
            "cycles": n,
            "presented_book_readable": pres,
            "depth_readable": dep,
            "touch_agreements": agree,
            "touch_disagreements": dis,
            "depth_elapsed_s": {
                "n": len(elapsed),
                "total": round(sum(elapsed), 3) if elapsed else 0.0,
                "max": round(max(elapsed), 3) if elapsed else None},
            "unreadable_reasons": sorted(set(why))[:10],
            "labels": ("a cycle with no readable presented book is a cycle "
                       "the controller quoted nothing in; UNREADABLE is "
                       "recorded, never replaced with an empty book."),
        }


def _expiry_epoch(symbol: str) -> Optional[float]:
    t = MD.terms_for(symbol) or {}
    raw = t.get("expiry") or t.get("expiryDate")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(
            str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def tte_of(symbol: str):
    """A live clock read from the venue's own expiry, never a local timer.

    Returns -1.0 when the expiry cannot be read. That is deliberate: the
    Driver treats a TTE under the withdraw floor as WITHDRAWN, so an
    unreadable clock fails closed instead of quoting into an unknown one.
    """
    def _now() -> float:
        exp = _expiry_epoch(symbol)
        if exp is None:
            return -1.0
        return exp - time.time()
    return _now


def eligible_symbol(now: Optional[float] = None,
                    band: Optional[Sequence[float]] = None,
                    cohort: Any = None) -> Dict[str, Any]:
    """The ONE contract this window will be bound to, or a refusal.

    `band` NARROWS the entry window for one experiment and can never widen
    it: it is intersected with the standing [MIN_ENTRY_TTE_S, the cohort's
    window length] bound, so a caller cannot use it to enter a contract the
    normal rule would refuse. Passing None is the standing behaviour.

    `cohort` selects the CONTRACT FAMILY -- the ticker prefix and the minute
    grid. It does not touch the frozen policy's TTE bounds: MIN_ENTRY_TTE_S
    is still built from `NO_NEW_OPEN_EXTEND_TTE_S + STALE_ORDER_S`, which are
    absolute safety margins from expiry and are the same number of seconds on
    every family.
    """
    from src.gemini_execution import contract_cohort as CC
    co = cohort if cohort is not None else CC.DEFAULT
    t = now if now is not None else time.time()
    # the floor is the frozen policy's own margin and never moves; the
    # ceiling is the family's window length, since a contract cannot be
    # entered before it exists. For the 5-minute family this is the standing
    # 300s and nothing changes.
    lo = MIN_ENTRY_TTE_S
    hi = MAX_ENTRY_TTE_S if co.minutes <= 5 else co.max_entry_tte_s
    if band is not None:
        lo, hi = max(lo, float(band[0])), min(hi, float(band[1]))
    tried: List[Dict[str, Any]] = []
    ticker = MD.current_ticker(t, prefix=co.prefix, minutes=co.minutes)
    for step in range(4):
        tk = (ticker if step == 0
              else MD.sibling_ticker(ticker, co.minutes * step))
        if not tk:
            continue
        sym = co.symbol_for(tk)
        terms = MD.terms_for(sym) or {}
        exp = _expiry_epoch(sym)
        tte = None if exp is None else exp - t
        row = {"symbol": sym, "tte_s": tte,
               "contract_status": terms.get("contract_status")
               or terms.get("status"),
               "market_state": terms.get("market_state")
               or terms.get("marketState")}
        if (tte is not None and lo <= tte <= hi
                and row["contract_status"] == "active"
                and row["market_state"] == "open"):
            return {"eligible": True, "chosen": row, "band": [lo, hi],
                    "cohort": co.name, "considered": tried + [row]}
        row["why"] = ("tte %s outside [%s, %s]" % (tte, lo, hi)
                      if tte is None or not (lo <= tte <= hi)
                      else "not active/open")
        tried.append(row)
    return {"eligible": False, "considered": tried, "band": [lo, hi],
            "cohort": co.name,
            "why": "no %s contract is inside the entry band [%s, %s] "
                   "right now" % (co.name, lo, hi)}


#: CONTINUOUS-8 EXPERIMENTAL SCHEDULING -- when to BEGIN, and nothing else.
#: C4 and C7 entered at 129s and 136s and filled nothing across 19 cycles; C5
#: and C6 entered at 282s and 207s and took one fill each. n=4 is a
#: CORRELATION and is deliberately NOT encoded as strategy: no controller,
#: price, size, envelope or pair-cost behaviour knows this number exists. It
#: chooses WHEN the experiment starts, which until now was decided by when the
#: workflow happened to run against the 5-minute grid -- C7 got 136s by
#: accident of the dispatch clock.
DEFAULT_ENTRY_BAND = (240.0, 280.0)
#: one full contract cycle plus margin: from the worst arrival (just under the
#: band) the next band opens ~260s later.
MAX_ENTRY_WAIT_S = 420.0
ENTRY_POLL_S = 5.0


async def await_entry_band(band: Sequence[float], *,
                           max_wait_s: Optional[float] = None,
                           poll_s: float = ENTRY_POLL_S,
                           now_fn: Callable[[], float] = time.time,
                           sleep_fn: Optional[Callable] = None,
                           scan_fn: Optional[Callable] = None,
                           cohort: Any = None) -> Dict[str, Any]:
    """WAIT, read-only, until a contract's TTE is inside the band.

    SENDS NOTHING AND SPENDS NOTHING. This runs BEFORE the preflight, before
    recovery, before the authorization gate and before the latch, so a wait
    that never reaches the band refuses with the authorization still unspent.
    The only thing it does is poll a PUBLIC read and sleep.

    BOUNDED IN TIME AND IN POLLS. A scheduler that can wait forever is a
    scheduler that can hold an armed workflow step open until its timeout,
    which is a worse failure than not entering at all.
    """
    sleep = sleep_fn if sleep_fn is not None else asyncio.sleep
    scan = scan_fn if scan_fn is not None else eligible_symbol
    if max_wait_s is None:
        max_wait_s = (cohort.max_entry_wait_s if cohort is not None
                      else MAX_ENTRY_WAIT_S)
    t0 = now_fn()
    polls = 0
    seen: List[Dict[str, Any]] = []
    while True:
        polls += 1
        sel = (scan(band=band) if cohort is None
               else scan(band=band, cohort=cohort))
        waited = round(now_fn() - t0, 2)
        if sel.get("eligible"):
            return {"in_band": True, "band": [band[0], band[1]],
                    "waited_s": waited, "polls": polls,
                    "entered_tte_s": (sel.get("chosen") or {}).get("tte_s"),
                    "selection": sel, "observed": seen[-6:],
                    "labels": ("EXPERIMENTAL SCHEDULING ONLY: this chose WHEN "
                               "to begin. The controller, prices, sizing and "
                               "every bound are unchanged and know nothing "
                               "about it.")}
        seen.append({"waited_s": waited,
                     "tte_s": [r.get("tte_s") for r in
                               (sel.get("considered") or [])][:4]})
        if waited + poll_s > max_wait_s:
            return {"in_band": False, "band": [band[0], band[1]],
                    "waited_s": waited, "polls": polls, "selection": sel,
                    "observed": seen[-6:],
                    "why": ("no contract reached the entry band "
                            "[%s, %s] within %ss; refusing BEFORE the latch "
                            "rather than entering a window the experiment did "
                            "not ask for" % (band[0], band[1], max_wait_s))}
        await sleep(poll_s)


def parse_entry_band(raw: Any) -> Optional[Sequence[float]]:
    """"240:280" -> (240.0, 280.0). None or empty means no scheduling."""
    if raw in (None, "", "none", "off"):
        return None
    lo, _, hi = str(raw).partition(":")
    lo_f, hi_f = float(lo), float(hi)
    if not (0 < lo_f < hi_f):
        raise ValueError("entry band must be LO:HI with 0 < LO < HI")
    return (lo_f, hi_f)


def _legs_from_orders(orders: Sequence[Mapping[str, Any]]
                      ) -> List[Dict[str, Any]]:
    """Filled venue orders -> the fill shape `CP.recover` consumes."""
    out = []
    for o in orders:
        q = o.get("filledQuantity") or o.get("filled_quantity")
        if q in (None, "", "0"):
            continue
        out.append({"leg": ("YES" if str(o.get("outcome", "")).lower() == "yes"
                            else "NO"),
                    "qty": q, "price": o.get("price")})
    return out


async def authoritative_state(symbol: str) -> Dict[str, Any]:
    """Fills, open orders and the order count for this symbol.

    Each value is None when its read did not succeed. None is UNREADABLE and
    stops the probe inside the Driver; it is never turned into an empty list
    here, because that is the substitution that makes a restart look flat.
    """
    from src.gemini_execution import transport as T

    out: Dict[str, Any] = {"symbol": symbol}
    try:
        # include_rows=True IS LOAD-BEARING. Without it order_history returns
        # counts and samples but never the row list, so this extraction read a
        # key that does not exist and produced [] on every call -- fills always
        # empty, orders_seen always empty, and therefore "a restart never
        # assumes flat" quietly untrue and the venue half of
        # reconstruct_budget vacuous. Found 2026-09-18 while reconciling
        # CONTINUOUS-2 slot 2.
        hist = await MD.order_history(symbol=symbol, statuses=("filled",),
                                      limit=100, include_rows=True)
        rows = None
        if hist.get("readable") is not False:
            per = hist.get("per_status") or []
            rows = []
            extractable = False
            for p in per:
                if isinstance(p.get("orders"), list):
                    extractable = True
                    rows.extend(p["orders"])
            # readable BUT not extractable is UNREADABLE, never empty.
            if not extractable and (hist.get("total_rows") or 0) > 0:
                rows = None
        out["fills"] = None if rows is None else _legs_from_orders(rows)
        out["orders_seen"] = None if rows is None else rows
        out["order_history_raw"] = {k: hist.get(k) for k in
                                    ("readable", "verdict", "total_rows")}
    except Exception as exc:                                   # noqa: BLE001
        out["fills"] = None
        out["orders_seen"] = None
        out["order_history_error"] = "%s: %s" % (type(exc).__name__, exc)

    # TERMINAL ROWS AND TRADES. CONTINUOUS-6 showed the two halves of this
    # read do not share a clock: an order was gone from active_orders 267ms
    # after it filled while order_history(filled) still returned nothing. The
    # coherence gate needs to ask "did it fill, or did it end another way?",
    # so both the terminal statuses and the venue's own trade record are
    # fetched. Best effort: a reader that fails leaves its key None, which is
    # UNREADABLE and never mistaken for "no evidence".
    try:
        # ONLY THE STATUS FILTER THE VENUE ACTUALLY SERVES. Asking for
        # ("cancelled","closed","expired") made the WHOLE read unreadable --
        # `order_history` reports readable:false if ANY requested status
        # fails -- so the gate lost its terminal evidence silently. C6's own
        # reconciliation showed a venue-closed order coming back under the
        # `cancelled` filter with row status "closed", so this one filter
        # covers cancellation and expiry both.
        th = await MD.order_history(symbol=symbol, statuses=("cancelled",),
                                    limit=100, include_rows=True)
        rows = []
        for pstat in (th.get("per_status") or []):
            if isinstance(pstat.get("orders"), list):
                rows.extend(pstat["orders"])
        out["terminal_seen"] = rows if th.get("readable") is not False else None
    except Exception as exc:                                   # noqa: BLE001
        out["terminal_seen"] = None
        out["terminal_history_error"] = "%s: %s" % (type(exc).__name__, exc)

    # /v1/mytrades is the venue's OWN trade record and a DIFFERENT read from
    # order history, which is the point: when the two halves disagree, a
    # third authority is what resolves it. Its rows carry order_id and
    # client_order_id, so a fill can be matched to the order that caused it.
    try:
        from src.gemini_prediction_auth import private_rest_request
        resp = await private_rest_request(
            "POST", "/v1/mytrades", timeout=15.0,
            params={"symbol": symbol, "limit_trades": 200})
        body = resp.json()
        out["trades"] = body if isinstance(body, list) else None
    except Exception as exc:                                   # noqa: BLE001
        out["trades"] = None
        out["trades_error"] = "%s: %s" % (type(exc).__name__, exc)

    try:
        act = await T.active_orders(symbol)
        out["open_orders"] = (T._orders_of(act) if act.get("ok") else None)
        out["active_raw"] = {k: act.get(k) for k in
                             ("ok", "status", "request_shape")}
    except Exception as exc:                                   # noqa: BLE001
        out["open_orders"] = None
        out["active_error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


async def main_async(auth_id: str, *, state_dir: str, ledger_path: str,
                     out_path: str,
                     wire_path: Optional[str] = None,
                     cohort_id: Optional[str] = None,
                     entry_band: Optional[Sequence[float]] = None,
                     envelope: "LE.Envelope" = LE.DEFAULT,
                     family: Any = None,
                     probe_spec: Any = None,
                     readiness: bool = False,
                     expect_spec_sha: Optional[str] = None
                     ) -> Dict[str, Any]:
    """Always writes its report, however it exits.

    EVERY REFUSAL PATH RETURNS EARLY, and the artifact write used to sit after
    the window -- so the runs worth recording, the ones that refused and sent
    nothing, wrote no artifact at all. That is the same mistake as the evidence
    commit that died on a missing pathspec: the record of a refusal is the
    record most worth keeping.
    """
    rep: Dict[str, Any] = {}
    try:
        rep = await _build(auth_id, state_dir=state_dir, family=family,
                           probe_spec=probe_spec, readiness=readiness,
                           expect_spec_sha=expect_spec_sha,
                           ledger_path=ledger_path, wire_path=wire_path,
                           cohort_id=cohort_id, entry_band=entry_band,
                           envelope=envelope)
        return rep
    finally:
        rep.setdefault("finished_utc",
                       datetime.now(timezone.utc).isoformat())
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, indent=2, default=str)
        except OSError:
            pass


async def _build(auth_id: str, *, state_dir: str, ledger_path: str,
                 wire_path: Optional[str] = None,
                 cohort_id: Optional[str] = None,
                 entry_band: Optional[Sequence[float]] = None,
                 envelope: "LE.Envelope" = LE.DEFAULT,
                 family: Any = None,
                 probe_spec: Any = None,
                 readiness: bool = False,
                 expect_spec_sha: Optional[str] = None
                 ) -> Dict[str, Any]:
    from src.gemini_execution.ws_session import GeminiMakerSession

    rep: Dict[str, Any] = {
        "label": LABEL, "started_utc": datetime.now(timezone.utc).isoformat(),
        "authorization_id": auth_id,
        "driver_executable_sha": WC.executable_sha(),
        # WHICH BOUNDS THIS RUN IS UNDER, in the artifact, so a report can
        # never be read against the wrong envelope after the fact.
        "envelope_name": envelope.name,
        "envelope_sha": envelope.sha(),
        "envelope": envelope.as_dict(),
        "driver_envelope_sha": WC.envelope_sha(),
        "bundle_sha": BUNDLE.bundle_sha(),
        "invoker_note": ("this invoker is NOT covered by the authorized "
                         "executable_sha, which covers the driver. It holds "
                         "no bound of its own; every limit is enforced inside "
                         "ws_continuous.Driver."),
    }

    # THE SPEC HASH INVARIANT. If readiness proved one invocation and live
    # resolves another, they are not the same experiment -- and the whole
    # point of the spec is that they cannot be. Checked BEFORE the latch, so
    # a mismatch spends no authorization.
    rep["readiness_mode"] = bool(readiness)
    # DECLARED AT THE TOP, FAIL-CLOSED. This was only assigned where the
    # session is wrapped, far below -- so every run that returned EARLIER
    # (the authorization gate, a family mismatch, an unreadable position)
    # left it None, and the readiness gate then could not tell "the boundary
    # was blocked" from "the boundary was never reached". Run 35615018494
    # failed on exactly that. The value is re-stated at the wrap; here it
    # records the mode the process is in from its first line.
    rep["mutation_boundary"] = ("BLOCKED (readiness)" if readiness
                                else "ENABLED")
    rep["probe_spec"] = probe_spec.as_dict() if probe_spec is not None else None
    #: recorded even with no --expect-spec-sha: the READINESS run is the one
    #: that produces this value, and the armed step is the one that asserts
    #: it. A hash that is only printed when it is already known would be
    #: useless for exactly the step that has to establish it.
    rep["spec_sha"] = probe_spec.spec_sha() if probe_spec is not None else None
    if probe_spec is not None and expect_spec_sha:
        got = probe_spec.spec_sha()
        rep["spec_sha_check"] = {"expected": expect_spec_sha, "resolved": got,
                                 "ok": got == str(expect_spec_sha)}
        if got != str(expect_spec_sha):
            rep["state"] = WC.DO_NOT_TRADE
            rep["why"] = ("spec hash mismatch: readiness proved %s but this "
                          "invocation resolves %s. They are not the same "
                          "experiment, so live refuses before the latch."
                          % (expect_spec_sha, got))
            rep["latch"] = {"consumed": False, "spent_by_this_run": False}
            return rep

    # WHICH CONTRACT FAMILY, RESOLVED ONCE AND RECORDED BEFORE ANYTHING ELSE.
    #
    # The selector already existed -- eligible_symbol(cohort=...) and
    # contract_cohort.BTC15M -- but nothing threaded it, so every invocation
    # fell through to CC.DEFAULT (BTC5m). A BTC15m envelope could therefore
    # have been asked for and a BTC05M contract entered: the authorization
    # saying one thing and the wire doing another, which is exactly the
    # failure the q-size work already cost a live grant to find.
    from src.gemini_execution import contract_cohort as CC
    fam = family if family is not None else CC.DEFAULT

    # FAMILY AND ENVELOPE MUST AGREE, AND A MISMATCH IS A REFUSAL, NOT A
    # PREFERENCE. An envelope carries the family it was sized for in
    # `cohort_prefix`; running it against another family would apply bounds
    # nobody derived for that market. Checked here, before the preflight,
    # the latch and any mutation.
    want_prefix = str(getattr(envelope, "cohort_prefix", "") or "")
    got_prefix = "GEMI-" + str(fam.prefix)
    rep["family"] = {"name": fam.name, "prefix": fam.prefix,
                     "minutes": fam.minutes, "resolved_symbol_prefix":
                     got_prefix, "explicit": family is not None}
    rep["envelope_family"] = want_prefix
    if want_prefix and want_prefix != got_prefix:
        rep["state"] = WC.DO_NOT_TRADE
        rep["why"] = ("envelope %s is sized for %s but family %s resolves to "
                      "%s; a bound derived for one market does not transfer "
                      "to another, so this is refused before the latch"
                      % (envelope.name, want_prefix, fam.name, got_prefix))
        rep["latch"] = {"consumed": False, "spent_by_this_run": False}
        return rep

    # EXPERIMENTAL SCHEDULING, FIRST AND READ-ONLY. Before the preflight, the
    # recovery, the authorization gate and the latch -- so a wait that never
    # reaches the band refuses with the authorization STILL UNSPENT, and every
    # read that follows is taken against the contract actually entered rather
    # than one that expired while we waited.
    if entry_band is not None:
        sched = await await_entry_band(entry_band, cohort=fam)
        rep["entry_schedule"] = sched
        sel = sched.get("selection") or {"eligible": False,
                                         "why": sched.get("why")}
        if not sched.get("in_band"):
            rep["state"] = WC.DO_NOT_TRADE
            rep["why"] = sched.get("why")
            rep["latch"] = {"consumed": False,
                            "why": ("the entry band was never reached; "
                                    "refused BEFORE the latch")}
            rep["symbol_selection"] = sel
            return rep
    else:
        rep["entry_schedule"] = {
            "scheduled": False,
            "why": ("no entry band configured; the contract is whichever one "
                    "is eligible at dispatch time")}
        sel = eligible_symbol(cohort=fam)
    rep["symbol_selection"] = sel
    if not sel.get("eligible"):
        rep["state"] = WC.DO_NOT_TRADE
        rep["why"] = sel.get("why")
        return rep
    symbol = sel["chosen"]["symbol"]

    # ------------------------------------------------- PREFLIGHT, THEN LATCH
    # THE READS ARE DONE HERE AND PASSED IN AS VALUES. MD.terms_status is a
    # COROUTINE FUNCTION; the driver's preflight is synchronous and cannot
    # await it, so handing it the function meant handing it a coroutine whose
    # .get() raises -- which read as "terms unreadable", which is a
    # DISCREPANCY, which failed the probe closed on every run. It cost a live
    # authorization to find, because no test had ever passed the real reader.
    ts_value: Dict[str, Any]
    try:
        ts_value = await MD.terms_status() or {}
    except Exception as exc:                                   # noqa: BLE001
        ts_value = {}
        rep["terms_read_error"] = "%s: %s" % (type(exc).__name__, exc)
    readers = {"terms_for": MD.terms_for,
               "terms_status": (lambda: ts_value),
               "outcome_book": MD.outcome_book}
    rep["terms_status"] = ts_value

    # THE PREFLIGHT RUNS BEFORE THE LATCH IS SPENT. A preflight is a READ, not
    # a mutation, so "consume the latch before the first mutation" does not
    # mean "before the first read" -- and consuming it first meant a
    # fail-closed preflight burned the one authorization without an order ever
    # being attempted. That is exactly what happened on 2026-09-18T13:33Z.
    pf = PF.preflight_now(symbol, readers=readers)
    agrees, bad = PF.check_frozen(pf)
    rep["preflight"] = {"agrees": agrees, "discrepancies": bad,
                        "checked_before_latch": True}
    if not agrees:
        rep["state"] = WC.DO_NOT_TRADE
        rep["why"] = ("fresh preflight differs from the frozen assumptions: %s"
                      % json.dumps(bad, default=str))
        rep["latch"] = {"consumed": False,
                        "why": "the preflight refused BEFORE the latch was "
                               "spent, so the authorization is untouched"}
        return rep

    # ------------------------------------- AUTHORITATIVE READ, THEN RECOVERY
    # BOTH BEFORE THE LATCH, AND THAT ORDERING IS THE WHOLE POINT OF THIS
    # BLOCK. CONTINUOUS-3 reached this line with every gate open, spent its
    # authorization, connected, and then refused inside run_window because a
    # CANCEL from CONTINUOUS-2 was still recorded UNKNOWN in the durable
    # ledger. Nothing was ever sent. A refusal that is DETERMINISTIC and
    # READ-ONLY must not cost an authorization, and recovery is exactly that:
    # it reads the ledger and authoritative venue state and cannot mutate
    # either. The preflight was moved here after CONTINUOUS-1 was lost the
    # same way; this is the same lesson one step further down.
    # ------------------------------------------------------------- COHORT
    # A cohort supplies its OWN empty ledger and its own bounded budget, and
    # records the historical ledger's hash so a reset can never be passed off
    # as a fresh start. Without one, the historical ledger is used exactly as
    # before -- which is how every prior window ran, and which now reads
    # 12 consumed of 12.
    cohort_budget: Optional[int] = None
    if cohort_id:
        from src.gemini_execution import cohort as CO
        try:
            co = CO.load(cohort_id)
            pred = CO.verify_predecessor(co)
            rep["cohort"] = CO.state(cohort_id)
            if not pred.get("intact"):
                rep["state"] = WC.DO_NOT_TRADE
                rep["why"] = ("cohort %s was created against a historical "
                              "ledger that has since changed: %s"
                              % (cohort_id, pred.get("why")))
                rep["latch"] = {"consumed": False,
                                "why": "refused before the latch"}
                return rep
            ledger_path = co.ledger_path
            cohort_budget = co.budget
        except Exception as exc:                               # noqa: BLE001
            rep["state"] = WC.DO_NOT_TRADE
            rep["why"] = "cohort %s unusable: %s: %s" % (
                cohort_id, type(exc).__name__, exc)
            rep["latch"] = {"consumed": False,
                            "why": "refused before the latch"}
            return rep

    st = await authoritative_state(symbol)
    rep["authoritative_state"] = {
        "fills": st.get("fills"), "open_orders": st.get("open_orders"),
        "order_history": st.get("order_history_raw"),
        "active": st.get("active_raw"),
        # THE COHERENCE GATE'S OWN EVIDENCE SOURCES, reported so a reader
        # that silently stopped working is visible here rather than as a
        # window that stalls on every disappearance. None is UNREADABLE and
        # is never the same claim as an empty list.
        "terminal_seen": (None if st.get("terminal_seen") is None
                          else len(st["terminal_seen"])),
        "trades": (None if st.get("trades") is None else len(st["trades"])),
        "coherence_readers": {
            "terminal_history_readable": st.get("terminal_seen") is not None,
            "trades_readable": st.get("trades") is not None,
            "terminal_history_error": st.get("terminal_history_error"),
            "trades_error": st.get("trades_error"),
            "why": ("the gate explains a disappearance from these two reads "
                    "plus filled history; if either is unreadable, an "
                    "unexplained order stops the window instead of being "
                    "assumed away")}}

    # A THROWAWAY Driver. recover() writes nothing -- not the ledger, not the
    # venue -- it only populates the instance it is called on, so a separate
    # instance can answer "would recovery succeed?" with no side effect. The
    # window below builds its own Driver and recovers again authoritatively;
    # this one exists solely to refuse before the latch.
    probe = WC.Driver(auth_id, symbol, ledger_path=ledger_path,
                      max_actions=cohort_budget, envelope=envelope)
    pre = probe.recover(venue_fills=st.get("fills"),
                        venue_open=st.get("open_orders"),
                        venue_orders=st.get("orders_seen"))
    rep["ledger_unresolved"] = [
        {k: r.get(k) for k in ("kind", "slot", "auth_id",
                               "client_order_id", "outcome")}
        for r in probe.ledger.unresolved()]
    rep["ledger_consumed_slots"] = probe.ledger.consumed()
    rep["pre_latch_recovery"] = {"checked_before_latch": True,
                                 "checked_before_authorization": True,
                                 **pre}

    # ---- THE AUTHORITATIVE POSITION GATE, BEFORE THE LATCH.
    #
    # Inventory above is rebuilt from FILLS. This asks the venue what it says
    # the account HOLDS, and refuses unless the two agree exactly. It is also
    # the read that answers, from our own account rather than from published
    # schemas, whether Gemini carries one signed position per instrument or
    # independent YES and NO inventories -- `position_ledger` records which
    # shape it saw. Unreadable is a refusal, not a flat account.
    pos = await MD.positions()
    pos_rec = PL.reconcile(probe.inv if probe.inv is not None else CP.Inventory(),
                           pos.get("rows"), symbol)
    rep["position_gate"] = {
        "read": {k: pos.get(k) for k in ("path", "status", "ok", "why",
                                         "total")},
        "n_rows_for_symbol": (None if pos.get("rows") is None
                              else len(pos["rows"])),
        "reconciliation": pos_rec,
    }
    if not pos.get("ok") or not pos_rec.get("tradeable"):
        rep["state"] = WC.DO_NOT_TRADE
        rep["why"] = ("the venue's own position could not be established or "
                      "does not agree with our ledger (%s); nothing is sent"
                      % (pos_rec.get("why") or pos.get("why")))
        return rep
    if probe.state in WC.TERMINAL or not pre.get("recovered"):
        rep["state"] = probe.state
        rep["why"] = (probe.why or pre.get("why")
                      or "recovery refused before the latch")
        rep["latch"] = {"consumed": False,
                        "why": "recovery refused BEFORE the latch was spent, "
                               "so the authorization is untouched. A known "
                               "stale ledger row never costs an "
                               "authorization again."}
        return rep

    # ----------------------------------------------------- AUTHORIZATION
    # THE ORDER IS FIXED AND IT MATTERS:
    #   read-only preflight -> READY -> authorization -> durable latch ->
    #   ledger intent/fsync -> socket mutation
    # EVERY READ HAS NOW HAPPENED AND ALL OF THEM WERE FREE: preflight,
    # authoritative venue state, and recovery. Authorization is validated
    # after them and before the latch, because spending an authorization we
    # were never going to be able to use is pure loss -- and because an
    # UNARMED run can then exercise and REPORT the whole read-only path
    # instead of stopping at a gate before it, which is what makes a
    # production-path verification worth anything.
    auth = WC.authorization_state()
    rep["authorization"] = auth
    if not auth["authorized"]:
        rep["state"] = READY_BUT_UNAUTHORIZED
        rep["why"] = "not authorized: %s" % "; ".join(auth["why_not"])
        rep["latch"] = {"consumed": False,
                        "why": "authorization was refused BEFORE the latch "
                               "was spent; the authorization is untouched"}
        return rep

    rep["bundle"] = BUNDLE.state()
    if not rep["bundle"]["authorized"]:
        rep["state"] = READY_BUT_UNAUTHORIZED
        rep["why"] = ("bundle not authorized: %s"
                      % "; ".join(rep["bundle"]["why_not"]))
        rep["latch"] = {"consumed": False,
                        "why": "the bundle authorization was refused BEFORE "
                               "the latch was spent; the authorization is "
                               "untouched"}
        return rep

    # ---------------------------------------------------------------- LATCH
    # ONE AUTHORIZATION, ONE WINDOW, AND THE LATCH IS WHAT MAKES THAT DURABLE.
    # The driver's MutationLedger bounds mutations WITHIN a window; it cannot
    # stop a SECOND window being run under the same authorization on a fresh
    # runner with a clean disk. This does, and it is consumed BEFORE the first
    # mutation -- before the session is even connected -- because a lost order
    # is preferred to a double order: only one of them is recoverable.
    #
    # It is consumed HERE rather than inside the driver so that the reviewed
    # executable stays byte-for-byte what was authorized. The guarantee is the
    # same: nothing mutates until the latch is durably spent.
    from src.gemini_execution import latch
    from src.gemini_execution.run_canary import (can_make_durable,
                                                 durable_check, make_durable)

    # ASK BEFORE SPENDING. latch.consume() marks the grant spent on disk, and
    # make_durable() pushes it only afterwards -- so a push that CANNOT
    # succeed costs an owner authorization for an infrastructure reason, with
    # nothing traded. That is how OWNER-2026-09-21-GEMINI-Q1-LIVE-1 died.
    # This probe pushes nothing and weakens nothing: the latch is still
    # written, committed and pushed before any mutation. It only moves an
    # unavoidable failure to the side of the line where it is free.
    pushable = can_make_durable()
    rep["latch_pushable"] = pushable
    if not pushable.get("ok"):
        rep["state"] = WC.DO_NOT_TRADE
        rep["why"] = ("the latch could not have been made durable (%s); "
                      "refused BEFORE consuming the authorization, so the "
                      "grant is INTACT and re-dispatchable once the "
                      "infrastructure is fixed" % pushable.get("why"))
        rep["latch"] = {"consumed": False, "spent_by_this_run": False}
        return rep

    # THE LATCH RECORDS THE ENVELOPE THIS RUN IS UNDER, NOT THE SHADOW ONE.
    #
    # This intent is consumed BEFORE the mutation and is the durable, auditable
    # record of what was authorized and attempted. It used to hardcode
    # `CP.ACTION_SIZE` and `CP.MAX_RESIDUAL` -- the 0.01 module constants --
    # whichever envelope was actually in force. LIVE-2 therefore left a latch
    # reading `amount 0.01`, `max_residual 0.04` on a window the owner had
    # authorized at q=1, and the durable record contradicted the order path.
    #
    # That is the same failure `resolve_action_size` refuses in the controller:
    # after the fact, an authorized q=1 run whose record says 0.01 is
    # indistinguishable from one that was never authorized at q=1 at all. The
    # record has to name the bounds that actually held.
    #
    # `max_residual` stays CONTRACT-denominated, as it has always been, so the
    # field keeps its meaning across old and new latches; the dollar bound the
    # money actually runs against is `max_residual_usd`, carried beside it.
    intent = {
        "intent_id": "cont-%s" % datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"),
        "symbol": symbol, "side": "buy", "outcome": "both",
        "price": None, "amount": str(envelope.action_size),
        "max_orders": int(envelope.max_actions),
        "max_residual": str(envelope.max_residual),
        "max_residual_usd": str(envelope.max_residual_usd),
        "max_committed_usd": str(envelope.max_committed_usd),
        "max_working_orders": int(envelope.max_working_orders),
        "envelope_name": envelope.name,
        "maker_only": True, "transport": "WEBSOCKET",
        "executable_sha": WC.executable_sha(),
        "envelope_sha": WC.envelope_sha(),
        "active_envelope_sha": envelope.sha(),
    }
    try:
        rec0 = latch.consume(auth_id, intent, state_dir,
                             durable_check=durable_check)
        make_durable(auth_id, rec0)
        rep["latch"] = {"consumed": True, "intent_id": rec0.get("intent_id"),
                        "state": rec0.get("state"),
                        "when": "BEFORE the session connected"}
    except latch.AlreadyConsumed as exc:
        rep["state"] = WC.DO_NOT_TRADE
        rep["why"] = ("authorization already consumed: %s. This is "
                      "reconciliation work, never a re-run." % exc)
        rep["latch"] = {"consumed": False, "already": True}
        return rep
    except Exception as exc:                                   # noqa: BLE001
        rep["state"] = WC.DO_NOT_TRADE
        rep["why"] = ("the latch could not be made durable (%s: %s); nothing "
                      "is sent, because a mutation whose authorization is not "
                      "durably spent is the one state we cannot reconcile"
                      % (type(exc).__name__, exc))
        rep["latch"] = {"consumed": False, "durable": False}
        return rep

    driver = WC.Driver(auth_id, symbol, ledger_path=ledger_path,
                       max_actions=cohort_budget, envelope=envelope)
    # the observer wraps the real session; the arm gate and every bound
    # still run inside the objects it delegates to.
    session = RecordingSession(GeminiMakerSession(id_prefix="l5cp"))
    # THE ONLY DIFFERENCE BETWEEN READINESS AND LIVE. Everything above this
    # line -- family, envelope, band, cohort, discovery, preflight, recovery,
    # position gate, latch, lifecycle -- is byte-identical. Here and only
    # here does readiness diverge: the boundary records the frame and refuses
    # to send it.
    if readiness:
        from src.gemini_execution import live_readiness as LR
        session = LR.ReadinessSession(session)
        # already declared at the top; restated here so the value and the
        # wrapping that makes it true sit in one place when read.
        rep["mutation_boundary"] = "BLOCKED (readiness)"
    else:
        rep["mutation_boundary"] = "ENABLED"
    # BOUND BEFORE THE WINDOW RUNS, not after it, so a window that dies
    # mid-cycle still leaves every decision it had already made on disk.
    # These are the observer's own lists, so later appends land in `rep`.
    # The size read is bound to THIS window's symbol and nothing else, so the
    # observer cannot be pointed at another contract by accident.
    decisions = DecisionObserver(
        depth_fn=lambda: MD.contract_depth(symbol),
        flow_fn=lambda: HB.public_flow(symbol))
    rep["decisions"] = decisions.cycles
    rep["authoritative_reads"] = decisions.reads
    rep["mutations_observed"] = decisions.mutations
    try:
        await session.connect()
        rep["session"] = "connected"
        # THE AUTHORITATIVE RE-READ THE DRIVER RECONCILES AGAINST. It is the
        # SAME function that produced the pre-latch state, so mid-window truth
        # and startup truth come from one reader with one set of observed
        # shapes -- and `authoritative_state` already returns None rather than
        # [] when a read cannot be extracted, which is what makes the driver's
        # fail-closed branch reachable instead of decorative.
        async def refresh() -> Dict[str, Any]:
            return await authoritative_state(symbol)

        # EVIDENCE ONLY: same decide call, same returned object, same plan,
        # same ordering. `attach` returns the observed refresh; the driver is
        # otherwise untouched and its executable_sha does not move.
        observed_refresh = decisions.attach(driver, refresh)

        out = await driver.run_window(
            session, tte_of=tte_of(symbol),
            venue_fills=st.get("fills"),
            venue_open=st.get("open_orders"),
            venue_orders=st.get("orders_seen"),
            readers=readers,
            refresh_fn=observed_refresh,
            interval_s=5.0)
        rep["window"] = out
        rep["refresh_link"] = decisions.link_refreshes(out.get("refreshes"))
    except Exception as exc:                                   # noqa: BLE001
        rep["window_error"] = "%s: %s" % (type(exc).__name__, exc)
        rep["state"] = driver.state
        rep["why"] = driver.why
    finally:
        # BEFORE close(), and in `finally`, because the whole point is to
        # still have the ack when the window ends badly.
        rep["wire"] = session.wire
        # THE FRAMES THAT WOULD HAVE GONE. Readiness has to be auditable on
        # the ONE thing it proves -- that nothing was sent and that what
        # would have been sent carried the authorized size -- so the boundary
        # publishes both into the report rather than leaving them in memory.
        if readiness:
            rep["readiness_attempted"] = getattr(session, "attempted", None)
            rep["readiness_mutations_sent"] = int(getattr(session, "sent", 0))
        rep["decision_capture"] = decisions.summary()
        rep["place_acks_observed"] = observed_place_acks(session.wire)
        rep["place_ack_note"] = (
            "OBSERVED, not inferred: each ack below is the venue's own "
            "reply recorded at the socket seam, with credential-shaped "
            "values redacted and key names and types preserved. An empty "
            "list means no order.place was ever sent.")
        if wire_path:
            try:
                with open(wire_path, "w", encoding="utf-8") as fh:
                    json.dump({"label": LABEL,
                               "authorization_id": auth_id,
                               "driver_executable_sha": WC.executable_sha(),
                               "wire": session.wire,
                               "place_acks_observed":
                                   rep["place_acks_observed"]},
                              fh, indent=2, default=str)
            except Exception as exc:                           # noqa: BLE001
                rep["wire_write_error"] = "%s: %s" % (
                    type(exc).__name__, exc)
        try:
            await session.close()
        except Exception:                                      # noqa: BLE001
            pass

    # authoritative AFTER-state, for reconciliation
    rep["after"] = await authoritative_state(symbol)
    rep["resolution"] = MD.resolution_for(symbol)
    rep["finished_utc"] = datetime.now(timezone.utc).isoformat()
    return rep


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auth-id", required=True)
    ap.add_argument("--state-dir", default="analysis/gemini/canary_latch")
    ap.add_argument("--ledger", default="analysis/gemini/continuous_ledger.jsonl")
    ap.add_argument("--cohort", default=None,
                    help="mutation cohort id; supplies its own ledger and "
                         "bounded budget and must already exist")
    ap.add_argument("--out", default="analysis/gemini/continuous_run.json")
    ap.add_argument("--wire", default="analysis/gemini/continuous_wire.json")
    ap.add_argument("--spec", default=None,
                    help=("a DECLARED LiveProbeSpec id. When given it is the "
                          "single source of family, envelope, entry band, "
                          "cohort and action size -- readiness and live "
                          "consume the SAME object, so two hand-built "
                          "command lines can no longer disagree."))
    ap.add_argument("--readiness", action="store_true",
                    help=("LIVE_READINESS_GATE mode. The mutation boundary "
                          "is replaced by a recorder that REFUSES; every "
                          "other resolved value is identical to live."))
    ap.add_argument("--expect-spec-sha", default=None,
                    help=("the spec hash readiness proved. Live refuses "
                          "before the latch if the spec it resolves hashes "
                          "to anything else."))
    ap.add_argument("--family", default="btc5m",
                    choices=("btc5m", "btc15m"),
                    help=("which CONTRACT FAMILY the window binds to. "
                          "DEFAULT btc5m, so every existing invocation keeps "
                          "the contract it already picked. btc15m resolves "
                          "explicitly to contract_cohort.BTC15M; there is no "
                          "inference from the envelope or the symbol."))
    ap.add_argument("--envelope", default="shadow_q001",
                    choices=("shadow_q001", "live_q1", "live_q1_btc15m"),
                    help=("which RISK ENVELOPE the driver runs under. The "
                          "default is the 0.01 shadow envelope; `live_q1` is "
                          "the reviewed q=1 manifest and must be asked for "
                          "explicitly. A bigger size is a decision, never a "
                          "default that arrives. `live_q1_btc15m` is the same "
                          "q=1 bounds sized for the 15-minute family and must "
                          "be paired with --family btc15m; a mismatch is "
                          "refused rather than reconciled."))
    ap.add_argument("--entry-tte-band", default=None,
                    help=("LO:HI seconds-to-expiry the window must BEGIN "
                          "inside, e.g. 240:280. Omitted, the contract is "
                          "whichever is eligible at dispatch time -- which is "
                          "how CONTINUOUS-7 got 136s. This narrows the "
                          "standing entry band and can never widen it."))
    a = ap.parse_args(argv)
    # THE SPEC IS THE SOURCE WHEN GIVEN. Hand-built flags remain for the
    # unarmed/diagnostic paths, but a spec overrides every one of them so
    # readiness and live cannot be assembled differently.
    spec = None
    if a.spec:
        from src.gemini_execution import live_probe_spec as SPEC
        spec = SPEC.get(a.spec)
        a.family = spec.family
        a.envelope = spec.envelope
        a.entry_tte_band = spec.entry_tte_band
        if spec.budget_cohort:
            a.cohort = spec.budget_cohort

    # EXACT MAPPING, NO FALLBACK. A dict lookup raises KeyError on anything
    # unknown rather than quietly yielding a smaller envelope -- the live_q1
    # name must never resolve to the shadow one, and live_q1_btc15m must
    # never resolve to live_q1.
    env = {"shadow_q001": LE.SHADOW_Q001,
           "live_q1": LE.LIVE_Q1,
           "live_q1_btc15m": LE.LIVE_Q1_BTC15M}[a.envelope]
    from src.gemini_execution import contract_cohort as CC
    fam = {"btc5m": CC.BTC5M, "btc15m": CC.BTC15M}[a.family]
    rep = asyncio.run(main_async(a.auth_id, state_dir=a.state_dir,
                                 ledger_path=a.ledger, out_path=a.out,
                                 wire_path=a.wire, cohort_id=a.cohort,
                                 entry_band=parse_entry_band(
                                     a.entry_tte_band),
                                 envelope=env, family=fam,
                                 probe_spec=spec, readiness=a.readiness,
                                 expect_spec_sha=a.expect_spec_sha))
    print("GEMINI_CONTINUOUS " + json.dumps(
        {k: rep.get(k) for k in ("state", "why", "symbol_selection",
                                 "family", "envelope_name", "envelope_family",
                                 "entry_schedule", "readiness_mode",
                                 "mutation_boundary", "spec_sha",
                                 "spec_sha_check", "terms_status",
                                 "driver_executable_sha")}, default=str),
          flush=True)
    w = rep.get("window") or {}
    print("GEMINI_CONTINUOUS_PLACE_ACKS " + json.dumps(
        rep.get("place_acks_observed") or [], default=str)[:6000],
          flush=True)
    print("GEMINI_CONTINUOUS_WINDOW " + json.dumps(
        {k: w.get(k) for k in ("state", "why", "cycles", "budget_consumed",
                               "budget_left", "inventory", "turns")},
        default=str)[:6000], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
