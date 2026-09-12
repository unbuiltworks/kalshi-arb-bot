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
        qm = queue_metrics(book, side_yes, px)
        out.append({
            "arm": "touch" if n == 0 else f"{n}_populated_level_back",
            "populated_levels_back": n,
            "price": str(px),
            "queue": qm.as_dict(),
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


def _strict_fill(prints: Iterable[Mapping[str, Any]], side_yes: bool, price: D,
                 start_s: float, end_s: float) -> Optional[dict]:
    """Earliest strict trade-through after a hypothetical maker quote.

    YES bid: a taker SELL print strictly below our bid.
    NO bid p: a taker BUY print strictly above the equivalent YES ask 1-p.
    The result remains hypothetical evidence, never a venue fill.
    """
    px = D(str(price))
    for p in prints or []:
        ts = int(p.get("timestampms") or 0) / 1000.0
        if ts <= start_s or ts > end_s:
            continue
        pp = _d(p.get("price"))
        typ = str(p.get("type") or "").lower()
        if pp is None:
            continue
        hit = (side_yes and typ == "sell" and pp < px) or (
            (not side_yes) and typ == "buy" and pp > ONE - px)
        if hit:
            return {"t": ts, "timestampms": int(p.get("timestampms") or 0),
                    "print_price": str(pp), "print_type": typ,
                    "quoted_price": str(px), "tid": p.get("tid")}
    return None


def _row_at_or_after(rows: Iterable[Mapping[str, Any]], t: float) -> Optional[Mapping[str, Any]]:
    after = [r for r in rows or [] if float(r.get("t") or 0) >= t and r.get("depth")]
    if after:
        return min(after, key=lambda r: float(r.get("t") or 0))
    before = [r for r in rows or [] if float(r.get("t") or 0) <= t and r.get("depth")]
    return max(before, key=lambda r: float(r.get("t") or 0)) if before else None


def evaluate_queue_window(symbol: str, bbo_rows: Iterable[Mapping[str, Any]],
                          prints: Iterable[Mapping[str, Any]], expiry_s: float,
                          tick: D, maker_fee_fn,
                          min_edge_after_fees: D = D("0.01"),
                          max_back: int = 2,
                          entry_max_tte_s: float = 300.0,
                          min_tte_s: float = 105.0,
                          first_wait_s: float = 180.0,
                          stop_before_expiry_s: float = 25.0) -> dict:
    """Sequential same-tape test of queue position versus complementability.

    At the first fully observed snapshot inside the canonical entry window we
    quote YES and NO simultaneously at touch / 1 populated level back /
    2 populated levels back. Strict trade-through determines which leg would
    hypothetically fill first. Only then do we re-read the actual book and ask
    whether a fee-safe complement at touch / 1-back / 2-back would strictly
    trade through before the stop horizon.

    This is deliberately not the legacy 0.98 complement rule and not a P&L
    claim. It isolates the transfer question: does better queue position buy
    enough two-sided completion to justify surrendering pair edge?
    """
    rows = sorted([r for r in bbo_rows or [] if r.get("depth")],
                  key=lambda r: float(r.get("t") or 0))
    ps = sorted(list(prints or []), key=lambda p: int(p.get("timestampms") or 0))
    eligible = [r for r in rows
                if min_tte_s <= expiry_s - float(r.get("t") or 0) <= entry_max_tte_s
                and (r.get("queue_arms") or {}).get("YES")
                and (r.get("queue_arms") or {}).get("NO")]
    if not eligible:
        return {"symbol": symbol, "status": "NO_QUEUE_ENTRY_COVERAGE",
                "semantics": SEMANTICS}
    entry = eligible[0]
    entry_t = float(entry.get("t") or 0)
    first_end = min(entry_t + first_wait_s, expiry_s - stop_before_expiry_s)
    result = {"symbol": symbol, "status": "SCORED", "entry_t": entry_t,
              "entry_tte_s": round(expiry_s - entry_t, 3),
              "entry_spread": entry.get("spread_depth"), "arms": [],
              "semantics": SEMANTICS}

    yes_arms = (entry.get("queue_arms") or {}).get("YES") or []
    no_arms = (entry.get("queue_arms") or {}).get("NO") or []
    for back in range(max_back + 1):
        if back >= len(yes_arms) or back >= len(no_arms):
            continue
        ya, na = yes_arms[back], no_arms[back]
        yp, np = D(ya["price"]), D(na["price"])
        initial_pair = pair_cost_after_known_maker_fees(yp, np, maker_fee_fn)
        yf = _strict_fill(ps, True, yp, entry_t, first_end)
        nf = _strict_fill(ps, False, np, entry_t, first_end)
        fills = [("YES", yf), ("NO", nf)]
        fills = [(s, f) for s, f in fills if f is not None]
        arm = {"entry_arm": ya.get("arm"), "populated_levels_back": back,
               "yes_price": str(yp), "no_price": str(np),
               "initial_pair_cost_after_known_fees": str(initial_pair),
               "initial_edge_after_known_fees": str(ONE - initial_pair),
               "yes_queue": ya.get("queue"), "no_queue": na.get("queue"),
               "first_fill": None, "complements": []}
        if not fills:
            arm["status"] = "NO_FIRST_FILL"
            result["arms"].append(arm)
            continue
        fills.sort(key=lambda x: x[1]["t"])
        first_side, ff = fills[0]
        # If both sides strictly trade through at the identical print timestamp,
        # count this only as an observed simultaneous strict-through event.
        simultaneous = (len(fills) > 1 and fills[1][1]["t"] == ff["t"])
        arm["first_fill"] = {"side": first_side, **ff}
        arm["simultaneous_strict_through"] = simultaneous
        if simultaneous:
            arm["status"] = "SIMULTANEOUS_STRICT_THROUGH"
            result["arms"].append(arm)
            continue

        comp_yes = first_side == "NO"
        comp_row = _row_at_or_after(rows, ff["t"])
        if comp_row is None:
            arm["status"] = "NO_COMPLEMENT_BOOK"
            result["arms"].append(arm)
            continue
        first_px = yp if first_side == "YES" else np
        comp_book = comp_row.get("depth") or {}
        cands = fee_safe_price_arms(comp_book, comp_yes, tick, first_px,
                                    maker_fee_fn, min_edge_after_fees,
                                    max_back=max_back)
        comp_end = expiry_s - stop_before_expiry_s
        for cand in cands:
            cp = D(cand["price"])
            cf = (_strict_fill(ps, comp_yes, cp, ff["t"], comp_end)
                  if cand.get("fee_safe") else None)
            arm["complements"].append({**cand,
                "side": "YES" if comp_yes else "NO",
                "quote_t": float(comp_row.get("t") or ff["t"]),
                "strict_fill": cf,
                "completed": cf is not None,
                "completion_latency_s": (round(cf["t"] - ff["t"], 3)
                                           if cf is not None else None)})
        arm["status"] = ("COMPLEMENTED" if any(c["completed"] for c in arm["complements"])
                         else "RESIDUAL")
        result["arms"].append(arm)
    return result


SEMANTICS = {
    "strict_trade_through": "strong hypothetical maker-fill evidence; not guaranteed fill",
    "at_price_print": "optimistic upper bound when queue priority is unknown",
    "shadow_pnl": "theoretical only; never realized P&L",
}
