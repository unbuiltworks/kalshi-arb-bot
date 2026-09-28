"""TRANSLATION of the frozen PROFIT_LOCK_5 policy into the reviewed executor.

A TRANSLATION, not a reinterpretation. Nothing here decides economics. The floor
comes from the frozen scorer, the completion economics come from the frozen
`taker_completion_ok`, the submission path comes from the reviewed micro executor,
and the money bound comes from the mandate. This module only *sequences* them and
records what happened.

EXACTLY ONE RISK/SIZING AUTHORITY: the reviewed mandate/GuardedTransport boundary.

    RAMP_LADDER_001 REGRESSION RATIONALE. On 2026-09-16 the R1 canary was
    authorized for one market at $0.05 and committed $19.3295 to one of eleven,
    because `pmus_l5_ramp_ladder.active()` auto-loaded three ten-day-old owner
    rungs and silently replaced the live limits -- per-market $0.05 -> $7.50,
    daily stop -$1.00 -> -$15.00. A second, dynamic, rung-based sizing authority
    is that incident.

    `pmus_micro_executor` is therefore NOT imported here. It defines
    `per_market_first_leg_cap()`, which lazily imports that ladder, and importing
    the module would put a rung-based cap in this artifact's reachable capability
    graph. Omitting it from a Dockerfile is not enough -- reachability is a
    property of the import graph, not of a COPY list.

    The primitive is composed WITHOUT it, at no cost in fidelity: `_body` in that
    module is a one-line delegation to `l5_order_body.build`, PROVEN byte-identical
    across 9 prices x 8 quantities x 2 sides x post_only by
    tests/test_r1_body_delegation_equivalence.py, and the completion economics
    (`taker_completion_ok`, `taker_fee`) live in `pmus_q1_executor`, which has no
    ladder dependency at all. `pmus_micro_executor` is not modified, so every
    other user of it is unaffected.

THE THREE RULES THAT DEFINE IT

1. THE FLOOR IS CONSUMED, NEVER RECOMPUTED.
   `pmus_completion_shadow.settlement_independent_floor` is imported and called.
   There is no second implementation here, and a test asserts this file contains
   no arithmetic resembling one. The threshold is exactly $5.00 and is compared
   BEFORE any rebate: `LOCK_USD` is a module constant, not a parameter, so there
   is no knob to turn at the call site.

2. ACK IS NOT FILL.
   `submit()` returns an ACK and changes NO inventory. Inventory and cash move
   only in `apply_authoritative_fill()`, which requires a venue-sourced fill or a
   reconciliation. Probe 04 filled two milliseconds after an ACK that said
   `filled_qty 0`; the ACK is a receipt.

3. THE MANDATE IS ABOVE STRATEGY INTENT.
   Every money-moving body goes through `mandate.admit`. A refusal is final:
   `LOCK` cannot override it, a completion the frozen policy permits cannot
   override it, and there is no path to the transport that skips it.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from src import l5_order_body as OB
from src import pmus_completion_shadow as SHADOW
from src import pmus_live_mandate as MANDATE
from src import pmus_probe_observability as OBS
from src import pmus_q1_executor as Q1

#: the frozen business threshold, BEFORE expected rebates or rewards.
#: A constant and not a parameter, so no call site can soften it.
LOCK_USD = D("5.00")

#: the single frozen floor implementation, bound once so the indirection is
#: visible and a copy would be obvious in review
settlement_independent_floor = SHADOW.settlement_independent_floor


class SafetyRefusal(RuntimeError):
    """The mandate refused. Strategy intent does not get a second opinion."""

    def __init__(self, verdict: Mapping[str, Any]) -> None:
        super().__init__(str(verdict.get("reason") or "REFUSED"))
        self.verdict = dict(verdict)


class Lock5Session:
    """One market, one window. Sequences frozen decisions through the boundary."""

    def __init__(self, *, market: str, book: "SHADOW.Book",
                 transport, mandate, state_dir, monitor: OBS.ProbeMonitor,
                 realized_today: Callable[[], D] = lambda: D(0)) -> None:
        self.market = str(market)
        self.book = book
        self.transport = transport          # already a GuardedTransport
        self.mandate = mandate
        self.state_dir = state_dir
        self.mon = monitor
        self.realized_today = realized_today
        self.locked = False
        self.lock_floor: Optional[D] = None
        self.submitted = 0

    # ------------------------------------------------------------- LOCK5

    def floor_now(self, *, resting: Optional[Mapping[str, Any]] = None) -> D:
        """The frozen floor, on the frozen implementation. No rebate term."""
        return settlement_independent_floor(self.book, resting=resting)

    def evaluate_lock(self, *, resting: Optional[Mapping[str, Any]] = None
                      ) -> Dict[str, Any]:
        """Latch LOCK once the guaranteed executable floor reaches $5.00."""
        floor = self.floor_now(resting=resting)
        newly = False
        if not self.locked and floor >= LOCK_USD:
            self.locked, self.lock_floor, newly = True, floor, True
        rec = {"floor": str(floor), "threshold": str(LOCK_USD),
               "locked": self.locked, "newly_locked": newly,
               "lock_floor": (None if self.lock_floor is None
                              else str(self.lock_floor)),
               "note": "floor is BEFORE expected rebates; rebates never lock"}
        self.mon.record("LOCK", rec)
        return rec

    def may_open_risk(self, action: str) -> Tuple[bool, str]:
        """Once locked, no new risk-opening or EXTEND action is permitted."""
        if self.locked and action.upper() in ("OPEN", "ACQUIRE", "EXTEND"):
            return False, "LOCKED_NO_NEW_RISK"
        return True, "PERMITTED"

    # -------------------------------------------------- taker completion

    def completion_permitted(self, first_cost: D, ask_cost: D) -> bool:
        """EXACTLY the frozen policy's condition. No live-only variant."""
        return bool(Q1.taker_completion_ok(D(str(first_cost)), D(str(ask_cost))))

    def submit_taker_completion(self, *, outcome_side: str, ask: D, qty: D,
                                first_cost: D, attempt: int = 1) -> Dict[str, Any]:
        """Compose the existing primitive. Never invents an aggressive fallback."""
        if not self.completion_permitted(first_cost, ask):
            rec = {"refused": "FROZEN_POLICY_DOES_NOT_PERMIT_COMPLETION",
                   "first_cost": str(first_cost), "ask": str(ask)}
            self.mon.record("DECISION", rec)
            return rec
        self.mon.record("DECISION", {
            "action": "COMPLETION", "side": outcome_side, "ask": str(ask),
            "qty": str(qty), "first_cost": str(first_cost),
            "taker_fee": str(Q1.taker_fee(D(str(ask)))),
            "admitted_by": "taker_completion_ok"})
        # the SAME body the micro primitive builds -- it delegates to this exact
        # function -- without importing the module that reaches the ramp ladder.
        # The non-positive-quantity refusal is preserved: OB.build raises too.
        body = OB.build(slug=self.market, side=outcome_side, price=D(str(ask)),
                        qty=D(str(qty)), post_only=False)
        return self.submit(body, stage="micro_complement_taker_%d" % attempt)

    # ------------------------------------------- the boundary, and only it

    def submit(self, body: Mapping[str, Any], *, stage: str) -> Dict[str, Any]:
        """The ONLY money-moving path. Mandate first, always."""
        self.mon.record("SUBMISSION", {"stage": stage, "body": dict(body)})
        verdict = MANDATE.admit(body, state_dir=self.state_dir,
                                mandate=self.mandate, slug=self.market,
                                realized_today=self.realized_today())
        # THE KEY IS `admits` (2026-09-28). `admit()` returns
        # {"admits": bool, "reason": ...}; "admitted" is only ever the REASON
        # STRING on a success, never a key. This line previously read
        # `verdict.get("admitted")`, which is always None, so EVERY submission
        # raised SafetyRefusal -- including admitted ones. Fail-closed, so no
        # money was ever at risk, but the reviewed translation could not submit
        # at all.
        #
        # It survived 39 green tests because those tests monkeypatched
        # MANDATE.admit to return {"admitted": True}: the fake was written from
        # the same wrong assumption as the code, so it confirmed the bug instead
        # of catching it. test_mandate_verdict_contract.py now asserts the real
        # function's keys, and the money-path tests use the real admit().
        if not verdict.get("admits"):
            self.mon.record("DECISION", {"stage": stage, "mandate": verdict,
                                         "outcome": "REFUSED_BY_MANDATE"})
            raise SafetyRefusal(verdict)
        # THE BARE BODY (2026-09-28). `/v1/orders` takes the order body itself;
        # only `/v1/order/preview` takes {"request": body}. This line previously
        # sent the preview envelope, so `pmus_live_mandate.admit` could not read
        # the quantity and refused `order_unpriceable:order_quantity_unreadable`
        # on every order -- fail-closed, and non-functional. Canonical shape from
        # pmus_micro_executor (`t.request("POST", "/v1/orders", body)`) and
        # l5_pmus_transport.order, which both send it bare.
        ack = self.transport.request("POST", "/v1/orders", body)
        self.submitted += 1
        # ACK IS NOT FILL: no inventory or cash moves here.
        self.mon.record("ACK", {"stage": stage, "ack": ack,
                                "note": "a receipt, not a final state; "
                                        "inventory unchanged"})
        return {"ack": ack, "stage": stage, "mandate": verdict}

    def apply_authoritative_fill(self, *, side: str, qty: D, price: D,
                                 source: str) -> Dict[str, Any]:
        """Inventory and cash move ONLY from a venue fill or reconciliation."""
        if source not in ("VENUE_FILL", "RECONCILIATION"):
            raise ValueError("inventory may not move from %r" % (source,))
        qty, price = D(str(qty)), D(str(price))
        self.book.add(side, qty, qty * price, maker=False,
                      fee=Q1.taker_fee(price) * qty / qty if qty else D(0))
        self.mon.record("FILL", {"side": side, "qty": str(qty),
                                 "price": str(price), "source": source})
        inv = {"up": str(self.book.q["up"]), "down": str(self.book.q["down"]),
               "cash_up": str(self.book.c["up"]),
               "cash_down": str(self.book.c["down"]),
               "fees": str(self.book.fees)}
        self.mon.record("INVENTORY", inv)
        self.evaluate_lock()
        return inv
