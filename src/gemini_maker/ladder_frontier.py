"""L-series: breadth vs depth ladder shadow (predeclared, fresh clock).

Successor experiment to the closed C-series, specified by the 2026-08-31
audit after the 21:10 read showed single-quote maker completion stuck at
13.3% with completion aggression buying zero extra completions, while the
L5 full-history ledger showed 78-89% matched inventory from a dense
two-sided multi-level ladder across many simultaneous windows.

Question: is the missing ingredient quote TOPOLOGY (depth of rungs),
BREADTH (many simultaneous windows), both — or does Gemini simply lack the
external flow for an L5-style machine?

PREDECLARED ARMS (constants frozen at commit; evidence = windows CLOSING
after LADDER_CUTOFF_UTC; anything earlier is in-sample diagnostic only):

  L0 control          single level per side, LIMITED market set
  L1 breadth          single level per side, ALL market set
  L2 depth            3-rung ladder per side, LIMITED market set
  L3 breadth+depth    3-rung ladder per side, ALL market set

Mechanics common to all arms (deliberately L5-shaped):
  * two-sided standing bids only: join price and (for ladders) rungs below;
    no completion chasing, no rescues, no unwinds;
  * residual inventory HELD TO SETTLEMENT and settlement-scored fail-closed
    (exact labels required; aggregate is INCOMPLETE if any engaged window
    lacks one);
  * per-window gross spend cap; no other exits.

Pass condition is ECONOMIC, per docs/GEMINI_SUBSIDY_METRICS.md — NOT a
matched-% target: does the ladder reduce residual loss per maker-turnover
dollar far enough that the measured (Sept 2) Gemini subsidy covers it?
This experiment measures the mechanical side of that inequality.

Fill proxy: identical conventions to the sealed frontier (print-proxy, one
fill per quote key per BBO snapshot, book age <= 2.0s, buyer_is_maker
semantics). A print can fill at most the single best-priced eligible rung
per side — conservative. Evidence class remains
SHADOW_MAKER_PRINT_PROXY_NOT_EXECUTABLE.

Usage:
  python3 -m src.gemini_maker.ladder_frontier            # in-sample diagnostic
  python3 -m src.gemini_maker.ladder_frontier --future   # evidence windows only
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.gemini_maker.completion_frontier import _fee, _rebate, load_ref_lag_windows
from src.gemini_maker.rescue_frontier import (
    MAX_BOOK_AGE_S,
    _win_close_epoch,
    load_settlements,
)

OUT_PATH = Path("analysis/gemini/ladder_frontier.json")
OUT_FUTURE = Path("analysis/gemini/ladder_frontier_future.json")

# ---- frozen constants (do not modify after commit) -------------------------
LADDER_CUTOFF_UTC = "2026-08-31T21:45"   # evidence = windows closing after this

LIMITED_ASSETS = ("BTC", "ETH")
LIMITED_HORIZONS = ("05M",)
ALL_ASSETS = ("BTC", "ETH", "SOL", "XRP")
ALL_HORIZONS = ("05M", "15M")

RUNG_OFFSETS_SINGLE = (0.00,)
RUNG_OFFSETS_MULTI = (0.00, 0.02, 0.05)   # cents below the join price
RUNG_SIZE = 1.0                            # contracts per rung, all arms
MAX_SPEND_PER_WINDOW = 10.0                # gross buy dollars per market-window
MIN_QUOTE_PRICE = 0.01
NO_NEW_QUOTES_TTE_S = 15.0                 # stop refreshing quotes at the end

ARMS: Dict[str, Dict[str, Any]] = {
    "L0": {"offsets": RUNG_OFFSETS_SINGLE, "scope": "limited"},
    "L1": {"offsets": RUNG_OFFSETS_SINGLE, "scope": "all"},
    "L2": {"offsets": RUNG_OFFSETS_MULTI, "scope": "limited"},
    "L3": {"offsets": RUNG_OFFSETS_MULTI, "scope": "all"},
}
# ---------------------------------------------------------------------------


def _in_scope(symbol: str, scope: str) -> bool:
    assets = LIMITED_ASSETS if scope == "limited" else ALL_ASSETS
    horizons = LIMITED_HORIZONS if scope == "limited" else ALL_HORIZONS
    return any(a in symbol for a in assets) and any(h in symbol for h in horizons)


def evaluate(symbol: str, rows: List[dict], offsets, settle: Optional[str]) -> Optional[Dict[str, Any]]:
    close = _win_close_epoch(symbol)
    if close is None:
        return None
    dur = 300 if "05M" in symbol else 900
    start = close - dur
    rows = sorted(rows, key=lambda e: e["t"])

    yes_qty = yes_cost = no_qty = no_cost = 0.0
    spend = 0.0
    fills: List[dict] = []
    book = None
    book_t = None
    consumed: set = set()
    cap_seconds = 0.0
    last_t = None

    for e in rows:
        t = e["t"]
        if t < start or t > close:
            continue
        if last_t is not None:
            cap_seconds += spend * (t - last_t)
        last_t = t
        if e["kind"] == "bbo":
            b, _B = e["bids"][0]
            a, _A = e["asks"][0]
            book = {"yb": b, "ya": a, "nb": round(1 - a, 4)}
            book_t = t
            consumed.clear()
            continue
        try:
            price = float(e["price"]); qty = float(e["qty"])
        except (TypeError, ValueError):
            continue
        taker_sold = bool(e.get("buyer_is_maker"))
        tte = close - t
        fresh = book is not None and book_t is not None and t - book_t <= MAX_BOOK_AGE_S
        if not fresh or spend >= MAX_SPEND_PER_WINDOW or tte <= NO_NEW_QUOTES_TTE_S:
            continue
        # Standing rungs this snapshot: best-priced eligible rung fills first;
        # one fill per (side, rung index) per snapshot.
        for side in ("yes", "no"):
            join = book["yb"] if side == "yes" else book["nb"]
            eff_price = price if side == "yes" else round(1 - price, 4)
            crosses = taker_sold if side == "yes" else not taker_sold
            if not crosses:
                continue
            for i, off in enumerate(offsets):
                qp = round(join - off, 2)
                key = (side, i)
                if qp < MIN_QUOTE_PRICE or key in consumed:
                    continue
                if qp >= eff_price - 1e-9:
                    filled = min(RUNG_SIZE, qty)
                    cost = filled * qp
                    if spend + cost > MAX_SPEND_PER_WINDOW:
                        break
                    spend += cost
                    if side == "yes":
                        yes_qty += filled; yes_cost += cost
                    else:
                        no_qty += filled; no_cost += cost
                    fills.append({"t": round(t - start, 1), "side": side,
                                  "rung": i, "price": qp, "qty": filled})
                    consumed.add(key)
                    break   # at most one rung per side per print

    if not fills:
        return None

    matched = min(yes_qty, no_qty)
    total_q = yes_qty + no_qty
    cu = yes_cost / yes_qty if yes_qty > 0 else None
    cd = no_cost / no_qty if no_qty > 0 else None
    pair_cost = (cu + cd) if (cu is not None and cd is not None) else None
    pair_locked = matched * (1.0 - pair_cost) if pair_cost is not None else 0.0

    maker_fees = sum(_fee(f["qty"], f["price"], 0.0175) for f in fills)
    rebates = sum(_rebate(f["qty"], f["price"]) for f in fills)

    settlement_pnl = None
    if settle in ("yes", "no"):
        win_qty = yes_qty if settle == "yes" else no_qty
        settlement_pnl = win_qty * 1.0 - (yes_cost + no_cost)

    res = abs(yes_qty - no_qty)
    worst_res_loss = (max(cu or 0.0, cd or 0.0)) * res if res > 1e-9 else 0.0
    return {
        "symbol": symbol,
        "close_epoch": close,
        "n_fills": len(fills),
        "buy_usdc": round(yes_cost + no_cost, 4),
        "yes_qty": yes_qty, "no_qty": no_qty,
        "matched_qty": matched,
        "matched_pct": round(2 * matched / total_q, 4) if total_q > 0 else None,
        "pair_cost": round(pair_cost, 4) if pair_cost is not None else None,
        "pair_locked": round(pair_locked, 4),
        "residual_qty": round(res, 4),
        "worst_residual_loss": round(worst_res_loss, 4),
        "maker_fees": round(maker_fees, 4),
        "rebates": round(rebates, 4),
        "settle": settle,
        "settlement_pnl": round(settlement_pnl, 4) if settlement_pnl is not None else None,
        "capital_minutes": round(cap_seconds / 60.0, 2),
        "rung_fill_counts": {str(i): sum(1 for f in fills if f["rung"] == i)
                             for i in range(len(offsets))},
    }


def run(future_only: bool = False) -> Dict[str, Any]:
    cutoff = datetime.fromisoformat(LADDER_CUTOFF_UTC).replace(
        tzinfo=timezone.utc).timestamp()
    # Load tape from well before the cutoff so in-sample diagnostics work;
    # future runs filter by window close below.
    windows = load_ref_lag_windows("data/ref_lag", cutoff - 12 * 3600)
    settlements = load_settlements()

    out: Dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_class": "SHADOW_MAKER_PRINT_PROXY_NOT_EXECUTABLE",
        "sample": "FUTURE_EVIDENCE" if future_only else "IN_SAMPLE_DIAGNOSTIC",
        "ladder_cutoff_utc": LADDER_CUTOFF_UTC,
        "constants": {"rung_size": RUNG_SIZE, "offsets_multi": RUNG_OFFSETS_MULTI,
                      "max_spend_per_window": MAX_SPEND_PER_WINDOW,
                      "limited": [LIMITED_ASSETS, LIMITED_HORIZONS]},
        "arms": {},
    }
    for arm, spec in ARMS.items():
        results = []
        missing_labels = 0
        for symbol, rows in windows.items():
            if not _in_scope(symbol, spec["scope"]):
                continue
            close = _win_close_epoch(symbol)
            if close is None or (future_only and close <= cutoff):
                continue
            settle = settlements.get(symbol)
            r = evaluate(symbol, rows, spec["offsets"], settle)
            if r is None:
                continue
            if r["settlement_pnl"] is None:
                missing_labels += 1
            results.append(r)

        engaged = len(results)
        scored = [r for r in results if r["settlement_pnl"] is not None]
        realized_all = (sum(r["settlement_pnl"] for r in scored)
                        if engaged and not missing_labels else None)
        turnover = sum(r["buy_usdc"] for r in results)
        fees = sum(r["maker_fees"] for r in results)
        agg = {
            "engaged": engaged,
            "missing_labels": missing_labels,
            "status": "INCOMPLETE" if missing_labels else "COMPLETE",
            "n_fills": sum(r["n_fills"] for r in results),
            "turnover_usdc": round(turnover, 2),
            "matched_pct_qty": round(
                2 * sum(r["matched_qty"] for r in results) /
                max(sum(r["yes_qty"] + r["no_qty"] for r in results), 1e-9), 4),
            "pair_locked": round(sum(r["pair_locked"] for r in results), 4),
            "realized_settlement_pnl_all": (round(realized_all, 4)
                                            if realized_all is not None else None),
            "net_sep2": (round(realized_all, 4) if realized_all is not None
                         else None),   # maker fees waived from Sept 2
            "net_aug31": (round(realized_all - fees, 4)
                          if realized_all is not None else None),
            "maker_fees": round(fees, 4),
            "rebates_separate": round(sum(r["rebates"] for r in results), 4),
            "residual_pnl": (round(realized_all -
                                   sum(r["pair_locked"] for r in scored), 4)
                             if realized_all is not None else None),
            "mech_net_per_turnover": (round(realized_all / turnover, 5)
                                      if realized_all is not None and turnover > 0
                                      else None),
            "worst_residual_loss_sum": round(
                sum(r["worst_residual_loss"] for r in results), 2),
            "max_single_window_loss": (round(min((r["settlement_pnl"]
                                                  for r in scored), default=0.0), 4)
                                       if scored else None),
            "capital_minutes": round(sum(r["capital_minutes"] for r in results), 1),
            "rung_fill_counts": {},
        }
        rc: Dict[str, int] = {}
        for r in results:
            for k, v in r["rung_fill_counts"].items():
                rc[k] = rc.get(k, 0) + v
        agg["rung_fill_counts"] = rc
        out["arms"][arm] = agg

    path = OUT_FUTURE if future_only else OUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    for arm, a in out["arms"].items():
        print(f"{arm}: eng={a['engaged']} fills={a['n_fills']} "
              f"turn=${a['turnover_usdc']} matched={a['matched_pct_qty']} "
              f"locked={a['pair_locked']} realizedAll={a['realized_settlement_pnl_all']} "
              f"net/$={a['mech_net_per_turnover']} status={a['status']} "
              f"rungs={a['rung_fill_counts']}")
    print(f"wrote {path}")
    return out


if __name__ == "__main__":
    run(future_only="--future" in sys.argv)
