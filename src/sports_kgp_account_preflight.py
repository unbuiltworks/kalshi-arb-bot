"""Read-only credential and cash preflight for the K/G/P sports canary.

This module contains no order placement calls and never creates an arm token. It
verifies that the three venue credentials resolve, Gemini Prediction Markets terms
are accepted, and each venue can report at least the tiny cash reserve required by
the one-contract canary.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
from typing import Any

from src.gemini_kalshi_probe_transport import KalshiV2Executor
from src.gemini_prediction_auth import private_rest_request
from src.sports_kgp_micro_transport import PolymarketMicroExecutor

D = Decimal
MIN_READY_CASH = D("2.00")


def _d(value: Any) -> Decimal:
    try:
        out = D(str(value))
        return out if out.is_finite() else D("0")
    except Exception:
        return D("0")


def _poly_available(body: Any) -> Decimal:
    candidates: list[Any] = []
    if isinstance(body, dict):
        candidates.extend([body.get("available"), body.get("availableBalance"), body.get("buyingPower")])
        rows = body.get("balances") or []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                currency = str(row.get("currency") or "USD").upper()
                if currency in {"USD", "USDC"}:
                    candidates.extend([row.get("available"), row.get("availableBalance"), row.get("balance")])
    parsed = [_d(value) for value in candidates if value is not None]
    return max(parsed, default=D("0"))


async def _gemini() -> dict[str, Any]:
    terms = await private_rest_request("GET", "/v1/prediction-markets/terms/status", timeout=5.0)
    terms.raise_for_status()
    accepted = bool(terms.json().get("hasAcceptedLatest"))
    balances = await private_rest_request("POST", "/v1/balances", timeout=5.0)
    balances.raise_for_status()
    rows = balances.json()
    usd = next((row for row in rows if str(row.get("currency") or "").upper() == "USD"), {})
    available = _d(usd.get("available") or "0")
    return {
        "reachable": True,
        "terms_accepted": accepted,
        "minimum_cash_ready": available >= MIN_READY_CASH,
    }


async def _kalshi() -> dict[str, Any]:
    client = KalshiV2Executor()
    try:
        response = await client.client.get("/portfolio/balance")
        response.raise_for_status()
        available = _d(response.json().get("balance") or "0") / D("100")
        return {
            "reachable": True,
            "minimum_cash_ready": available >= MIN_READY_CASH,
        }
    finally:
        await client.close()


async def _polymarket() -> dict[str, Any]:
    client = PolymarketMicroExecutor()
    try:
        body = await asyncio.to_thread(client.client.account.balances)
        available = _poly_available(body)
        return {
            "reachable": True,
            "minimum_cash_ready": available >= MIN_READY_CASH,
        }
    finally:
        client.close()


async def run_preflight() -> dict[str, Any]:
    checks = {"gemini": _gemini, "kalshi": _kalshi, "polymarket": _polymarket}
    result: dict[str, Any] = {
        "status": "SPORTS_KGP_ACCOUNT_PREFLIGHT",
        "read_only": True,
        "order_entry": False,
        "arm_token_created": False,
        "minimum_ready_cash": str(MIN_READY_CASH),
        "venues": {},
    }
    for venue, fn in checks.items():
        try:
            result["venues"][venue] = await fn()
        except Exception as exc:
            result["venues"][venue] = {
                "reachable": False,
                "minimum_cash_ready": False,
                "error_type": type(exc).__name__,
                "error": str(exc)[:180],
            }
    result["ready"] = all(
        row.get("reachable") and row.get("minimum_cash_ready")
        for row in result["venues"].values()
    ) and bool(result["venues"].get("gemini", {}).get("terms_accepted"))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    print(json.dumps(asyncio.run(run_preflight()), sort_keys=True))


if __name__ == "__main__":
    main()
