"""Read-only Crypto.com DCM prediction-instrument catalog scanner.

Scans non-combo DCM instruments with documented cursor pagination, identifies
BTC/ETH/SOL/XRP event contracts from structured instrument/event metadata, and probes
public order books for a small set of actual DCM symbols. No authentication or order
entry is used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger("kalshi_arb.cryptocom_dcm_crypto_catalog_shadow")

DCM_BASE = "https://api.crypto.com/dcm/v1"
ASSET_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "BTC": (
        re.compile(r"(?:^|[^A-Z0-9])BTC(?:[^A-Z0-9]|$)", re.I),
        re.compile(r"BITCOIN", re.I),
    ),
    "ETH": (
        re.compile(r"(?:^|[^A-Z0-9])ETH(?:[^A-Z0-9]|$)", re.I),
        re.compile(r"ETHEREUM", re.I),
        re.compile(r"ETHER(?:[^A-Z]|$)", re.I),
    ),
    "SOL": (
        re.compile(r"(?:^|[^A-Z0-9])SOL(?:[^A-Z0-9]|$)", re.I),
        re.compile(r"SOLANA", re.I),
    ),
    "XRP": (
        re.compile(r"(?:^|[^A-Z0-9])XRP(?:[^A-Z0-9]|$)", re.I),
        re.compile(r"RIPPLE", re.I),
    ),
}


def _payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text[:1200]


def _unwrap(response: httpx.Response) -> dict[str, Any]:
    payload = _payload(response)
    if response.status_code >= 400:
        raise ValueError(f"DCM HTTP {response.status_code} body={payload}")
    if not isinstance(payload, dict):
        raise ValueError(f"DCM response not object: {payload}")
    if int(payload.get("code", -1)) != 0:
        raise ValueError(
            f"DCM code={payload.get('code')} message={payload.get('message')} original={payload.get('original')}"
        )
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def _search_blob(row: dict[str, Any]) -> str:
    """Search only fields that can legitimately encode underlying/event identity."""
    fields = {
        "symbol": row.get("symbol"),
        "display_name": row.get("display_name"),
        "base_ccy": row.get("base_ccy"),
        "quote_ccy": row.get("quote_ccy"),
        "underlying_symbol": row.get("underlying_symbol"),
        "product_type": row.get("product_type"),
        "event_details": row.get("event_details"),
    }
    return json.dumps(fields, separators=(",", ":"), default=str)


def classify_assets(row: dict[str, Any]) -> list[str]:
    blob = _search_blob(row)
    return [
        asset for asset, patterns in ASSET_PATTERNS.items()
        if any(pattern.search(blob) for pattern in patterns)
    ]


def compact_instrument(row: dict[str, Any]) -> dict[str, Any]:
    event = row.get("event_details") if isinstance(row.get("event_details"), dict) else {}
    attrs = event.get("attributes") if isinstance(event.get("attributes"), dict) else {}
    metadata = event.get("metaData") if isinstance(event.get("metaData"), dict) else {}
    return {
        "symbol": row.get("symbol"),
        "inst_type": row.get("inst_type"),
        "display_name": row.get("display_name"),
        "base_ccy": row.get("base_ccy"),
        "quote_ccy": row.get("quote_ccy"),
        "underlying_symbol": row.get("underlying_symbol"),
        "product_type": row.get("product_type"),
        "tradable": row.get("tradable"),
        "expiry_timestamp_ms": row.get("expiry_timestamp_ms"),
        "price_tick_size": row.get("price_tick_size"),
        "qty_tick_size": row.get("qty_tick_size"),
        "event_code": event.get("eventCode"),
        "event_name": event.get("eventName"),
        "event_metadata": metadata,
        "open_time": attrs.get("OPEN_TIME"),
        "close_time": attrs.get("CLOSE_TIME"),
        "period_code": attrs.get("PERIOD_CODE"),
        "period_index": attrs.get("PERIOD_INDEX"),
        "strike_index": attrs.get("STRIKE_INDEX"),
        "strike_operator": attrs.get("STRIKE_OPERATOR"),
        "display_precision": attrs.get("DISPLAY_PRECISION"),
        "underlying_rounding_value": attrs.get("UNDERLYING_ROUNDING_VALUE"),
    }


def top_of_book(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") or []
    row = data[0] if data and isinstance(data[0], dict) else {}
    bids = row.get("bids") or []
    asks = row.get("asks") or []
    return {
        "best_bid": bids[0] if bids else None,
        "best_ask": asks[0] if asks else None,
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "book_instrument_name": row.get("instrument_name"),
        "book_depth": row.get("depth"),
    }


async def scan_catalog(
    http: httpx.AsyncClient,
    *,
    max_pages: int,
    page_limit: int,
    target_per_asset: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    found: dict[str, list[dict[str, Any]]] = {asset: [] for asset in ASSET_PATTERNS}
    seen_symbols: set[str] = set()
    cursor: str | None = None
    pages = 0
    rows_seen = 0
    event_rows_seen = 0
    errors: list[str] = []

    while pages < max_pages:
        params: dict[str, str] = {
            "limit": str(page_limit),
            "is_combo": "false",
        }
        if cursor:
            params["cursor"] = cursor
        else:
            params["since"] = "0"
        try:
            response = await http.get("/public/get-instruments", params=params)
            result = _unwrap(response)
        except Exception as exc:
            errors.append(f"page:{pages + 1}:{type(exc).__name__}:{exc}")
            break

        rows = result.get("data") or []
        pages += 1
        rows_seen += len(rows)
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("product_type") or "").upper() == "EVENTS" or isinstance(row.get("event_details"), dict):
                event_rows_seen += 1
            assets = classify_assets(row)
            if not assets:
                continue
            symbol = str(row.get("symbol") or "")
            if not symbol or symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            compact = compact_instrument(row)
            for asset in assets:
                if len(found[asset]) < target_per_asset:
                    found[asset].append(compact)
                    logger.info(
                        "CRYPTOCOM_DCM_CRYPTO_INSTRUMENT %s",
                        json.dumps({
                            "asset": asset,
                            **compact,
                            "authentication_used": False,
                            "trade_authorized": False,
                            "read_only": True,
                            "order_entry": False,
                        }, separators=(",", ":"), default=str),
                    )

        cursor = str(result.get("next_cursor") or "") or None
        if all(len(rows_) >= target_per_asset for rows_ in found.values()):
            break
        if not cursor:
            break

    meta = {
        "pages_scanned": pages,
        "rows_seen": rows_seen,
        "event_rows_seen": event_rows_seen,
        "next_cursor_present": bool(cursor),
        "matches": {asset: len(rows) for asset, rows in found.items()},
        "errors": errors,
    }
    return found, meta


async def probe_actual_books(
    http: httpx.AsyncClient,
    found: dict[str, list[dict[str, Any]]],
    *,
    depth: int,
    books_per_asset: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for asset, rows in found.items():
        candidates = sorted(
            rows,
            key=lambda row: (
                not bool(row.get("tradable")),
                row.get("expiry_timestamp_ms") is None,
                row.get("expiry_timestamp_ms") or 2**63 - 1,
            ),
        )[:books_per_asset]
        for instrument in candidates:
            symbol = str(instrument.get("symbol") or "")
            if not symbol:
                continue
            try:
                response = await http.get(
                    "/public/get-book",
                    params={"instrument_name": symbol, "depth": str(depth)},
                )
                result = _unwrap(response)
                record = {
                    "asset": asset,
                    **instrument,
                    **top_of_book(result),
                    "dcm_http_status": response.status_code,
                    "observed_at_ms": int(time.time() * 1000),
                    "venue": "CRYPTO_COM_DCM_PUBLIC_BOOK",
                    "authentication_used": False,
                    "trade_authorized": False,
                    "read_only": True,
                    "order_entry": False,
                }
                records.append(record)
                logger.info(
                    "CRYPTOCOM_DCM_ACTUAL_BOOK %s",
                    json.dumps(record, separators=(",", ":"), default=str),
                )
            except Exception as exc:
                errors.append(f"book:{asset}:{symbol}:{type(exc).__name__}:{exc}")
    return records, errors


async def cycle(
    http: httpx.AsyncClient,
    *,
    max_pages: int,
    page_limit: int,
    target_per_asset: int,
    books_per_asset: int,
    depth: int,
) -> dict[str, Any]:
    found, catalog_meta = await scan_catalog(
        http,
        max_pages=max_pages,
        page_limit=page_limit,
        target_per_asset=target_per_asset,
    )
    books, book_errors = await probe_actual_books(
        http,
        found,
        depth=depth,
        books_per_asset=books_per_asset,
    )
    two_sided = sum(
        1 for row in books
        if row.get("best_bid") is not None and row.get("best_ask") is not None
    )
    summary = {
        **catalog_meta,
        "symbols": {
            asset: [str(row.get("symbol")) for row in rows]
            for asset, rows in found.items()
        },
        "books_returned": len(books),
        "two_sided_books": two_sided,
        "book_errors": book_errors[:20],
        "dcm_base": DCM_BASE,
        "filter_is_combo": False,
        "authentication_used": False,
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }
    logger.info(
        "CRYPTOCOM_DCM_CRYPTO_CATALOG_SUMMARY %s",
        json.dumps(summary, separators=(",", ":"), default=str),
    )
    return summary


async def run(
    *,
    seconds: float,
    interval_s: float,
    max_pages: int,
    page_limit: int,
    target_per_asset: int,
    books_per_asset: int,
    depth: int,
) -> None:
    deadline = None if seconds <= 0 else time.monotonic() + seconds
    headers = {"Accept": "application/json", "User-Agent": "kalshi-arb-bot-read-only-research/1.0"}
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(base_url=DCM_BASE, headers=headers, timeout=timeout) as http:
        while deadline is None or time.monotonic() < deadline:
            started = time.monotonic()
            try:
                await cycle(
                    http,
                    max_pages=max_pages,
                    page_limit=page_limit,
                    target_per_asset=target_per_asset,
                    books_per_asset=books_per_asset,
                    depth=depth,
                )
            except Exception:
                logger.exception("CRYPTOCOM_DCM_CRYPTO_CATALOG_CYCLE_FAILED read_only=true order_entry=false")
            sleep_s = max(0.0, interval_s - (time.monotonic() - started))
            if deadline is not None:
                sleep_s = min(sleep_s, max(0.0, deadline - time.monotonic()))
            if sleep_s:
                await asyncio.sleep(sleep_s)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--interval-s", type=float, default=300.0)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--page-limit", type=int, default=1000)
    parser.add_argument("--target-per-asset", type=int, default=6)
    parser.add_argument("--books-per-asset", type=int, default=2)
    parser.add_argument("--depth", type=int, default=10)
    args = parser.parse_args()
    if args.interval_s < 60:
        raise SystemExit("--interval-s must be >=60")
    if not 1 <= args.max_pages <= 50:
        raise SystemExit("--max-pages must be 1..50")
    if not 1 <= args.page_limit <= 1000:
        raise SystemExit("--page-limit must be 1..1000")
    if not 1 <= args.target_per_asset <= 20:
        raise SystemExit("--target-per-asset must be 1..20")
    if not 1 <= args.books_per_asset <= 5:
        raise SystemExit("--books-per-asset must be 1..5")
    if not 1 <= args.depth <= 50:
        raise SystemExit("--depth must be 1..50")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logger.info(
        "CRYPTOCOM_DCM_CRYPTO_CATALOG_ENABLED assets=BTC,ETH,SOL,XRP is_combo=false "
        "authentication_used=false trade_authorized=false read_only=true order_entry=false"
    )
    asyncio.run(run(
        seconds=args.seconds,
        interval_s=args.interval_s,
        max_pages=args.max_pages,
        page_limit=args.page_limit,
        target_per_asset=args.target_per_asset,
        books_per_asset=args.books_per_asset,
        depth=args.depth,
    ))


if __name__ == "__main__":
    main()
