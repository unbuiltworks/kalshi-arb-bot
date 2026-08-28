"""Attach official public settlement outcomes to l5 shadow replay artifacts.

This module never reads user positions or credentials. Kalshi resolutions come from
GET /markets/{ticker} ``result``. Gemini resolutions come from the public recently
settled event feed and each UP contract's ``resolutionSide``.

Output remains SHADOW_SETTLED, never realized account P&L. For Kalshi we preserve
both maker-fee scenarios because current maker-fee applicability must be certified
separately. Gemini uses the replay's modeled maker fees and modeled rebate ledger,
with rebate-inclusive economics explicitly marked modeled/not-realized.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal as D
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import httpx

KALSHI = "https://external-api.kalshi.com/trade-api/v2"
GEMINI = "https://api.gemini.com"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("replay artifact must be a JSON object")
    return payload


def _normalized_side(value: Any) -> str | None:
    side = str(value or "").strip().upper()
    return side if side in {"YES", "NO"} else None


def score_kalshi_detail(detail: Mapping[str, Any], resolution: str) -> dict[str, Any]:
    side = _normalized_side(resolution)
    if side is None:
        raise ValueError("invalid Kalshi resolution")
    no_fee_key = "payoff_if_yes_no_maker_fee" if side == "YES" else "payoff_if_no_no_maker_fee"
    fee_key = "payoff_if_yes_with_general_maker_fee" if side == "YES" else "payoff_if_no_with_general_maker_fee"
    return {
        **dict(detail),
        "resolution_side": side,
        "settled_shadow_pnl_no_maker_fee": str(D(str(detail[no_fee_key]))),
        "settled_shadow_pnl_with_general_maker_fee_if_applicable": str(D(str(detail[fee_key]))),
        "classification": "SHADOW_SETTLED_NOT_REALIZED",
    }


def score_gemini_detail(detail: Mapping[str, Any], resolution: str) -> dict[str, Any]:
    side = _normalized_side(resolution)
    if side is None:
        raise ValueError("invalid Gemini resolution")
    trading_key = "trading_payoff_if_yes" if side == "YES" else "trading_payoff_if_no"
    modeled_key = "modeled_payoff_if_yes_with_rebate" if side == "YES" else "modeled_payoff_if_no_with_rebate"
    return {
        **dict(detail),
        "resolution_side": side,
        "settled_shadow_trading_pnl": str(D(str(detail[trading_key]))),
        "settled_shadow_modeled_pnl_with_maker_rebate": str(D(str(detail[modeled_key]))),
        "maker_rebate_status": "MODELED_NOT_REALIZED",
        "liquidity_reward_status": "NOT_MODELED_OR_REALIZED",
        "classification": "SHADOW_SETTLED_NOT_REALIZED",
    }


async def _kalshi_resolutions(http: httpx.AsyncClient, tickers: Iterable[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ticker in sorted(set(tickers)):
        response = await http.get(f"{KALSHI}/markets/{ticker}")
        response.raise_for_status()
        market = response.json().get("market") or {}
        side = _normalized_side(market.get("result"))
        out[ticker] = {
            "resolution_side": side,
            "status": market.get("status"),
            "settlement_value_dollars": market.get("settlement_value_dollars"),
            "settlement_ts": market.get("settlement_ts"),
            "expiration_value": market.get("expiration_value"),
        }
    return out


async def _gemini_recent_resolutions(http: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    offset = 0
    while offset < 5000:
        response = await http.get(
            f"{GEMINI}/v1/prediction-markets/events/recently-settled",
            params={"category": "Crypto", "limit": 500, "offset": offset},
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") or []
        for event in rows:
            if not isinstance(event, Mapping):
                continue
            ticker = str(event.get("ticker") or "")
            if not ticker:
                continue
            up_contract = None
            for contract in event.get("contracts") or []:
                if not isinstance(contract, Mapping):
                    continue
                symbol = str(contract.get("instrumentSymbol") or "").upper()
                if symbol.endswith("-UP"):
                    up_contract = contract
                    break
            if up_contract is None:
                continue
            side = _normalized_side(up_contract.get("resolutionSide"))
            out[ticker] = {
                "resolution_side": side,
                "status": event.get("status"),
                "resolved_at": up_contract.get("resolvedAt") or event.get("resolvedAt"),
                "instrument_symbol": up_contract.get("instrumentSymbol"),
            }
        pagination = payload.get("pagination") or {}
        total = int(pagination.get("total") or len(rows))
        offset += len(rows)
        if not rows or offset >= total:
            break
    return out


async def score(venue: str, replay: dict[str, Any]) -> dict[str, Any]:
    details = [row for row in replay.get("markets_detail", []) if isinstance(row, Mapping)]
    tickers = [str(row.get("ticker") or "") for row in details if str(row.get("ticker") or "")]
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers={"User-Agent": "arb-l5-shadow-settlement/1.0"}) as http:
        if venue == "kalshi":
            resolutions = await _kalshi_resolutions(http, tickers)
        elif venue == "gemini":
            resolutions = await _gemini_recent_resolutions(http)
        else:
            raise ValueError("venue must be kalshi or gemini")

    scored: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for detail in details:
        ticker = str(detail.get("ticker") or "")
        resolution = resolutions.get(ticker) or {}
        side = _normalized_side(resolution.get("resolution_side"))
        row = {**dict(detail), "public_settlement": resolution or None}
        if side is None:
            row["classification"] = "SHADOW_UNRESOLVED_NOT_REALIZED"
            unresolved.append(row)
            continue
        row = score_kalshi_detail(row, side) if venue == "kalshi" else score_gemini_detail(row, side)
        scored.append(row)

    output: dict[str, Any] = {
        "type": "L5_SHADOW_PUBLIC_SETTLEMENT_SCORE",
        "venue": venue,
        "markets_in_replay": len(details),
        "settled_markets": len(scored),
        "unresolved_markets": len(unresolved),
        "classification": "SHADOW_SETTLED_NOT_REALIZED",
        "settled_detail": scored,
        "unresolved_detail": unresolved,
    }
    if venue == "kalshi":
        output["settled_shadow_total_no_maker_fee"] = str(sum((D(row["settled_shadow_pnl_no_maker_fee"]) for row in scored), D("0")))
        output["settled_shadow_total_with_general_maker_fee_if_applicable"] = str(sum((D(row["settled_shadow_pnl_with_general_maker_fee_if_applicable"]) for row in scored), D("0")))
        output["maker_fee_applicability_status"] = "SCENARIO_ONLY_REQUIRES_CURRENT_FEE_SCHEDULE_CERTIFICATION"
    else:
        output["settled_shadow_total_trading_pnl"] = str(sum((D(row["settled_shadow_trading_pnl"]) for row in scored), D("0")))
        output["settled_shadow_total_modeled_pnl_with_maker_rebate"] = str(sum((D(row["settled_shadow_modeled_pnl_with_maker_rebate"]) for row in scored), D("0")))
        output["maker_rebate_status"] = "MODELED_NOT_REALIZED"
        output["liquidity_reward_status"] = "NOT_MODELED_OR_REALIZED"
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--venue", choices=("kalshi", "gemini"), required=True)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(score(args.venue, _load(args.input)))
    text = json.dumps(result, indent=2, sort_keys=True, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
