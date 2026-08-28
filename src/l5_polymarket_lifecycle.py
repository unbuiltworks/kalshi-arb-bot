"""Read-only lifecycle collector for l5 Polymarket short-duration crypto activity.

Uses only Polymarket's public Data API. It never authenticates and never places,
modifies, or cancels orders. The purpose is to avoid misclassifying trade-only
inventory as realized P&L when public MERGE/SPLIT/REDEEM/rebate activity exists.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any

import httpx

DATA_API = "https://data-api.polymarket.com"
DEFAULT_WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
ACTIVITY_TYPES = ("TRADE", "MERGE", "SPLIT", "REDEEM", "MAKER_REBATE", "TAKER_REBATE")
WINDOW_RE = re.compile(r"^(btc|eth)-updown-(5m|15m)-(\d+)$", re.IGNORECASE)
D = Decimal


def dec(value: Any) -> D:
    try:
        return D(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return D("0")


def short_crypto_slug(row: dict[str, Any]) -> tuple[str, str, int] | None:
    for key in ("slug", "eventSlug"):
        match = WINDOW_RE.match(str(row.get(key) or ""))
        if match:
            asset, horizon, start = match.groups()
            return asset.lower(), horizon.lower(), int(start)
    return None


def activity_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(row.get("timestamp") or 0),
        str(row.get("type") or ""),
        str(row.get("conditionId") or ""),
        str(row.get("asset") or ""),
        str(row.get("side") or ""),
        str(row.get("outcome") or ""),
        str(row.get("size") or ""),
        str(row.get("usdcSize") or ""),
        str(row.get("price") or ""),
        str(row.get("transactionHash") or ""),
    )


async def fetch_complete_activity(
    wallet: str,
    *,
    start: int,
    end: int,
    slice_seconds: int = 900,
    page_size: int = 500,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch activity in bounded time slices so the API's offset cap cannot censor a long window."""
    if end < start:
        raise ValueError("end must be >= start")
    slice_seconds = max(60, int(slice_seconds))
    page_size = max(1, min(int(page_size), 500))
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=20.0, follow_redirects=True)
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    try:
        cursor = int(start)
        while cursor <= end:
            slice_end = min(cursor + slice_seconds - 1, end)
            offset = 0
            while True:
                params = {
                    "user": wallet,
                    "type": ",".join(ACTIVITY_TYPES),
                    "start": cursor,
                    "end": slice_end,
                    "sortBy": "TIMESTAMP",
                    "sortDirection": "ASC",
                    "limit": page_size,
                    "offset": offset,
                }
                response = await http.get(f"{DATA_API}/activity", params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise RuntimeError("unexpected Polymarket activity payload")
                rows = [row for row in payload if isinstance(row, dict)]
                for row in rows:
                    key = activity_key(row)
                    if key not in seen:
                        seen.add(key)
                        out.append(row)
                if len(rows) < page_size:
                    break
                offset += len(rows)
                # Official activity endpoint offset is capped at 5000. A single
                # 15m slice should never approach this for one wallet; fail
                # closed instead of silently truncating if it ever does.
                if offset >= 5000:
                    raise RuntimeError(
                        f"activity slice hit offset cap; reduce slice_seconds: {cursor}-{slice_end}"
                    )
            cursor = slice_end + 1
    finally:
        if own_client:
            await http.aclose()
    return sorted(out, key=lambda row: (int(row.get("timestamp") or 0), activity_key(row)))


def summarize_lifecycle(rows: list[dict[str, Any]]) -> dict[str, Any]:
    short_rows: list[dict[str, Any]] = []
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        parsed = short_crypto_slug(row)
        if parsed is None:
            continue
        short_rows.append(row)
        condition = str(row.get("conditionId") or row.get("slug") or row.get("eventSlug") or "")
        by_market[condition].append(row)

    type_counts = Counter(str(row.get("type") or "UNKNOWN") for row in short_rows)
    markets = []
    lifecycle_markets = 0
    for _, market_rows in sorted(
        by_market.items(), key=lambda item: min(int(r.get("timestamp") or 0) for r in item[1])
    ):
        first = market_rows[0]
        parsed = short_crypto_slug(first)
        assert parsed is not None
        coin, horizon, start_ts = parsed
        counts = Counter(str(r.get("type") or "UNKNOWN") for r in market_rows)
        if any(counts.get(t, 0) for t in ("MERGE", "SPLIT", "REDEEM", "MAKER_REBATE", "TAKER_REBATE")):
            lifecycle_markets += 1
        trade_buy_notional = D("0")
        trade_sell_notional = D("0")
        rebate_usdc = D("0")
        redeem_usdc = D("0")
        merge_usdc = D("0")
        split_usdc = D("0")
        for row in market_rows:
            typ = str(row.get("type") or "")
            side = str(row.get("side") or "").upper()
            usdc = dec(row.get("usdcSize"))
            if typ == "TRADE":
                # Prefer public usdcSize; fall back to size*price.
                notional = usdc or (dec(row.get("size")) * dec(row.get("price")))
                if side == "BUY":
                    trade_buy_notional += notional
                elif side == "SELL":
                    trade_sell_notional += notional
            elif typ in {"MAKER_REBATE", "TAKER_REBATE"}:
                rebate_usdc += usdc
            elif typ == "REDEEM":
                redeem_usdc += usdc
            elif typ == "MERGE":
                merge_usdc += usdc
            elif typ == "SPLIT":
                split_usdc += usdc
        markets.append({
            "slug": str(first.get("slug") or first.get("eventSlug") or ""),
            "condition_id": str(first.get("conditionId") or ""),
            "coin": coin,
            "horizon": horizon,
            "window_start_ts": start_ts,
            "activity_count": len(market_rows),
            "type_counts": dict(counts),
            "trade_buy_notional": str(trade_buy_notional),
            "trade_sell_notional": str(trade_sell_notional),
            "public_rebate_usdc": str(rebate_usdc),
            "public_redeem_usdc": str(redeem_usdc),
            "public_merge_usdc": str(merge_usdc),
            "public_split_usdc": str(split_usdc),
        })

    return {
        "short_crypto_activity_count": len(short_rows),
        "market_count": len(markets),
        "type_counts": dict(type_counts),
        "markets_with_nontrade_lifecycle_activity": lifecycle_markets,
        "markets": markets,
        "accounting_warning": (
            "TRADE-only mark-to-resolution is not realized P&L when MERGE/SPLIT/REDEEM/rebate activity exists. "
            "Lifecycle cash-flow semantics must be audited before calling wallet profit realized."
        ),
    }


async def _amain() -> None:
    parser = argparse.ArgumentParser(description="Read-only l5 public Polymarket lifecycle collector")
    parser.add_argument("--wallet", default=DEFAULT_WALLET)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--slice-seconds", type=int, default=900)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    rows = await fetch_complete_activity(
        args.wallet,
        start=args.start,
        end=args.end,
        slice_seconds=args.slice_seconds,
    )
    result = summarize_lifecycle(rows)
    if args.summary_only:
        result = {k: v for k, v in result.items() if k != "markets"}
    print(json.dumps(result, indent=2, sort_keys=True))


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
