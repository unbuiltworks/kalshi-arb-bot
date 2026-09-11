"""Queue-aware Gemini L5 shadow primitives.

READ ONLY. No order placement. This module translates the mechanism learned
from L5/PMUS into Gemini-native book geometry without copying PMUS constants.

Semantics:
- strict trade-through is evidence for a hypothetical maker fill, not a fill
- at-price prints are an optimistic bound when queue priority is unknown
- all P&L produced by callers remains theoretical until an order actually
  fills, settles, and venue cashflow is confirmed
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D, ROUND_DOWN
from typing import Any, Iterable, Mapping, Optional

ONE = D("1")


def _d(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:
        return None


@dataclass(frozen=True)
class Level:
    price: D
    amount: D


@dataclass(frozen=True)
class QueueMetrics:
    side: str
    price: D
    touch_price: Optional[D]
    touch_depth: Optional[D]
    size_at_price: D
    queue_ahead_qty: D
    populated_levels_better: int
    populated_levels_to_touch: int
    spread: Optional[D]

    def as_dict(self) -> dict:
        return {
            "side": self.side,
            "price": str(self.price),
            "touch_price": str(self.touch_price) if self.touch_price is not None else None,
            "touch_depth": str(self.touch_depth) if self.touch_depth is not None else None,
            "size_at_price": str(self.size_at_price),
            "queue_ahead_qty": str(self.queue_ahead_qty),
            "populated_levels_better": self.populated_levels_better,
            "populated_levels_to_touch": self.populated_levels_to_touch,
            "spread": str(self.spread) if self.spread is not None else None,
        }


def _levels(rows: Iterable[Mapping[str, Any]], *, descending: bool) -> list[Level]:
    out: list[Level] = []
    for row in rows or []:
        p, q = _d(row.get("price")), _d(row.get("amount"))
        if p is None or q is None or q <= 0:
            continue
        out.append(Level(p, q))
    out.sort(key=lambda x: x.price, reverse=descending)
    return out


def retain_book(raw: Mapping[str, Any], max_levels: int = 25) -> dict:
    """Retain a bounded Gemini YES-book ladder in canonical price order."""
    bids = _levels(raw.get("bids") or [], descending=True)[:max_levels]
    asks = _levels(raw.get("asks") or [], descending=False)[:max_levels]
    return {
        "bids": [{"price": str(x.price), "amount": str(x.amount)} for x in bids],
        "asks": [{"price": str(x.price), "amount": str(x.amount)} for x in asks],
    }


def yes_touch(book: Mapping[str, Any]) -> tuple[Optional[D], Optional[D]]:
    bids = _levels(book.get("bids") or [], descending=True)
    return ((bids[0].price, bids[0].amount) if bids else (None, None))


def no_touch(book: Mapping[str, Any]) -> tuple[Optional[D], Optional[D]]:
    asks = _levels(book.get("asks") or [], descending=False)
    return ((ONE - asks[0].price, asks[0].amount) if asks else (None, None))


def spread(book: Mapping[str, Any]) -> Optional[D]:
    bids = _levels(book.get("bids") or [], descending=True)
    asks = _levels(book.get("asks") or [], descending=False)
    if not bids or not asks:
        return None
    return asks[0].price - bids[0].price


def queue_metrics(book: Mapping[str, Any], side_yes: bool, price: D) -> QueueMetrics:
    """Side-aware queue geometry for a hypothetical BUY maker order.

    YES queues on Gemini YES bids. NO queues on Gemini YES asks because a NO
    bid p is equivalent to a YES ask 1-p. Better YES bids are higher prices;
    better NO bids are lower YES asks.
    """
    px = D(str(price))
    bids = _levels(book.get("bids") or [], descending=True)
    asks = _levels(book.get("asks") or [], descending=False)
    spr = (asks[0].price - bids[0].price) if bids and asks else None

    if side_yes:
        touch_px = bids[0].price if bids else None
        touch_depth = bids[0].amount if bids else None
        better = [x for x in bids if x.price > px]
        at = sum((x.amount for x in bids if x.price == px), D("0"))
    else:
        touch_px = ONE - asks[0].price if asks else None
        touch_depth = asks[0].amount if asks else None
        implied_yes_ask = ONE - px
        better = [x for x in asks if x.price < implied_yes_ask]
        at = sum((x.amount for x in asks if x.price == implied_yes_ask), D("0"))

    return QueueMetrics(
        side="YES" if side_yes else "NO",
        price=px,
        touch_price=touch_px,
        touch_depth=touch_depth,
        size_at_price=at,
        queue_ahead_qty=sum((x.amount for x in better), D("0")),
        populated_levels_better=len(better),
        populated_levels_to_touch=len(better),
        spread=spr,
    )


def populated_price_arms(book: Mapping[str, Any], side_yes: bool,
                         tick: D, max_back: int = 2) -> list[dict]:
    """Gemini-valid maker arms by populated level, not nominal tick distance.

    Returns touch, 1 populated level back, 2 populated levels back, ... .
    Empty price gaps therefore do not pretend to improve queue position.
    """
    tick = D(str(tick))
    rows = (_levels(book.get("bids") or [], descending=True) if side_yes
            else _levels(book.get("asks") or [], descending=False))
    if not rows:
        return []

    side_prices: list[D] = []
    for row in rows:
        p = row.price if side_yes else ONE - row.price
        p = (p / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        if p not in side_prices:
            side_prices.append(p)
        if len(side_prices) >= max_back + 1:
            break

    out = []
    for n, px in enumerate(side_prices):
        q = queue_metrics(book, side_yes, px)
        out.append({
            "arm": "touch" if n == 0 else f"{n}_populated_level_back",
            "populated_levels_back": n,
            "price": str(px),
            "queue": q.as_dict(),
        })
    return out


def pair_cost_after_known_maker_fees(first_px: D, second_px: D,
                                      maker_fee_fn, qty: D = D("1")) -> D:
    q = D(str(qty))
    a, b = D(str(first_px)), D(str(second_px))
    return a + b + maker_fee_fn(a, q) / q + maker_fee_fn(b, q) / q


def fee_safe_price_arms(book: Mapping[str, Any], side_yes: bool, tick: D,
                        first_px: D, maker_fee_fn,
                        min_edge_after_fees: D,
                        max_back: int = 2) -> list[dict]:
    """Annotate same-tape populated-level arms using Gemini fee economics.

    No PMUS pair target is imported. The only economic test is the caller's
    Gemini-native minimum edge after known maker fees.
    """
    min_edge = D(str(min_edge_after_fees))
    out = []
    for arm in populated_price_arms(book, side_yes, tick, max_back=max_back):
        px = D(arm["price"])
        pair = pair_cost_after_known_maker_fees(first_px, px, maker_fee_fn)
        edge = ONE - pair
        out.append({**arm,
                    "pair_cost_after_known_fees": str(pair),
                    "edge_after_known_fees": str(edge),
                    "fee_safe": edge >= min_edge})
    return out


def recent_flow(prints: Iterable[Mapping[str, Any]], now_ms: int,
                window_s: int = 60) -> dict:
    cutoff = int(now_ms) - int(window_s * 1000)
    rows = [p for p in prints or [] if int(p.get("timestampms") or 0) >= cutoff]
    qty = sum((_d(p.get("amount")) or D("0") for p in rows), D("0"))
    buy = sum(1 for p in rows if str(p.get("type") or "").lower() == "buy")
    sell = sum(1 for p in rows if str(p.get("type") or "").lower() == "sell")
    return {"window_s": window_s, "prints": len(rows), "quantity": str(qty),
            "prints_per_minute": round(len(rows) * 60.0 / window_s, 3),
            "buy_prints": buy, "sell_prints": sell,
            "two_sided_print_flow": buy > 0 and sell > 0}


def movement(bbo_rows: Iterable[Mapping[str, Any]], now_s: float,
             window_s: int = 60) -> dict:
    rows = [r for r in bbo_rows or [] if float(r.get("t") or 0) >= now_s - window_s]
    mids: list[D] = []
    for r in rows:
        b, a = _d(r.get("bid")), _d(r.get("ask"))
        if b is not None and a is not None:
            mids.append((b + a) / 2)
    if not mids:
        return {"window_s": window_s, "observations": 0,
                "mid_move": None, "mid_range": None}
    return {"window_s": window_s, "observations": len(mids),
            "mid_move": str(mids[-1] - mids[0]),
            "mid_range": str(max(mids) - min(mids))}


SEMANTICS = {
    "strict_trade_through": "strong hypothetical maker-fill evidence; not guaranteed fill",
    "at_price_print": "optimistic upper bound when queue priority is unknown",
    "shadow_pnl": "theoretical only; never realized P&L",
}
