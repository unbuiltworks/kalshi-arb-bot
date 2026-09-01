"""Read-only aggregate audit of recent Gemini Prediction Markets partial fills.

Uses the authenticated order-history endpoint only. It never submits/cancels an
order and never logs credentials, order IDs, client IDs, prices, or account balances.
The goal is to learn whether recent crypto prediction orders have actually closed
with fractional filled quantities, which is relevant to resting-maker risk.

The REST OrderResponse does not expose fill-level fees, so this module intentionally
does NOT claim anything about fee-rounding semantics. It reports that limitation
explicitly and leaves live admission fail-closed on the conservative fee model.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import re
from typing import Any, Mapping

from src.gemini_prediction_auth import private_rest_request

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_prediction_partial_history_audit")
CRYPTO_RE = re.compile(r"^GEMI-(BTC|ETH|SOL|XRP)(?:15M|05M|\d)", re.I)


def _d(value: Any) -> D | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


async def _history(status: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    response = await private_rest_request(
        "POST",
        "/v1/prediction-markets/orders/history",
        params={"status": status, "limit": 1000, "from": start_ms, "to": end_ms},
        timeout=10.0,
    )
    response.raise_for_status()
    body = response.json()
    rows = body.get("orders") if isinstance(body, Mapping) else []
    return [dict(row) for row in (rows or []) if isinstance(row, Mapping)]


def _bucket(q: D) -> str:
    if q < D("0.10"): return "0.01-0.09"
    if q < D("0.25"): return "0.10-0.24"
    if q < D("0.36"): return "0.25-0.35"
    if q < D("0.50"): return "0.36-0.49"
    if q < D("0.77"): return "0.50-0.76"
    if q < D("1.00"): return "0.77-0.99"
    return "1.00+"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    crypto = []
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if not CRYPTO_RE.match(symbol):
            continue
        quantity = _d(row.get("quantity"))
        filled = _d(row.get("filledQuantity"))
        remaining = _d(row.get("remainingQuantity"))
        if quantity is None or filled is None or remaining is None or quantity <= 0 or filled < 0:
            continue
        crypto.append((symbol, quantity, filled, remaining, str(row.get("status") or "").lower()))

    partial_closed = [(s,q,f,r,st) for s,q,f,r,st in crypto if D("0") < f < q]
    fractional_filled = [(s,q,f,r,st) for s,q,f,r,st in crypto if f > 0 and f != f.to_integral_value()]
    assets = Counter()
    buckets = Counter()
    exact_sizes = Counter()
    for symbol, quantity, filled, remaining, status in fractional_filled:
        match = CRYPTO_RE.match(symbol)
        if match:
            assets[match.group(1).upper()] += 1
        buckets[_bucket(filled)] += 1
        exact_sizes[str(filled.normalize())] += 1

    return {
        "crypto_orders": len(crypto),
        "crypto_orders_with_any_fill": sum(1 for _,_,f,_,_ in crypto if f > 0),
        "closed_partial_fill_orders": len(partial_closed),
        "fractional_filled_quantity_orders": len(fractional_filled),
        "fractional_by_asset": dict(sorted(assets.items())),
        "fractional_fill_buckets": dict(sorted(buckets.items())),
        "most_common_fractional_fill_sizes": exact_sizes.most_common(12),
        "fee_fields_exposed_by_history": False,
        "fill_level_fee_semantics_resolved": False,
    }


async def run(days: int = 7) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    # Query one UTC day at a time to stay below the endpoint's bounded 1000-result cap.
    rows: list[dict[str, Any]] = []
    cursor = start
    while cursor < now:
        end = min(now, cursor + timedelta(days=1))
        start_ms = int(cursor.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        filled, cancelled = await asyncio.gather(
            _history("filled", start_ms, end_ms),
            _history("cancelled", start_ms, end_ms),
        )
        rows.extend(filled)
        rows.extend(cancelled)
        cursor = end

    out = {
        "lookback_days": days,
        "aggregate": summarize(rows),
        "read_only": True,
        "order_entry": False,
        "realized": False,
        "privacy": "aggregate_only_no_order_ids_prices_or_balances",
    }
    logger.warning("GEMINI_PARTIAL_HISTORY_AUDIT %s", json.dumps(out, separators=(",", ":"), default=str))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
