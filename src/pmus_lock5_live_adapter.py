"""Orchestration that puts the reviewed Rung-A components ON the money path.

WHY THIS EXISTS (OWNER 2026-09-28)

`build_lock5`, `pair_intent`, `lock5_may_open_risk`, `lock5_complete` and
`arm_with_bounded_retry` were all defined on the Probe and **never called**.
`run()` never loaded a mandate and never imported `pmus_live_mandate`. So the
executing path had no LOCK5 session, no paired record, no bounded arm retry and
no monetary authority. The composition tests called `build_lock5` themselves, and
their negative controls disconnected components *inside* it -- which is the
original BLOCKER 1 vacuity one level up.

Orders are submitted deep inside `l5_pmus_live_wiring.run_window` ->
`EX.FaithfulExecutor`, which takes a `transport`. So the correct place to put the
reviewed components is AROUND THAT TRANSPORT, not inside `run()` and not by
editing the frozen executor. Every order the executor sends therefore passes
through, in order:

    pair_intent  ->  LOCK5 new-risk gate  ->  GuardedTransport.admit  ->  wire

NOTHING HERE DECIDES ECONOMICS. No price, size, side, timing or completion
condition is computed in this module. The floor comes from the frozen scorer via
`Lock5Session`, the completion condition from `pmus_q1_executor`, the dollars from
the mandate. This module only sequences them and records what happened.

ACK IS NOT FILL. `request()` records an ACK and moves no inventory.
`note_venue_progress()` is the only path to inventory, and it acts only on a
venue-sourced cumulative-fill delta read back with a GET.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from src import l5_live_execution as EX
from src import l5_order_body as OB
from src import pmus_live_mandate as MANDATE

#: the venue path that moves money. Same constant the mandate guard keys on.
ORDER_PATH = MANDATE.ORDER_PATH

#: venue outcome enum -> the frozen Book's side keys. Straight from
#: l5_order_body.STATIC_CONTRACT: yes_is UP, no_is DOWN. Not inferred from price.
_SIDE_OF = {"OUTCOME_SIDE_YES": "up", "OUTCOME_SIDE_NO": "down"}


class Lock5Refusal(RuntimeError):
    """LOCK is latched and this order would open or extend risk."""

    def __init__(self, reason: str, detail: Mapping[str, Any]) -> None:
        super().__init__(str(reason))
        self.reason = str(reason)
        self.detail = dict(detail)


def outcome_terms_price(body: Mapping[str, Any]) -> D:
    """The body's price back in the outcome's OWN terms.

    `l5_order_body.venue_price` sends a NO order as `1 - price`, so reading
    `price.value` straight off a NO body understates what we are paying for that
    outcome. Inverting is not optional: the paired record and the floor are both
    in outcome terms, and Probe 05's completes went out at wire 0.57 for a NO.
    """
    wire = D(str((body.get("price") or {}).get("value") or 0))
    os_ = str(body.get("outcomeSide") or "")
    return OB.ONE - wire if os_.upper().endswith("NO") else wire


def side_of(body: Mapping[str, Any]) -> str:
    os_ = str(body.get("outcomeSide") or "")
    try:
        return _SIDE_OF[os_]
    except KeyError:
        raise ValueError("unmapped outcomeSide %r" % (os_,)) from None


class PairedLockTransport:
    """Transport decorator: paired record + LOCK5 gate in front of the guard.

    Wraps the ALREADY mandate-guarded transport, so the mandate remains the last
    word: this layer can refuse an order the mandate would have admitted, and can
    never admit one the mandate refuses.
    """

    def __init__(self, inner: Any, *, lock5: Any, paired: Any,
                 journal: Any = None, limits: Any = None) -> None:
        # THE INVARIANT, at the boundary itself. This class only ever wraps a
        # POST-capable transport, so a measurement-only envelope arriving here is
        # a defect and the construction fails rather than the money moving.
        if limits is not None:
            EX.refuse_measurement_authority(limits, inner,
                                            where="PairedLockTransport")
        self._limits = limits
        self._inner = inner
        self._lock5 = lock5
        self._paired = paired
        self._journal = journal
        #: client order id -> paired row, so an authoritative fill arriving
        #: later can be attached to the intent that caused it.
        self.rows: Dict[str, Any] = {}
        self.refusals: list = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    # ------------------------------------------------------------- classify

    def classify(self, body: Mapping[str, Any]) -> str:
        """OPEN / EXTEND / COMPLETE from inventory arithmetic, not a new rule.

        An order on the side holding LESS raises `min(q_up, q_down)` -- it is the
        completing leg. One on the side already holding as much or more deepens
        the imbalance. Flat is an OPEN. This is the frozen meaning of completion
        restated arithmetically; it introduces no policy.
        """
        side = side_of(body)
        other = "down" if side == "up" else "up"
        q = self._lock5.book.q
        same, opp = D(str(q[side])), D(str(q[other]))
        if same == 0 and opp == 0:
            return "OPEN"
        return "COMPLETE" if same < opp else "EXTEND"

    def _first_leg_unit_cost(self, completing_side: str) -> Optional[D]:
        """Average unit cost of the leg being completed, from the frozen book.

        Arithmetic on the book the frozen scorer maintains -- cash divided by
        quantity -- not a new cost model. None when there is nothing to complete,
        in which case the frozen condition has no opinion and is not consulted.
        """
        aged = "down" if completing_side == "up" else "up"
        q = D(str(self._lock5.book.q[aged]))
        if q <= 0:
            return None
        return D(str(self._lock5.book.c[aged])) / q

    # -------------------------------------------------------------- the path

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None, **kw: Any) -> Any:
        if not (str(method).upper() == "POST" and str(path) == ORDER_PATH):
            return self._inner.request(method, path, body, **kw)
        b = dict(body or {})
        # the executor nests the order under "request"; accept either shape
        order = dict(b.get("request") or b)
        action = self.classify(order)
        px, qty = outcome_terms_price(order), D(str(order.get("quantity") or 0))

        # 1. THE PAIRED ROW FIRST, before any chance to submit. A row created
        #    after a fill cannot record a non-fill, and the non-fill is the
        #    finding this experiment is most likely to produce.
        row = self._paired.intend(action=action, side=side_of(order),
                                  shadow_price=px, shadow_qty=qty,
                                  shadow_filled=True)
        # registered IMMEDIATELY, before any gate. A row only registered after a
        # successful ACK would leave every refusal unfindable, and the refusals
        # are exactly what the paired record exists to keep.
        self.rows["__last__"] = row

        # 2. LOCK5: once latched, no new or deepened risk. A COMPLETE is still
        #    permitted, which is the frozen policy, not a concession.
        ok, why = self._lock5.may_open_risk(action)
        if not ok:
            row.cancelled(refused=why, action=action)
            self.refusals.append({"reason": why, "action": action})
            self._note("lock5_refused_new_risk",
                       {"action": action, "reason": why})
            raise Lock5Refusal(why, {"action": action, "side": side_of(order)})

        # 2b. A COMPLETION must satisfy the FROZEN completion condition, on the
        #     frozen implementation. `completion_permitted` delegates to
        #     `pmus_q1_executor.taker_completion_ok`; nothing is re-derived here,
        #     and there is deliberately no more permissive "live variant".
        if action == "COMPLETE":
            first = self._first_leg_unit_cost(side_of(order))
            if first is not None and not self._lock5.completion_permitted(first, px):
                row.cancelled(refused="FROZEN_POLICY_DOES_NOT_PERMIT_COMPLETION",
                              first_cost=str(first), ask=str(px))
                self.refusals.append({"reason": "COMPLETION_NOT_PERMITTED",
                                      "first_cost": str(first), "ask": str(px)})
                self._note("completion_refused",
                           {"first_cost": str(first), "ask": str(px)})
                raise Lock5Refusal("FROZEN_POLICY_DOES_NOT_PERMIT_COMPLETION",
                                   {"first_cost": str(first), "ask": str(px)})

        row.submitted(order)
        # 3. the mandate, via the guard we wrap. It may still refuse.
        try:
            ack = self._inner.request(method, path, body, **kw)
        except MANDATE.MandateRefusal as exc:
            row.refused(exc.verdict)
            self.refusals.append(dict(exc.verdict))
            raise
        # 4. ACK IS NOT FILL. Inventory is untouched here, whatever the ACK says.
        row.acked(ack if isinstance(ack, Mapping) else {"ack": str(ack)})
        coid = str(order.get("clientOrderId") or kw.get("coid") or "")
        if coid:
            self.rows[coid] = row
        return ack

    # --------------------------------------------- the ONLY path to inventory

    def note_venue_progress(self, res: Mapping[str, Any], *, side: str) -> Optional[Dict[str, Any]]:
        """Book a venue-sourced cumulative-fill delta. Nothing else may.

        `res` is what `InventoryStore.apply_order_progress` returned for a row
        read back from the venue with a GET, so a positive `delta` is
        authoritative fill evidence and not an ACK's opinion of itself.
        """
        delta = D(str(res.get("delta") or 0))
        coid = str(res.get("coid") or "")
        row = self.rows.get(coid) or self.rows.get("__last__")
        if delta <= 0:
            if bool(res.get("terminal")) and row is not None and not row.resolved:
                row.nonfill("terminal_without_fill:%s" % (res.get("state") or "",))
            return None
        px = D(str(res.get("avg_px") or 0))
        inv = self._lock5.apply_authoritative_fill(
            side=side, qty=delta, price=px, source="VENUE_FILL")
        lock = self._lock5.evaluate_lock()
        if row is not None:
            row.venue_fill(price=px, qty=delta, source="VENUE_FILL",
                           inventory=inv, floor=self._lock5.floor_now(),
                           locked=self._lock5.locked)
        self._note("authoritative_fill", {"coid": coid, "side": side,
                                          "qty": str(delta), "price": str(px),
                                          "lock": lock})
        return {"inventory": inv, "lock": lock}

    def _note(self, kind: str, payload: Mapping[str, Any]) -> None:
        if self._journal is not None:
            try:
                self._journal.write(kind, dict(payload))
            except Exception:                                  # noqa: BLE001
                pass
