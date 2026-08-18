"""Read-only Odds-API.io MLB WebSocket shadow probe.

This service is intentionally isolated from order entry. It verifies the new
sportsbook feed in production, discovers the MLB league slug, ensures the
requested recreational books are selected when possible, then logs live
WebSocket update cadence/latency without placing any trade.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import logging
import os
import time
from typing import Any, Mapping
from urllib.parse import urlencode

import httpx
import websockets

logger = logging.getLogger("kalshi_arb.odds_api_io_mlb_ws_shadow")
REST_BASE = "https://api.odds-api.io/v3"
WS_BASE = "wss://api.odds-api.io/v3/ws"


def _names(value: Any) -> set[str]:
    """Best-effort extraction of bookmaker names across response variants."""
    out: set[str] = set()
    if isinstance(value, str):
        if value.strip():
            out.add(value.strip())
    elif isinstance(value, list):
        for item in value:
            out.update(_names(item))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in {"name", "bookmaker", "bookie"} and isinstance(item, str):
                if item.strip():
                    out.add(item.strip())
            elif str(key).lower() in {"selected", "bookmakers", "data", "items"}:
                out.update(_names(item))
    return out


def _mlb_league_slug(events: Any) -> str | None:
    if not isinstance(events, list):
        return None
    for row in events:
        if not isinstance(row, Mapping):
            continue
        league = row.get("league")
        if not isinstance(league, Mapping):
            continue
        name = str(league.get("name") or "").strip()
        slug = str(league.get("slug") or "").strip()
        if "mlb" in name.lower() or "mlb" in slug.lower() or "major league baseball" in name.lower():
            return slug or None
    return None


def _iso_age_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None
    return max(0, int(time.time() * 1000) - int(dt.timestamp() * 1000))


def _message_latency_ms(message: Mapping[str, Any]) -> int | None:
    raw = message.get("timestamp")
    try:
        stamp = float(raw)
    except (TypeError, ValueError):
        return None
    # Docs show Unix seconds. Tolerate millisecond timestamps too.
    if stamp > 10_000_000_000:
        stamp /= 1000.0
    return max(0, int((time.time() - stamp) * 1000))


def _market_summary(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    markets = message.get("markets")
    if not isinstance(markets, list):
        return out
    for market in markets[:20]:
        if not isinstance(market, Mapping):
            continue
        odds = market.get("odds")
        out.append({
            "name": str(market.get("name") or ""),
            "updated_age_ms": _iso_age_ms(market.get("updatedAt")),
            "odds_rows": len(odds) if isinstance(odds, list) else 0,
        })
    return out


async def _json_request(
    http: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    api_key: str,
    params: Mapping[str, str] | None = None,
) -> Any:
    merged = {"apiKey": api_key}
    if params:
        merged.update(params)
    response = await http.request(method, f"{REST_BASE}{path}", params=merged)
    response.raise_for_status()
    if not response.content:
        return None
    return response.json()


async def ensure_bookmakers(http: httpx.AsyncClient, api_key: str, requested: list[str]) -> set[str]:
    try:
        before_raw = await _json_request(http, "GET", "/bookmakers/selected", api_key=api_key)
        before = _names(before_raw)
        missing = [name for name in requested if name.lower() not in {x.lower() for x in before}]
        if missing:
            try:
                await _json_request(
                    http,
                    "PUT",
                    "/bookmakers/selected/select",
                    api_key=api_key,
                    params={"bookmakers": ",".join(missing)},
                )
            except Exception as exc:
                logger.warning(
                    "ODDS_API_IO_BOOKMAKER_SELECT_FAILED requested=%s error_type=%s",
                    missing,
                    type(exc).__name__,
                )
        after_raw = await _json_request(http, "GET", "/bookmakers/selected", api_key=api_key)
        after = _names(after_raw)
        logger.info(
            "ODDS_API_IO_SELECTED_BOOKMAKERS %s",
            json.dumps({"selected": sorted(after), "requested": requested}, separators=(",", ":")),
        )
        return after
    except Exception as exc:
        logger.warning("ODDS_API_IO_SELECTED_BOOKMAKERS_READ_FAILED error_type=%s", type(exc).__name__)
        return set()


async def discover_mlb_league(http: httpx.AsyncClient, api_key: str) -> str | None:
    try:
        events = await _json_request(
            http,
            "GET",
            "/events",
            api_key=api_key,
            params={"sport": "baseball", "status": "pending,live"},
        )
        slug = _mlb_league_slug(events)
        logger.info(
            "ODDS_API_IO_MLB_DISCOVERY %s",
            json.dumps({
                "event_count": len(events) if isinstance(events, list) else 0,
                "mlb_league_slug": slug,
            }, separators=(",", ":")),
        )
        return slug
    except Exception as exc:
        logger.warning("ODDS_API_IO_MLB_DISCOVERY_FAILED error_type=%s", type(exc).__name__)
        return None


def _ws_url(*, api_key: str, markets: str, league_slug: str | None, last_seq: int) -> str:
    params: dict[str, str] = {"apiKey": api_key, "markets": markets}
    if league_slug:
        params["leagues"] = league_slug
    else:
        params["sport"] = "baseball"
    if last_seq > 0:
        params["lastSeq"] = str(last_seq)
    return f"{WS_BASE}?{urlencode(params)}"


async def stream(api_key: str, markets: str, requested_books: list[str]) -> None:
    last_seq = 0
    total_messages = 0
    update_messages = 0
    by_book: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    latency_samples: list[int] = []
    started = time.monotonic()

    async with httpx.AsyncClient(timeout=20.0) as http:
        selected = await ensure_bookmakers(http, api_key, requested_books)
        league_slug = await discover_mlb_league(http, api_key)

    logger.info(
        "ODDS_API_IO_WS_SHADOW_START %s",
        json.dumps({
            "league_slug": league_slug,
            "markets": markets.split(","),
            "requested_books": requested_books,
            "selected_books": sorted(selected),
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")),
    )

    backoff = 1.0
    while True:
        url = _ws_url(api_key=api_key, markets=markets, league_slug=league_slug, last_seq=last_seq)
        try:
            async with websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=4 * 1024 * 1024,
            ) as ws:
                backoff = 1.0
                async for raw in ws:
                    received_ms = int(time.time() * 1000)
                    try:
                        message = json.loads(raw)
                    except Exception:
                        logger.warning("ODDS_API_IO_WS_NON_JSON bytes=%s", len(raw))
                        continue
                    if not isinstance(message, Mapping):
                        continue
                    total_messages += 1
                    kind = str(message.get("type") or "unknown")
                    by_type[kind] += 1
                    seq = message.get("seq")
                    try:
                        seq_i = int(seq)
                    except (TypeError, ValueError):
                        seq_i = 0
                    if seq_i > last_seq:
                        last_seq = seq_i

                    if kind == "welcome":
                        logger.info(
                            "ODDS_API_IO_WS_WELCOME %s",
                            json.dumps({
                                "bookmakers": message.get("bookmakers"),
                                "league_filter": message.get("leagues_filter") or message.get("league_filter"),
                                "sport_filter": message.get("sport_filter"),
                                "market_filter": message.get("market_filter"),
                                "status_filter": message.get("status_filter"),
                                "read_only": True,
                            }, separators=(",", ":"), default=str),
                        )
                        continue

                    if kind == "resync_required":
                        logger.warning(
                            "ODDS_API_IO_WS_RESYNC_REQUIRED %s",
                            json.dumps({"reason": message.get("reason"), "last_seq": last_seq}, separators=(",", ":")),
                        )
                        # Reconnect without an old cursor. This probe does not trade, so a
                        # clean live restart is safer than pretending a stale cache is complete.
                        last_seq = 0
                        break

                    if kind in {"created", "updated", "deleted", "no_markets"}:
                        if kind == "updated":
                            update_messages += 1
                        book = str(message.get("bookie") or message.get("bookmaker") or "unknown")
                        by_book[book] += 1
                        latency = _message_latency_ms(message)
                        if latency is not None and latency < 120_000:
                            latency_samples.append(latency)
                            if len(latency_samples) > 1000:
                                latency_samples = latency_samples[-1000:]
                        logger.info(
                            "ODDS_API_IO_WS_EVENT %s",
                            json.dumps({
                                "type": kind,
                                "seq": seq_i or None,
                                "event_id": str(message.get("id") or ""),
                                "book": book,
                                "message_latency_ms": latency,
                                "received_ms": received_ms,
                                "markets": _market_summary(message),
                            }, separators=(",", ":"), default=str),
                        )

                    if total_messages % 25 == 0:
                        sorted_lat = sorted(latency_samples)
                        p50 = sorted_lat[len(sorted_lat) // 2] if sorted_lat else None
                        p95 = sorted_lat[min(len(sorted_lat) - 1, int(len(sorted_lat) * 0.95))] if sorted_lat else None
                        logger.info(
                            "ODDS_API_IO_WS_HEALTH %s",
                            json.dumps({
                                "uptime_s": round(time.monotonic() - started, 1),
                                "messages": total_messages,
                                "updates": update_messages,
                                "last_seq": last_seq,
                                "types": dict(by_type),
                                "books": dict(by_book),
                                "message_latency_p50_ms": p50,
                                "message_latency_p95_ms": p95,
                                "read_only": True,
                                "order_entry": False,
                            }, separators=(",", ":")),
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Never log the URL because it contains the API key.
            logger.warning(
                "ODDS_API_IO_WS_DISCONNECTED error_type=%s retry_s=%.1f last_seq=%s",
                type(exc).__name__,
                backoff,
                last_seq,
            )
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2.0)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", default="ML,Spread,Totals")
    parser.add_argument("--bookmakers", default="DraftKings,FanDuel")
    args = parser.parse_args()

    api_key = os.getenv("ODDS_API_IO_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ODDS_API_IO_API_KEY is required")
    requested = [x.strip() for x in args.bookmakers.split(",") if x.strip()]
    asyncio.run(stream(api_key, args.markets, requested))


if __name__ == "__main__":
    main()
