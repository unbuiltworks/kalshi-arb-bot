"""Read-only reconciliation for exact Gemini/Kalshi 15m candidate contracts.

Polls official public venue data for exact contract settlement. It imports no order
transport and cannot trade. Intended for quote-conditioned basis-risk evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from typing import Any

import httpx

from src.gemini_kalshi_fast_overlap import GEMINI_REST, KALSHI_REST

logger = logging.getLogger("kalshi_arb.gemini_kalshi_exact_candidate_reconcile")


def _find_gemini_contract(events: list[dict[str, Any]], symbol: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    want = symbol.upper()
    for event in events:
        for contract in event.get("contracts") or []:
            if not isinstance(contract, dict):
                continue
            if str(contract.get("instrumentSymbol") or "").upper() == want:
                return event, contract
    return None


async def _gemini_settled(http: httpx.AsyncClient, symbol: str) -> dict[str, Any] | None:
    # Recent 15m contracts are expected in the first 1500 settled crypto events.
    for offset in (0, 500, 1000):
        resp = await http.get(
            f"{GEMINI_REST}/events",
            params={"status": "settled", "category": "crypto", "limit": 500, "offset": offset},
        )
        resp.raise_for_status()
        payload = resp.json()
        events = payload.get("data") or []
        found = _find_gemini_contract(events, symbol)
        if found is not None:
            event, contract = found
            strike_obj = contract.get("strike") or {}
            event_settlement = event.get("settlement") or {}
            return {
                "symbol": symbol,
                "event_ticker": event.get("ticker"),
                "resolution_side": str(contract.get("resolutionSide") or "").lower() or None,
                "strike": strike_obj.get("value") if isinstance(strike_obj, dict) else None,
                "settlement_value": contract.get("settlementValue")
                    if contract.get("settlementValue") is not None
                    else (event_settlement.get("value") if isinstance(event_settlement, dict) else None),
            }
        if len(events) < 500:
            break
    return None


async def _kalshi_market(http: httpx.AsyncClient, ticker: str) -> dict[str, Any] | None:
    resp = await http.get(f"{KALSHI_REST}/markets/{ticker}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    payload = resp.json()
    market = payload.get("market") or payload
    result = str(market.get("result") or "").lower()
    if result not in {"yes", "no"}:
        return None
    return {
        "ticker": ticker,
        "result": result,
        "expiration_value": market.get("expiration_value"),
        "yes_sub_title": market.get("yes_sub_title"),
        "settlement_value": market.get("settlement_value"),
        "status": market.get("status"),
    }


def _classification(direction: str, g: str, k: str) -> str:
    direction = direction.upper()
    if direction == "KALSHI_YES_GEMINI_NO":
        if k == "yes" and g == "no":
            return "FAVORABLE_DOUBLE_WIN"
        if k == "no" and g == "yes":
            return "ADVERSE_DOUBLE_LOSS"
        return "NORMAL_ONE_WIN"
    if direction == "GEMINI_YES_KALSHI_NO":
        if g == "yes" and k == "no":
            return "FAVORABLE_DOUBLE_WIN"
        if g == "no" and k == "yes":
            return "ADVERSE_DOUBLE_LOSS"
        return "NORMAL_ONE_WIN"
    return "UNKNOWN_DIRECTION"


async def run(candidates: list[dict[str, str]], wait_seconds: int, poll_seconds: float) -> None:
    deadline = time.monotonic() + max(0, wait_seconds)
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        unresolved = list(candidates)
        resolved: list[dict[str, Any]] = []
        while unresolved:
            next_unresolved = []
            for candidate in unresolved:
                try:
                    g, k = await asyncio.gather(
                        _gemini_settled(http, candidate["gemini_symbol"]),
                        _kalshi_market(http, candidate["kalshi_ticker"]),
                    )
                except Exception as exc:
                    logger.warning(
                        "CRYPTO_EXACT_CANDIDATE_RECONCILE_RETRY %s",
                        json.dumps({"asset": candidate["asset"], "error": type(exc).__name__}, separators=(",", ":")),
                    )
                    next_unresolved.append(candidate)
                    continue
                if g is None or k is None:
                    next_unresolved.append(candidate)
                    continue
                g_out = str(g.get("resolution_side") or "").lower()
                k_out = str(k.get("result") or "").lower()
                row = {
                    **candidate,
                    "gemini": g,
                    "kalshi": k,
                    "venue_outcomes_agree": g_out == k_out,
                    "candidate_payoff_classification": _classification(candidate["direction"], g_out, k_out),
                    "classification": "QUOTE_CONDITIONED_SETTLEMENT_RECONCILIATION",
                    "trade_authorized": False,
                    "read_only": True,
                    "order_entry": False,
                }
                resolved.append(row)
                logger.warning("CRYPTO_EXACT_CANDIDATE_RECONCILED %s", json.dumps(row, separators=(",", ":"), default=str))
            unresolved = next_unresolved
            if not unresolved or time.monotonic() >= deadline:
                break
            await asyncio.sleep(max(0.5, poll_seconds))

    logger.warning(
        "CRYPTO_EXACT_CANDIDATE_RECONCILE_SUMMARY %s",
        json.dumps({
            "requested": len(candidates),
            "resolved": len(resolved),
            "unresolved": [c["asset"] for c in unresolved],
            "trade_authorized": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":"), default=str),
    )
    if unresolved:
        raise RuntimeError("exact candidate settlements not yet available")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--wait-seconds", type=int, default=180)
    p.add_argument("--poll-seconds", type=float, default=5.0)
    args = p.parse_args()
    candidates = [
        {
            "asset": "BTC",
            "gemini_symbol": "GEMI-BTC15M2608222115-UP",
            "kalshi_ticker": "KXBTC15M-26AUG221715-15",
            "direction": "GEMINI_YES_KALSHI_NO",
            "observed_best_locked_quote_profit": "0.0460",
            "threshold_gap_abs_bps": "0.6881080443452005",
            "direction_matched_safe_orientation": "false",
        },
        {
            "asset": "XRP",
            "gemini_symbol": "GEMI-XRP15M2608222115-UP",
            "kalshi_ticker": "KXXRP15M-26AUG221715-15",
            "direction": "KALSHI_YES_GEMINI_NO",
            "observed_best_locked_quote_profit": "0.0300",
            "threshold_gap_abs_bps": "2.092883240001614",
            "direction_matched_safe_orientation": "true",
        },
    ]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(candidates, args.wait_seconds, args.poll_seconds))


if __name__ == "__main__":
    main()
