"""Lot-level forensics for a public Polymarket short-crypto market.

This module is strictly read-only. It fetches public wallet activity and
reconstructs how BUY fills on UP/DOWN inventory can pair through time.

Important interpretation:
- FIFO temporal pairing is an accounting model, not proof the trader intended
  every matched lot as an arbitrage.
- Public activity does not reliably expose maker/taker fee attribution, so we
  report gross pair economics and a conservative modeled-taker-fee view.
- No order submission, signing, cancellation, or credential access exists here.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import json
import time
from typing import Any

import httpx

from src.polymarket_wallet_shadow import TRADERS, _as_float, fetch_activity_window

POLY_CRYPTO_TAKER_FEE_RATE = 0.07


@dataclass
class ResidualLot:
    outcome: str
    price: float
    quantity: float
    timestamp: int


def modeled_taker_fee(price: float, quantity: float) -> float:
    """Conservative Polymarket crypto taker-fee model used by the shadow lane."""
    return POLY_CRYPTO_TAKER_FEE_RATE * quantity * price * (1.0 - price)


def _norm_outcome(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if text in {"UP", "DOWN"} else None


def analyze_condition(rows: list[dict[str, Any]], condition_id: str) -> dict[str, Any]:
    buys: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("conditionId") or "").lower() != condition_id.lower():
            continue
        if str(row.get("side") or "").upper() != "BUY":
            continue
        outcome = _norm_outcome(row.get("outcome"))
        price = _as_float(row.get("price"))
        size = _as_float(row.get("size"))
        if outcome is None or price is None or size is None or size <= 0:
            continue
        buys.append(row)
    buys.sort(key=lambda row: (int(row.get("timestamp") or 0), str(row.get("transactionHash") or "")))

    residual: dict[str, deque[ResidualLot]] = {"UP": deque(), "DOWN": deque()}
    pairs: list[dict[str, Any]] = []
    totals = {
        "UP": {"quantity": 0.0, "cost": 0.0, "modeled_taker_fee": 0.0, "fills": 0},
        "DOWN": {"quantity": 0.0, "cost": 0.0, "modeled_taker_fee": 0.0, "fills": 0},
    }

    for row in buys:
        outcome = _norm_outcome(row.get("outcome"))
        assert outcome is not None
        price = float(row["price"])
        quantity = float(row["size"])
        timestamp = int(row.get("timestamp") or 0)
        totals[outcome]["quantity"] += quantity
        totals[outcome]["cost"] += price * quantity
        totals[outcome]["modeled_taker_fee"] += modeled_taker_fee(price, quantity)
        totals[outcome]["fills"] += 1

        opposite = "DOWN" if outcome == "UP" else "UP"
        remaining = quantity
        while remaining > 1e-12 and residual[opposite]:
            old = residual[opposite][0]
            matched = min(remaining, old.quantity)
            if outcome == "UP":
                up_price, down_price = price, old.price
                up_ts, down_ts = timestamp, old.timestamp
            else:
                up_price, down_price = old.price, price
                up_ts, down_ts = old.timestamp, timestamp
            pair_price = up_price + down_price
            gross_edge_per_pair = 1.0 - pair_price
            fees = modeled_taker_fee(up_price, matched) + modeled_taker_fee(down_price, matched)
            modeled_net_edge = gross_edge_per_pair * matched - fees
            pairs.append({
                "quantity": matched,
                "up_price": up_price,
                "down_price": down_price,
                "pair_price": pair_price,
                "gross_edge_per_pair": gross_edge_per_pair,
                "gross_edge_usd": gross_edge_per_pair * matched,
                "modeled_taker_fees_usd": fees,
                "modeled_net_edge_usd": modeled_net_edge,
                "first_ts": min(up_ts, down_ts),
                "completion_ts": max(up_ts, down_ts),
                "completion_lag_s": abs(up_ts - down_ts),
            })
            remaining -= matched
            old.quantity -= matched
            if old.quantity <= 1e-12:
                residual[opposite].popleft()
        if remaining > 1e-12:
            residual[outcome].append(ResidualLot(outcome, price, remaining, timestamp))

    matched_qty = sum(pair["quantity"] for pair in pairs)
    gross_pair_edge = sum(pair["gross_edge_usd"] for pair in pairs)
    modeled_pair_fees = sum(pair["modeled_taker_fees_usd"] for pair in pairs)
    modeled_net_pair_edge = sum(pair["modeled_net_edge_usd"] for pair in pairs)
    positive_gross_qty = sum(pair["quantity"] for pair in pairs if pair["gross_edge_per_pair"] > 0)
    three_cent_gross_qty = sum(pair["quantity"] for pair in pairs if pair["gross_edge_per_pair"] >= 0.03)
    positive_modeled_net_qty = sum(
        pair["quantity"] for pair in pairs if pair["modeled_net_edge_usd"] > 0
    )

    residual_summary: dict[str, Any] = {}
    for outcome in ("UP", "DOWN"):
        lots = list(residual[outcome])
        qty = sum(lot.quantity for lot in lots)
        cost = sum(lot.quantity * lot.price for lot in lots)
        fees = sum(modeled_taker_fee(lot.price, lot.quantity) for lot in lots)
        residual_summary[outcome] = {
            "quantity": qty,
            "cost_usd": cost,
            "modeled_taker_fee_usd": fees,
            "avg_price": (cost / qty) if qty > 0 else None,
            "lot_count": len(lots),
        }

    title = next((str(row.get("title") or "") for row in buys if row.get("title")), "")
    first_ts = min((int(row.get("timestamp") or 0) for row in buys), default=None)
    last_ts = max((int(row.get("timestamp") or 0) for row in buys), default=None)
    return {
        "type": "temporal_market_forensics",
        "condition_id": condition_id,
        "title": title,
        "buy_fill_count": len(buys),
        "first_trade_ts": first_ts,
        "last_trade_ts": last_ts,
        "side_totals": totals,
        "matched_quantity_fifo": matched_qty,
        "fifo_gross_pair_edge_usd": gross_pair_edge,
        "fifo_modeled_taker_fees_usd": modeled_pair_fees,
        "fifo_modeled_net_pair_edge_usd": modeled_net_pair_edge,
        "fifo_positive_gross_pair_quantity": positive_gross_qty,
        "fifo_gross_edge_ge_3c_quantity": three_cent_gross_qty,
        "fifo_positive_modeled_net_quantity": positive_modeled_net_qty,
        "residual": residual_summary,
        "pair_count_lots": len(pairs),
        "pair_examples": pairs[:100],
        "interpretation_warning": (
            "FIFO is accounting, not intent. Public fee role is unobserved; modeled taker fees are conservative. "
            "Do not count this output as realized P&L."
        ),
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only lot-level Polymarket temporal forensics")
    parser.add_argument("--condition-id", action="append", required=True)
    parser.add_argument("--trader", choices=sorted(TRADERS), default="l5")
    parser.add_argument("--history-hours", type=float, default=2.0)
    args = parser.parse_args()

    trader = TRADERS[args.trader]
    end = int(time.time())
    start = end - max(60, int(max(0.01, args.history_hours) * 3600))
    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": "arb-temporal-forensics/1.0"}) as http:
        rows = fetch_activity_window(http, trader, start=start, end=end)
    for condition_id in args.condition_id:
        print(json.dumps(analyze_condition(rows, condition_id), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
