"""Profile observable execution behavior of the l5 Polymarket reference wallet.

READ ONLY. Consumes the completed SQLite activity spool produced by the existing
full-history backtest lane. It does not call Polymarket, use credentials, or place
orders.

Purpose: estimate the observable parameters needed to reproduce the wallet's
maker/inventory behavior without pretending we can observe resting orders that
never filled. Outputs fill timing, clip size, side-switching, temporary inventory
imbalance, and chronological pair-lag distributions by 5m/15m stream.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
import sqlite3
from statistics import mean
from typing import Any

from src import polymarket_l5_full_history_backtest as base
from src.polymarket_wallet_shadow import activity_id


def f(v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if math.isfinite(x) else 0.0


def i(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def quantiles(values: list[float]) -> dict[str, float | None]:
    xs = sorted(x for x in values if math.isfinite(x))
    if not xs:
        return {"p10": None, "p25": None, "p50": None, "p75": None, "p90": None, "mean": None}
    def q(p: float) -> float:
        if len(xs) == 1:
            return xs[0]
        pos = (len(xs) - 1) * p
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return xs[lo]
        w = pos - lo
        return xs[lo] * (1 - w) + xs[hi] * w
    return {
        "p10": q(0.10), "p25": q(0.25), "p50": q(0.50),
        "p75": q(0.75), "p90": q(0.90), "mean": mean(xs),
    }


def market_profile(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: (i(r.get("timestamp")), activity_id(r)))
    asset, horizon, start_ts = base.market_meta(rows[0])
    if asset not in {"BTC", "ETH", "SOL", "XRP"} or horizon not in {5, 15}:
        return None

    buys = [r for r in rows if str(r.get("side") or "").upper() == "BUY"]
    sells = [r for r in rows if str(r.get("side") or "").upper() == "SELL"]
    if not buys:
        return None

    end_ts = start_ts + horizon * 60 if start_ts is not None else None
    fill_sizes = [f(r.get("size")) for r in buys if f(r.get("size")) > 0]
    prices = [f(r.get("price")) for r in buys if 0 < f(r.get("price")) < 1]
    ts = [i(r.get("timestamp")) for r in buys]
    gaps = [max(0.0, float(b - a)) for a, b in zip(ts, ts[1:])]
    outcomes = [str(r.get("outcome") or "").upper() for r in buys]
    outcomes = [o for o in outcomes if o in {"UP", "DOWN"}]
    switches = sum(1 for a, b in zip(outcomes, outcomes[1:]) if a != b)

    run_lengths: list[int] = []
    if outcomes:
        run = 1
        for a, b in zip(outcomes, outcomes[1:]):
            if a == b:
                run += 1
            else:
                run_lengths.append(run)
                run = 1
        run_lengths.append(run)

    up = down = 0.0
    max_abs_imbalance = 0.0
    max_imbalance_share = 0.0
    first_opposite_lag: float | None = None
    first_side = outcomes[0] if outcomes else None
    first_side_ts = None
    for r in buys:
        o = str(r.get("outcome") or "").upper()
        q = f(r.get("size"))
        t = i(r.get("timestamp"))
        if o == "UP":
            up += q
        elif o == "DOWN":
            down += q
        if first_side_ts is None and o in {"UP", "DOWN"}:
            first_side_ts = t
        elif first_opposite_lag is None and first_side and o in {"UP", "DOWN"} and o != first_side and first_side_ts is not None:
            first_opposite_lag = max(0.0, float(t - first_side_ts))
        total = up + down
        imbalance = abs(up - down)
        max_abs_imbalance = max(max_abs_imbalance, imbalance)
        if total > 0:
            max_imbalance_share = max(max_imbalance_share, imbalance / total)

    pair = base.fifo_pairing(buys)
    first_offset = (ts[0] - start_ts) if start_ts is not None and ts else None
    last_before_expiry = (end_ts - ts[-1]) if end_ts is not None and ts else None

    return {
        "asset": asset,
        "horizon_minutes": horizon,
        "stream": f"{asset}_{horizon}m",
        "buy_count": len(buys),
        "sell_count": len(sells),
        "two_sided": up > 0 and down > 0,
        "side_switches": switches,
        "switch_share": switches / max(1, len(outcomes) - 1),
        "fill_sizes": fill_sizes,
        "prices": prices,
        "inter_fill_gaps_seconds": gaps,
        "run_lengths": run_lengths,
        "first_trade_offset_seconds": first_offset,
        "last_trade_seconds_before_expiry": last_before_expiry,
        "first_opposite_fill_lag_seconds": first_opposite_lag,
        "max_abs_inventory_imbalance_qty": max_abs_imbalance,
        "max_inventory_imbalance_share": max_imbalance_share,
        "fifo_avg_pair_lag_seconds": pair.get("avg_pair_lag_seconds"),
        "fifo_avg_pair_cost": pair.get("avg_pair_cost"),
        "fifo_matched_quantity": pair.get("matched_quantity"),
    }


def finish(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "markets": len(rows),
        "two_sided_market_share": sum(bool(r["two_sided"]) for r in rows) / len(rows) if rows else None,
        "avg_buy_count": mean([r["buy_count"] for r in rows]) if rows else None,
        "avg_sell_count": mean([r["sell_count"] for r in rows]) if rows else None,
        "side_switches_per_market": quantiles([float(r["side_switches"]) for r in rows]),
        "switch_share": quantiles([float(r["switch_share"]) for r in rows]),
        "fill_size": quantiles([x for r in rows for x in r["fill_sizes"]]),
        "buy_price": quantiles([x for r in rows for x in r["prices"]]),
        "inter_fill_gap_seconds": quantiles([x for r in rows for x in r["inter_fill_gaps_seconds"]]),
        "same_side_run_length": quantiles([float(x) for r in rows for x in r["run_lengths"]]),
        "first_trade_offset_seconds": quantiles([float(r["first_trade_offset_seconds"]) for r in rows if r["first_trade_offset_seconds"] is not None]),
        "last_trade_seconds_before_expiry": quantiles([float(r["last_trade_seconds_before_expiry"]) for r in rows if r["last_trade_seconds_before_expiry"] is not None]),
        "first_opposite_fill_lag_seconds": quantiles([float(r["first_opposite_fill_lag_seconds"]) for r in rows if r["first_opposite_fill_lag_seconds"] is not None]),
        "max_abs_inventory_imbalance_qty": quantiles([float(r["max_abs_inventory_imbalance_qty"]) for r in rows]),
        "max_inventory_imbalance_share": quantiles([float(r["max_inventory_imbalance_share"]) for r in rows]),
        "fifo_avg_pair_lag_seconds": quantiles([float(r["fifo_avg_pair_lag_seconds"]) for r in rows if r["fifo_avg_pair_lag_seconds"] is not None]),
        "fifo_avg_pair_cost": quantiles([float(r["fifo_avg_pair_cost"]) for r in rows if r["fifo_avg_pair_cost"] is not None]),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Profile observable l5 reference-wallet execution behavior")
    p.add_argument("--db", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    db = sqlite3.connect(args.db)
    db.execute("PRAGMA query_only=ON")
    cur = db.execute(
        "SELECT condition_id,payload FROM activity WHERE kind='TRADE' AND condition_id<>'' ORDER BY condition_id,ts,row_key"
    )
    current: str | None = None
    buf: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []

    def consume(cid: str | None, rows: list[dict[str, Any]]) -> None:
        if cid is None:
            return
        out = market_profile(rows)
        if out is not None:
            profiles.append(out)

    for cid, payload in cur:
        cid = str(cid)
        if current is None:
            current = cid
        if cid != current:
            consume(current, buf)
            current, buf = cid, []
        buf.append(json.loads(payload))
    consume(current, buf)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in profiles:
        grouped[r["stream"]].append(r)

    result = {
        "type": "POLY_L5_REFERENCE_REPLICA_PROFILE",
        "wallet": base.WALLET,
        "markets": len(profiles),
        "overall": finish(profiles),
        "by_horizon": {
            "5m": finish([r for r in profiles if r["horizon_minutes"] == 5]),
            "15m": finish([r for r in profiles if r["horizon_minutes"] == 15]),
        },
        "by_stream": {k: finish(v) for k, v in sorted(grouped.items())},
        "limits": {
            "resting_orders_unobserved": "Public history contains fills, not all quotes that rested and never filled; exact quote placement/cancel policy cannot be recovered from history alone.",
            "maker_taker": "Use the separate takerOnly comparison for execution-role mix; Activity rows alone do not reliably label maker/taker.",
            "purpose": "Parameter inference for shadow replication, not user realized P&L and not proof of executable capacity.",
        },
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
    }
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({"type": result["type"], "markets": result["markets"], "output": args.output, "shadow_only": True, "order_entry": False}, sort_keys=True))


if __name__ == "__main__":
    main()
