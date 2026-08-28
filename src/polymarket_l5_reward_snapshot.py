"""Read-only public reward-payment snapshot for the observed l5 Polymarket wallet.

This intentionally reports payment timestamps only. It does NOT assume a payment
belongs to trades from the same UTC day; earning-period attribution must be proven
before combining the payment with trade/settlement P&L.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import time
from typing import Any

import httpx

DATA_API = "https://data-api.polymarket.com"
DEFAULT_WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
REWARD_TYPES = ("MAKER_REBATE", "TAKER_REBATE")


def dec(value: Any) -> D:
    try:
        return D(str(value or 0))
    except Exception:
        return D("0")


def fetch_reward_rows(wallet: str, *, start: int, end: int, timeout_s: float = 20.0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout_s, follow_redirects=True, headers={"User-Agent": "arb-polymarket-reward-snapshot/1.0"}) as client:
        for typ in REWARD_TYPES:
            response = client.get(
                f"{DATA_API}/activity",
                params={
                    "user": wallet,
                    "type": typ,
                    "start": start,
                    "end": end,
                    "sortBy": "TIMESTAMP",
                    "sortDirection": "ASC",
                    "limit": 500,
                    "offset": 0,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError("unexpected Polymarket activity payload")
            for row in payload:
                if not isinstance(row, dict):
                    continue
                ts = int(row.get("timestamp") or 0)
                rows.append(
                    {
                        "type": typ,
                        "timestamp": ts,
                        "iso_utc": datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None,
                        "usdc_size": str(dec(row.get("usdcSize"))),
                        "transaction_hash": str(row.get("transactionHash") or ""),
                    }
                )
    return sorted(rows, key=lambda r: (r["timestamp"], r["type"]))


def snapshot(wallet: str = DEFAULT_WALLET, *, hours: int = 48) -> dict[str, Any]:
    end = int(time.time())
    start = end - max(1, int(hours)) * 3600
    rows = fetch_reward_rows(wallet, start=start, end=end)
    by_type = {typ: D("0") for typ in REWARD_TYPES}
    for row in rows:
        by_type[row["type"]] += dec(row["usdc_size"])
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "wallet": wallet,
        "window_start": start,
        "window_end": end,
        "hours": hours,
        "classification": "PUBLIC_REWARD_PAYMENT_TIMESTAMPS_ONLY",
        "earning_period_attribution": "UNPROVEN",
        "realized_pnl_credit": "DO_NOT_COMBINE_WITH_TRADING_DAY_UNTIL_ATTRIBUTION_PROVEN",
        "maker_rebate_paid_in_window": str(by_type["MAKER_REBATE"]),
        "taker_rebate_paid_in_window": str(by_type["TAKER_REBATE"]),
        "total_reward_payments_in_window": str(sum(by_type.values(), D("0"))),
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", default=DEFAULT_WALLET)
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    print(json.dumps(snapshot(args.wallet, hours=args.hours), indent=2 if args.pretty else None, sort_keys=True))


if __name__ == "__main__":
    main()
