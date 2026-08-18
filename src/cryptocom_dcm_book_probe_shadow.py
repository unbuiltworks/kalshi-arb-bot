"""Read-only Crypto.com DCM order-book probe for prediction contracts.

This worker cross-checks contract symbols discovered through Crypto.com's public
Predictions catalog against the production DCM public instrument/book endpoints. It
exists because catalog/price fields are not sufficient evidence of executable bid/ask
semantics. No authentication, account endpoint, or order endpoint is used.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, InvalidOperation
import json
import logging
import time
from typing import Any

import httpx

from src import cryptocom_prediction_discovery_shadow as catalog

logger = logging.getLogger("kalshi_arb.cryptocom_dcm_book_probe_shadow")

CATALOG_BASE = "https://data-api.crypto.com"
DCM_BASE = "https://api.crypto.com/dcm/v1"
D = Decimal


def _decimal(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def choose_contract(contracts: list[dict[str, Any]], asset: str) -> dict[str, Any] | None:
    """Choose the active matching contract closest to 50% catalog chance."""
    matching = [
        row for row in contracts
        if isinstance(row, dict)
        and str(row.get("status") or "").lower() in {"active", "live", "open"}
        and catalog._symbol_matches_asset(asset, row.get("symbol"))
    ]
    if not matching:
        return None

    def rank(row: dict[str, Any]) -> tuple[int, Decimal]:
        chance = _decimal(row.get("chance"))
        if chance is None:
            return (1, D("999"))
        return (0, abs(chance - D("50")))

    return min(matching, key=rank)


def top_of_book(result: dict[str, Any] | None) -> dict[str, Any]:
    data = (result or {}).get("data") or []
    row = data[0] if data and isinstance(data[0], dict) else {}
    bids = row.get("bids") or []
    asks = row.get("asks") or []
    bid = bids[0] if bids else None
    ask = asks[0] if asks else None
    bid_px = _decimal(bid[0]) if isinstance(bid, (list, tuple)) and bid else None
    ask_px = _decimal(ask[0]) if isinstance(ask, (list, tuple)) and ask else None
    return {
        "best_bid": bid,
        "best_ask": ask,
        "best_bid_price": str(bid_px) if bid_px is not None else None,
        "best_ask_price": str(ask_px) if ask_px is not None else None,
        "crossed": bool(bid_px is not None and ask_px is not None and bid_px >= ask_px),
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "raw_depth": row.get("depth"),
        "raw_instrument_name": row.get("instrument_name"),
    }


def _response_payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text[:1000]


def _unwrap_dcm(response: httpx.Response) -> dict[str, Any]:
    payload = _response_payload(response)
    if response.status_code >= 400:
        raise ValueError(f"DCM HTTP {response.status_code} body={payload}")
    if not isinstance(payload, dict):
        raise ValueError(f"DCM response was not an object: {payload}")
    if int(payload.get("code", -1)) != 0:
        raise ValueError(
            f"DCM code={payload.get('code')} message={payload.get('message')} original={payload.get('original')}"
        )
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


async def _catalog_get(http: httpx.AsyncClient, path: str, **params: Any) -> Any:
    response = await http.get(path, params=params or None)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("catalog response was not an object")
    return payload.get("data")


async def discover_representatives(
    http: httpx.AsyncClient,
    *,
    events_per_asset: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for asset, queries in catalog.ASSET_QUERIES.items():
        seen_events: set[str] = set()
        for query in queries:
            if len([row for row in selected if row["asset"] == asset]) >= events_per_asset:
                break
            search_rows = await _catalog_get(
                http,
                "/api/v1/predictions/events/search",
                q=query,
                status="active",
                limit=8,
            )
            for summary in search_rows or []:
                if len([row for row in selected if row["asset"] == asset]) >= events_per_asset:
                    break
                if not isinstance(summary, dict):
                    continue
                event_id = str(summary.get("id") or "")
                if not event_id or event_id in seen_events:
                    continue
                seen_events.add(event_id)
                detail = await _catalog_get(http, f"/api/v1/predictions/events/{event_id}")
                if not isinstance(detail, dict):
                    continue
                contract = choose_contract(detail.get("contracts") or [], asset)
                if contract is None:
                    continue
                selected.append({
                    "asset": asset,
                    "event_id": event_id,
                    "event_title": detail.get("title"),
                    "event_date": detail.get("event_date"),
                    "event_kind": detail.get("kind"),
                    "contract_title": contract.get("title"),
                    "symbol": contract.get("symbol"),
                    "catalog_chance": contract.get("chance"),
                    "catalog_yes": contract.get("yes"),
                    "catalog_no": contract.get("no"),
                })
    return selected


async def fetch_dcm_instrument_index(dcm_http: httpx.AsyncClient) -> tuple[set[str], dict[str, Any]]:
    response = await dcm_http.get("/public/get-instruments", params={"limit": "1000"})
    result = _unwrap_dcm(response)
    rows = result.get("data") or []
    symbols = {
        str(row.get("symbol"))
        for row in rows
        if isinstance(row, dict) and row.get("symbol")
    }
    prediction_like = [
        row for row in rows
        if isinstance(row, dict)
        and (
            str(row.get("product_type") or "").upper() == "EVENTS"
            or isinstance(row.get("event_details"), dict)
            or str(row.get("symbol") or "").endswith(".NXO")
        )
    ]
    meta = {
        "http_status": response.status_code,
        "instruments_returned": len(rows),
        "prediction_like_instruments": len(prediction_like),
        "sample_prediction_symbols": [str(row.get("symbol")) for row in prediction_like[:12]],
        "next_cursor_present": bool(result.get("next_cursor")),
    }
    return symbols, meta


async def probe_cycle(
    catalog_http: httpx.AsyncClient,
    dcm_http: httpx.AsyncClient,
    *,
    events_per_asset: int,
    depth: int,
) -> dict[str, Any]:
    errors: list[str] = []
    representatives: list[dict[str, Any]] = []
    try:
        representatives = await discover_representatives(
            catalog_http,
            events_per_asset=events_per_asset,
        )
    except Exception as exc:
        errors.append(f"catalog:{type(exc).__name__}:{exc}")

    dcm_symbols: set[str] = set()
    dcm_catalog_meta: dict[str, Any] = {}
    try:
        dcm_symbols, dcm_catalog_meta = await fetch_dcm_instrument_index(dcm_http)
        selected_symbols = [str(row.get("symbol") or "") for row in representatives]
        dcm_catalog_meta["selected_present_count"] = sum(symbol in dcm_symbols for symbol in selected_symbols)
        dcm_catalog_meta["selected_missing"] = [symbol for symbol in selected_symbols if symbol not in dcm_symbols]
        logger.info(
            "CRYPTOCOM_DCM_INSTRUMENT_SUMMARY %s",
            json.dumps({
                **dcm_catalog_meta,
                "authentication_used": False,
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str),
        )
    except Exception as exc:
        errors.append(f"instruments:{type(exc).__name__}:{exc}")

    books = 0
    healthy_two_sided = 0
    for row in representatives:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        try:
            response = await dcm_http.get(
                "/public/get-book",
                params={"instrument_name": symbol, "depth": str(depth)},
            )
            result = _unwrap_dcm(response)
            top = top_of_book(result)
            books += 1
            if top["best_bid"] is not None and top["best_ask"] is not None and not top["crossed"]:
                healthy_two_sided += 1
            payload = {
                **row,
                **top,
                "present_in_dcm_instrument_page": symbol in dcm_symbols if dcm_symbols else None,
                "dcm_http_status": response.status_code,
                "observed_at_ms": int(time.time() * 1000),
                "venue": "CRYPTO_COM_DCM_PUBLIC_BOOK",
                "authentication_used": False,
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }
            logger.info("CRYPTOCOM_DCM_BOOK %s", json.dumps(payload, separators=(",", ":"), default=str))
        except Exception as exc:
            errors.append(f"book:{row.get('asset')}:{symbol}:{type(exc).__name__}:{exc}")

    summary = {
        "representatives": len(representatives),
        "books_returned": books,
        "healthy_two_sided_books": healthy_two_sided,
        "assets_selected": sorted({str(row.get("asset")) for row in representatives}),
        "symbols_selected": [str(row.get("symbol")) for row in representatives],
        "dcm_catalog": dcm_catalog_meta,
        "errors": errors[:20],
        "catalog_base": CATALOG_BASE,
        "dcm_base": DCM_BASE,
        "authentication_used": False,
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
    logger.info("CRYPTOCOM_DCM_BOOK_SUMMARY %s", json.dumps(summary, separators=(",", ":"), default=str))
    return summary


async def run(*, seconds: float, interval_s: float, events_per_asset: int, depth: int) -> None:
    deadline = None if seconds <= 0 else time.monotonic() + seconds
    headers = {"Accept": "application/json", "User-Agent": "kalshi-arb-bot-read-only-research/1.0"}
    timeout = httpx.Timeout(15.0, connect=10.0)
    async with (
        httpx.AsyncClient(base_url=CATALOG_BASE, headers=headers, timeout=timeout) as catalog_http,
        httpx.AsyncClient(base_url=DCM_BASE, headers=headers, timeout=timeout) as dcm_http,
    ):
        while deadline is None or time.monotonic() < deadline:
            started = time.monotonic()
            try:
                await probe_cycle(
                    catalog_http,
                    dcm_http,
                    events_per_asset=events_per_asset,
                    depth=depth,
                )
            except Exception:
                logger.exception("CRYPTOCOM_DCM_BOOK_CYCLE_FAILED read_only=true order_entry=false")
            sleep_s = max(0.0, interval_s - (time.monotonic() - started))
            if deadline is not None:
                sleep_s = min(sleep_s, max(0.0, deadline - time.monotonic()))
            if sleep_s:
                await asyncio.sleep(sleep_s)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--interval-s", type=float, default=30.0)
    parser.add_argument("--events-per-asset", type=int, default=2)
    parser.add_argument("--depth", type=int, default=10)
    args = parser.parse_args()
    if args.interval_s < 10:
        raise SystemExit("--interval-s must be >=10")
    if not 1 <= args.events_per_asset <= 4:
        raise SystemExit("--events-per-asset must be 1..4")
    if not 1 <= args.depth <= 50:
        raise SystemExit("--depth must be 1..50")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logger.info(
        "CRYPTOCOM_DCM_BOOK_PROBE_ENABLED assets=BTC,ETH,SOL,XRP "
        "authentication_used=false trade_authorized=false read_only=true order_entry=false"
    )
    asyncio.run(run(
        seconds=args.seconds,
        interval_s=args.interval_s,
        events_per_asset=args.events_per_asset,
        depth=args.depth,
    ))


if __name__ == "__main__":
    main()
