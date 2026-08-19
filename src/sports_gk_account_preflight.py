"""Read-only Gemini/Kalshi account preflight for the sports live canary."""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json

from src.gemini_kalshi_probe_transport import KalshiV2Executor
from src.gemini_prediction_auth import private_rest_request, verify_authenticated_ws

D = Decimal
MIN_READY_CASH = D("2.00")


async def main_async() -> dict:
    out = {
        "status": "SPORTS_GK_ACCOUNT_PREFLIGHT",
        "read_only": True,
        "order_entry": False,
        "minimum_ready_cash": str(MIN_READY_CASH),
        "venues": {},
    }
    try:
        terms = await private_rest_request("GET", "/v1/prediction-markets/terms/status", timeout=5.0)
        terms.raise_for_status()
        balances = await private_rest_request("POST", "/v1/balances", timeout=5.0)
        balances.raise_for_status()
        rows = balances.json()
        usd = next((row for row in rows if str(row.get("currency") or "").upper() == "USD"), {})
        available = D(str(usd.get("available") or "0"))
        ws_ok = await verify_authenticated_ws(timeout=5.0)
        out["venues"]["gemini"] = {
            "reachable": True,
            "terms_accepted": bool(terms.json().get("hasAcceptedLatest")),
            "authenticated_ws": bool(ws_ok),
            "minimum_cash_ready": available >= MIN_READY_CASH,
        }
    except Exception as exc:
        out["venues"]["gemini"] = {
            "reachable": False,
            "terms_accepted": False,
            "authenticated_ws": False,
            "minimum_cash_ready": False,
            "error_type": type(exc).__name__,
        }

    kalshi = KalshiV2Executor()
    try:
        status = await kalshi.client.get("/exchange/status")
        status.raise_for_status()
        balance = await kalshi.client.get("/portfolio/balance")
        balance.raise_for_status()
        available = D(str(balance.json().get("balance") or "0")) / D("100")
        out["venues"]["kalshi"] = {
            "reachable": True,
            "production_authenticated": True,
            "minimum_cash_ready": available >= MIN_READY_CASH,
        }
    except Exception as exc:
        out["venues"]["kalshi"] = {
            "reachable": False,
            "production_authenticated": False,
            "minimum_cash_ready": False,
            "error_type": type(exc).__name__,
        }
    finally:
        await kalshi.close()

    g = out["venues"].get("gemini", {})
    k = out["venues"].get("kalshi", {})
    out["ready"] = bool(
        g.get("reachable") and g.get("terms_accepted") and g.get("authenticated_ws")
        and g.get("minimum_cash_ready") and k.get("reachable")
        and k.get("production_authenticated") and k.get("minimum_cash_ready")
    )
    return out


def main() -> None:
    print(json.dumps(asyncio.run(main_async()), sort_keys=True))


if __name__ == "__main__":
    main()
