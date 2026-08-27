"""Read-only pairing decompositions for public Polymarket short-crypto fills.

Compares two accounting views:
1. FIFO temporal completion (implemented elsewhere), and
2. best ex-post fungible pairing, which matches the cheapest UP inventory with
   the cheapest DOWN inventory and leaves the more expensive excess as
   directional residual.

The second view is a lower bound on matched-pair cost with hindsight. It is NOT
an executable strategy proof and must not be counted as realized arbitrage.
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Any

import httpx

from src.polymarket_temporal_market_forensics import modeled_taker_fee
from src.polymarket_wallet_shadow import TRADERS, _as_float, fetch_activity_window


def _lots(rows: list[dict[str, Any]], condition_id: str, outcome: str) -> list[list[float]]:
    out: list[list[float]] = []
    for row in rows:
        if str(row.get("conditionId") or "").lower() != condition_id.lower():
            continue
        if str(row.get("side") or "").upper() != "BUY":
            continue
        if str(row.get("outcome") or "").upper() != outcome:
            continue
        p = _as_float(row.get("price"))
        q = _as_float(row.get("size"))
        if p is None or q is None or q <= 0:
            continue
        out.append([p, q])
    out.sort(key=lambda x: x[0])
    return out


def optimal_pairing(rows: list[dict[str, Any]], condition_id: str) -> dict[str, Any]:
    up = _lots(rows, condition_id, "UP")
    down = _lots(rows, condition_id, "DOWN")
    ui = di = 0
    matched = gross_edge = fees = 0.0
    positive_qty = edge3_qty = positive_modeled_net_qty = 0.0
    pair_chunks = 0
    while ui < len(up) and di < len(down):
        uq = up[ui][1]
        dq = down[di][1]
        q = min(uq, dq)
        up_price = up[ui][0]
        down_price = down[di][0]
        edge = 1.0 - up_price - down_price
        fee = modeled_taker_fee(up_price, q) + modeled_taker_fee(down_price, q)
        net = edge * q - fee
        matched += q
        gross_edge += edge * q
        fees += fee
        positive_qty += q if edge > 0 else 0.0
        edge3_qty += q if edge >= 0.03 else 0.0
        positive_modeled_net_qty += q if net > 0 else 0.0
        pair_chunks += 1
        up[ui][1] -= q
        down[di][1] -= q
        if up[ui][1] <= 1e-12:
            ui += 1
        if down[di][1] <= 1e-12:
            di += 1

    residual = {}
    for name, lots, idx in (("UP", up, ui), ("DOWN", down, di)):
        remaining = lots[idx:]
        qty = sum(q for _, q in remaining)
        cost = sum(p * q for p, q in remaining)
        residual[name] = {
            "quantity": qty,
            "cost_usd": cost,
            "avg_price": cost / qty if qty else None,
        }

    return {
        "type": "temporal_optimal_pairing_bound",
        "condition_id": condition_id,
        "matched_quantity": matched,
        "gross_pair_edge_usd": gross_edge,
        "avg_gross_edge_per_pair": gross_edge / matched if matched else None,
        "modeled_taker_fees_usd": fees,
        "modeled_net_pair_edge_usd": gross_edge - fees,
        "positive_gross_pair_quantity": positive_qty,
        "gross_edge_ge_3c_quantity": edge3_qty,
        "positive_modeled_net_quantity": positive_modeled_net_qty,
        "pair_chunks": pair_chunks,
        "residual": residual,
        "interpretation_warning": "Best ex-post fungible pairing uses hindsight; it is a decomposition bound, not proof of executable arbitrage or trader intent.",
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Best ex-post pairing bound for public wallet fills")
    parser.add_argument("--condition-id", action="append", required=True)
    parser.add_argument("--trader", choices=sorted(TRADERS), default="l5")
    parser.add_argument("--history-hours", type=float, default=2.0)
    args = parser.parse_args()
    trader = TRADERS[args.trader]
    end = int(time.time())
    start = end - max(60, int(max(0.01, args.history_hours) * 3600))
    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": "arb-pairing-bounds/1.0"}) as http:
        rows = fetch_activity_window(http, trader, start=start, end=end)
    for condition_id in args.condition_id:
        print(json.dumps(optimal_pairing(rows, condition_id), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
