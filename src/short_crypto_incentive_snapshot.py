"""Public, read-only incentive discovery for Gemini and Kalshi short crypto.

No authenticated endpoints and no order-entry code live here. The output answers:
1) what maker-rebate/liquidity-reward rules are active on Gemini right now?
2) which Kalshi crypto 15-minute markets currently have public incentives?
3) what public fee configuration does Kalshi report for our target 15m series?
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from typing import Any, Mapping

import httpx

GEMINI = "https://api.gemini.com"
KALSHI = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_TARGET_SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M")
KALSHI_TARGET_15M_PREFIXES = tuple(f"{series}-" for series in KALSHI_TARGET_SERIES)


def _rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(r) for r in payload if isinstance(r, Mapping)]
    if isinstance(payload, Mapping):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(r) for r in value if isinstance(r, Mapping)]
    return []


def is_short_crypto_reward_row(row: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(row.get(k) or "")
        for k in (
            "event_ticker", "eventTicker", "contract_type", "contractType",
            "series_ticker", "seriesTicker", "market_ticker", "marketTicker",
            "title", "name", "description", "incentive_description",
        )
    ).upper()
    crypto = any(token in text for token in ("BTC", "BITCOIN", "ETH", "ETHEREUM", "CRYPTO"))
    short = any(token in text for token in ("5M", "05M", "5 MIN", "5-MIN", "15M", "15 MIN", "15-MIN"))
    return crypto and short


def is_kalshi_crypto15_incentive(row: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(row.get(k) or "")
        for k in ("series_ticker", "market_ticker", "incentive_description", "description")
    ).upper()
    crypto = any(token in text for token in ("BTC", "BITCOIN", "ETH", "ETHEREUM", "CRYPTO"))
    fifteen = any(token in text for token in ("15M", "15 MIN", "15-MIN"))
    return crypto and fifteen


def is_kalshi_target_15m_incentive(row: Mapping[str, Any]) -> bool:
    ticker = str(row.get("market_ticker") or row.get("marketTicker") or "").upper()
    series = str(row.get("series_ticker") or row.get("seriesTicker") or "").upper()
    if ticker.startswith(KALSHI_TARGET_15M_PREFIXES):
        return True
    return series in set(KALSHI_TARGET_SERIES)


def _kalshi_series_summary(status: int, payload: Any, series: str) -> dict[str, Any]:
    row = payload.get("series") if isinstance(payload, Mapping) else None
    if not isinstance(row, Mapping):
        return {"series_ticker": series, "http_status": status, "available": False}
    return {
        "series_ticker": series,
        "http_status": status,
        "available": status == 200,
        "title": row.get("title"),
        "category": row.get("category"),
        "frequency": row.get("frequency"),
        "fee_type": row.get("fee_type"),
        "fee_multiplier": row.get("fee_multiplier"),
        "fee_type_override": row.get("fee_type_override"),
        "volume_fp": row.get("volume_fp") or row.get("volume"),
        "settlement_sources": row.get("settlement_sources"),
        "contract_url": row.get("contract_url"),
        "contract_terms_url": row.get("contract_terms_url"),
    }


async def fetch_snapshot(timeout_s: float = 15.0) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers={"User-Agent": "arb-short-crypto-incentive-snapshot/1.2"}) as client:
        async def get(url: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
            response = await client.get(url, params=params)
            try:
                payload: Any = response.json()
            except Exception:
                payload = {"text": response.text[:500]}
            return response.status_code, payload

        common = await asyncio.gather(
            get(f"{GEMINI}/v1/prediction-markets/maker-rebate/rates", {"category": "Crypto"}),
            get(f"{GEMINI}/v1/prediction-markets/liquidity-rewards/config"),
            get(f"{GEMINI}/v1/prediction-markets/liquidity-rewards/events", {"category": "Crypto", "limit": 100, "offset": 0}),
            get(f"{KALSHI}/incentive_programs", {"status": "active", "type": "all", "limit": 10000}),
        )
        series_payloads = await asyncio.gather(*[
            get(f"{KALSHI}/series/{series}", {"include_volume": "true"})
            for series in KALSHI_TARGET_SERIES
        ])

    (gem_rates, gem_liq_config, gem_liq_events, kalshi_active) = common
    gem_rate_status, gem_rate_payload = gem_rates
    gem_cfg_status, gem_cfg_payload = gem_liq_config
    gem_event_status, gem_event_payload = gem_liq_events
    kalshi_status, kalshi_payload = kalshi_active

    gem_event_rows = _rows(gem_event_payload, "events", "data", "liquidity_reward_events")
    kalshi_rows = _rows(kalshi_payload, "incentive_programs", "data")
    crypto15_rows = [r for r in kalshi_rows if is_kalshi_crypto15_incentive(r)]
    target_rows = [r for r in kalshi_rows if is_kalshi_target_15m_incentive(r)]
    other_rows = [r for r in crypto15_rows if not is_kalshi_target_15m_incentive(r)]
    target_series = [
        _kalshi_series_summary(status, payload, series)
        for series, (status, payload) in zip(KALSHI_TARGET_SERIES, series_payloads)
    ]

    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "classification": "READ_ONLY_PUBLIC_INCENTIVE_SNAPSHOT",
        "order_entry": False,
        "gemini": {
            "maker_rebate_http_status": gem_rate_status,
            "maker_rebate_rates": gem_rate_payload,
            "liquidity_rewards_config_http_status": gem_cfg_status,
            "liquidity_rewards_config": gem_cfg_payload,
            "liquidity_rewards_events_http_status": gem_event_status,
            "short_crypto_reward_events": [r for r in gem_event_rows if is_short_crypto_reward_row(r)],
            "all_crypto_reward_event_count": len(gem_event_rows),
        },
        "kalshi": {
            "incentives_http_status": kalshi_status,
            "active_incentive_count": len(kalshi_rows),
            "target_15m_incentives": target_rows,
            "target_15m_incentive_count": len(target_rows),
            "other_crypto15_incentives": other_rows,
            "other_crypto15_incentive_count": len(other_rows),
            "target_series": target_series,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    snapshot = asyncio.run(fetch_snapshot())
    print(json.dumps(snapshot, indent=2 if args.pretty else None, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
