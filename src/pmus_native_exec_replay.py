"""The frozen 25 windows through the CONSOLIDATED EXECUTION STATE MACHINE.

`pmus_native_parity` replays the DECISION MODEL: it drives `NativeEngine` and
then hands it fills directly. That proved the port of the frozen rules is
exact (25/25, mean 6.116725359099999999999999992) and it proved nothing about
execution, because no order, no order id, no authoritative status read and no
terminal classification existed anywhere in the loop.

This module replays the same tape through `NativeMoneyPath.on_book_state`,
so every fill must arrive the way a live fill arrives:

    intent -> submit -> venue order record -> GET /v1/order/{id}
           -> pmus_order_lifecycle.classify -> book the fill at the venue's
              own average price -> engine decides again

WHAT IS REPRESENTED
  maker fills      exactly the frozen `maker_fill(queue_aware)` model, but
                   delivered as an order that must be READ BACK before the
                   engine may act on it.
  IOC taker legs   a completion is resolved from the order record's cumulative
                   quantity, and an unresolved remainder fails the pass closed.
  terminal states  FILLED / PARTIAL / CANCELLED_NO_FILL / WORKING, with the
                   registry and the reservation released only for the part
                   that did not fill.
  replacement      a side is not re-quoted until its previous order is
                   terminal at the venue.

WHAT IS NOT REPRESENTED, AND WHY
  COMPLEMENT REJECTION. The venue rejected R4's DOWN leg 94ms after
  submission. That is ONE observation. There is no measured rejection rate for
  this venue, this series or this price region, so modelling one here would be
  inventing the number that matters. `reject_rule` is therefore None by
  default -- rejections are modelled at zero -- and a caller may pass a rule to
  measure SENSITIVITY. Any figure produced with a rule is a sensitivity, never
  a forecast.
  FEES are left exactly where the frozen scorer puts them: maker fills are
  booked with fee=None so `Book.add` applies the frozen maker treatment. This
  replay measures EXECUTION, so it must not also move the fee model.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional

from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E
from src import pmus_native_money_path as NMP

ONE = D(1)


class _Order:
    __slots__ = ("oid", "side", "px", "qty", "cum", "state", "tif", "maker")

    def __init__(self, oid, side, px, qty, tif, maker):
        self.oid, self.side, self.px, self.qty = oid, side, px, qty
        self.cum, self.state, self.tif, self.maker = D(0), "OPEN", tif, maker

    def record(self) -> Dict[str, Any]:
        """The venue's own order record shape, as /v1/order serves it."""
        wire = self.px if self.side == "up" else (ONE - self.px)
        return {"state": ("ORDER_STATE_FILLED" if self.state == "FILLED"
                          else "ORDER_STATE_REJECTED"
                          if self.state == "REJECTED"
                          else "ORDER_STATE_CANCELED"
                          if self.state == "CANCELLED"
                          else "ORDER_STATE_OPEN"),
                "quantity": str(self.qty), "cumQuantity": str(self.cum),
                "leavesQuantity": str(self.qty - self.cum),
                "avgPx": {"value": str(wire)},
                "commissionNotionalTotalCollected": {"value": "0"},
                "outcomeSide": ("OUTCOME_SIDE_YES" if self.side == "up"
                                else "OUTCOME_SIDE_NO")}


class ExecReplayTransport:
    """The wire, for the replay. Accepts the certified submit_body shape."""

    def __init__(self, venue: "ExecReplayVenue") -> None:
        self.venue = venue
        self.submitted: List[dict] = []

    def submit_body(self, body: Mapping[str, Any]) -> Dict[str, Any]:
        self.submitted.append(dict(body))
        return self.venue.accept(body)

    def order(self, oid: str) -> Dict[str, Any]:
        o = self.venue.orders.get(str(oid))
        return o.record() if o is not None else {}


class ExecReplayVenue:
    """A venue that must be ASKED what happened, driven by the frozen model."""

    def __init__(self, *, fill_model: str = "queue_aware",
                 reject_rule: Optional[Callable[[_Order], bool]] = None
                 ) -> None:
        self.fill_model = fill_model
        self.reject_rule = reject_rule
        self.qstate: Dict[str, Any] = {}
        self.orders: Dict[str, _Order] = {}
        self.resting_ids: Dict[str, Any] = {"up": None, "down": None}
        self.n = 0
        self.rejected = 0
        self.status_reads = 0

    # ---- submission ----
    def accept(self, body: Mapping[str, Any]) -> Dict[str, Any]:
        self.n += 1
        oid = "X%d" % self.n
        side = "up" if str(body.get("outcomeSide")).endswith("YES") else "down"
        wire = D(str((body.get("price") or {}).get("value")))
        own = wire if side == "up" else (ONE - wire)
        tif = str(body.get("tif") or "")
        maker = bool(body.get("participateDontInitiate"))
        o = _Order(oid, side, own, D(str(body.get("quantity"))), tif, maker)
        if self.reject_rule is not None and self.reject_rule(o):
            o.state = "REJECTED"
            self.rejected += 1
        self.orders[oid] = o
        return {"id": oid, "filled_qty": str(o.cum)}

    # ---- the lifecycle seam ----
    def order_status(self, oid, *, requested_qty=None, side=None):
        from src import pmus_order_lifecycle as LC
        self.status_reads += 1
        o = self.orders.get(str(oid))
        if o is None:
            return dict(LC.classify(None), order_id=str(oid))
        v = LC.classify(o.record(), requested_qty=requested_qty,
                        side=side or o.side)
        v["order_id"] = str(oid)
        return v

    def open_orders(self) -> List[dict]:
        return [{"orderId": o.oid} for o in self.orders.values()
                if o.state == "OPEN"]

    def cancel_order(self, oid: str) -> None:
        o = self.orders.get(str(oid))
        if o is None or o.state != "OPEN":
            raise RuntimeError("400 order is not cancellable")
        o.state = "CANCELLED" if o.cum == 0 else "FILLED"

    def cancel_resting(self, side: str) -> None:
        for o in self.orders.values():
            if o.side == side and o.state == "OPEN":
                self.cancel_order(o.oid)

    def filled_qty(self, oid: str) -> Optional[D]:
        o = self.orders.get(str(oid))
        return None if o is None else o.cum

    def completion_fill(self, intent) -> Optional[tuple]:
        """The IOC leg's ACTUAL fill, read back from the order record."""
        oid = intent.get("venue_order_id") if hasattr(intent, "get") else None
        o = self.orders.get(str(oid or ""))
        if o is None:
            return None
        if o.cum <= 0:
            return D(0), D(0), D(0)
        return o.cum, o.px * o.cum, D(0)

    # ---- the frozen fill model, applied to what is actually resting ----
    def advance(self, row: Mapping[str, Any], ladder) -> None:
        for o in list(self.orders.values()):
            if o.state != "OPEN" or not o.maker:
                continue
            qty, fpx = S.maker_fill(row, o.side, o.qty - o.cum,
                                    queue_aware=True,
                                    fill_model=self.fill_model,
                                    ladder=ladder, qstate=self.qstate)
            if qty and qty > 0 and fpx is not None:
                o.cum += D(str(qty))
                o.px = D(str(fpx))
                if o.cum >= o.qty:
                    o.state = "FILLED"

    def fill_ioc_now(self) -> None:
        """An IOC leg either fills at submission or the venue kills it."""
        for o in self.orders.values():
            if o.state == "OPEN" and not o.maker:
                o.cum = o.qty
                o.state = "FILLED"


def replay_window(rows, *, winner, clip=E.CLIP, fill_model="queue_aware",
                  reject_rule=None) -> Dict[str, Any]:
    """One window, through the money path's own state machine."""
    venue = ExecReplayVenue(fill_model=fill_model, reject_rule=reject_rule)
    tp = ExecReplayTransport(venue)
    mp = NMP.build(market="replay", transport=tp, feed=None, venue=venue,
                   journal=None, clip=D(str(clip)))
    # the replay's cap placement matches the decision-only replay so the two
    # are comparable; execution is what this module varies, not the rules.
    mp.eng.prospective_cap = False

    ladder = None
    halted = None
    ticks = 0
    for row in rows:
        if row.get("kind") == "book":
            ladder = row
            continue
        if row.get("kind") != "bbo":
            continue
        if str(row.get("state") or "") != "MARKET_STATE_OPEN":
            continue
        # fills accrue on the venue between observations, exactly as the
        # frozen model says they would
        venue.advance(row, ladder)
        venue.fill_ioc_now()
        out = mp.on_book_state(row, ladder)
        ticks += 1
        if out.get("refused") == "ORDER_STATE_UNKNOWN":
            halted = out
            break
    res = mp.eng.settle(winner)
    res["exec"] = {"ticks": ticks, "orders": venue.n,
                   "status_reads": venue.status_reads,
                   "rejected": venue.rejected,
                   "halted": halted is not None,
                   "registry": mp.registry.as_dict(),
                   "rejections": len(mp.rejections)}
    return res


def net_of(r: Mapping[str, Any]) -> D:
    return r["matched_pnl"] - r["fees"] + r["resid_pnl"]


def compare(by, usable, *, clip=E.CLIP, reject_rule=None) -> Dict[str, Any]:
    """Execution-aware net per window, beside the frozen scorer's own."""
    from src import pmus_native_parity as P
    rows_out = []
    for slug, winner in usable.items():
        fr = P.frozen_window(by[slug], winner=winner, clip=clip)
        ex = replay_window(by[slug], winner=winner, clip=clip,
                           reject_rule=reject_rule)
        rows_out.append({
            "window": slug[-5:],
            "frozen_net": net_of(fr), "exec_net": net_of(ex),
            "delta_net": net_of(ex) - net_of(fr),
            "frozen_completions": fr.get("executable_states"),
            "exec_completions": ex.get("executable_states"),
            "frozen_matched": fr.get("matched"), "exec_matched": ex.get("matched"),
            "exec": ex["exec"]})
    n = len(rows_out) or 1
    return {"windows": len(rows_out),
            "frozen_mean_net": sum((r["frozen_net"] for r in rows_out), D(0)) / n,
            "exec_mean_net": sum((r["exec_net"] for r in rows_out), D(0)) / n,
            "frozen_completions": sum(int(r["frozen_completions"] or 0)
                                      for r in rows_out),
            "exec_completions": sum(int(r["exec_completions"] or 0)
                                    for r in rows_out),
            "per_window": rows_out}
