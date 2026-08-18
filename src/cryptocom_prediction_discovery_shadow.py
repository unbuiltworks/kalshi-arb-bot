"""Read-only Crypto.com prediction-market discovery for short-horizon crypto research.

The public Crypto.com Predictions API exposes event discovery and contract bid/ask
pricing without authentication. This worker intentionally uses only those GET endpoints.
It never creates orders and never accepts trading credentials.

Purpose:
* discover active BTC/ETH/SOL/XRP prediction contracts;
* record event/contract identity and current bid/ask/mid/spread;
* measure whether Crypto.com adds useful short-horizon market surface area before
  investing in any execution integration;
* stay comfortably below anonymous public-data rate limits.

This is research telemetry, not realized P&L and not trade authorization.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("kalshi_arb.cryptocom_prediction_discovery_shadow")

BASE_URL = "https://data-api.crypto.com"
ASSET_QUERIES: dict[str, tuple[str, ...]] = {
    "BTC": ("Bitcoin", "BTC"),
    "ETH": ("Ethereum", "ETH"),
    "SOL": ("Solana", "SOL"),
    "XRP": ("XRP", "Ripple"),
}
ASSET_ALIASES: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "ether", "eth"),
    "SOL": ("solana", "sol"),
    "XRP": ("xrp", "ripple"),
}


def _json_data(response: httpx.Response) -> Any:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Crypto.com response was not an object")
    return payload.get("data")


def _text_matches_asset(asset: str, *values: Any) -> bool:
    text = " ".join(str(value or "") for value in values).lower()
    return any(alias in text for alias in ASSET_ALIASES[asset])


def _rate_snapshot(headers: httpx.Headers) -> dict[str, int | None]:
    def parse(name: str) -> int | None:
        raw = headers.get(name)
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "minute_limit": parse("X-RateLimit-Limit"),
        "minute_remaining": parse("X-RateLimit-Remaining"),
        "day_limit": parse("X-RateLimit-Day-Limit"),
        "day_remaining": parse("X-RateLimit-Day-Remaining"),
    }


def _build_record(
    *,
    asset: str,
    event: dict[str, Any],
    contract: dict[str, Any],
    price: dict[str, Any],
    observed_at_ms: int,
) -> dict[str, Any]:
    updated_at = price.get("updated_at")
    return {
        "asset": asset,
        "event_id": event.get("id"),
        "event_title": event.get("title"),
        "event_date": event.get("event_date"),
        "event_kind": event.get("kind"),
        "event_type": event.get("type"),
        "market_format": event.get("market_format"),
        "contract_id": contract.get("id"),
        "symbol": contract.get("symbol"),
        "contract_title": contract.get("title"),
        "contract_status": contract.get("status"),
        "catalog_yes": contract.get("yes"),
        "catalog_no": contract.get("no"),
        "catalog_chance": contract.get("chance"),
        "bid": price.get("bid"),
        "ask": price.get("ask"),
        "mid": price.get("mid"),
        "spread": price.get("spread"),
        "probability": price.get("probability"),
        "price_status": price.get("status"),
        "price_updated_at": updated_at,
        "observed_at_ms": observed_at_ms,
        "venue": "CRYPTO_COM_PREDICTIONS_PUBLIC_DATA",
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }


async def _get_json(
    http: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, int | None]]:
    response = await http.get(path, params=params)
    return _json_data(response), _rate_snapshot(response.headers)


async def discover_cycle(
    http: httpx.AsyncClient,
    *,
    max_events_per_asset: int = 8,
    max_prices_per_cycle: int = 32,
    previous_prices: dict[str, tuple[Any, ...]] | None = None,
) -> dict[str, Any]:
    """Run one bounded, keyless discovery cycle and emit changed price records."""
    if previous_prices is None:
        previous_prices = {}

    counters: defaultdict[str, int] = defaultdict(int)
    errors: list[str] = []
    rate_samples: list[dict[str, int | None]] = []
    discovered: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []

    for asset, queries in ASSET_QUERIES.items():
        events_by_id: dict[str, dict[str, Any]] = {}
        for query in queries:
            try:
                data, rate = await _get_json(
                    http,
                    "/api/v1/predictions/events/search",
                    params={"q": query, "status": "active", "limit": max_events_per_asset},
                )
                rate_samples.append(rate)
                counters["search_calls"] += 1
                for row in data or []:
                    if not isinstance(row, dict):
                        continue
                    event_id = str(row.get("id") or "")
                    if event_id:
                        events_by_id[event_id] = row
            except Exception as exc:  # fail closed; discovery continues for other assets
                counters["errors"] += 1
                errors.append(f"search:{asset}:{query}:{type(exc).__name__}:{exc}")

        ranked = list(events_by_id.values())[:max_events_per_asset]
        counters[f"{asset.lower()}_search_events"] = len(ranked)

        for summary in ranked:
            event_id = str(summary.get("id") or "")
            if not event_id:
                continue
            try:
                detail, rate = await _get_json(http, f"/api/v1/predictions/events/{event_id}")
                rate_samples.append(rate)
                counters["event_calls"] += 1
            except Exception as exc:
                counters["errors"] += 1
                errors.append(f"event:{asset}:{event_id}:{type(exc).__name__}:{exc}")
                continue
            if not isinstance(detail, dict):
                continue
            if not _text_matches_asset(asset, detail.get("title"), summary.get("title")):
                continue

            contracts = detail.get("contracts") or []
            for contract in contracts:
                if counters["price_calls"] >= max_prices_per_cycle:
                    break
                if not isinstance(contract, dict):
                    continue
                if str(contract.get("status") or "").lower() not in {"active", "live", "open"}:
                    continue
                if not _text_matches_asset(
                    asset,
                    detail.get("title"),
                    contract.get("title"),
                    contract.get("symbol"),
                ):
                    continue
                symbol = str(contract.get("symbol") or "")
                if not symbol:
                    continue
                try:
                    price_data, rate = await _get_json(
                        http,
                        f"/api/v1/predictions/contracts/{symbol}/price",
                    )
                    rate_samples.append(rate)
                    counters["price_calls"] += 1
                except Exception as exc:
                    counters["errors"] += 1
                    errors.append(f"price:{asset}:{symbol}:{type(exc).__name__}:{exc}")
                    continue
                if not isinstance(price_data, dict):
                    continue

                record = _build_record(
                    asset=asset,
                    event=detail,
                    contract=contract,
                    price=price_data,
                    observed_at_ms=int(time.time() * 1000),
                )
                discovered.append(record)
                signature = (
                    record.get("bid"),
                    record.get("ask"),
                    record.get("mid"),
                    record.get("spread"),
                    record.get("price_status"),
                    record.get("price_updated_at"),
                )
                if previous_prices.get(symbol) != signature:
                    previous_prices[symbol] = signature
                    changed.append(record)

            if counters["price_calls"] >= max_prices_per_cycle:
                break
        if counters["price_calls"] >= max_prices_per_cycle:
            break

    minute_remaining = [
        sample["minute_remaining"] for sample in rate_samples
        if sample.get("minute_remaining") is not None
    ]
    day_remaining = [
        sample["day_remaining"] for sample in rate_samples
        if sample.get("day_remaining") is not None
    ]
    counters["contracts_discovered"] = len(discovered)
    counters["changed_price_records"] = len(changed)

    for record in changed:
        logger.info(
            "CRYPTOCOM_PREDICTION_PRICE %s",
            json.dumps(record, separators=(",", ":"), default=str),
        )

    summary = {
        "counters": dict(counters),
        "symbols": sorted({str(row.get("symbol")) for row in discovered if row.get("symbol")}),
        "assets_seen": sorted({str(row.get("asset")) for row in discovered if row.get("asset")}),
        "min_rate_remaining_this_minute": min(minute_remaining) if minute_remaining else None,
        "min_rate_remaining_today": min(day_remaining) if day_remaining else None,
        "errors": errors[:20],
        "base_url": BASE_URL,
        "public_data_only": True,
        "authentication_used": False,
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
    logger.info(
        "CRYPTOCOM_PREDICTION_DISCOVERY_SUMMARY %s",
        json.dumps(summary, separators=(",", ":"), default=str),
    )
    return summary


async def run(
    *,
    seconds: float,
    interval_s: float,
    max_events_per_asset: int,
    max_prices_per_cycle: int,
) -> None:
    deadline = None if seconds <= 0 else time.monotonic() + seconds
    previous_prices: dict[str, tuple[Any, ...]] = {}
    timeout = httpx.Timeout(15.0, connect=10.0)
    headers = {"Accept": "application/json", "User-Agent": "kalshi-arb-bot-read-only-research/1.0"}
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=timeout, headers=headers) as http:
        while deadline is None or time.monotonic() < deadline:
            started = time.monotonic()
            try:
                await discover_cycle(
                    http,
                    max_events_per_asset=max_events_per_asset,
                    max_prices_per_cycle=max_prices_per_cycle,
                    previous_prices=previous_prices,
                )
            except Exception:
                logger.exception("CRYPTOCOM_PREDICTION_DISCOVERY_CYCLE_FAILED read_only=true order_entry=false")
            sleep_s = max(0.0, interval_s - (time.monotonic() - started))
            if deadline is not None:
                sleep_s = min(sleep_s, max(0.0, deadline - time.monotonic()))
            if sleep_s:
                await asyncio.sleep(sleep_s)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--interval-s", type=float, default=60.0)
    parser.add_argument("--max-events-per-asset", type=int, default=8)
    parser.add_argument("--max-prices-per-cycle", type=int, default=32)
    args = parser.parse_args()
    if args.interval_s < 10:
        raise SystemExit("--interval-s must be >=10s for public-data rate-limit safety")
    if not 1 <= args.max_events_per_asset <= 20:
        raise SystemExit("--max-events-per-asset must be between 1 and 20")
    if not 1 <= args.max_prices_per_cycle <= 60:
        raise SystemExit("--max-prices-per-cycle must be between 1 and 60")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logger.info(
        "CRYPTOCOM_PREDICTION_DISCOVERY_ENABLED assets=BTC,ETH,SOL,XRP "
        "public_data_only=true authentication_used=false trade_authorized=false "
        "read_only=true order_entry=false"
    )
    asyncio.run(run(
        seconds=args.seconds,
        interval_s=args.interval_s,
        max_events_per_asset=args.max_events_per_asset,
        max_prices_per_cycle=args.max_prices_per_cycle,
    ))


if __name__ == "__main__":
    main()
