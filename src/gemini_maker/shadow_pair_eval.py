"""Shadow-replay the paired-maker engine over collected Gemini tape.

Answers the test the plan names: could we detect the start of a hot window,
acquire BOTH sides as maker, and finish near-flat at <= 100c combined cost?

Fill proxy (same discipline as the Kalshi shadow): a real print fills our
resting quote when it trades at/through our price - front-of-queue assumption,
stated caveat. Anti-lookahead: quotes come from book+flow state STRICTLY
before the scored print; the scored print is excluded from its own flow gauge.

Book mapping (single UP-contract book): our yes bid rests on the UP bid side;
our no bid at q is an UP ask at (1-q).
  - taker SELL print (buyer_is_maker=True) at p fills our yes bid if bid >= p
  - taker BUY print (buyer_is_maker=False) at p fills our no bid if bid >= 1-p

Fees: maker fees 0 (Gemini waives qualifying prediction maker fees Sep 2 -
Dec 31); rescue taker cost is the crossing price itself, no extra fee modeled
(flagged - real taker fee schedule TBD). Incentives are NOT included anywhere.

Winner: last-print heuristic (last UP price >= 0.5 -> Up). Flagged.

Usage: python3 -m src.gemini_maker.shadow_pair_eval [--since-utc 2026-08-31T11:00]
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.gemini_maker.pair_engine import (
    PairBook,
    PairInventory,
    decide,
)

OUT_PATH = Path("analysis/gemini/pair_engine_shadow.json")


def _win_close_epoch(symbol: str) -> Optional[float]:
    # BTC05M2608311205-UP -> closes 2026-08-31 12:05 UTC
    core = symbol.split("-")[0]
    digits = core[-10:]
    if not digits.isdigit():
        return None
    try:
        return datetime(2000 + int(digits[0:2]), int(digits[2:4]), int(digits[4:6]),
                        int(digits[6:8]), int(digits[8:10]), tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def load_windows(tape_dir: str, since_ts: float) -> Dict[str, List[dict]]:
    events: Dict[str, List[dict]] = defaultdict(list)
    for path in sorted(glob.glob(f"{tape_dir}/tape_*.jsonl")):
        for line in open(path, encoding="utf-8"):
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
    flow = deque()          # (t, dollars)
    book = PairBook(None, None, None, None)
    fills: List[dict] = []
    states = defaultdict(int)
    rescue_done = False
    last_up_price = None
    first_hot_t = None

    def flow_60s(now: float, exclude_last: bool = False) -> float:
        while flow and now - flow[0][0] > 60.0:
            flow.popleft()
        rows_ = list(flow)[:-1] if (exclude_last and flow) else list(flow)
        return sum(d for _, d in rows_)

    for e in rows:
        t = e["t"]
        if t < start or t > close:
            if e["kind"] == "print":
                try:
                    last_up_price = float(e["price"])
                except (TypeError, ValueError):
                    pass
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
            continue
        # print
        try:
            price = float(e["price"]); qty = float(e["qty"])
        except (TypeError, ValueError):
            continue
        taker_sold = bool(e.get("buyer_is_maker"))
        dollars = price * qty
        flow.append((t, dollars))
        last_up_price = price
        # decide from state BEFORE this print (exclude it from the gauge)
        f60 = flow_60s(t, exclude_last=True)
        decision = decide(inv, book, f60, close - t, in_reward_pool=False)
        states[decision.state] += 1
        if decision.state == "HOT" and first_hot_t is None:
            first_hot_t = t - start
        for q in decision.quotes:
            if q.side == "yes" and taker_sold and q.price >= price - 1e-9:
                filled = min(q.size, qty)
                inv.apply_fill("yes", filled, q.price)
                fills.append({"t": round(t - start, 1), "side": "yes",
                              "price": q.price, "qty": filled, "reason": q.reason})
                break
            if q.side == "no" and not taker_sold and q.price >= (1 - price) - 1e-9:
                filled = min(q.size, qty)
                inv.apply_fill("no", filled, q.price)
                fills.append({"t": round(t - start, 1), "side": "no",
                              "price": q.price, "qty": filled, "reason": q.reason})
                break
        if decision.rescue is not None and not rescue_done:
            r = decision.rescue
            inv.apply_fill(r.side, r.size, r.price)
            fills.append({"t": round(t - start, 1), "side": r.side, "price": r.price,
                          "qty": r.size, "reason": "RESCUE_TAKER"})
            rescue_done = True

    if not fills:
        return None
    winner = None
    if last_up_price is not None:
        winner = "yes" if last_up_price >= 0.5 else "no"
    payout = None
    pnl = None
    if winner is not None:
        payout = inv.yes_qty if winner == "yes" else inv.no_qty
        pnl = payout - inv.spend
    return {
        "market": symbol,
        "fills": fills,
        "maker_fills": sum(1 for f in fills if f["reason"] != "RESCUE_TAKER"),
        "rescue_fills": sum(1 for f in fills if f["reason"] == "RESCUE_TAKER"),
        "yes_qty": inv.yes_qty, "no_qty": inv.no_qty,
        "pairs": inv.pairs_completed(),
        "avg_pair_cost": inv.avg_pair_cost(),
        "residual": inv.residual,
        "spend": round(inv.spend, 4),
        "first_hot_s": round(first_hot_t, 1) if first_hot_t is not None else None,
        "state_counts": dict(states),
        "winner_heuristic": winner,
        "trading_pnl": round(pnl, 4) if pnl is not None else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tape-dir", default="data/policy_d_port")
    parser.add_argument("--since-utc", default="2026-08-31T10:59",
                        help="only score tape at/after this UTC time (persistent-ws era)")
    args = parser.parse_args()
    since_ts = datetime.fromisoformat(args.since_utc).replace(
        tzinfo=timezone.utc).timestamp()
    windows = load_windows(args.tape_dir, since_ts)
    results = []
    for symbol, rows in sorted(windows.items()):
        r = evaluate_window(symbol, rows)
        if r:
            results.append(r)
    scored = [r for r in results if r["trading_pnl"] is not None]
    agg = {
        "windows_with_fills": len(results),
        "windows_scored": len(scored),
        "total_pairs": sum(r["pairs"] for r in results),
        "pairs_le_100": sum(1 for r in results if r["avg_pair_cost"] is not None
                            and r["avg_pair_cost"] <= 1.0 + 1e-9),
        "total_spend": round(sum(r["spend"] for r in results), 2),
        "total_trading_pnl": round(sum(r["trading_pnl"] for r in scored), 4),
        "windows_flat_at_expiry": sum(1 for r in results if abs(r["residual"]) < 1.0),
    }
    payload = {"generated_utc": datetime.now(timezone.utc).isoformat(),
               "since_utc": args.since_utc, "aggregate": agg, "per_window": results}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(json.dumps(agg, indent=1))
    for r in results:
        print(f'{r["market"]:24s} pairs={r["pairs"]:.0f} avg_pair='
              f'{(r["avg_pair_cost"] or 0):.3f} residual={r["residual"]:+.0f} '
              f'pnl={r["trading_pnl"]} first_hot_s={r["first_hot_s"]}')


if __name__ == "__main__":
    main()
