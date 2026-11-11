"""Conservative shadow replay for the Gemini paired-maker engine.

Evidence class: SHADOW_MAKER_PRINT_PROXY_NOT_EXECUTABLE.

Causal rules:
- quotes are computed from book + flow strictly before the scored print;
- the scored print is added to the flow gauge only after scoring;
- one maker fill per side is allowed per BBO snapshot (consumed quote proxy),
  avoiding instantaneous infinite re-quoting at the front of the queue;
- rescue is recomputed after any maker fill before it can execute, so a maker
  completion cannot be followed by a stale taker rescue from the pre-fill state.

Promotion accounting:
- outcome-independent locked P&L is reported only for flat portfolios;
- last-print winner P&L is retained as a clearly-labeled heuristic diagnostic;
- incentives are excluded;
- rescue taker fees are not modeled, so any window using rescue is not
  promotion-grade evidence.
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.gemini_maker.pair_engine import PairBook, PairInventory, decide

OUT_PATH = Path("analysis/gemini/pair_engine_shadow.json")
MAX_BOOK_AGE_S = 30.0


def _win_close_epoch(symbol: str) -> Optional[float]:
    # "GEMI-BTC05M2608311215-UP" -> window core is the middle segment
    parts = symbol.split("-")
    core = parts[-2] if len(parts) >= 3 else parts[0]
    digits = core[-10:]
    if not digits.isdigit():
        return None
    try:
        return datetime(
            2000 + int(digits[0:2]), int(digits[2:4]), int(digits[4:6]),
            int(digits[6:8]), int(digits[8:10]), tzinfo=timezone.utc,
        ).timestamp()
    except ValueError:
        return None


def load_windows(tape_dir: str, since_ts: float) -> Dict[str, List[dict]]:
    events: Dict[str, List[dict]] = defaultdict(list)
    for path in sorted(glob.glob(f"{tape_dir}/tape_*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("venue") != "gemini" or e.get("kind") not in ("print", "bbo"):
                    continue
                market = str(e.get("market") or "")
                if not market.endswith("-UP") or e.get("t", 0) < since_ts:
                    continue
                events[market].append(e)
    return events


def evaluate_window(symbol: str, rows: List[dict]) -> Optional[Dict[str, Any]]:
    close = _win_close_epoch(symbol)
    if close is None:
        return None
    dur = 300 if "05M" in symbol else 900
    start = close - dur
    rows.sort(key=lambda e: e["t"])

    inv = PairInventory()
    flow = deque()  # (t, print dollars), only prints strictly before current score
    book = PairBook(None, None, None, None)
    book_t: Optional[float] = None
    consumed_sides = set()
    fills: List[dict] = []
    states = defaultdict(int)
    last_up_price = None
    first_hot_t = None
    stale_book_skips = 0

    def flow_60s(now: float) -> float:
        while flow and now - flow[0][0] > 60.0:
            flow.popleft()
        return sum(d for _, d in flow)

    for e in rows:
        t = e["t"]
        if t < start or t > close:
            continue

        if e["kind"] == "bbo":
            try:
                bids = [(float(p), float(q)) for p, q in (e.get("bids") or [])]
                asks = [(float(p), float(q)) for p, q in (e.get("asks") or [])]
            except (TypeError, ValueError):
                continue
            yb = max((p for p, _ in bids), default=None)
            ya = min((p for p, _ in asks), default=None)
            book = PairBook(
                yes_bid=yb, yes_ask=ya,
                no_bid=(1 - ya) if ya is not None else None,
                no_ask=(1 - yb) if yb is not None else None,
            )
            book_t = t
            consumed_sides.clear()
            continue

        try:
            price = float(e["price"])
            qty = float(e["qty"])
        except (TypeError, ValueError):
            continue
        taker_sold = bool(e.get("buyer_is_maker"))
        dollars = price * qty
        last_up_price = price

        # Strict anti-lookahead: current print is not yet in flow.
        f60 = flow_60s(t)
        if book_t is None or t - book_t > MAX_BOOK_AGE_S:
            stale_book_skips += 1
            flow.append((t, dollars))
            continue

        decision = decide(inv, book, f60, close - t, in_reward_pool=False)
        states[decision.state] += 1
        if decision.state == "HOT" and first_hot_t is None:
            first_hot_t = t - start

        maker_filled = False
        for q in decision.quotes:
            if q.side in consumed_sides:
                continue
            if q.side == "yes" and taker_sold and q.price >= price - 1e-9:
                filled = min(q.size, qty)
                inv.apply_fill("yes", filled, q.price)
                fills.append({"t": round(t - start, 1), "side": "yes", "price": q.price,
                              "qty": filled, "reason": q.reason})
                consumed_sides.add("yes")
                maker_filled = True
                break
            if q.side == "no" and not taker_sold and q.price >= (1 - price) - 1e-9:
                filled = min(q.size, qty)
                inv.apply_fill("no", filled, q.price)
                fills.append({"t": round(t - start, 1), "side": "no", "price": q.price,
                              "qty": filled, "reason": q.reason})
                consumed_sides.add("no")
                maker_filled = True
                break

        # Recompute rescue AFTER maker fill. This closes the stale-rescue bug
        # where the pre-fill decision could rescue an inventory already paired
        # by the scored maker print.
        rescue = decision.rescue
        if maker_filled:
            rescue = decide(inv, book, f60, close - t, in_reward_pool=False).rescue
        if rescue is not None:
            inv.apply_fill(rescue.side, rescue.size, rescue.price)
            fills.append({"t": round(t - start, 1), "side": rescue.side,
                          "price": rescue.price, "qty": rescue.size,
                          "reason": "RESCUE_TAKER"})

        # Only now may the scored print influence subsequent flow state.
        flow.append((t, dollars))

    if not fills:
        return None

    flat = abs(inv.residual) < 1e-9
    flat_pair_cost = inv.avg_pair_cost() if flat else None
    locked_pnl = (inv.pairs_completed() - inv.spend) if flat else None

    heuristic_winner = None
    heuristic_pnl = None
    if last_up_price is not None:
        heuristic_winner = "yes" if last_up_price >= 0.5 else "no"
        payout = inv.yes_qty if heuristic_winner == "yes" else inv.no_qty
        heuristic_pnl = payout - inv.spend

    rescue_fills = sum(1 for f in fills if f["reason"] == "RESCUE_TAKER")
    return {
        "market": symbol,
        "fills": fills,
        "maker_fills": len(fills) - rescue_fills,
        "rescue_fills": rescue_fills,
        "yes_qty": inv.yes_qty,
        "no_qty": inv.no_qty,
        "pairs": inv.pairs_completed(),
        "flat_at_expiry": flat,
        "avg_pair_cost_if_flat": flat_pair_cost,
        "residual": inv.residual,
        "worst_settlement_loss": round(inv.worst_settlement_loss(), 4),
        "spend": round(inv.spend, 4),
        "locked_pnl_if_flat": round(locked_pnl, 4) if locked_pnl is not None else None,
        "first_hot_s": round(first_hot_t, 1) if first_hot_t is not None else None,
        "state_counts": dict(states),
        "stale_book_skips": stale_book_skips,
        "winner_heuristic": heuristic_winner,
        "heuristic_pnl_not_for_promotion": round(heuristic_pnl, 4) if heuristic_pnl is not None else None,
        "promotion_grade": bool(flat and rescue_fills == 0 and flat_pair_cost is not None and flat_pair_cost <= 1.0 + 1e-9),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape-dir", default="data/policy_d_port")
    parser.add_argument("--since-utc", default="2026-08-31T10:59")
    args = parser.parse_args()
    since_ts = datetime.fromisoformat(args.since_utc).replace(tzinfo=timezone.utc).timestamp()
    windows = load_windows(args.tape_dir, since_ts)
    results = []
    for symbol, rows in sorted(windows.items()):
        r = evaluate_window(symbol, rows)
        if r:
            results.append(r)

    flat = [r for r in results if r["flat_at_expiry"]]
    agg = {
        "evidence_class": "SHADOW_MAKER_PRINT_PROXY_NOT_EXECUTABLE",
        "windows_with_fills": len(results),
        "flat_windows": len(flat),
        "promotion_grade_windows": sum(1 for r in results if r["promotion_grade"]),
        "total_pairs": sum(r["pairs"] for r in results),
        "flat_pairs_le_100": sum(1 for r in flat if r["avg_pair_cost_if_flat"] is not None
                                 and r["avg_pair_cost_if_flat"] <= 1.0 + 1e-9),
        "total_spend": round(sum(r["spend"] for r in results), 2),
        "locked_pnl_flat_windows": round(sum(r["locked_pnl_if_flat"] or 0.0 for r in flat), 4),
        "residual_worst_loss": round(sum(r["worst_settlement_loss"] for r in results if not r["flat_at_expiry"]), 4),
        "rescue_fills": sum(r["rescue_fills"] for r in results),
        "stale_book_skips": sum(r["stale_book_skips"] for r in results),
    }
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "since_utc": args.since_utc,
        "assumptions": {
            "queue_model": "one fill per side per BBO snapshot; still a print-through proxy",
            "maker_fees": "excluded here; live promo economics must be applied separately",
            "rescue_taker_fees": "not modeled; rescue windows are not promotion grade",
            "winner": "last-print heuristic diagnostic only; never promotion accounting",
            "incentives": "excluded from decisions and shadow P&L",
        },
        "aggregate": agg,
        "per_window": results,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(json.dumps(agg, indent=1))
    for r in results:
        pc = r["avg_pair_cost_if_flat"]
        print(f'{r["market"]:24s} pairs={r["pairs"]:.0f} flat={r["flat_at_expiry"]} '
              f'pair_cost={(pc if pc is not None else float("nan")):.3f} '
              f'residual={r["residual"]:+.0f} locked={r["locked_pnl_if_flat"]} '
              f'rescues={r["rescue_fills"]} hot_s={r["first_hot_s"]}')


if __name__ == "__main__":
    main()
