"""Execution attribution ladder, built FROM the known-good parity loop.

The previous order-based harness was abandoned, not patched: it double-counted
taker fills, matched orders to stale prices, granted fills on locked rows and
reported an execution-aware mean ABOVE frozen, which cannot be true.

This module starts from `pmus_native_parity.replay_window`, which is 25/25
exact at mean 6.116725359099999999999999992 with 44 completions and 136 granted
maker fills, and adds execution responsibilities ONE RUNG AT A TIME. Rung 0 is
that loop with a lifecycle OBSERVER attached -- it records what an order-based
world would have seen and changes nothing -- so it reproduces frozen by
construction rather than by luck.

    RUNG 0  frozen exact control            parity loop + passive observer
    RUNG 1  canonical order lifecycle       every fill classified through
                                            pmus_order_lifecycle on the venue's
                                            own record shape
    RUNG 2  one fill per real order         an order fills once; a refill needs
                                            an explicit new order, which exists
                                            only where the engine re-quoted
    RUNG 3  maker/taker semantics           post-only makers; taker legs read
                                            back from the order record
    RUNG 4  measured latency                a fill is not observable until the
                                            next book observation
    RUNG 5  terminal/reject/partial         terminal classification and
                                            reservation release
    RUNG 6  production accounting           registry exposure and order_terminal

NO REJECTION PROBABILITY IS INVENTED. R4 is n=1. `reject_rule` exists for
sensitivity only and is None everywhere in the ladder.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P
from src import pmus_order_lifecycle as LC

ONE = D(1)

#: the rungs, in order. Each is the previous one plus exactly one effect.
RUNGS = ("R0_FROZEN_CONTROL", "R1_LIFECYCLE", "R2_ONE_FILL_PER_ORDER",
         "R3_MAKER_TAKER", "R4_LATENCY", "R5_TERMINAL_HANDLING",
         "R6_PRODUCTION_ACCOUNTING")


def rung_flags(rung: str) -> Dict[str, bool]:
    i = RUNGS.index(rung)
    return {"lifecycle": i >= 1, "one_fill_per_order": i >= 2,
            "maker_taker": i >= 3, "latency": i >= 4,
            "terminal_handling": i >= 5, "accounting": i >= 6}


class Obs:
    """The lifecycle observer. At rung 0 it only watches."""

    def __init__(self, flags: Mapping[str, bool]) -> None:
        self.f = dict(flags)
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.n = 0
        self.maker_fills = 0
        self.taker_fills = 0
        self.lost: List[dict] = []
        self.terminals = 0
        self.rejections = 0
        self.reserved = D(0)
        self.filled_commitment = D(0)

    # ---- submission ----
    def rest(self, side: str, px: D, qty: D, t: int) -> str:
        self.n += 1
        oid = "O%d" % self.n
        self.orders[oid] = {"oid": oid, "side": side, "px": D(str(px)),
                            "qty": D(str(qty)), "cum": D(0), "state": "OPEN",
                            "maker": True, "t": t}
        self.reserved += D(str(px)) * D(str(qty))
        return oid

    def taker(self, side: str, px: D, qty: D, t: int) -> str:
        self.n += 1
        oid = "O%d" % self.n
        self.orders[oid] = {"oid": oid, "side": side, "px": D(str(px)),
                            "qty": D(str(qty)), "cum": D(0), "state": "OPEN",
                            "maker": False, "t": t}
        return oid

    def cancel(self, side: str) -> None:
        for o in self.orders.values():
            if o["side"] == side and o["state"] == "OPEN" and o["maker"]:
                o["state"] = "CANCELLED" if o["cum"] == 0 else "FILLED"
                if self.f["terminal_handling"]:
                    self.terminals += 1
                    self.reserved -= (o["qty"] - o["cum"]) * o["px"]

    def live_maker(self, side: str, px: D):
        """The OPEN maker order that IS this quote, matched on side and price."""
        for o in self.orders.values():
            if (o["side"] == side and o["state"] == "OPEN" and o["maker"]
                    and o["px"] == D(str(px))):
                return o
        return None

    # ---- the venue record, in the venue's own shape ----
    @staticmethod
    def record(o: Mapping[str, Any]) -> Dict[str, Any]:
        wire = o["px"] if o["side"] == "up" else (ONE - o["px"])
        state = {"FILLED": "ORDER_STATE_FILLED",
                 "CANCELLED": "ORDER_STATE_CANCELED",
                 "REJECTED": "ORDER_STATE_REJECTED"}.get(o["state"],
                                                         "ORDER_STATE_OPEN")
        return {"state": state, "quantity": str(o["qty"]),
                "cumQuantity": str(o["cum"]),
                "leavesQuantity": str(o["qty"] - o["cum"]),
                "avgPx": {"value": str(wire), "currency": "USD"},
                "commissionNotionalTotalCollected": {"value": "0",
                                                     "currency": "USD"},
                "outcomeSide": ("OUTCOME_SIDE_YES" if o["side"] == "up"
                                else "OUTCOME_SIDE_NO")}

    def observe_fill(self, o, qty: D, fpx: D) -> D:
        """Credit the order and return the price the ENGINE should book at.

        At rung 0 the price is frozen's own fill price, untouched. From rung 1
        the price comes back through `pmus_order_lifecycle.classify` on the
        venue's record shape, which is what production reads -- so any
        round-trip loss shows up as a rung-1 delta rather than hiding.
        """
        o["cum"] += qty
        o["px"] = D(str(fpx))
        if o["cum"] >= o["qty"]:
            o["state"] = "FILLED"
            if self.f["terminal_handling"]:
                self.terminals += 1
        if o["maker"]:
            self.maker_fills += 1
        else:
            self.taker_fills += 1
        if self.f["accounting"]:
            self.filled_commitment += D(str(fpx)) * qty
            self.reserved -= D(str(fpx)) * qty
        if not self.f["lifecycle"]:
            return D(str(fpx))
        v = LC.classify(self.record(o), requested_qty=o["qty"], side=o["side"])
        px = v.get("price_own")
        return D(str(fpx)) if px is None else D(str(px))


def replay_window(rows, *, winner, rung="R0_FROZEN_CONTROL", clip=E.CLIP,
                  fill_model="queue_aware", reject_rule=None) -> Dict[str, Any]:
    """`pmus_native_parity.replay_window`, with the observer and one rung.

    The control path is byte-for-byte the parity loop: same engine
    construction, same `_execute_taker`, same `locked_at` skip, same per-side
    fill order, same `_cap_permits` placement.
    """
    f = rung_flags(rung)
    eng = E.NativeEngine(clip=clip, prospective_cap=False)
    venue = P.ReplayVenue(fill_model=fill_model)
    obs = Obs(f)
    ladder: Optional[Mapping[str, Any]] = None
    pending: List[tuple] = []          # rung 4: fills seen one observation late
    t = 0

    for row in rows:
        kind = row.get("kind")
        if kind == "book":
            ladder = row
            eng.ladder = row
            continue
        if kind != "bbo":
            continue
        if str(row.get("state") or "") != "MARKET_STATE_OPEN":
            continue
        t += 1

        # rung 4: whatever filled on the previous observation becomes visible
        # now. Latency is the one thing R4 measured -- 10.701s from submit to
        # fill, which at this tape's cadence is on the order of one
        # observation -- so it is represented as exactly that and no more.
        if f["latency"] and pending:
            for side, qty, px in pending:
                eng.on_fill(side, qty, qty * px, maker=True)
            pending = []

        def _execute_taker(it):
            if not f["maker_taker"]:
                return it["qty"], it["cost"], it["fee"]
            # taker legs become real orders and are read back
            oid = obs.taker(it["side"], D(str(it.get("limit_price")
                                             or it["price"])),
                            D(str(it["qty"])), t)
            o = obs.orders[oid]
            o["cum"] = o["qty"]                       # IOC fills at the walk
            o["state"] = "FILLED"
            obs.taker_fills += 1
            if f["terminal_handling"]:
                obs.terminals += 1
            return it["qty"], it["cost"], it["fee"]

        try:
            intents = eng.on_book(row, ladder, execute_taker=_execute_taker)
        except E.StaleBook:
            continue

        for it in intents:
            if it.kind == E.CANCEL:
                obs.cancel(it["side"])
            elif it.kind == E.REST:
                obs.rest(it["side"], D(str(it["price"])), D(str(it["qty"])), t)

        if eng.locked_at is not None:
            continue

        for side in ("up", "down"):
            st = eng.resting.get(side)
            if not st:
                continue
            got = venue.fills_for(eng, row, side, D(str(st["px"])),
                                  D(str(st["qty"])), ladder)
            if got is None:
                continue
            qty, fpx = got
            qty = D(str(qty))
            if not eng._cap_permits(side, qty):
                continue

            o = obs.live_maker(side, D(str(st["px"])))
            if o is None:
                # frozen refills a notional clip its order has exhausted
                if f["one_fill_per_order"]:
                    obs.lost.append({"t": t, "side": side,
                                     "px": str(st["px"]), "qty": str(qty),
                                     "reason": "NO_LIVE_ORDER_AT_THIS_PRICE",
                                     "detail": ("frozen refills a clip whose "
                                                "order already filled; a real "
                                                "order is terminal and the "
                                                "engine emitted no re-quote")})
                    continue
                book_px = D(str(fpx))
                obs.maker_fills += 1
            else:
                book_px = obs.observe_fill(o, qty, fpx)

            if f["latency"]:
                pending.append((side, qty, book_px))
            else:
                eng.on_fill(side, qty, qty * book_px, maker=True)

    if f["latency"]:
        for side, qty, px in pending:
            eng.on_fill(side, qty, qty * px, maker=True)

    res = eng.settle(winner)
    res["obs"] = {"orders": obs.n, "maker_fills": obs.maker_fills,
                  "taker_fills": obs.taker_fills, "lost": obs.lost,
                  "terminals": obs.terminals,
                  "filled_commitment": str(obs.filled_commitment),
                  "reserved": str(obs.reserved)}
    return res


def ladder(by, usable, *, clip=E.CLIP) -> List[Dict[str, Any]]:
    """Every rung over all 25 windows, with the deltas that matter."""
    out = []
    prev = None
    frozen_mean = None
    for rung in RUNGS:
        rows = []
        for slug, w in usable.items():
            r = replay_window(by[slug], winner=w, rung=rung, clip=clip)
            rows.append((slug, r))
        n = D(len(rows) or 1)
        mean = sum((P.net_of(r) for _, r in rows), D(0)) / n
        rec = {
            "rung": rung,
            "mean_net": mean,
            "completions": sum(int(r.get("executable_states") or 0)
                               for _, r in rows),
            "maker_fills": sum(r["obs"]["maker_fills"] for _, r in rows),
            "taker_fills": sum(r["obs"]["taker_fills"] for _, r in rows),
            "matched_pnl": sum((r["matched_pnl"] for _, r in rows), D(0)) / n,
            "resid_pnl": sum((r["resid_pnl"] for _, r in rows), D(0)) / n,
            "fees": sum((r["fees"] for _, r in rows), D(0)) / n,
            "lost": [dict(x, window=slug[-5:]) for slug, r in rows
                     for x in r["obs"]["lost"]],
        }
        if frozen_mean is None:
            frozen_mean = mean
        rec["delta_prev"] = None if prev is None else mean - prev
        rec["delta_frozen"] = mean - D("6.1167253591")
        prev = mean
        out.append(rec)
    return out
