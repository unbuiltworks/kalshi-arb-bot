"""Frozen policy-D shadow evaluation over collected Kalshi/Gemini tapes.

FROZEN constants (L5 clone-state replay, 2026-08-30): 30-print rolling tape,
light-side threshold -0.1, price floor $0.30. Do not tune here.

Shadow fill semantics (MAKER_PRINT_PROXY, per the repo's census methodology):
we hypothetically JOIN the best bid of the tape-contra side. A later print in
which the taker sells INTO that side at/through our price counts as an eligible
shadow fill. This proxies fill-conditioned economics; it does not prove queue
position, and it is scored as if we captured min(print_qty, Q_CAP) contracts.

Anti-lookahead guards:
- the tape imbalance driving a stance excludes the print being evaluated;
- the quote price comes from the latest BBO snapshot strictly BEFORE the print;
- a stance must have been continuously active >= MIN_STANCE_SECONDS before the
  print;
- at most one shadow fill per side per BBO snapshot (a resting order, once
  hit, is gone until we re-quote).

Scoring: settlement per contract (winner pays $1) minus entry price, minus the
venue maker fee ceil(0.0175 * C * P * (1-P)) (both venues' published maker
schedule). Windows without an authoritative or high-confidence resolution are
excluded and counted.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict, deque
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TAPE_WINDOW = 30
TAPE_LIGHT = -0.1
PRICE_FLOOR = 0.30
MIN_STANCE_SECONDS = 1.0
Q_CAP = 10.0

KALSHI_REST = "https://api.elections.kalshi.com/trade-api/v2"
GEMINI_REST = "https://api.gemini.com"


def maker_fee(price: float, quantity: float) -> float:
    return math.ceil(0.0175 * quantity * price * (1.0 - price) * 100.0) / 100.0


def load_events(paths: List[Path]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    events.sort(key=lambda e: e.get("t", 0))
    return events


def _print_direction(event: Dict[str, Any]) -> Optional[str]:
    """Which side did the taker BUY? 'Up' or 'Down'."""
    if event["venue"] == "kalshi":
        side = event.get("taker_side")
        return "Up" if side == "yes" else ("Down" if side == "no" else None)
    maker_flag = event.get("buyer_is_maker")
    if maker_flag is None:
        return None
    return "Down" if maker_flag else "Up"


def _print_price_qty(event: Dict[str, Any]) -> Tuple[Optional[float], float]:
    if event["venue"] == "kalshi":
        price = event.get("yes_price")
        qty = event.get("count")
    else:
        price = event.get("price")
        qty = event.get("qty")
    try:
        return (float(price) if price not in (None, "") else None,
                float(qty) if qty not in (None, "") else 0.0)
    except (TypeError, ValueError):
        return None, 0.0


def _bbo_quotes(event: Dict[str, Any]) -> Dict[str, float]:
    """Joinable bid price per side, in UP/YES-price terms for Up, own terms for Down."""
    quotes: Dict[str, float] = {}
    try:
        if event["venue"] == "kalshi":
            yes_bids = event.get("yes_bids") or []
            no_bids = event.get("no_bids") or []
            if yes_bids:
                quotes["Up"] = max(float(level[0]) for level in yes_bids)
            if no_bids:
                quotes["Down"] = max(float(level[0]) for level in no_bids)
        else:
            bids = event.get("bids") or []
            asks = event.get("asks") or []
            if bids:
                quotes["Up"] = max(float(level[0]) for level in bids)
            if asks:
                quotes["Down"] = round(1.0 - min(float(level[0]) for level in asks), 6)
    except (TypeError, ValueError, IndexError):
        return {}
    return quotes


class Tape:
    def __init__(self) -> None:
        self.rows: deque = deque(maxlen=TAPE_WINDOW)

    def add(self, direction: str, qty: float) -> None:
        self.rows.append((direction, qty))

    def imbalance_toward(self, side: str) -> float:
        total = sum(q for _, q in self.rows)
        if total <= 1e-12:
            return 0.0
        up = sum(q for d, q in self.rows if d == "Up")
        signed = (2.0 * up - total) / total
        return signed if side == "Up" else -signed


def evaluate_market(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Shadow-run frozen policy D over one market's time-ordered events."""
    tape = Tape()
    quotes: Dict[str, float] = {}
    quote_epoch: Dict[str, int] = {}       # BBO snapshot counter per side
    filled_epoch: Dict[str, int] = {"Up": -1, "Down": -1}
    stance_since: Dict[str, Optional[float]] = {"Up": None, "Down": None}
    bbo_count = 0
    fills: List[Dict[str, Any]] = []
    prints = 0

    for event in events:
        kind = event.get("kind")
        now = event.get("t", 0.0)
        if kind == "bbo":
            bbo_count += 1
            for side, price in _bbo_quotes(event).items():
                quotes[side] = price
                quote_epoch[side] = bbo_count
            continue
        if kind != "print":
            continue
        direction = _print_direction(event)
        price, qty = _print_price_qty(event)
        if direction is None or price is None or qty <= 0:
            continue
        prints += 1

        # Update stance clocks from the tape EXCLUDING this print.
        for side in ("Up", "Down"):
            active = (
                tape.imbalance_toward(side) < TAPE_LIGHT
                and quotes.get(side) is not None
                and quotes[side] >= PRICE_FLOOR
            )
            if active and stance_since[side] is None:
                stance_since[side] = now
            elif not active:
                stance_since[side] = None

        sold_side = "Down" if direction == "Up" else "Up"
        stance_start = stance_since[sold_side]
        quote = quotes.get(sold_side)
        if (
            stance_start is not None
            and now - stance_start >= MIN_STANCE_SECONDS
            and quote is not None
            and filled_epoch[sold_side] < quote_epoch.get(sold_side, 0)
        ):
            # Print price is in YES/UP terms; our Down quote fills when the
            # taker's sale reaches our level on that side's own price scale.
            print_on_side = price if sold_side == "Up" else round(1.0 - price, 6)
            if print_on_side <= quote + 1e-9:
                fill_qty = min(qty, Q_CAP)
                fills.append({
                    "t": now, "side": sold_side, "price": quote, "qty": fill_qty,
                    "fee": maker_fee(quote, fill_qty),
                })
                filled_epoch[sold_side] = quote_epoch.get(sold_side, 0)

        tape.add(direction, qty)

    return {"prints": prints, "bbo_snapshots": bbo_count, "fills": fills}


def resolve_winner(venue: str, market: str, events: List[Dict[str, Any]],
                   http: Any = None) -> Tuple[str, str]:
    """(winner, source). Kalshi: authoritative market result. Gemini: last-print
    heuristic (>=0.95 Up / <=0.05 Down) flagged as provisional."""
    if venue == "kalshi" and http is not None:
        try:
            response = http.get(f"{KALSHI_REST}/markets/{market}")
            if response.status_code == 200:
                result = str((response.json().get("market") or {}).get("result") or "")
                if result == "yes":
                    return "Up", "kalshi_result"
                if result == "no":
                    return "Down", "kalshi_result"
        except Exception:
            pass
        return "", "unresolved"
    last_price = None
    for event in reversed(events):
        if event.get("kind") == "print":
            price, _ = _print_price_qty(event)
            if price is not None:
                last_price = price
                break
    if last_price is None:
        return "", "unresolved"
    if last_price >= 0.95:
        return "Up", "last_print_heuristic"
    if last_price <= 0.05:
        return "Down", "last_print_heuristic"
    return "", "unresolved"


def run(tape_dir: Path = Path("data/policy_d_port"),
        out_path: Optional[Path] = None) -> Dict[str, Any]:
    import httpx

    events = load_events(sorted(tape_dir.glob("tape_*.jsonl")))
    by_market: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("venue") in ("kalshi", "gemini") and event.get("market"):
            by_market[(event["venue"], event["market"])].append(event)

    per_market = []
    agg: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    with httpx.Client(timeout=10) as http:
        for (venue, market), rows in sorted(by_market.items()):
            shadow = evaluate_market(rows)
            winner, source = resolve_winner(venue, market, rows, http)
            gross = fees = dollars = 0.0
            for fill in shadow["fills"]:
                dollars += fill["price"] * fill["qty"]
                fees += fill["fee"]
                if winner:
                    gross += fill["qty"] * (1.0 if fill["side"] == winner else 0.0) - fill["price"] * fill["qty"]
            row = {
                "venue": venue, "market": market, "prints": shadow["prints"],
                "bbo_snapshots": shadow["bbo_snapshots"], "shadow_fills": len(shadow["fills"]),
                "winner": winner, "winner_source": source,
                "fill_dollars": round(dollars, 4),
                "gross_pnl": round(gross, 4) if winner else None,
                "fees": round(fees, 4),
                "net_pnl": round(gross - fees, 4) if winner else None,
            }
            per_market.append(row)
            key = venue
            agg[key]["markets"] += 1
            agg[key]["prints"] += shadow["prints"]
            agg[key]["shadow_fills"] += len(shadow["fills"])
            if winner:
                agg[key]["resolved_markets"] += 1
                agg[key]["fill_dollars"] += dollars
                agg[key]["gross_pnl"] += gross
                agg[key]["fees"] += fees
            else:
                agg[key]["unresolved_markets"] += 1

    summary = {}
    for venue, acc in agg.items():
        net = acc["gross_pnl"] - acc["fees"]
        summary[venue] = {
            **{k: round(v, 4) for k, v in acc.items()},
            "net_pnl": round(net, 4),
            "net_pnl_per_dollar": round(net / acc["fill_dollars"], 5) if acc.get("fill_dollars") else None,
        }
    payload = {
        "generated_at": time.time(),
        "frozen_constants": {"tape_window": TAPE_WINDOW, "light": TAPE_LIGHT,
                             "price_floor": PRICE_FLOOR, "min_stance_s": MIN_STANCE_SECONDS,
                             "q_cap": Q_CAP},
        "summary": summary,
        "per_market": per_market,
    }
    out_path = Path(out_path or Path("analysis/l5/policy_d_port_shadow.json"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":  # pragma: no cover
    result = run()
    print(json.dumps(result["summary"], indent=1, default=str))
