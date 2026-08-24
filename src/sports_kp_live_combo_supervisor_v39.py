"""Production combo supervisor v39: Polymarket combo/RFQ auth + MLB symbol preflight.

Read-only diagnostic. Verifies whether the installed authenticated SDK generic
transport can reach RFQ identity/account endpoints, and inspects public MLB event
market-side schema for exact combo-leg symbols. It never logs credentials, account
IDs, RFQ user IDs, balances, or other private values. No combo creation, RFQ write,
quote acceptance, or order entry occurs in this version.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
from typing import Any, Mapping

import httpx

from src import sports_kp_live_combo_supervisor_v38 as v38

logger = v38.logger


def _shape(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {"type": "mapping", "keys": sorted(str(k) for k in value.keys())[:40]}
    if isinstance(value, list):
        return {"type": "list", "length": len(value), "item_type": type(value[0]).__name__ if value else None,
                "item_keys": sorted(str(k) for k in value[0].keys())[:40] if value and isinstance(value[0], Mapping) else []}
    return {"type": type(value).__name__}


def _safe_market(m: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": m.get("id"), "slug": m.get("slug"), "title": m.get("title") or m.get("question"),
        "sportsMarketType": m.get("sportsMarketType"), "sportsMarketTypeV2": m.get("sportsMarketTypeV2"),
        "outcomes": m.get("outcomes"), "marketSides": m.get("marketSides"),
        "active": m.get("active"), "closed": m.get("closed"), "acceptingOrders": m.get("acceptingOrders"),
    }


def _auth_probe() -> None:
    try:
        from polymarket_us import PolymarketUS
        client = PolymarketUS(key_id=os.environ["POLYMARKET_KEY_ID"], secret_key=os.environ["POLYMARKET_SECRET_KEY"])
        details: dict[str, Any] = {
            "get_signature": str(inspect.signature(client.get)),
            "post_signature": str(inspect.signature(client.post)),
            "api_base_url": str(getattr(client, "api_base_url", "")),
            "gateway_base_url": str(getattr(client, "gateway_base_url", "")),
        }
        for label, path in (("rfq_user", "/v1/rfqs/user-id"), ("accounts", "/v1/accounts"), ("whoami", "/v1/whoami")):
            try:
                result = client.get(path)
                details[label] = {"ok": True, "shape": _shape(result)}
            except Exception as exc:
                details[label] = {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:140]}
        try:
            balances = client.account.balances()
            details["retail_balances"] = {"ok": True, "shape": _shape(balances)}
        except Exception as exc:
            details["retail_balances"] = {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:140]}
        client.close()
        logger.warning("SPORTS_POLY_COMBO_AUTH_PREFLIGHT %s", json.dumps({
            **details, "private_values_logged": False, "writes": False, "order_entry": False,
        }, separators=(",", ":"), default=str))
    except Exception as exc:
        logger.warning("SPORTS_POLY_COMBO_AUTH_PREFLIGHT_ERROR %s", json.dumps({
            "error": type(exc).__name__, "detail": str(exc)[:180], "private_values_logged": False, "order_entry": False,
        }, separators=(",", ":")))


async def _mlb_schema_probe() -> None:
    url = "https://gateway.polymarket.us/v2/leagues/mlb/events"
    params = {"limit": 500, "offset": 0, "type": "sport", "section": "general"}
    targets = (("atl", "mil"), ("wsh", "mia"))
    found: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            r = await http.get(url, params=params)
            r.raise_for_status()
            body = r.json()
        events = body.get("events") or body.get("data") or [] if isinstance(body, Mapping) else []
        for e in events:
            if not isinstance(e, Mapping):
                continue
            hay = " ".join(str(e.get(k) or "") for k in ("slug", "title", "ticker", "gameId")).lower()
            if not any(a in hay and b in hay for a, b in targets):
                continue
            markets = [_safe_market(m) for m in (e.get("markets") or []) if isinstance(m, Mapping)]
            found.append({
                "id": e.get("id"), "slug": e.get("slug"), "title": e.get("title"),
                "startTime": e.get("startTime") or e.get("startDate"), "market_count": len(markets),
                "markets": markets[:30],
            })
        logger.warning("SPORTS_POLY_MLB_COMBO_SYMBOL_PREFLIGHT %s", json.dumps({
            "target_games_found": len(found), "targets": found, "public_data_only": True,
            "combo_creation": False, "rfq": False, "order_entry": False,
        }, separators=(",", ":"), default=str))
    except Exception as exc:
        logger.warning("SPORTS_POLY_MLB_COMBO_SYMBOL_PREFLIGHT_ERROR %s", json.dumps({
            "error": type(exc).__name__, "detail": str(exc)[:180], "order_entry": False,
        }, separators=(",", ":")))


async def run() -> None:
    await asyncio.to_thread(_auth_probe)
    await _mlb_schema_probe()
    await v38.run()


def main() -> None:
    import logging
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
