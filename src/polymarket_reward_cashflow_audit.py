"""Read-only public activity audit for maker rebates, liquidity rewards, and redeems."""
from __future__ import annotations

import argparse
import json
import time
from typing import Any

import httpx

DATA_API = "https://data-api.polymarket.com"
WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def fetch_kind(http: httpx.Client, kind: str, start: int, end: int) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, 5000, 500):
        resp = http.get(
            f"{DATA_API}/activity",
            params={
                "user": WALLET,
                "type": kind,
                "start": start,
                "end": end,
                "limit": 500,
                "offset": offset,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        page = [r for r in payload if isinstance(r, dict)] if isinstance(payload, list) else []
        rows.extend(page)
        if len(page) < 500:
            return rows, False
    return rows, True


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=float, default=7.0)
    args = p.parse_args()
    end = int(time.time())
    start = end - int(max(args.days, 0.01) * 86400)
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": "arb-reward-audit/1.0"}) as http:
        for kind in ("MAKER_REBATE", "REWARD", "REDEEM"):
            rows, saturated = fetch_kind(http, kind, start, end)
            payload = {
                "type": "polymarket_reward_cashflow_audit",
                "wallet": WALLET,
                "activity_type": kind,
                "days": args.days,
                "rows": len(rows),
                "usdc_size_sum": sum(_f(r.get("usdcSize")) for r in rows),
                "size_sum": sum(_f(r.get("size")) for r in rows),
                "saturated_at_5000_rows": saturated,
                "shadow_only": True,
                "order_entry": False,
                "realized_pnl": 0.0,
            }
            print(json.dumps(payload, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
