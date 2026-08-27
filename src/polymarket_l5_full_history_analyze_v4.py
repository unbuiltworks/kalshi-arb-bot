"""Fast single-pass analyzer for the disk-backed l5 full-history spool.

READ ONLY with respect to Polymarket and the SQLite spool. This module does not
fetch activity and does not place orders. It consumes the already-completed v3
SQLite spool, streams TRADE rows once in condition_id order, analyzes one
condition at a time, and writes compact full-history outputs.
"""
from __future__ import annotations

import argparse
import heapq
import json
import os
import sqlite3
import time
from typing import Any

from src import polymarket_l5_full_history_backtest as base
from src import polymarket_l5_full_history_backtest_v3 as v3


def _redeem_map(db: sqlite3.Connection) -> dict[str, tuple[float, int | None]]:
    out: dict[str, tuple[float, int | None]] = {}
    for cid, usd, ts in db.execute(
        "SELECT condition_id, COALESCE(SUM(usdc_size),0), MAX(ts) "
        "FROM activity WHERE kind='REDEEM' AND condition_id<>'' GROUP BY condition_id"
    ):
        out[str(cid)] = (float(usd or 0.0), int(ts) if ts is not None else None)
    return out


def _final_summary(
    db: sqlite3.Connection,
    overall: dict[str, float],
    by_horizon_raw: dict[int, dict[str, float]],
    by_stream_raw: dict[tuple[str, int], dict[str, float]],
    best: list[tuple[float, int, dict[str, Any]]],
    worst: list[tuple[float, int, dict[str, Any]]],
) -> dict[str, Any]:
    overall_done = v3.finish_group(overall)
    trading_pnl = overall_done["resolved_trading_pnl_usd"]
    buy_cost = overall_done["resolved_buy_cost_usd"]
    reward_usd = float(db.execute(
        "SELECT COALESCE(SUM(usdc_size),0) FROM activity WHERE kind='REWARD'"
    ).fetchone()[0] or 0.0)
    rebate_usd = float(db.execute(
        "SELECT COALESCE(SUM(usdc_size),0) FROM activity WHERE kind='MAKER_REBATE'"
    ).fetchone()[0] or 0.0)
    return {
        "type": "POLY_L5_FULL_HISTORY_BACKTEST_V4",
        "wallet": base.WALLET,
        "short_crypto_markets": overall_done["markets"],
        "resolved_short_crypto_markets": overall_done["resolved_markets"],
        "resolved_buy_cost_usd": buy_cost,
        "resolved_trading_pnl_usd": trading_pnl,
        "resolved_trading_return_on_buy_cost": (trading_pnl / buy_cost) if buy_cost else None,
        "maker_rebate_usd": rebate_usd,
        "liquidity_reward_usd": reward_usd,
        "resolved_plus_explicit_incentives_usd": trading_pnl + rebate_usd + reward_usd,
        "overall_behavior": overall_done,
        "by_horizon": {
            "5m": v3.finish_group(by_horizon_raw[5]),
            "15m": v3.finish_group(by_horizon_raw[15]),
        },
        "by_stream": {
            f"{a}_{h}m": v3.finish_group(by_stream_raw[(a, h)])
            for a in ("BTC", "ETH", "SOL", "XRP") for h in (5, 15)
        },
        "best_markets": [x[2] for x in sorted(best, key=lambda x: x[0], reverse=True)],
        "worst_markets": [x[2] for x in sorted(worst, key=lambda x: -x[0], reverse=True)],
        "interpretation": {
            "reference_wallet_only": "Public reference-wallet economics, not user realized P&L or executable capacity.",
            "resolved_trading_pnl": "SELL proceeds + observed REDEEM payout - BUY cost for markets with an observed redeem.",
            "redeem_limit": "Markets without an observed redeem are excluded from resolved trading P&L; this is not a complete audited profit figure.",
            "fifo_pairing": "Chronological decomposition of observed buys, not proof of pre-locked arbitrage or trader intent.",
            "maker_taker": "Full-history Activity rows do not expose a reliable maker/taker flag; use separate takerOnly comparison for execution-role mix.",
        },
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Single-pass analyzer for l5 full-history SQLite spool")
    p.add_argument("--db", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--markets-jsonl", required=True)
    args = p.parse_args()

    db = sqlite3.connect(args.db)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA temp_store=FILE")
    db.execute("PRAGMA cache_size=-65536")
    db.execute("PRAGMA mmap_size=134217728")

    redeems = _redeem_map(db)
    total_conditions = int(db.execute(
        "SELECT COUNT(DISTINCT condition_id) FROM activity WHERE kind='TRADE' AND condition_id<>''"
    ).fetchone()[0])
    fetch_counts = {
        kind.lower() + "_rows": int(db.execute(
            "SELECT COUNT(*) FROM activity WHERE kind=?", (kind,)
        ).fetchone()[0])
        for kind in ("TRADE", "REDEEM", "REWARD", "MAKER_REBATE")
    }

    overall = v3.new_group()
    by_horizon_raw = {5: v3.new_group(), 15: v3.new_group()}
    by_stream_raw = {(a, h): v3.new_group() for a in ("BTC", "ETH", "SOL", "XRP") for h in (5, 15)}
    best: list[tuple[float, int, dict[str, Any]]] = []
    worst: list[tuple[float, int, dict[str, Any]]] = []
    seq = 0
    scanned = 0
    qualifying = 0
    current_cid: str | None = None
    current_rows: list[dict[str, Any]] = []
    started = time.time()

    os.makedirs(os.path.dirname(args.markets_jsonl) or ".", exist_ok=True)
    tmp_markets = args.markets_jsonl + ".tmp"

    def consume(cid: str | None, rows: list[dict[str, Any]], out) -> None:
        nonlocal seq, scanned, qualifying
        if cid is None:
            return
        scanned += 1
        redeem_usd, redeem_ts = redeems.get(cid, (0.0, None))
        analyzed = base.analyze_market(rows, redeem_usd, redeem_ts)
        if analyzed is not None:
            qualifying += 1
            out.write(json.dumps(analyzed, sort_keys=True, separators=(",", ":"), default=str) + "\n")
            v3.update_group(overall, analyzed)
            h = int(analyzed["horizon_minutes"])
            a = str(analyzed["asset"])
            v3.update_group(by_horizon_raw[h], analyzed)
            v3.update_group(by_stream_raw[(a, h)], analyzed)
            if analyzed.get("resolved") and analyzed.get("trading_pnl_usd") is not None:
                pnl = base._f(analyzed.get("trading_pnl_usd"))
                cm = v3.compact_market(analyzed)
                seq += 1
                heapq.heappush(best, (pnl, seq, cm))
                if len(best) > 10:
                    heapq.heappop(best)
                heapq.heappush(worst, (-pnl, seq, cm))
                if len(worst) > 10:
                    heapq.heappop(worst)
        if scanned % 500 == 0 or scanned == total_conditions:
            elapsed = max(0.001, time.time() - started)
            rate = scanned / elapsed
            eta = (total_conditions - scanned) / rate if rate > 0 else None
            print(
                f"L5_V4_ANALYZE scanned={scanned}/{total_conditions} "
                f"pct={100.0*scanned/total_conditions:.2f} qualifying={qualifying} "
                f"rate={rate:.1f}_conditions_s eta_s={eta:.1f}",
                flush=True,
            )

    with open(tmp_markets, "w", encoding="utf-8") as out:
        cursor = db.execute(
            "SELECT condition_id,payload FROM activity "
            "WHERE kind='TRADE' AND condition_id<>'' ORDER BY condition_id,ts,row_key"
        )
        for cid, payload in cursor:
            cid = str(cid)
            if current_cid is None:
                current_cid = cid
            if cid != current_cid:
                consume(current_cid, current_rows, out)
                current_rows = []
                current_cid = cid
            current_rows.append(json.loads(payload))
        consume(current_cid, current_rows, out)

    os.replace(tmp_markets, args.markets_jsonl)
    summary = _final_summary(db, overall, by_horizon_raw, by_stream_raw, best, worst)
    result = {
        "fetch": fetch_counts,
        "analysis": {
            "total_conditions": total_conditions,
            "conditions_scanned": scanned,
            "qualifying_markets": qualifying,
            "elapsed_seconds": time.time() - started,
        },
        "summary": summary,
        "artifacts": {"sqlite": args.db, "markets_jsonl": args.markets_jsonl},
    }
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    tmp_output = args.output + ".tmp"
    with open(tmp_output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, sort_keys=True, separators=(",", ":"))
    os.replace(tmp_output, args.output)
    db.close()

    print(json.dumps({"analysis": result["analysis"], "summary": summary}, sort_keys=True), flush=True)
    print(f"L5_V4_COMPLETE output={args.output} markets_jsonl={args.markets_jsonl}", flush=True)


if __name__ == "__main__":
    main()
