"""Replay the preregistered maker lifecycle over recorded windows.

CONCURRENT, not cumulative. "$29 of intent notional" is a cumulative number
and it answers nothing: the question is how much is resting AT ONCE, because
that is what an envelope has to hold. This walks each window in event order,
places what the rules allow, ages the resting orders, and reports peaks.

NO FILLS ARE INFERRED. BOOK_REACHED (the touch later came to our price) and
TRADE_FLOW (a print happened at or through it) are executability and flow.
Neither is a fill and neither books inventory here or anywhere else. A
historical order's lifetime therefore ends at a cancel rule or at the window
guard -- never at an imagined fill, which would flatter every number below.

WORST-CASE EXPOSURE FOR A BOUGHT OUTCOME TOKEN is the premium paid: a token
settles at 0 or 1, so a buyer can lose exactly what they put in. Concurrent
worst case is therefore the sum of price x quantity over everything resting
and everything filled -- not the count of orders.
"""
from __future__ import annotations

import collections
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import l5_maker_execution as MK

MAKER_COPYABLE = "MAKER_COPYABLE"


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def touch_for(side: str, bbo: Mapping[str, Any]) -> Optional[D]:
    """Our touch in this outcome's own terms."""
    bid, ask = _d(bbo.get("bid")), _d(bbo.get("ask"))
    if bid is None or ask is None:
        return None
    return ask if str(side).upper() == "UP" else (D(1) - bid)


class Resting:
    """One simulated resting order. It never fills."""

    __slots__ = ("key", "side", "price", "qty", "placed_t", "ended_t",
                 "book_reached", "trade_flow", "end_reason")

    def __init__(self, key, side, price, qty, placed_t):
        self.key, self.side = key, side
        self.price, self.qty = price, qty
        self.placed_t, self.ended_t = placed_t, None
        self.book_reached = False
        self.trade_flow = False
        self.end_reason: Optional[str] = None

    @property
    def premium(self) -> D:
        return self.price * self.qty

    def lifetime(self, end_t: float) -> float:
        return round((self.ended_t if self.ended_t is not None else end_t)
                     - self.placed_t, 3)


class WindowReplay:
    """One window, walked in event order under the preregistered rules."""

    def __init__(self, slug: str, *, lam: D, cap_usd: D = D(5),
                 max_live: Optional[int] = 1, increment: D = D("0.01"),
                 guard_s: float = 120.0, window_s: float = 900.0) -> None:
        self.slug = slug
        self.lam, self.cap, self.max_live = lam, cap_usd, max_live
        self.increment, self.guard_s, self.window_s = increment, guard_s, window_s
        self.resting: List[Resting] = []
        self.done: List[Resting] = []
        self.filled_premium = D(0)          # nothing ever fills; kept at 0
        self.peak_concurrent = 0
        self.peak_premium = D(0)
        self.intents = 0
        self.placed = 0
        self.blocked_one_live = 0
        self.blocked_envelope = 0
        self.arrived_while_resting = 0
        self.window_start: Optional[float] = None

    # -------------------------------------------------------------- rules

    def _exposure(self) -> D:
        return sum((r.premium for r in self.resting), D(0)) + self.filled_premium

    def _floor(self, qty: D) -> D:
        from src.l5_live_execution import floor_to_increment
        return floor_to_increment(qty, self.increment)

    def offer(self, *, key: str, side: str, price: D, size: D,
              t: float) -> Optional[str]:
        """Try to rest one maker intent. Returns the refusal, or None."""
        self.intents += 1
        if self.resting:
            self.arrived_while_resting += 1
        qty = self._floor(size * self.lam)
        if qty <= 0:
            return "BELOW_MIN_INCREMENT"
        if self.max_live is not None and len(self.resting) >= self.max_live:
            self.blocked_one_live += 1
            return "MAX_LIVE_ORDERS"
        if self._exposure() + price * qty > self.cap:
            self.blocked_envelope += 1
            return "MAX_USD_PER_MARKET"
        self.resting.append(Resting(key, side, price, qty, t))
        self.placed += 1
        n, prem = len(self.resting), self._exposure()
        self.peak_concurrent = max(self.peak_concurrent, n)
        self.peak_premium = max(self.peak_premium, prem)
        return None

    def observe_book(self, side_touch: Mapping[str, Optional[D]]) -> None:
        """BOOK_REACHED only. Executability, never a fill."""
        for r in self.resting:
            t = side_touch.get(r.side)
            if t is not None and t <= r.price:
                r.book_reached = True

    def observe_prints(self, prints: Sequence[Mapping[str, Any]]) -> None:
        """TRADE_FLOW only. Real opposing flow, still never our fill."""
        for p in prints:
            px = _d(p.get("px"))
            if px is None:
                continue
            for r in self.resting:
                # a print is in YES terms; compare in the order's own terms
                own = px if r.side == "UP" else (D(1) - px)
                if own <= r.price:
                    r.trade_flow = True

    def cancel(self, reason: str, *, t: float,
               side: Optional[str] = None) -> None:
        keep: List[Resting] = []
        for r in self.resting:
            if side is None or r.side == side:
                r.ended_t, r.end_reason = t, reason
                self.done.append(r)
            else:
                keep.append(r)
        self.resting = keep

    def close(self, t: float) -> None:
        self.cancel(MK.CANCEL_WINDOW_GUARD, t=t)

    # ------------------------------------------------------------- report

    def as_dict(self) -> Dict[str, Any]:
        lives = sorted(r.lifetime(0.0) for r in self.done)
        def pct(p):
            if not lives:
                return None
            i = min(len(lives) - 1, int(round((len(lives) - 1) * p)))
            return lives[i]
        return {
            "market": self.slug,
            "maker_intents": self.intents,
            "placed": self.placed,
            "peak_simultaneous": self.peak_concurrent,
            "peak_premium_usd": str(self.peak_premium),
            "blocked_one_live": self.blocked_one_live,
            "blocked_envelope": self.blocked_envelope,
            "arrived_while_another_rested": self.arrived_while_resting,
            "lifetime_p50": pct(0.50), "lifetime_p75": pct(0.75),
            "lifetime_p90": pct(0.90),
            "book_reached": sum(1 for r in self.done if r.book_reached),
            "trade_flow": sum(1 for r in self.done if r.trade_flow),
            "labels": ("BOOK_REACHED and TRADE_FLOW are executability and "
                       "flow. No fill is inferred anywhere in this replay."),
        }
