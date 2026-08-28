"""Read-only daily economics reconciler for l5 Polymarket short crypto.

Fetches public TRADE and REDEEM activity in small UTC time slices to avoid silently
truncating a high-volume wallet. Reports observed trade->redeem cashflow separately
from reward payments. Same-day and next-day reward payments are shown side by side;
NO earning-period attribution is assumed.

This is public cashflow analysis, not tax accounting and not realized P&L for our
system. No authentication or order entry.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
import json
import re
import time
from typing import Any

import httpx

from src.polymarket_l5_reward_snapshot import DEFAULT_WALLET, fetch_reward_rows, dec

DATA_API = "https://data-api.polymarket.com"
WINDOW_RE = re.compile(r"^(btc|eth)-updown-(5m|15m)-(\d+)$", re.IGNORECASE)


def is_short_crypto(row: dict[str, Any]) -> bool:
    return any(
        WINDOW_RE.match(str(row.get(key) or "").strip())
        for key in ("slug", "eventSlug")
    )


def _dedupe_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(row.get(k) or "")
        for k in (
            "type", "timestamp", "transactionHash", "conditionId", "asset",
            "side", "outcome", "size", "usdcSize", "price",
        )
    )


def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(30.0, max(0.25, float(retry_after)))
        except ValueError:
            pass
    return min(30.0, 0.75 * (2 ** max(0, attempt - 1)))


def fetch_activity_type(
    wallet: str,
    typ: str,
    *,
    start: int,
    end: int,
    slice_seconds: int = 3600,
    page_limit: int = 500,
    timeout_s: float = 20.0,
    max_attempts: int = 7,
) -> list[dict[str, Any]]:
    """Fetch one activity type using time slices + pagination.

    Data API offset has practical limits; slicing makes the query robust for a
    wallet doing thousands of fills per day. Boundary duplicates are deduped.
    Transient 429/5xx responses are retried with bounded exponential backoff.
    """
    if end <= start:
        return []
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, Any]] = []
    with httpx.Client(
        timeout=timeout_s,
        follow_redirects=True,
        headers={"User-Agent": "arb-polymarket-l5-daily-economics/1.1"},
    ) as client:
        cursor = start
        while cursor < end:
            slice_end = min(end, cursor + max(60, slice_seconds))
            offset = 0
            while True:
                params = {
                    "user": wallet,
                    "type": typ,
                    "start": cursor,
                    "end": slice_end,
                    "sortBy": "TIMESTAMP",
                    "sortDirection": "ASC",
                    "limit": page_limit,
                    "offset": offset,
                }
                response: httpx.Response | None = None
                for attempt in range(1, max_attempts + 1):
                    response = client.get(f"{DATA_API}/activity", params=params)
                    if response.status_code != 429 and response.status_code < 500:
                        break
                    if attempt >= max_attempts:
                        break
                    time.sleep(_retry_delay_seconds(response, attempt))
                assert response is not None
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise RuntimeError(f"unexpected Polymarket {typ} activity payload")
                for row in payload:
                    if not isinstance(row, dict):
                        continue
                    item = dict(row)
                    item.setdefault("type", typ)
                    key = _dedupe_key(item)
                    if key not in seen:
                        seen.add(key)
                        out.append(item)
                if len(payload) < page_limit:
                    break
                offset += page_limit
                if offset > 9500:
                    raise RuntimeError(
                        f"activity slice exceeded safe pagination range for {typ}; reduce slice_seconds"
                    )
            cursor = slice_end
    return sorted(out, key=lambda r: (int(r.get("timestamp") or 0), str(r.get("transactionHash") or "")))


def _utc_bounds(day: date) -> tuple[int, int]:
    start_dt = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def reward_total(rows: list[dict[str, Any]]) -> D:
    return sum((dec(row.get("usdc_size")) for row in rows), D("0"))


def reconcile_day(wallet: str, day: date) -> dict[str, Any]:
    start, end = _utc_bounds(day)
    trades = [r for r in fetch_activity_type(wallet, "TRADE", start=start, end=end) if is_short_crypto(r)]
    redeems = [r for r in fetch_activity_type(wallet, "REDEEM", start=start, end=end) if is_short_crypto(r)]

    buy_rows = [r for r in trades if str(r.get("side") or "").upper() == "BUY"]
    sell_rows = [r for r in trades if str(r.get("side") or "").upper() == "SELL"]
    buy_usdc = sum((dec(r.get("usdcSize")) for r in buy_rows), D("0"))
    sell_usdc = sum((dec(r.get("usdcSize")) for r in sell_rows), D("0"))
    redeem_usdc = sum((dec(r.get("usdcSize")) for r in redeems), D("0"))
    observed_cashflow = sell_usdc + redeem_usdc - buy_usdc

    same_day_rewards = fetch_reward_rows(wallet, start=start, end=end)
    next_day_rewards = fetch_reward_rows(wallet, start=end, end=end + 86400)
    same_reward_total = reward_total(same_day_rewards)
    next_reward_total = reward_total(next_day_rewards)

    return {
        "wallet": wallet,
        "trading_day_utc": day.isoformat(),
        "window_start": start,
        "window_end": end,
        "classification": "PUBLIC_CASHFLOW_RECONCILIATION_ATTRIBUTION_UNPROVEN",
        "order_entry": False,
        "short_crypto": {
            "trade_rows": len(trades),
            "buy_rows": len(buy_rows),
            "sell_rows": len(sell_rows),
            "redeem_rows": len(redeems),
            "buy_usdc": str(buy_usdc),
            "sell_usdc": str(sell_usdc),
            "redeem_usdc": str(redeem_usdc),
            "observed_trade_redeem_cashflow": str(observed_cashflow),
        },
        "reward_payments": {
            "same_utc_day_total": str(same_reward_total),
            "same_utc_day_rows": same_day_rewards,
            "next_utc_day_total": str(next_reward_total),
            "next_utc_day_rows": next_day_rewards,
            "earning_period_attribution": "UNPROVEN",
        },
        "scenarios_not_realized_attribution": {
            "if_same_day_payments_belong_to_trading_day": str(observed_cashflow + same_reward_total),
            "if_next_day_payments_belong_to_trading_day": str(observed_cashflow + next_reward_total),
        },
        "warnings": [
            "Reward payment timestamp is not proof of the earning period.",
            "Observed Data API cashflow is not tax characterization.",
            "No reward scenario is counted as our realized P&L.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", default=DEFAULT_WALLET)
    parser.add_argument("--date", required=True, help="UTC date YYYY-MM-DD")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    day = date.fromisoformat(args.date)
    result = reconcile_day(args.wallet, day)
    text = json.dumps(result, indent=2 if args.pretty else None, sort_keys=True)
    if args.output:
        from pathlib import Path
        p = Path(args.output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
