"""Rescue/exit frontier (shadow, predeclared) — can we cut taker rescues to
< 1 per 15-20 completed pairs while keeping the maker fills?

ChatGPT-scoped experiment after the tick-level replay showed rescue drag
(6-7 rescues at taker fees) is the dominant cost, with completed pairs
structurally flat at fresh books. Production engines are NOT modified.

PREDECLARED VARIANTS (all constants fixed here before replay; the eight
2026-08-31 hot windows are IN-SAMPLE DIAGNOSTIC ONLY — decision evidence
comes from windows closing AFTER FUTURE_CUTOFF_UTC):

  R0 baseline        entry: join both bids when flow>=WARM; completion at
                     min(portfolio ceiling, ask-1c); rescue zone 60s,
                     premium cap 3c. (Reproduces the prior replay.)
  R1 patient         completion joins the complement bid, leaning linearly
                     to the ceiling from 120s->30s tte (policy B pricing);
                     rescue is an EMERGENCY BRAKE: only inside 30s tte AND
                     residual worst-loss > $0.50.
  R2 dual-exit       R1 + a simultaneous same-side maker UNWIND ask on the
                     held side at max(join ask, avg_cost+0.01) — get flat by
                     selling the first leg back instead of paying taker.
  R3 pairable-entry  R1 + entries only when trailing flow classifies
                     PAIRABLE (alternation, dominance, two-sided depth via
                     flow_class thresholds) — don't take a first leg whose
                     second leg doesn't look fillable.
  R4 router          R2 + R3 combined (pairability-gated entry, dual maker
                     exits, emergency-only rescue). Approximates the V3
                     four-exit router without the fair-value gate (which has
                     not passed its evidence gates).

Fees: official schedule, ceil-per-fill (maker 0.0175 / taker 0.07); rebates
30% of raw taker formula, floored per fill, 20-80c band, separate line.
Fill proxy and causality: identical to completion_frontier (one fill per
side per BBO snapshot, book age <= MAX_BOOK_AGE_S, prints excluded from
their own flow gauge). Source: tick-level ref_lag pbook/ptrade.

Headline metrics per variant: rescues per 15 pairs, flat rate, locked raw,
net under Sep-2 fee-free maker, rebates separate, P&L per capital-minute.

Usage:
  python3 -m src.gemini_maker.rescue_frontier            # in-sample diagnostic
  python3 -m src.gemini_maker.rescue_frontier --future   # future windows only
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.gemini_maker.completion_frontier import (
    _fee,
    _rebate,
    load_ref_lag_windows,
)
from src.gemini_maker.shadow_pair_eval import _win_close_epoch
from src.gemini_maker.flow_class import (
    PAIRABLE_MAX_ONE_SIDE,
    PAIRABLE_MIN_ALTERNATION,
    PAIRABLE_MIN_PRINTS,
)

OUT_PATH = Path("analysis/gemini/rescue_frontier.json")
FUTURE_CUTOFF_UTC = "2026-08-31T15:45"   # windows closing after this = evidence

# Predeclared constants (do not tune after replay)
WARM_FLOW_60S = 10.0
ENTRY_SIZE = 5.0
MAX_BOOK_AGE_S = 2.0            # fresh-book requirement for all variants
LEAN_START_S = 120.0
LEAN_FULL_S = 30.0
PAIR_CEILING = 1.00
BASE_RESCUE_ZONE_S = 60.0
BASE_RESCUE_PREMIUM = 0.03
EMERGENCY_RESCUE_ZONE_S = 30.0
EMERGENCY_MIN_WORST_LOSS = 0.50
UNWIND_MIN_PROFIT = 0.01
MAX_SPEND = 25.0
MAX_WORST_LOSS = 5.0
VARIANTS = ("R0", "R1", "R2", "R3", "R4")


class Inv:
    def __init__(self) -> None:
        self.yes_qty = self.yes_cost = self.no_qty = self.no_cost = 0.0
        self.realized = 0.0

    def buy(self, side: str, qty: float, price: float) -> None:
        if side == "yes":
            self.yes_qty += qty; self.yes_cost += qty * price
        else:
            self.no_qty += qty; self.no_cost += qty * price

    def sell(self, side: str, qty: float, price: float) -> None:
        if side == "yes":
            avg = self.yes_cost / self.yes_qty
            self.yes_qty -= qty; self.yes_cost -= qty * avg
            self.realized += qty * (price - avg)
        else:
            avg = self.no_cost / self.no_qty
            self.no_qty -= qty; self.no_cost -= qty * avg
            self.realized += qty * (price - avg)

    @property
    def spend(self) -> float:
        return self.yes_cost + self.no_cost

    @property
    def residual(self) -> float:
        return self.yes_qty - self.no_qty

    def worst_loss(self) -> float:
        return max(0.0, -min(self.yes_qty - self.spend,
                             self.no_qty - self.spend))

    def pairs(self) -> float:
        return min(self.yes_qty, self.no_qty)

    def ceiling(self) -> Optional[float]:
        qty = abs(self.residual)
        if qty < 1e-9:
            return None
        return (max(self.yes_qty, self.no_qty) * PAIR_CEILING - self.spend) / qty


def _pairable(prints: List[tuple], bid_depth: float, ask_depth: float) -> bool:
    rows = [p for p in prints]
    total = sum(d for _, _, d in rows)
    if total < WARM_FLOW_60S or len(rows) < PAIRABLE_MIN_PRINTS:
        return False
    buys = sum(d for _, s, d in rows if s == "buy")
    one_side = max(buys, total - buys) / total if total else 1.0
    flips = sum(1 for (_, a, _), (_, b, _) in zip(rows, rows[1:]) if a != b)
    alternation = flips / (len(rows) - 1) if len(rows) > 1 else 0.0
    return (alternation >= PAIRABLE_MIN_ALTERNATION
            and one_side <= PAIRABLE_MAX_ONE_SIDE
            and bid_depth >= 10 and ask_depth >= 10)


def evaluate(symbol: str, rows: List[dict], variant: str) -> Optional[Dict[str, Any]]:
    close = _win_close_epoch(symbol)
    if close is None:
        return None
    dur = 300 if "05M" in symbol else 900
    start = close - dur
    rows = sorted(rows, key=lambda e: e["t"])
    inv = Inv()
    flow: deque = deque()          # (t, "buy"/"sell", dollars)
    book = None
    book_t = None
    consumed: set = set()
    fills: List[dict] = []
    cap_seconds = 0.0
    last_t = None

    dual_exit = variant in ("R2", "R4")
    pair_gate = variant in ("R3", "R4")
    patient = variant != "R0"

    def flow60(now: float) -> List[tuple]:
        while flow and now - flow[0][0] > 60.0:
            flow.popleft()
        return list(flow)

    for e in rows:
        t = e["t"]
        if t < start or t > close:
            continue
        if last_t is not None:
            cap_seconds += inv.spend * (t - last_t)
        last_t = t
        if e["kind"] == "bbo":
            b, B = e["bids"][0]
            a, A = e["asks"][0]
            book = {"yb": b, "yB": B, "ya": a, "yA": A,
                    "nb": round(1 - a, 4), "na": round(1 - b, 4)}
            book_t = t
            consumed.clear()
            continue
        try:
            price = float(e["price"]); qty = float(e["qty"])
        except (TypeError, ValueError):
            continue
        taker_sold = bool(e.get("buyer_is_maker"))
        tte = close - t
        fl = flow60(t)
        fresh = book is not None and book_t is not None and t - book_t <= MAX_BOOK_AGE_S
        if fresh and inv.spend < MAX_SPEND:
            quotes: List[tuple] = []   # (kind, side, price, size)
            res = inv.residual
            if abs(res) < 1.0:
                total_flow = sum(d for _, _, d in fl)
                entry_ok = total_flow >= WARM_FLOW_60S and tte > 30.0
                if entry_ok and pair_gate:
                    entry_ok = _pairable(fl, book["yB"], book["yA"])
                if entry_ok:
                    probe_wl = max(book["yb"], book["nb"]) * ENTRY_SIZE
                    if probe_wl <= MAX_WORST_LOSS:
                        quotes.append(("buy", "yes", book["yb"], ENTRY_SIZE))
                        quotes.append(("buy", "no", book["nb"], ENTRY_SIZE))
            else:
                held, comp = ("yes", "no") if res > 0 else ("no", "yes")
                ceil_p = inv.ceiling()
                comp_bid = book["nb"] if comp == "no" else book["yb"]
                comp_ask = book["na"] if comp == "no" else book["ya"]
                if ceil_p is not None and ceil_p >= 0.01:
                    if patient:
                        frac = 1.0 if tte <= LEAN_FULL_S else (
                            0.0 if tte >= LEAN_START_S else
                            (LEAN_START_S - tte) / (LEAN_START_S - LEAN_FULL_S))
                        px = comp_bid + frac * max(0.0, ceil_p - comp_bid)
                    else:
                        px = min(ceil_p, (comp_ask - 0.01) if comp_ask else ceil_p)
                    px = round(min(px, ceil_p), 2)
                    if px >= 0.01:
                        quotes.append(("buy", comp, px, abs(res)))
                if dual_exit:
                    held_ask = book["ya"] if held == "yes" else book["na"]
                    avg = (inv.yes_cost / inv.yes_qty if held == "yes"
                           else inv.no_cost / inv.no_qty)
                    upx = round(max(held_ask, avg + UNWIND_MIN_PROFIT), 2)
                    quotes.append(("sell", held, upx, abs(res)))
            for kind, side, qprice, qsize in quotes:
                key = (kind, side)
                if key in consumed:
                    continue
                filled = 0.0
                if kind == "buy" and side == "yes" and taker_sold and qprice >= price - 1e-9:
                    filled = min(qsize, qty); inv.buy("yes", filled, qprice)
                elif kind == "buy" and side == "no" and not taker_sold and qprice >= (1 - price) - 1e-9:
                    filled = min(qsize, qty); inv.buy("no", filled, qprice)
                elif kind == "sell" and side == "yes" and not taker_sold and price >= qprice - 1e-9:
                    filled = min(qsize, qty, inv.yes_qty); inv.sell("yes", filled, qprice)
                elif kind == "sell" and side == "no" and taker_sold and (1 - price) >= qprice - 1e-9:
                    filled = min(qsize, qty, inv.no_qty); inv.sell("no", filled, qprice)
                if filled > 0:
                    fills.append({"t": round(t - start, 1), "kind": kind,
                                  "side": side, "price": qprice, "qty": filled})
                    consumed.add(key)
                    break
        # Rescue check (uses fresh book only)
        res = inv.residual
        if fresh and abs(res) >= 1.0:
            held, comp = ("yes", "no") if res > 0 else ("no", "yes")
            comp_ask = book["na"] if comp == "no" else book["ya"]
            ceil_p = inv.ceiling()
            if variant == "R0":
                allowed = (tte <= BASE_RESCUE_ZONE_S and ceil_p is not None
                           and comp_ask is not None
                           and comp_ask <= ceil_p + BASE_RESCUE_PREMIUM)
            else:
                allowed = (tte <= EMERGENCY_RESCUE_ZONE_S
                           and inv.worst_loss() > EMERGENCY_MIN_WORST_LOSS
                           and comp_ask is not None and ceil_p is not None
                           and comp_ask <= ceil_p + BASE_RESCUE_PREMIUM)
            if allowed:
                qty_r = abs(res)
                inv.buy(comp, qty_r, comp_ask)
                fills.append({"t": round(t - start, 1), "kind": "rescue",
                              "side": comp, "price": comp_ask, "qty": qty_r})
        flow.append((t, "buy" if not taker_sold else "sell", price * qty))

    if not fills:
        return None
    maker = [f for f in fills if f["kind"] in ("buy", "sell")]
    rescues = [f for f in fills if f["kind"] == "rescue"]
    flat = abs(inv.residual) < 1e-9
    maker_fees = sum(_fee(f["qty"], f["price"], 0.0175) for f in maker)
    taker_fees = sum(_fee(f["qty"], f["price"], 0.07) for f in rescues)
    rebates = sum(_rebate(f["qty"], f["price"]) for f in maker)
    locked = (inv.pairs() - inv.spend + inv.realized) if flat else None
    return {
        "market": symbol, "flat": flat, "maker_fills": len(maker),
        "unwind_fills": sum(1 for f in maker if f["kind"] == "sell"),
        "rescues": len(rescues), "pairs": round(inv.pairs(), 1),
        "locked_raw": round(locked, 4) if locked is not None else None,
        "realized_unwind": round(inv.realized, 4),
        "residual_worst_loss": round(inv.worst_loss(), 4),
        "maker_fees": round(maker_fees, 2), "taker_fees": round(taker_fees, 2),
        "rebates_separate": round(rebates, 2),
        "capital_minutes": round(cap_seconds / 60.0, 2),
    }


def run(future_only: bool = False) -> Dict[str, Any]:
    since = datetime.fromisoformat("2026-08-31T10:59").replace(
        tzinfo=timezone.utc).timestamp()
    cutoff = datetime.fromisoformat(FUTURE_CUTOFF_UTC).replace(
        tzinfo=timezone.utc).timestamp()
    windows = load_ref_lag_windows("data/ref_lag", since)
    table: Dict[str, Any] = {}
    for variant in VARIANTS:
        results = []
        for symbol, rows in sorted(windows.items()):
            close = _win_close_epoch(symbol)
            if close is None:
                continue
            if future_only and close <= cutoff:
                continue
            if not future_only and close > cutoff:
                continue
            r = evaluate(symbol, rows, variant)
            if r:
                results.append(r)
        flat = [r for r in results if r["flat"]]
        pairs = sum(r["pairs"] for r in results)
        rescues = sum(r["rescues"] for r in results)
        locked = sum(r["locked_raw"] or 0 for r in flat)
        unreal = sum(r["realized_unwind"] for r in results if not r["flat"])
        taker_fees = sum(r["taker_fees"] for r in results)
        maker_fees = sum(r["maker_fees"] for r in results)
        cap_min = sum(r["capital_minutes"] for r in results)
        net_sep2 = locked - taker_fees
        table[variant] = {
            "engaged": len(results), "flat_windows": len(flat),
            "maker_fills": sum(r["maker_fills"] for r in results),
            "unwind_fills": sum(r["unwind_fills"] for r in results),
            "pairs": round(pairs, 1), "rescues": rescues,
            "rescues_per_15_pairs": round(15.0 * rescues / pairs, 2) if pairs else None,
            "locked_raw": round(locked, 2),
            "residual_worst_loss": round(sum(
                r["residual_worst_loss"] for r in results if not r["flat"]), 2),
            "net_aug31": round(locked - maker_fees - taker_fees, 2),
            "net_sep2_feefree": round(net_sep2, 2),
            "rebates_separate": round(sum(r["rebates_separate"] for r in results), 2),
            "capital_minutes": round(cap_min, 1),
            "net_sep2_per_capital_minute": round(net_sep2 / cap_min, 4) if cap_min else None,
        }
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "SHADOW_MAKER_PRINT_PROXY_NOT_EXECUTABLE",
        "sample": "FUTURE_EVIDENCE" if future_only else "IN_SAMPLE_DIAGNOSTIC",
        "future_cutoff_utc": FUTURE_CUTOFF_UTC,
        "max_book_age_s": MAX_BOOK_AGE_S,
        "frontier": table,
    }
    out = OUT_PATH if not future_only else Path(
        "analysis/gemini/rescue_frontier_future.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    for v, row in table.items():
        print(f'{v}: eng={row["engaged"]} pairs={row["pairs"]} rescues={row["rescues"]} '
              f'(per15={row["rescues_per_15_pairs"]}) unwinds={row["unwind_fills"]} '
              f'flat={row["flat_windows"]} locked={row["locked_raw"]} '
              f'netSep2={row["net_sep2_feefree"]} rebates={row["rebates_separate"]} '
              f'perCapMin={row["net_sep2_per_capital_minute"]}')
    return payload


if __name__ == "__main__":
    import sys
    run(future_only="--future" in sys.argv)
