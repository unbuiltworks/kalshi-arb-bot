"""Rolling public-data economics ledger for the l5 Polymarket wallet.

READ ONLY. Computes conservative cash economics for short-duration markets that
have an observed REDEEM in the target window, plus explicit MAKER_REBATE and
REWARD activity. It does not treat current positions or shadow fills as realized.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import time
from typing import Any

import httpx

from src.polymarket_wallet_shadow import activity_id, classify_market

DATA_API = "https://data-api.polymarket.com"
WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _emit(x: dict[str, Any]) -> None:
    print(json.dumps(x, sort_keys=True, default=str), flush=True)


def fetch_page(http: httpx.Client, *, kind: str, start: int, end: int, offset: int) -> list[dict[str, Any]]:
    r = http.get(
        f"{DATA_API}/activity",
        params={
            "user": WALLET,
            "type": kind,
            "start": int(start),
            "end": int(end),
            "limit": 500,
            "offset": int(offset),
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        },
    )
    r.raise_for_status()
    data = r.json()
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def fetch_window(http: httpx.Client, *, kind: str, start: int, end: int, depth: int = 0) -> list[dict[str, Any]]:
    """Read activity without silently crossing the public ~5k offset boundary."""
    rows: list[dict[str, Any]] = []
    saturated = True
    for offset in range(0, 5000, 500):
        page = fetch_page(http, kind=kind, start=start, end=end, offset=offset)
        rows.extend(page)
        if len(page) < 500:
            saturated = False
            break
    if saturated:
        if depth >= 16 or end - start <= 1:
            raise RuntimeError(f"activity slice saturated for {kind}: {start}-{end}")
        mid = (start + end) // 2
        rows = fetch_window(http, kind=kind, start=start, end=mid, depth=depth + 1) + fetch_window(
            http, kind=kind, start=mid + 1, end=end, depth=depth + 1
        )
    dedup = {activity_id(row): row for row in rows}
    return list(dedup.values())


def build_ledger(*, trades: list[dict[str, Any]], redeems: list[dict[str, Any]], rewards: list[dict[str, Any]], rebates: list[dict[str, Any]], target_start: int, target_end: int) -> dict[str, Any]:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        cid = str(row.get("conditionId") or "").strip()
        if cid:
            by_market[cid].append(row)

    redeem_by_market: dict[str, float] = defaultdict(float)
    redeem_ts_by_market: dict[str, list[int]] = defaultdict(list)
    for row in redeems:
        cid = str(row.get("conditionId") or "").strip()
        if not cid:
            continue
        ts = int(row.get("timestamp") or 0)
        if target_start <= ts <= target_end:
            redeem_by_market[cid] += _f(row.get("usdcSize"))
            redeem_ts_by_market[cid].append(ts)

    resolved_rows: list[dict[str, Any]] = []
    for cid, payout in redeem_by_market.items():
        market_trades = by_market.get(cid, [])
        if not market_trades:
            continue
        timestamps = [int(r.get("timestamp") or 0) for r in market_trades]
        # The caller fetches a buffer before target_start; require the whole observed
        # market lifecycle to start inside that buffer so boundary leakage is visible.
        buy_cost = 0.0
        sell_proceeds = 0.0
        buy_count = sell_count = 0
        shares_by_outcome: dict[str, float] = defaultdict(float)
        for r in market_trades:
            side = str(r.get("side") or "").upper()
            price, size = _f(r.get("price")), _f(r.get("size"))
            if side == "BUY":
                buy_cost += price * size
                buy_count += 1
                shares_by_outcome[str(r.get("outcome") or "UNKNOWN")] += size
            elif side == "SELL":
                sell_proceeds += price * size
                sell_count += 1
                shares_by_outcome[str(r.get("outcome") or "UNKNOWN")] -= size
        sample = market_trades[0]
        family = classify_market(sample)
        pnl = sell_proceeds + payout - buy_cost
        resolved_rows.append({
            "condition_id": cid,
            "title": sample.get("title"),
            "family": family,
            "first_trade_ts": min(timestamps),
            "last_trade_ts": max(timestamps),
            "redeem_ts": max(redeem_ts_by_market[cid]),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "buy_cost_usd": buy_cost,
            "sell_proceeds_usd": sell_proceeds,
            "redeem_usd": payout,
            "trading_pnl_usd": pnl,
            "shares_by_outcome": dict(shares_by_outcome),
        })

    short = [r for r in resolved_rows if r["family"] in {"crypto_updown_5m", "crypto_updown_15m"}]
    trading_pnl = sum(r["trading_pnl_usd"] for r in short)
    buy_cost = sum(r["buy_cost_usd"] for r in short)
    redeemed = sum(r["redeem_usd"] for r in short)
    rebate_usd = sum(_f(r.get("usdcSize")) for r in rebates if target_start <= int(r.get("timestamp") or 0) <= target_end)
    reward_usd = sum(_f(r.get("usdcSize")) for r in rewards if target_start <= int(r.get("timestamp") or 0) <= target_end)
    total = trading_pnl + rebate_usd + reward_usd

    by_family: dict[str, dict[str, float]] = {}
    for family in ("crypto_updown_5m", "crypto_updown_15m"):
        rows = [r for r in short if r["family"] == family]
        by_family[family] = {
            "resolved_markets": len(rows),
            "buy_cost_usd": sum(r["buy_cost_usd"] for r in rows),
            "redeem_usd": sum(r["redeem_usd"] for r in rows),
            "trading_pnl_usd": sum(r["trading_pnl_usd"] for r in rows),
        }

    winners = sorted(short, key=lambda r: r["trading_pnl_usd"], reverse=True)[:5]
    losers = sorted(short, key=lambda r: r["trading_pnl_usd"])[:5]
    return {
        "type": "POLY_L5_ECON_LEDGER",
        "wallet": WALLET,
        "target_start": target_start,
        "target_end": target_end,
        "resolved_short_crypto_markets": len(short),
        "resolved_buy_cost_usd": buy_cost,
        "resolved_redeem_usd": redeemed,
        "resolved_trading_pnl_usd": trading_pnl,
        "maker_rebate_usd": rebate_usd,
        "liquidity_reward_usd": reward_usd,
        "resolved_plus_incentives_usd": total,
        "by_family": by_family,
        "best_resolved_markets": [{k: r[k] for k in ("title", "trading_pnl_usd", "buy_cost_usd", "redeem_usd")} for r in winners],
        "worst_resolved_markets": [{k: r[k] for k in ("title", "trading_pnl_usd", "buy_cost_usd", "redeem_usd")} for r in losers],
        "warning": "Conservative resolved-market ledger. Markets without an observed redeem are excluded; incentive cashflows are explicit public activity. Boundary inventory is not forced into P&L.",
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
    }


def run_once(hours: float) -> dict[str, Any]:
    end = int(time.time())
    start = end - int(hours * 3600)
    # 30-minute buffer captures the full lifecycle for 5m/15m markets whose
    # redemption lands just inside the target window.
    fetch_start = start - 1800
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": "arb-l5-econ-ledger/1.0"}) as http:
        trades = fetch_window(http, kind="TRADE", start=fetch_start, end=end)
        redeems = fetch_window(http, kind="REDEEM", start=fetch_start, end=end)
        rewards = fetch_window(http, kind="REWARD", start=start, end=end)
        rebates = fetch_window(http, kind="MAKER_REBATE", start=start, end=end)
    return build_ledger(
        trades=trades,
        redeems=redeems,
        rewards=rewards,
        rebates=rebates,
        target_start=start,
        target_end=end,
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--loop-seconds", type=float, default=0.0)
    args = p.parse_args()
    while True:
        try:
            _emit(run_once(max(1.0, args.hours)))
        except Exception as exc:
            _emit({
                "type": "POLY_L5_ECON_LEDGER_ERROR",
                "error": type(exc).__name__,
                "message": str(exc)[:500],
                "shadow_only": True,
                "order_entry": False,
                "realized_pnl": 0.0,
            })
        if args.loop_seconds <= 0:
            break
        time.sleep(max(60.0, args.loop_seconds))


if __name__ == "__main__":
    main()
