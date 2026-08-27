"""Disk-backed full-history reconstruction for the l5 Polymarket wallet.

READ ONLY. Public Polymarket Data API only; no credentials or order authority.

Why v3 exists
-------------
The original full-history implementation accumulated every raw activity row in
RAM before analysis. A high-volume wallet can exceed the small AWS hot host's
memory and be killed with exit 137. This version spools each bounded activity
slice into SQLite, releases it, then analyzes one condition at a time. It also
checkpoints completed fetch slices so a restart does not need to refetch them.

The detailed per-market analyses are written as JSONL instead of being retained
in memory. The final JSON contains the compact full-history summary plus fetch
counts and artifact paths.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import heapq
import json
import os
import sqlite3
import time
from typing import Any

import httpx

from src import polymarket_l5_full_history_backtest as base
from src.polymarket_l5_full_history_backtest_v2 import resilient_fetch_page


def parse_date(value: str) -> int:
    text = value.strip()
    if text.lower() == "now":
        return int(time.time())
    if text.isdigit():
        return int(text)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def row_key(kind: str, row: dict[str, Any]) -> str:
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((kind + "\n" + payload).encode("utf-8")).hexdigest()


def open_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA temp_store=FILE")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS activity (
            row_key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            condition_id TEXT NOT NULL DEFAULT '',
            ts INTEGER NOT NULL DEFAULT 0,
            usdc_size REAL NOT NULL DEFAULT 0,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_activity_kind_condition
            ON activity(kind, condition_id);
        CREATE INDEX IF NOT EXISTS idx_activity_kind_ts
            ON activity(kind, ts);
        CREATE TABLE IF NOT EXISTS fetch_progress (
            kind TEXT NOT NULL,
            start_ts INTEGER NOT NULL,
            end_ts INTEGER NOT NULL,
            rows_seen INTEGER NOT NULL,
            completed_at INTEGER NOT NULL,
            PRIMARY KEY(kind, start_ts, end_ts)
        );
        """
    )
    return db


def completed(db: sqlite3.Connection, kind: str, lo: int, hi: int) -> bool:
    row = db.execute(
        "SELECT 1 FROM fetch_progress WHERE kind=? AND start_ts=? AND end_ts=?",
        (kind, lo, hi),
    ).fetchone()
    return row is not None


def insert_rows(db: sqlite3.Connection, kind: str, rows: list[dict[str, Any]]) -> None:
    records = []
    for row in rows:
        cid = str(row.get("conditionId") or "").strip()
        ts = base._i(row.get("timestamp"))
        usdc = base._f(row.get("usdcSize"))
        payload = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
        records.append((row_key(kind, row), kind, cid, ts, usdc, payload))
    db.executemany(
        "INSERT OR IGNORE INTO activity(row_key,kind,condition_id,ts,usdc_size,payload) "
        "VALUES(?,?,?,?,?,?)",
        records,
    )


def spool_kind(
    db: sqlite3.Connection,
    http: httpx.Client,
    *,
    kind: str,
    start: int,
    end: int,
    chunk_seconds: int,
) -> None:
    cursor = int(start)
    chunk = max(60, int(chunk_seconds))
    while cursor <= end:
        hi = min(end, cursor + chunk - 1)
        if completed(db, kind, cursor, hi):
            cursor = hi + 1
            continue

        rows = base._fetch_slice(http, kind=kind, start=cursor, end=hi)
        with db:
            insert_rows(db, kind, rows)
            db.execute(
                "INSERT OR REPLACE INTO fetch_progress(kind,start_ts,end_ts,rows_seen,completed_at) "
                "VALUES(?,?,?,?,?)",
                (kind, cursor, hi, len(rows), int(time.time())),
            )
        total = db.execute("SELECT COUNT(*) FROM activity WHERE kind=?", (kind,)).fetchone()[0]
        print(
            f"L5_BACKTEST_SPOOL kind={kind} slice={cursor}-{hi} "
            f"rows_seen={len(rows)} unique_total={total}",
            flush=True,
        )
        del rows
        cursor = hi + 1


def new_group() -> dict[str, float]:
    return {
        "markets": 0.0,
        "resolved_markets": 0.0,
        "buys": 0.0,
        "sells": 0.0,
        "two_sided_markets": 0.0,
        "side_switches": 0.0,
        "fractional_buy_fills": 0.0,
        "resolved_buy_cost_usd": 0.0,
        "resolved_trading_pnl_usd": 0.0,
        "fifo_matched_quantity": 0.0,
        "fifo_gross_pair_edge_usd": 0.0,
        "fifo_positive_pair_quantity": 0.0,
        "fifo_edge_ge_1c_quantity": 0.0,
        "fifo_edge_ge_2c_quantity": 0.0,
        "fifo_edge_ge_3c_quantity": 0.0,
        "fifo_pair_cost_weighted": 0.0,
        "fifo_pair_lag_weighted": 0.0,
        "late_favorite_ge_80c_quantity_last_60s": 0.0,
        "opening_45_55_quantity_first_60s": 0.0,
    }


def update_group(g: dict[str, float], m: dict[str, Any]) -> None:
    pair = m.get("fifo_pairing") or {}
    matched = base._f(pair.get("matched_quantity"))
    g["markets"] += 1
    g["buys"] += int(m.get("buy_count") or 0)
    g["sells"] += int(m.get("sell_count") or 0)
    g["two_sided_markets"] += 1 if m.get("two_sided_buys") else 0
    g["side_switches"] += int(m.get("side_switches") or 0)
    g["fractional_buy_fills"] += int(m.get("fractional_buy_fill_count") or 0)
    if m.get("resolved") and m.get("trading_pnl_usd") is not None:
        g["resolved_markets"] += 1
        g["resolved_buy_cost_usd"] += base._f(m.get("buy_cost_usd"))
        g["resolved_trading_pnl_usd"] += base._f(m.get("trading_pnl_usd"))
    g["fifo_matched_quantity"] += matched
    g["fifo_gross_pair_edge_usd"] += base._f(pair.get("gross_pair_edge_usd"))
    g["fifo_positive_pair_quantity"] += base._f(pair.get("positive_pair_quantity"))
    g["fifo_edge_ge_1c_quantity"] += base._f(pair.get("edge_ge_1c_quantity"))
    g["fifo_edge_ge_2c_quantity"] += base._f(pair.get("edge_ge_2c_quantity"))
    g["fifo_edge_ge_3c_quantity"] += base._f(pair.get("edge_ge_3c_quantity"))
    if matched > 0:
        avg_cost = pair.get("avg_pair_cost")
        avg_lag = pair.get("avg_pair_lag_seconds")
        if avg_cost is not None:
            g["fifo_pair_cost_weighted"] += matched * base._f(avg_cost)
        if avg_lag is not None:
            g["fifo_pair_lag_weighted"] += matched * base._f(avg_lag)
    g["late_favorite_ge_80c_quantity_last_60s"] += base._f(
        m.get("late_favorite_ge_80c_quantity_last_60s")
    )
    g["opening_45_55_quantity_first_60s"] += base._f(
        m.get("opening_45_55_quantity_first_60s")
    )


def finish_group(g: dict[str, float]) -> dict[str, Any]:
    markets = int(g["markets"])
    buys = int(g["buys"])
    matched = g["fifo_matched_quantity"]
    buy_cost = g["resolved_buy_cost_usd"]
    return {
        "markets": markets,
        "resolved_markets": int(g["resolved_markets"]),
        "buys": buys,
        "sells": int(g["sells"]),
        "two_sided_market_share": (g["two_sided_markets"] / markets) if markets else None,
        "avg_side_switches": (g["side_switches"] / markets) if markets else None,
        "fractional_buy_fill_share": (g["fractional_buy_fills"] / buys) if buys else None,
        "resolved_buy_cost_usd": buy_cost,
        "resolved_trading_pnl_usd": g["resolved_trading_pnl_usd"],
        "resolved_return_on_buy_cost": (g["resolved_trading_pnl_usd"] / buy_cost) if buy_cost else None,
        "fifo_matched_quantity": matched,
        "fifo_gross_pair_edge_usd": g["fifo_gross_pair_edge_usd"],
        "fifo_positive_pair_quantity": g["fifo_positive_pair_quantity"],
        "fifo_edge_ge_1c_quantity": g["fifo_edge_ge_1c_quantity"],
        "fifo_edge_ge_2c_quantity": g["fifo_edge_ge_2c_quantity"],
        "fifo_edge_ge_3c_quantity": g["fifo_edge_ge_3c_quantity"],
        "fifo_positive_pair_share": (g["fifo_positive_pair_quantity"] / matched) if matched else None,
        "fifo_avg_pair_cost": (g["fifo_pair_cost_weighted"] / matched) if matched else None,
        "fifo_avg_pair_lag_seconds": (g["fifo_pair_lag_weighted"] / matched) if matched else None,
        "late_favorite_ge_80c_quantity_last_60s": g["late_favorite_ge_80c_quantity_last_60s"],
        "opening_45_55_quantity_first_60s": g["opening_45_55_quantity_first_60s"],
    }


def compact_market(m: dict[str, Any]) -> dict[str, Any]:
    return {
        k: m.get(k)
        for k in (
            "title",
            "asset",
            "horizon_minutes",
            "buy_cost_usd",
            "redeem_usd",
            "trading_pnl_usd",
        )
    }


def analyze_spool(db: sqlite3.Connection, markets_jsonl: str) -> tuple[dict[str, Any], int]:
    overall = new_group()
    by_horizon_raw = {5: new_group(), 15: new_group()}
    by_stream_raw = {(a, h): new_group() for a in ("BTC", "ETH", "SOL", "XRP") for h in (5, 15)}
    best: list[tuple[float, int, dict[str, Any]]] = []
    worst: list[tuple[float, int, dict[str, Any]]] = []
    seq = 0

    condition_ids = db.execute(
        "SELECT condition_id FROM activity WHERE kind='TRADE' AND condition_id<>'' GROUP BY condition_id"
    )
    os.makedirs(os.path.dirname(markets_jsonl) or ".", exist_ok=True)
    with open(markets_jsonl, "w", encoding="utf-8") as out:
        for (cid,) in condition_ids:
            trade_rows = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM activity WHERE kind='TRADE' AND condition_id=? ORDER BY ts,row_key",
                    (cid,),
                )
            ]
            redeem = db.execute(
                "SELECT COALESCE(SUM(usdc_size),0), MAX(ts) FROM activity "
                "WHERE kind='REDEEM' AND condition_id=?",
                (cid,),
            ).fetchone()
            redeem_usd = float(redeem[0] or 0.0)
            redeem_ts = int(redeem[1]) if redeem[1] is not None else None
            analyzed = base.analyze_market(trade_rows, redeem_usd, redeem_ts)
            del trade_rows
            if analyzed is None:
                continue

            out.write(json.dumps(analyzed, sort_keys=True, separators=(",", ":"), default=str) + "\n")
            update_group(overall, analyzed)
            h = int(analyzed["horizon_minutes"])
            a = str(analyzed["asset"])
            update_group(by_horizon_raw[h], analyzed)
            update_group(by_stream_raw[(a, h)], analyzed)

            if analyzed.get("resolved") and analyzed.get("trading_pnl_usd") is not None:
                pnl = base._f(analyzed.get("trading_pnl_usd"))
                cm = compact_market(analyzed)
                seq += 1
                heapq.heappush(best, (pnl, seq, cm))
                if len(best) > 10:
                    heapq.heappop(best)
                heapq.heappush(worst, (-pnl, seq, cm))
                if len(worst) > 10:
                    heapq.heappop(worst)

            if int(overall["markets"]) % 500 == 0:
                print(f"L5_BACKTEST_ANALYZE markets={int(overall['markets'])}", flush=True)

    reward_usd = float(
        db.execute("SELECT COALESCE(SUM(usdc_size),0) FROM activity WHERE kind='REWARD'").fetchone()[0] or 0.0
    )
    rebate_usd = float(
        db.execute("SELECT COALESCE(SUM(usdc_size),0) FROM activity WHERE kind='MAKER_REBATE'").fetchone()[0] or 0.0
    )
    overall_done = finish_group(overall)
    trading_pnl = overall_done["resolved_trading_pnl_usd"]
    buy_cost = overall_done["resolved_buy_cost_usd"]
    summary = {
        "type": "POLY_L5_FULL_HISTORY_BACKTEST_V3",
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
            "5m": finish_group(by_horizon_raw[5]),
            "15m": finish_group(by_horizon_raw[15]),
        },
        "by_stream": {
            f"{a}_{h}m": finish_group(by_stream_raw[(a, h)])
            for a in ("BTC", "ETH", "SOL", "XRP")
            for h in (5, 15)
        },
        "best_markets": [x[2] for x in sorted(best, key=lambda x: x[0], reverse=True)],
        "worst_markets": [x[2] for x in sorted(worst, key=lambda x: -x[0], reverse=True)],
        "interpretation": {
            "reference_wallet_only": "These are public reference-wallet economics, not user realized P&L or executable capacity.",
            "resolved_trading_pnl": "SELL proceeds + observed REDEEM payout - BUY cost for markets with an observed redeem.",
            "redeem_limit": "Markets without an observed redeem are excluded from resolved trading P&L; this can omit losing-only or otherwise unredeemed inventory and must not be treated as a complete audited profit figure.",
            "fifo_pairing": "Chronological decomposition of observed buys, not proof of pre-locked arbitrage or trader intent.",
            "maker_taker": "Full-history Activity rows do not expose a reliable maker/taker flag; use separate takerOnly comparison for execution-role mix.",
        },
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
    }
    return summary, int(overall["markets"])


def main() -> None:
    p = argparse.ArgumentParser(description="Disk-backed l5 full-history public activity reconstruction")
    p.add_argument("--start", default="2026-01-01T00:00:00Z")
    p.add_argument("--end", default="now")
    p.add_argument("--output", default="/tmp/l5_full_history_backtest.json")
    p.add_argument("--db", default="/tmp/l5_full_history_spool.sqlite3")
    p.add_argument("--markets-jsonl", default="/tmp/l5_full_history_markets.jsonl")
    args = p.parse_args()

    start = parse_date(args.start)
    end = parse_date(args.end)
    if end <= start:
        raise SystemExit("--end must be after --start")

    base._fetch_page = resilient_fetch_page
    db = open_db(args.db)
    with httpx.Client(timeout=45.0, follow_redirects=True, headers={"User-Agent": "arb-l5-full-history-v3/1.0"}) as http:
        spool_kind(db, http, kind="TRADE", start=start, end=end, chunk_seconds=6 * 3600)
        spool_kind(db, http, kind="REDEEM", start=start, end=end, chunk_seconds=24 * 3600)
        spool_kind(db, http, kind="REWARD", start=start, end=end, chunk_seconds=7 * 86400)
        spool_kind(db, http, kind="MAKER_REBATE", start=start, end=end, chunk_seconds=7 * 86400)

    summary, market_count = analyze_spool(db, args.markets_jsonl)
    fetch_counts = {
        kind.lower() + "_rows": int(
            db.execute("SELECT COUNT(*) FROM activity WHERE kind=?", (kind,)).fetchone()[0]
        )
        for kind in ("TRADE", "REDEEM", "REWARD", "MAKER_REBATE")
    }
    result = {
        "fetch": {"start": start, "end": end, **fetch_counts},
        "summary": summary,
        "artifacts": {"sqlite": args.db, "markets_jsonl": args.markets_jsonl},
    }
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    tmp = args.output + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, sort_keys=True, separators=(",", ":"))
    os.replace(tmp, args.output)
    db.close()

    print(json.dumps({"fetch": result["fetch"], "summary": summary}, sort_keys=True), flush=True)
    print(
        f"L5_FULL_HISTORY_WRITTEN path={args.output} markets={market_count} "
        f"markets_jsonl={args.markets_jsonl}",
        flush=True,
    )


if __name__ == "__main__":
    main()
