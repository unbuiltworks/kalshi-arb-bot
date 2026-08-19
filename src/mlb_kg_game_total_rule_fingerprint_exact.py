"""Exact-event wrapper for the read-only K/G MLB game-total rule fingerprint probe.

Unlike the original collector's active-event scan, this fetches the Gemini parent
event directly from the exact instrument symbol. That lets us audit a signal even
if the event moved from active to closed/settled before the Railway probe starts.
No credentials or order-entry endpoints are used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any, Mapping

import httpx

from src.mlb_kg_game_total_rule_fingerprint import (
    GEMINI_REST,
    KALSHI_REST,
    build_pair_fingerprint,
    find_gemini_contract,
)


def gemini_event_ticker(instrument_symbol: str) -> str:
    symbol = str(instrument_symbol or "").strip().upper()
    if not symbol.startswith("GEMI-") or "-" not in symbol:
        raise ValueError("invalid Gemini instrument symbol")
    event_ticker, contract_token = symbol.rsplit("-", 1)
    if not event_ticker or not contract_token:
        raise ValueError("Gemini instrument symbol is missing its contract token")
    return event_ticker


async def fetch_exact_event_rules(
    *,
    kalshi_ticker: str,
    gemini_symbol: str,
    timeout: float = 15.0,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Fetch only the two exact public market/event payloads."""
    event_ticker = gemini_event_ticker(gemini_symbol)
    async with httpx.AsyncClient(timeout=timeout) as http:
        k_resp, g_resp = await asyncio.gather(
            http.get(f"{KALSHI_REST}/markets/{kalshi_ticker}"),
            http.get(f"{GEMINI_REST}/events/{event_ticker}"),
        )
        k_resp.raise_for_status()
        g_resp.raise_for_status()

    k_payload = k_resp.json()
    k_market = k_payload.get("market", k_payload) if isinstance(k_payload, Mapping) else {}
    if not isinstance(k_market, Mapping) or not (k_market.get("ticker") or k_market.get("market_ticker")):
        raise RuntimeError("Kalshi exact market payload not found")

    g_payload = g_resp.json()
    contract = find_gemini_contract(g_payload, gemini_symbol)
    if contract is None:
        raise RuntimeError("Gemini exact contract was not found uniquely in its exact event")
    return k_market, contract


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    k_market, g_contract = await fetch_exact_event_rules(
        kalshi_ticker=args.kalshi_ticker,
        gemini_symbol=args.gemini_symbol,
    )
    return build_pair_fingerprint(
        settlement_key=args.settlement_key,
        kalshi_market=k_market,
        gemini_contract=g_contract,
        kalshi_source_side=args.kalshi_source_side,
        gemini_source_side=args.gemini_source_side,
    ).as_dict()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settlement-key", required=True)
    parser.add_argument("--kalshi-ticker", required=True)
    parser.add_argument("--gemini-symbol", required=True)
    parser.add_argument("--kalshi-source-side", default="YES")
    parser.add_argument("--gemini-source-side", default="CONTRACT")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args)), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
