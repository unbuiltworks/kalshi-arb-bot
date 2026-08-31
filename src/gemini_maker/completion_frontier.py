"""Completion-policy frontier for the Gemini pair engine (shadow only).

ChatGPT audit follow-up to the first hot-window result: the production
engine's completion quote (min(portfolio ceiling, ask-1c)) spends the whole
pair budget immediately, so every completed pair costs exactly $1.000. This
experiment measures the patience/aggression frontier WITHOUT touching the
production engine: completion pricing is overridden in the replay layer only.

PREDECLARED POLICIES (thresholds fixed before replay; do not tune after):
  A  baseline: engine behavior, min(ceiling, ask-1c).
  B  join-then-lean: start at the missing side's best bid; lean linearly
     toward the ceiling from LEAN_START_S=120s tte, reaching full ceiling at
     LEAN_FULL_S=30s tte.
  C  <=99c raw pair until the safety zone (SAFETY_S=60s tte), then <=100c.
  D  staged: <=98c while tte>180s, <=99c while 180>=tte>60s, <=100c after.

All policies remain capped by the portfolio-safe ceiling and by ask-1c
(maker-only). Entries and everything else are untouched.

EVIDENCE CLASS: SHADOW_MAKER_PRINT_PROXY_NOT_EXECUTABLE. Additionally, every
simulated fill records book_age_ms, and results are reported at fresh-book
thresholds [0.25s, 0.5s, 1s, 2s, 5s, 30s]. NOTE: the policy_d_port tape
throttles BBO writes to ~5s, so sub-5s thresholds are TAPE-limited, not
market-limited; the tick-level ref_lag pbook stream is the migration path.

FEES / REBATES (reported separately, never mixed into decisions):
- Aug-31 maker fee model: rate*C*P*(1-P) with FEE_RATE_AUG31=0.07 —
  REVERSE-ENGINEERED from the audit's rebate example; the live wrapper must
  fetch the actual schedule. Sep-2+: maker fees waived (0).
- Rebate model: floor_to_cent(0.30 * 0.07 * C * P * (1-P)) per maker fill
  (30% Crypto multiplier, per-fill round-down). Reported as a separate line;
  projected rebates never justify >$1 pairs.

TERMINOLOGY: a $1.000 raw maker/maker pair is STRUCTURALLY FLAT — it is NOT
economically breakeven under Aug-31 fees.

Usage: python3 -m src.gemini_maker.completion_frontier
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.gemini_maker.pair_engine import PairBook, PairInventory, decide
from src.gemini_maker.shadow_pair_eval import _win_close_epoch, load_windows

OUT_PATH = Path("analysis/gemini/completion_frontier.json")

# Predeclared policy constants — fixed before replay.
LEAN_START_S = 120.0
LEAN_FULL_S = 30.0
C_SAFETY_S = 60.0
D_T1_S = 180.0
D_T2_S = 60.0
POLICIES = ("A", "B", "C", "D")

BOOK_AGE_THRESHOLDS_S = (0.25, 0.5, 1.0, 2.0, 5.0, 30.0)

FEE_RATE_AUG31 = 0.07      # reverse-engineered; live fetch required
REBATE_MULT = 0.30


def _fee(count: float, price: float, rate: float = FEE_RATE_AUG31) -> float:
    return rate * count * price * (1.0 - price)


def _rebate(count: float, price: float) -> float:
    return math.floor(REBATE_MULT * _fee(count, price) * 100.0) / 100.0


def _policy_cap(policy: str, tte: float, held_avg: float,
                comp_bid: Optional[float], ceiling: float) -> float:
    """Max completion price this policy allows right now (before the
    portfolio-ceiling and maker caps, which always also apply)."""
    if policy == "A":
        return ceiling
    if policy == "B":
        base = comp_bid if comp_bid is not None else ceiling
        if tte >= LEAN_START_S:
            frac = 0.0
        elif tte <= LEAN_FULL_S:
            frac = 1.0
        else:
            frac = (LEAN_START_S - tte) / (LEAN_START_S - LEAN_FULL_S)
        return base + frac * max(0.0, ceiling - base)
    if policy == "C":
        return (0.99 - held_avg) if tte > C_SAFETY_S else ceiling
    if policy == "D":
        if tte > D_T1_S:
            return 0.98 - held_avg
        if tte > D_T2_S:
            return 0.99 - held_avg
        return ceiling
    raise ValueError(policy)


def evaluate_window_policy(symbol: str, rows: List[dict], policy: str,
                           max_book_age_s: float) -> Optional[Dict[str, Any]]:
    close = _win_close_epoch(symbol)
    if close is None:
        return None
    dur = 300 if "05M" in symbol else 900
    start = close - dur
    rows = sorted(rows, key=lambda e: e["t"])
    inv = PairInventory()
    flow: deque = deque()
    book = PairBook(None, None, None, None)
    book_t: Optional[float] = None
    consumed = set()
    fills: List[dict] = []
    last_up = None

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
            book = PairBook(yes_bid=yb, yes_ask=ya,
                            no_bid=(1 - ya) if ya is not None else None,
                            no_ask=(1 - yb) if yb is not None else None)
            book_t = t
            consumed.clear()
            continue
        try:
            price = float(e["price"]); qty = float(e["qty"])
        except (TypeError, ValueError):
            continue
        taker_sold = bool(e.get("buyer_is_maker"))
        last_up = price
        f60 = flow_60s(t)
        book_age = (t - book_t) if book_t is not None else None
        if book_age is None or book_age > max_book_age_s:
            flow.append((t, price * qty))
            continue
        decision = decide(inv, book, f60, close - t, in_reward_pool=False)
        quotes = []
        for q in decision.quotes:
            if q.reason == "LEAN_COMPLETE_PORTFOLIO":
                # POLICY OVERRIDE (replay layer only).
                heavy_qty = max(inv.yes_qty, inv.no_qty)
                heavy_cost = inv.yes_cost if inv.yes_qty >= inv.no_qty else inv.no_cost
                held_avg = heavy_cost / heavy_qty if heavy_qty > 0 else 0.0
                comp_bid = book.no_bid if q.side == "no" else book.yes_bid
                cap = _policy_cap(policy, close - t, held_avg, comp_bid,
                                  q.price)  # q.price already = engine ceiling cap
                newp = round(min(q.price, max(0.01, cap)), 2)
                if newp >= 0.01:
                    quotes.append((q.side, newp, q.size, q.reason))
            else:
                quotes.append((q.side, q.price, q.size, q.reason))
        maker_filled = False
        for side, qprice, qsize, reason in quotes:
            if side in consumed:
                continue
            if side == "yes" and taker_sold and qprice >= price - 1e-9:
                filled = min(qsize, qty)
                inv.apply_fill("yes", filled, qprice)
                fills.append({"t": round(t - start, 1), "side": "yes",
                              "price": qprice, "qty": filled, "reason": reason,
                              "book_age_ms": round(book_age * 1000)})
                consumed.add("yes"); maker_filled = True
                break
            if side == "no" and not taker_sold and qprice >= (1 - price) - 1e-9:
                filled = min(qsize, qty)
                inv.apply_fill("no", filled, qprice)
                fills.append({"t": round(t - start, 1), "side": "no",
                              "price": qprice, "qty": filled, "reason": reason,
                              "book_age_ms": round(book_age * 1000)})
                consumed.add("no"); maker_filled = True
                break
        rescue = decision.rescue
        if maker_filled:
            rescue = decide(inv, book, f60, close - t,
                            in_reward_pool=False).rescue
        if rescue is not None:
            inv.apply_fill(rescue.side, rescue.size, rescue.price)
            fills.append({"t": round(t - start, 1), "side": rescue.side,
                          "price": rescue.price, "qty": rescue.size,
                          "reason": "RESCUE_TAKER",
                          "book_age_ms": round(book_age * 1000)})
        flow.append((t, price * qty))

    if not fills:
        return None
    flat = abs(inv.residual) < 1e-9
    maker_fills = [f for f in fills if f["reason"] != "RESCUE_TAKER"]
    rescue_fills = [f for f in fills if f["reason"] == "RESCUE_TAKER"]
    maker_fees_aug31 = sum(_fee(f["qty"], f["price"]) for f in maker_fills)
    rescue_cost_fees = sum(_fee(f["qty"], f["price"]) for f in rescue_fills)
    rebate_model = sum(_rebate(f["qty"], f["price"]) for f in maker_fills)
    locked = (inv.pairs_completed() - inv.spend) if flat else None
    return {
        "market": symbol, "flat": flat,
        "maker_fills": len(maker_fills), "rescue_fills": len(rescue_fills),
        "pairs_qty": inv.pairs_completed(),
        "pair_cost": (inv.spend / inv.yes_qty) if flat and inv.yes_qty else None,
        "locked_raw": round(locked, 4) if locked is not None else None,
        "residual_worst_loss": round(inv.worst_settlement_loss(), 4),
        "gross_deployed": round(inv.spend, 4),
        "maker_fees_aug31": round(maker_fees_aug31, 4),
        "rescue_taker_fees": round(rescue_cost_fees, 4),
        "rebate_model_separate": round(rebate_model, 4),
        "book_age_ms_max": max(f["book_age_ms"] for f in fills),
        "fills": fills,
    }


def run(tape_dir: str = "data/policy_d_port",
        since_utc: str = "2026-08-31T10:59") -> Dict[str, Any]:
    since_ts = datetime.fromisoformat(since_utc).replace(
        tzinfo=timezone.utc).timestamp()
    windows = load_windows(tape_dir, since_ts)
    table: Dict[str, Any] = {}
    for policy in POLICIES:
        by_age: Dict[str, Any] = {}
        for age in BOOK_AGE_THRESHOLDS_S:
            results = []
            for symbol, rows in sorted(windows.items()):
                r = evaluate_window_policy(symbol, rows, policy, age)
                if r:
                    results.append(r)
            flat = [r for r in results if r["flat"]]
            pair_costs = [r["pair_cost"] for r in flat if r["pair_cost"]]
            locked = sum(r["locked_raw"] or 0 for r in flat)
            fees31 = sum(r["maker_fees_aug31"] + r["rescue_taker_fees"]
                         for r in results)
            rescue_fees = sum(r["rescue_taker_fees"] for r in results)
            by_age[str(age)] = {
                "engaged_windows": len(results),
                "maker_fills": sum(r["maker_fills"] for r in results),
                "pairs_qty": round(sum(r["pairs_qty"] for r in results), 1),
                "flat_windows": len(flat),
                "flat_windows_le_100": sum(
                    1 for r in flat if r["pair_cost"] is not None
                    and r["pair_cost"] <= 1.0 + 1e-9),
                "flat_pairs_qty_le_100": round(sum(
                    r["pairs_qty"] for r in flat if r["pair_cost"] is not None
                    and r["pair_cost"] <= 1.0 + 1e-9), 1),
                "avg_pair_cost": round(statistics.mean(pair_costs), 4)
                if pair_costs else None,
                "median_pair_cost": round(statistics.median(pair_costs), 4)
                if pair_costs else None,
                "locked_raw_pnl": round(locked, 4),
                "residual_worst_loss": round(sum(
                    r["residual_worst_loss"] for r in results
                    if not r["flat"]), 4),
                "rescue_count": sum(r["rescue_fills"] for r in results),
                "gross_deployed": round(sum(r["gross_deployed"]
                                            for r in results), 2),
                "rebate_model_separate": round(sum(
                    r["rebate_model_separate"] for r in results), 4),
                "net_aug31_fees": round(locked - fees31, 4),
                "net_sep2_feefree": round(locked - rescue_fees, 4),
            }
        table[policy] = by_age
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "SHADOW_MAKER_PRINT_PROXY_NOT_EXECUTABLE",
        "predeclared": {"LEAN_START_S": LEAN_START_S, "LEAN_FULL_S": LEAN_FULL_S,
                        "C_SAFETY_S": C_SAFETY_S, "D_T1_S": D_T1_S,
                        "D_T2_S": D_T2_S,
                        "fee_model": "rate*C*P*(1-P), FEE_RATE_AUG31=0.07 "
                                     "(reverse-engineered; live fetch required)",
                        "rebate_model": "floor_cents(0.30*fee); separate line"},
        "tape_note": "policy_d_port BBO writes throttled ~5s: sub-5s book-age "
                     "thresholds are tape-limited, not market-limited",
        "frontier": table,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return payload


if __name__ == "__main__":
    p = run()
    for policy, ages in p["frontier"].items():
        row = ages["5.0"]
        print(f'{policy}: eng={row["engaged_windows"]} flat={row["flat_windows"]} '
              f'pairs={row["pairs_qty"]} avg_cost={row["avg_pair_cost"]} '
              f'locked={row["locked_raw_pnl"]} resid={row["residual_worst_loss"]} '
              f'rescues={row["rescue_count"]} rebate~={row["rebate_model_separate"]} '
              f'netAug31={row["net_aug31_fees"]} netSep2={row["net_sep2_feefree"]}')
