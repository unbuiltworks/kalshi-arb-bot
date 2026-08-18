"""Read-only Polymarket US fee/commission preview for a live market.

Uses the official order preview endpoint only. It does not submit an order.
"""

from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal
from typing import Any

import httpx
from polymarket_us import PolymarketUS

GATEWAY = "https://gateway.polymarket.us"


def _amount(level: dict[str, Any]) -> Decimal:
    raw = level.get("px")
    if isinstance(raw, dict):
        raw = raw.get("value")
    return Decimal(str(raw))


def compact(preview: Any) -> Any:
    if not isinstance(preview, dict):
        return preview
    order = preview.get("order") if isinstance(preview.get("order"), dict) else preview
    keys = (
        "marketSlug",
        "intent",
        "side",
        "price",
        "quantity",
        "avgPx",
        "commissionNotionalTotalCollected",
        "commissionsBasisPoints",
        "makerCommissionsBasisPoints",
        "cashOrderQty",
        "state",
    )
    out = {key: order.get(key) for key in keys if key in order}
    for key in ("estimatedCost", "estimatedFees", "estimatedCommission"):
        if key in preview:
            out[key] = preview.get(key)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--quantity", type=int, default=10)
    args = parser.parse_args()

    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret:
        raise SystemExit("POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY are required")

    with httpx.Client(timeout=10.0) as http:
        response = http.get(f"{GATEWAY}/v1/markets/{args.slug}/book")
        response.raise_for_status()
        body = response.json()
        data = body.get("marketData") if isinstance(body.get("marketData"), dict) else body
        bids = data.get("bids") or []
        offers = data.get("offers") or data.get("asks") or []
        if not bids or not offers:
            raise SystemExit("market has no executable bid/offer")
        best_yes_bid = _amount(bids[0])
        best_yes_ask = _amount(offers[0])

    client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=10.0)
    try:
        yes_preview = client.orders.preview({
            "marketSlug": args.slug,
            "intent": "ORDER_INTENT_BUY_LONG",
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(best_yes_ask), "currency": "USD"},
            "quantity": args.quantity,
            "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        })
        # Polymarket order prices always reference YES. Buying NO at (1-bid)
        # therefore uses BUY_SHORT with the current YES bid as price.value.
        no_preview = client.orders.preview({
            "marketSlug": args.slug,
            "intent": "ORDER_INTENT_BUY_SHORT",
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(best_yes_bid), "currency": "USD"},
            "quantity": args.quantity,
            "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        })
        print(json.dumps({
            "slug": args.slug,
            "quantity": args.quantity,
            "book": {
                "best_yes_bid": str(best_yes_bid),
                "best_yes_ask": str(best_yes_ask),
                "derived_no_ask": str(Decimal("1") - best_yes_bid),
            },
            "buy_yes_preview": compact(yes_preview),
            "buy_no_preview": compact(no_preview),
        }, indent=2, default=str))
    finally:
        client.close()


if __name__ == "__main__":
    main()
