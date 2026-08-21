"""Request one market-maker quote for the exact live 49ers + Braves Kalshi MVE.

This is pre-trade only: it creates a q=1 RFQ, observes quotes briefly, and does
NOT accept any quote or place any order. No funds are spent by this probe.
"""
from __future__ import annotations

import asyncio
import json

from src.auth import KalshiClient

BASE = "https://external-api.kalshi.com/trade-api/v2"
MARKET = "KXMVECROSSCATEGORY-SHARD1-S20268C8F3EDA15F-692042E5C77"


def _safe_quote(row: dict) -> dict:
    keys = (
        "id", "rfq_id", "market_ticker", "status", "yes_bid_dollars",
        "no_bid_dollars", "contracts", "contracts_fp", "yes_contracts_fp",
        "no_contracts_fp", "created_ts", "updated_ts", "expires_at",
    )
    return {k: row.get(k) for k in keys if row.get(k) is not None}


async def main_async() -> dict:
    client = KalshiClient(base_url=BASE)
    try:
        create = await client.post(
            "/communications/rfqs",
            json={
                "market_ticker": MARKET,
                "contracts": 1,
                "rest_remainder": False,
                "replace_existing": True,
            },
        )
        create_body = create.json() if create.content else {}
        rfq_id = str(create_body.get("id") or create_body.get("rfq", {}).get("id") or "") if isinstance(create_body, dict) else ""
        quotes: list[dict] = []
        errors: list[str] = []
        if create.status_code < 300 and rfq_id:
            seen = set()
            for _ in range(16):
                await asyncio.sleep(0.25)
                try:
                    resp = await client.get(
                        "/communications/quotes",
                        params={"rfq_id": rfq_id, "rfq_user_filter": "self", "limit": 100},
                    )
                    body = resp.json() if resp.content else {}
                    rows = body.get("quotes") or body.get("data") or [] if isinstance(body, dict) else []
                    for raw in rows:
                        if not isinstance(raw, dict):
                            continue
                        safe = _safe_quote(raw)
                        sig = json.dumps(safe, sort_keys=True, default=str)
                        if sig not in seen:
                            seen.add(sig)
                            quotes.append(safe)
                except Exception as exc:
                    errors.append(f"{type(exc).__name__}:{str(exc)[:160]}")
        return {
            "status": "SPORTS_KP_MVE_RFQ_PROBE",
            "market_ticker": MARKET,
            "create_http_status": create.status_code,
            "rfq_id": rfq_id or None,
            "quotes": quotes,
            "errors": errors[:5],
            "quote_count": len(quotes),
            "quote_acceptance": False,
            "order_entry": False,
            "funds_at_risk": False,
        }
    finally:
        await client.close()


def main() -> None:
    print(json.dumps(asyncio.run(main_async()), separators=(",", ":"), default=str))


if __name__ == "__main__":
    main()
