"""Robust read-only K/G MLB game-total rule fingerprint probe.

Searches Gemini across all documented event statuses plus the recently-settled
feed so a sub-second arb can still be audited after its event closes. No auth,
order entry, approval mutation, or live arming is used.
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


def _event_candidates_from_symbol(instrument_symbol: str) -> list[str]:
    symbol = str(instrument_symbol or "").strip().upper()
    if not symbol or "-" not in symbol:
        return []
    event = symbol.rsplit("-", 1)[0]
    out = [event]
    if event.startswith("GEMI-"):
        out.append(event.removeprefix("GEMI-"))
    return list(dict.fromkeys(out))


async def _get_json(http: httpx.AsyncClient, url: str, *, params: Any = None) -> tuple[int, Any]:
    resp = await http.get(url, params=params)
    try:
        body = resp.json()
    except Exception:
        body = None
    return resp.status_code, body


async def fetch_rule_pair(
    *,
    kalshi_ticker: str,
    gemini_symbol: str,
    timeout: float = 20.0,
) -> tuple[Mapping[str, Any], Mapping[str, Any], dict[str, Any]]:
    """Fetch the exact Kalshi market and locate the exact Gemini contract fail-closed."""
    async with httpx.AsyncClient(timeout=timeout) as http:
        k_task = _get_json(http, f"{KALSHI_REST}/markets/{kalshi_ticker}")
        all_task = _get_json(
            http,
            f"{GEMINI_REST}/events",
            params={"sport": "baseball", "limit": 500, "offset": 0},
        )
        settled_task = _get_json(
            http,
            f"{GEMINI_REST}/events/recently-settled",
            params={"sport": "baseball", "limit": 500, "offset": 0},
        )
        (k_status, k_payload), (all_status, all_payload), (settled_status, settled_payload) = await asyncio.gather(
            k_task, all_task, settled_task
        )

        if k_status != 200:
            raise RuntimeError(f"Kalshi exact market fetch failed: HTTP {k_status}")
        k_market = k_payload.get("market", k_payload) if isinstance(k_payload, Mapping) else {}
        if not isinstance(k_market, Mapping) or not (k_market.get("ticker") or k_market.get("market_ticker")):
            raise RuntimeError("Kalshi exact market payload not found")

        search_payloads: list[Any] = []
        if all_status == 200:
            search_payloads.append(all_payload)
        if settled_status == 200:
            search_payloads.append(settled_payload)
        contract = None
        found_via = ""
        for label, payload in (("events_all_status", all_payload), ("recently_settled", settled_payload)):
            if payload is None:
                continue
            contract = find_gemini_contract(payload, gemini_symbol)
            if contract is not None:
                found_via = label
                break

        exact_attempts: list[dict[str, Any]] = []
        if contract is None:
            for event_ticker in _event_candidates_from_symbol(gemini_symbol):
                status, payload = await _get_json(http, f"{GEMINI_REST}/events/{event_ticker}")
                exact_attempts.append({"event_ticker": event_ticker, "http_status": status})
                if status != 200:
                    continue
                contract = find_gemini_contract(payload, gemini_symbol)
                if contract is not None:
                    found_via = f"exact_event:{event_ticker}"
                    break

    if contract is None:
        raise RuntimeError(
            "Gemini exact contract not found; diagnostics="
            + json.dumps(
                {
                    "events_http_status": all_status,
                    "recently_settled_http_status": settled_status,
                    "exact_attempts": exact_attempts,
                },
                sort_keys=True,
            )
        )
    diagnostics = {
        "gemini_found_via": found_via,
        "events_http_status": all_status,
        "recently_settled_http_status": settled_status,
        "exact_attempts": exact_attempts,
    }
    return k_market, contract, diagnostics


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    k_market, g_contract, diagnostics = await fetch_rule_pair(
        kalshi_ticker=args.kalshi_ticker,
        gemini_symbol=args.gemini_symbol,
    )
    result = build_pair_fingerprint(
        settlement_key=args.settlement_key,
        kalshi_market=k_market,
        gemini_contract=g_contract,
        kalshi_source_side=args.kalshi_source_side,
        gemini_source_side=args.gemini_source_side,
    ).as_dict()
    result["probe_diagnostics"] = diagnostics
    return result


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
