"""No-order readiness gate for a future BTC Gemini/Kalshi execution probe.

This command is intentionally incapable of placing orders. It checks credentials,
terms, authenticated account-stream access, Kalshi authentication, and current BTC
market alignment, then prints one JSON readiness report.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from src.auth import KalshiClient
from src.gemini_kalshi_aligned_watch import _discover_pairs
from src.gemini_prediction_auth import (
    credentials,
    has_credentials,
    is_account_scoped_key,
    private_rest_request,
    verify_authenticated_ws,
)


def _error_name(exc: Exception) -> str:
    """Return only an exception class, never credential-bearing text."""
    return type(exc).__name__


async def check_readiness() -> dict[str, Any]:
    result: dict[str, Any] = {
        "strategy": "GEMINI_KALSHI_BTC_15M",
        "mode": "NO_ORDER_READINESS_CHECK",
        "read_only": True,
        "order_entry": False,
        "kalshi_authenticated": False,
        "gemini_credentials_present": False,
        "gemini_account_scoped_key": False,
        "gemini_terms_accepted": False,
        "gemini_authenticated_ws": False,
        "btc_pair_available": False,
        "btc_gemini_symbol": None,
        "btc_kalshi_ticker": None,
        "btc_window_end_utc": None,
        "ready_for_live_probe": False,
        "blockers": [],
        "errors": {},
    }

    kalshi = None
    try:
        kalshi = KalshiClient()
        await kalshi.get_exchange_status()
        result["kalshi_authenticated"] = True
    except Exception as exc:
        result["errors"]["kalshi_auth"] = _error_name(exc)
        result["blockers"].append("kalshi_authentication_failed")
    finally:
        if kalshi is not None:
            try:
                await kalshi.close()
            except Exception:
                pass

    key, _secret = credentials()
    result["gemini_credentials_present"] = has_credentials()
    result["gemini_account_scoped_key"] = bool(key) and is_account_scoped_key(key)
    if not result["gemini_credentials_present"]:
        result["blockers"].append("gemini_credentials_missing")
    elif not result["gemini_account_scoped_key"]:
        result["blockers"].append("gemini_key_not_account_scoped")
    else:
        try:
            response = await private_rest_request("GET", "/v1/prediction-markets/terms/status")
            if response.status_code == 200:
                payload = response.json()
                result["gemini_terms_accepted"] = bool(payload.get("hasAcceptedLatest"))
                if not result["gemini_terms_accepted"]:
                    result["blockers"].append("gemini_prediction_terms_not_accepted")
            else:
                result["errors"]["gemini_terms"] = f"HTTP_{response.status_code}"
                result["blockers"].append("gemini_terms_status_unavailable")
        except Exception as exc:
            result["errors"]["gemini_terms"] = _error_name(exc)
            result["blockers"].append("gemini_terms_status_unavailable")

        try:
            result["gemini_authenticated_ws"] = await verify_authenticated_ws(timeout=5.0)
            if not result["gemini_authenticated_ws"]:
                result["blockers"].append("gemini_authenticated_ws_failed")
        except Exception as exc:
            result["errors"]["gemini_ws"] = _error_name(exc)
            result["blockers"].append("gemini_authenticated_ws_failed")

    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            pairs = await _discover_pairs(http, 30.0)
        now = time.time()
        btc = next(
            (p for p in pairs if p.asset == "BTC" and p.window_end.timestamp() > now),
            None,
        )
        if btc is not None:
            result["btc_pair_available"] = True
            result["btc_gemini_symbol"] = btc.gemini_symbol
            result["btc_kalshi_ticker"] = btc.kalshi_ticker
            result["btc_window_end_utc"] = btc.window_end.isoformat()
        else:
            result["blockers"].append("no_current_aligned_btc_pair")
    except Exception as exc:
        result["errors"]["btc_discovery"] = _error_name(exc)
        result["blockers"].append("btc_pair_discovery_failed")

    required = (
        result["kalshi_authenticated"],
        result["gemini_credentials_present"],
        result["gemini_account_scoped_key"],
        result["gemini_terms_accepted"],
        result["gemini_authenticated_ws"],
        result["btc_pair_available"],
    )
    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    result["ready_for_live_probe"] = all(required) and not result["blockers"]
    return result


async def _main() -> None:
    report = await check_readiness()
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
