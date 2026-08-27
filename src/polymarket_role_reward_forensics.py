"""Public-data execution-role and reward forensics for the l5 Polymarket wallet.

READ ONLY. No credentials, signing, or order mutation.

Maker/taker inference uses the Data API's documented `takerOnly` switch:
`/trades?user=...&takerOnly=true` is compared as a multiset against
`takerOnly=false`. Rows present only in the latter are inferred maker fills.
Activity cashflows are read separately for MAKER_REBATE, REWARD and REDEEM.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import time
from typing import Any

import httpx

DATA_API = "https://data-api.polymarket.com"
L5_WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"


def _emit(x: dict[str, Any]) -> None:
    print(json.dumps(x, sort_keys=True, default=str), flush=True)


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def trade_key(r: dict[str, Any]) -> tuple[Any, ...]:
    return (
        r.get("transactionHash"),
        int(r.get("timestamp") or 0),
        r.get("conditionId"),
        r.get("asset"),
        str(r.get("side") or "").upper(),
        r.get("outcome"),
        round(_f(r.get("price")), 10),
        round(_f(r.get("size")), 10),
    )


def fetch_trades(http: httpx.Client, *, taker_only: bool, limit: int = 10_000) -> list[dict[str, Any]]:
    resp = http.get(
        f"{DATA_API}/trades",
        params={
            "user": L5_WALLET,
            "takerOnly": str(bool(taker_only)).lower(),
            "limit": min(max(int(limit), 1), 10_000),
            "offset": 0,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def infer_execution_roles(all_rows: list[dict[str, Any]], taker_rows: list[dict[str, Any]]) -> dict[str, Any]:
    remaining_taker = Counter(trade_key(r) for r in taker_rows)
    maker_rows: list[dict[str, Any]] = []
    taker_rows_matched: list[dict[str, Any]] = []
    for r in all_rows:
        k = trade_key(r)
        if remaining_taker[k] > 0:
            remaining_taker[k] -= 1
            taker_rows_matched.append(r)
        else:
            maker_rows.append(r)

    def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        by_family: dict[str, int] = defaultdict(int)
        for r in rows:
            title = str(r.get("title") or "").lower()
            if "up or down" in title and "5m" in str(r.get("slug") or "").lower():
                by_family["5m"] += 1
            elif "up or down" in title:
                by_family["short_crypto"] += 1
            else:
                by_family["other"] += 1
        notional = sum(_f(r.get("price")) * _f(r.get("size")) for r in rows)
        shares = sum(_f(r.get("size")) for r in rows)
        return {"fills": len(rows), "shares": shares, "notional_usd": notional, "families": dict(by_family)}

    total = len(all_rows)
    return {
        "type": "polymarket_execution_role_forensics",
        "wallet": L5_WALLET,
        "sample_all_trades": total,
        "sample_taker_trades": len(taker_rows_matched),
        "sample_inferred_maker_trades": len(maker_rows),
        "maker_fill_share": (len(maker_rows) / total) if total else None,
        "maker": stats(maker_rows),
        "taker": stats(taker_rows_matched),
        "warning": "Maker status is inferred by multiset difference between public takerOnly=false and takerOnly=true responses; validate against authenticated CLOB data before execution decisions.",
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
    }


def fetch_activity_type(http: httpx.Client, kind: str, start: int, end: int, depth: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, 10_000, 500):
        resp = http.get(
            f"{DATA_API}/activity",
            params={
                "user": L5_WALLET,
                "type": kind,
                "start": int(start),
                "end": int(end),
                "limit": 500,
                "offset": offset,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
            },
        )
        resp.raise_for_status()
        page = resp.json()
        page = [x for x in page if isinstance(x, dict)] if isinstance(page, list) else []
        rows.extend(page)
        if len(page) < 500:
            return rows
    if depth >= 12 or end - start <= 1:
        raise RuntimeError(f"activity range saturated for {kind}: {start}-{end}")
    mid = (start + end) // 2
    return fetch_activity_type(http, kind, start, mid, depth + 1) + fetch_activity_type(http, kind, mid + 1, end, depth + 1)


def summarize_activity(http: httpx.Client, days: float) -> dict[str, Any]:
    end = int(time.time())
    start = end - int(max(days, 0.01) * 86400)
    out: dict[str, Any] = {
        "type": "polymarket_wallet_cashflow_forensics",
        "wallet": L5_WALLET,
        "start": start,
        "end": end,
        "days": days,
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
        "activity": {},
    }
    for kind in ("MAKER_REBATE", "REWARD", "REDEEM", "TRADE"):
        rows = fetch_activity_type(http, kind, start, end)
        cash = sum(_f(r.get("usdcSize")) for r in rows)
        buys = sum(1 for r in rows if str(r.get("side") or "").upper() == "BUY")
        sells = sum(1 for r in rows if str(r.get("side") or "").upper() == "SELL")
        out["activity"][kind] = {"rows": len(rows), "usdc_size_sum": cash, "buys": buys, "sells": sells}
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--trade-limit", type=int, default=10_000)
    p.add_argument("--activity-days", type=float, default=7.0)
    args = p.parse_args()
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": "arb-role-forensics/1.0"}) as http:
        all_rows = fetch_trades(http, taker_only=False, limit=args.trade_limit)
        taker_rows = fetch_trades(http, taker_only=True, limit=args.trade_limit)
        _emit(infer_execution_roles(all_rows, taker_rows))
        _emit(summarize_activity(http, args.activity_days))


if __name__ == "__main__":
    main()
