"""Read-only live Polymarket temporal-pair shadow for BTC/ETH/SOL/XRP.

Uses only public Gamma/CLOB GET endpoints. No credentials, signing, order entry,
order cancellation, or mutation endpoints exist in this module.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import time
from typing import Any, Iterable

import httpx

from src.temporal_binary_shadow import FIRST_TIER_ASSETS, StreamKey, TemporalMarketShadow

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
POLY_CRYPTO_TAKER_FEE_RATE = 0.07

ASSET_PATTERNS = {
    "BTC": re.compile(r"\b(bitcoin|btc)\b", re.I),
    "ETH": re.compile(r"\b(ethereum|eth)\b", re.I),
    "SOL": re.compile(r"\b(solana|sol)\b", re.I),
    "XRP": re.compile(r"\b(xrp|ripple)\b", re.I),
}
ASSET_SEARCH_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "XRP": "XRP",
}
ASSET_SLUGS = {
    "BTC": "btc",
    "ETH": "eth",
    "SOL": "sol",
    "XRP": "xrp",
}
WINDOW_RE = re.compile(
    r"(?P<h1>\d{1,2}):(?P<m1>\d{2})\s*(?P<a1>AM|PM)?\s*-\s*"
    r"(?P<h2>\d{1,2}):(?P<m2>\d{2})\s*(?P<a2>AM|PM)\s*ET",
    re.I,
)
HORIZON_TEXT_PATTERNS = {
    5: re.compile(r"(?:\b5\s*(?:m|min|mins|minute|minutes)\b|[-_]5m[-_]|\b5m\b)", re.I),
    15: re.compile(r"(?:\b15\s*(?:m|min|mins|minute|minutes)\b|[-_]15m[-_]|\b15m\b)", re.I),
}


@dataclass(frozen=True)
class PolyBinaryMarket:
    condition_id: str
    asset: str
    horizon_minutes: int
    question: str
    slug: str
    up_token_id: str
    down_token_id: str
    end_ts_ms: int | None

    @property
    def stream(self) -> StreamKey:
        return StreamKey("polymarket", self.asset, self.horizon_minutes)


def _jsonish_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []


def _asset(text: str) -> str | None:
    for symbol, pattern in ASSET_PATTERNS.items():
        if pattern.search(text):
            return symbol
    return None


def _clock_minutes(hour: int, minute: int, ampm: str) -> int:
    hour %= 12
    if ampm.upper() == "PM":
        hour += 12
    return hour * 60 + minute


def infer_horizon_minutes(text: str) -> int | None:
    cleaned = (text or "").replace("–", "-")
    match = WINDOW_RE.search(cleaned)
    if match:
        end_ampm = match.group("a2").upper()
        start_ampm = (match.group("a1") or end_ampm).upper()
        start = _clock_minutes(int(match.group("h1")), int(match.group("m1")), start_ampm)
        end = _clock_minutes(int(match.group("h2")), int(match.group("m2")), end_ampm)
        duration = (end - start) % (24 * 60)
        if duration:
            return duration
    for minutes, pattern in HORIZON_TEXT_PATTERNS.items():
        if pattern.search(cleaned):
            return minutes
    return None


def _iso_ms(value: Any) -> int | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _contextual_market_row(row: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(row)
    enriched["_event_title"] = str(event.get("title") or "")
    enriched["_event_slug"] = str(event.get("slug") or "")
    enriched["_event_end_date"] = event.get("endDate")
    return enriched


def parse_market(row: dict[str, Any]) -> PolyBinaryMarket | None:
    question = str(row.get("question") or row.get("title") or "")
    slug = str(row.get("slug") or "")
    event_title = str(row.get("_event_title") or "")
    event_slug = str(row.get("_event_slug") or "")
    joined = " ".join(x for x in (question, slug, event_title, event_slug) if x)
    if "up or down" not in joined.lower() and "updown" not in joined.lower():
        return None

    asset = _asset(joined)
    horizon = infer_horizon_minutes(joined)
    if asset not in FIRST_TIER_ASSETS or horizon not in {5, 15}:
        return None

    outcomes = [str(x).strip().upper() for x in _jsonish_list(row.get("outcomes"))]
    token_ids = [str(x).strip() for x in _jsonish_list(row.get("clobTokenIds"))]
    if len(outcomes) != len(token_ids) or len(outcomes) < 2:
        return None
    mapping = {outcome: token for outcome, token in zip(outcomes, token_ids) if token}
    if "UP" not in mapping or "DOWN" not in mapping:
        return None

    condition_id = str(row.get("conditionId") or "").strip()
    if not condition_id:
        return None

    return PolyBinaryMarket(
        condition_id=condition_id,
        asset=asset,
        horizon_minutes=horizon,
        question=question or event_title,
        slug=slug or event_slug,
        up_token_id=mapping["UP"],
        down_token_id=mapping["DOWN"],
        end_ts_ms=_iso_ms(
            row.get("endDate") or row.get("endDateIso") or row.get("_event_end_date")
        ),
    )


def _flatten_event(event: Any) -> list[dict[str, Any]]:
    if not isinstance(event, dict):
        return []
    if event.get("closed") is True or event.get("active") is False:
        return []
    return [
        _contextual_market_row(market, event)
        for market in event.get("markets") or []
        if isinstance(market, dict)
    ]


def _flatten_search_events(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    for event in payload.get("events") or []:
        rows.extend(_flatten_event(event))
    return rows


def _deterministic_slug_rows(
    http: httpx.Client,
    *,
    asset: str,
    horizon: int,
    now_s: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch current/nearby rolling windows from Polymarket's stable slug scheme.

    Rolling crypto event URLs use e.g. `eth-updown-5m-<window_start_epoch>`.
    We probe the previous, current, and several upcoming aligned starts so a
    search-ranking miss cannot erase a venue/asset stream from measurement.
    """
    slug_asset = ASSET_SLUGS[asset]
    interval_s = horizon * 60
    now = int(time.time()) if now_s is None else int(now_s)
    aligned = now - (now % interval_s)
    rows: list[dict[str, Any]] = []
    for offset in range(-1, 5):
        start_s = aligned + offset * interval_s
        slug = f"{slug_asset}-updown-{horizon}m-{start_s}"
        response = http.get(f"{GAMMA_API}/events/slug/{slug}")
        if response.status_code == 404:
            continue
        response.raise_for_status()
        rows.extend(_flatten_event(response.json()))
    return rows


def _search_rows(
    http: httpx.Client,
    *,
    asset: str,
    horizon: int,
) -> list[dict[str, Any]]:
    name = ASSET_SEARCH_NAMES[asset]
    queries = [
        f"{name} Up or Down {horizon}m",
        f"{asset} Up or Down {horizon}m",
    ]
    rows: list[dict[str, Any]] = []
    for query in dict.fromkeys(queries):
        response = http.get(
            f"{GAMMA_API}/public-search",
            params={
                "q": query,
                "events_status": "active",
                "limit_per_type": 50,
                "page": 1,
                "keep_closed_markets": 0,
                "search_profiles": "false",
                "search_tags": "false",
            },
        )
        response.raise_for_status()
        rows.extend(_flatten_search_events(response.json()))
    return rows


def _fallback_market_rows(http: httpx.Client, *, max_pages: int = 4) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    limit = 500
    for page in range(max_pages):
        response = http.get(
            f"{GAMMA_API}/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": page * limit,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("unexpected Gamma markets payload")
        rows.extend(row for row in payload if isinstance(row, dict))
        if len(payload) < limit:
            break
    return rows


def discover_markets(
    http: httpx.Client,
    *,
    assets: Iterable[str] = FIRST_TIER_ASSETS,
    horizons: Iterable[int] = (5, 15),
) -> list[PolyBinaryMarket]:
    """Discover rolling short-crypto markets with deterministic + search paths."""
    wanted_assets = {a.upper() for a in assets}
    wanted_horizons = {int(x) for x in horizons}
    found: dict[str, PolyBinaryMarket] = {}
    now_ms = int(time.time() * 1000)
    max_future_ms = now_ms + 70 * 60 * 1000

    candidate_rows: list[dict[str, Any]] = []
    for asset in sorted(wanted_assets):
        if asset not in ASSET_SEARCH_NAMES:
            continue
        for horizon in sorted(wanted_horizons):
            candidate_rows.extend(_deterministic_slug_rows(http, asset=asset, horizon=horizon))
            candidate_rows.extend(_search_rows(http, asset=asset, horizon=horizon))

    if not candidate_rows:
        candidate_rows.extend(_fallback_market_rows(http))

    for row in candidate_rows:
        market = parse_market(row)
        if market is None:
            continue
        if market.asset not in wanted_assets or market.horizon_minutes not in wanted_horizons:
            continue
        if market.end_ts_ms is not None:
            if market.end_ts_ms < now_ms - 30_000:
                continue
            if market.end_ts_ms > max_future_ms:
                continue
        found[market.condition_id] = market

    return sorted(found.values(), key=lambda m: (m.asset, m.horizon_minutes, m.end_ts_ms or 0))


def _best_ask(book: dict[str, Any]) -> tuple[float | None, float | None]:
    asks: list[tuple[float, float]] = []
    for row in book.get("asks") or []:
        if not isinstance(row, dict):
            continue
        try:
            price = float(row.get("price"))
            size = float(row.get("size"))
        except (TypeError, ValueError):
            continue
        if 0 <= price <= 1 and size > 0:
            asks.append((price, size))
    if not asks:
        return None, None
    return min(asks, key=lambda x: x[0])


def fetch_best_ask(http: httpx.Client, token_id: str) -> tuple[float | None, float | None]:
    response = http.get(f"{CLOB_API}/book", params={"token_id": token_id})
    response.raise_for_status()
    payload = response.json()
    return _best_ask(payload if isinstance(payload, dict) else {})


class PolymarketTemporalShadow:
    def __init__(
        self,
        *,
        min_net_edge: float = 0.03,
        first_leg_max_price: float = 0.45,
        quantity: float = 1.0,
        horizons: tuple[int, ...] = (5, 15),
    ) -> None:
        self.min_net_edge = min_net_edge
        self.first_leg_max_price = first_leg_max_price
        self.quantity = quantity
        self.horizons = horizons
        self.engines: dict[str, TemporalMarketShadow] = {}
        self.markets: dict[str, PolyBinaryMarket] = {}

    def sync_markets(self, markets: Iterable[PolyBinaryMarket]) -> list[dict]:
        events: list[dict] = []
        for market in markets:
            self.markets[market.condition_id] = market
            if market.condition_id not in self.engines:
                self.engines[market.condition_id] = TemporalMarketShadow(
                    stream=market.stream,
                    market_id=market.condition_id,
                    quantity=self.quantity,
                    min_net_edge=self.min_net_edge,
                    first_leg_max_price=self.first_leg_max_price,
                    taker_fee_rate=POLY_CRYPTO_TAKER_FEE_RATE,
                )
                events.append({
                    "type": "TEMPORAL_MARKET_DISCOVERED",
                    "stream": market.stream.name,
                    "market_id": market.condition_id,
                    "question": market.question,
                    "end_ts_ms": market.end_ts_ms,
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
        return events

    def observe_market(
        self,
        market: PolyBinaryMarket,
        *,
        up_ask: float | None,
        down_ask: float | None,
        timestamp_ms: int,
    ) -> list[dict]:
        engine = self.engines[market.condition_id]
        if market.end_ts_ms is not None and timestamp_ms >= market.end_ts_ms:
            stranded = engine.expire(timestamp_ms)
            return [stranded] if stranded else []
        return engine.observe(up_ask=up_ask, down_ask=down_ask, timestamp_ms=timestamp_ms)

    def summary(self) -> dict[str, Any]:
        snapshots = [engine.snapshot() for engine in self.engines.values()]
        by_stream: dict[str, dict[str, float | int]] = {}
        for row in snapshots:
            stream = row["stream"]
            agg = by_stream.setdefault(
                stream,
                {"markets": 0, "completed": 0, "one_sided": 0, "quote_observations": 0, "locked_profit_usd": 0.0},
            )
            agg["markets"] += 1
            agg["completed"] += int(row["state"] == "COMPLETED")
            agg["one_sided"] += int(row["state"] == "ONE_SIDED")
            agg["quote_observations"] += int(row["quote_observations"])
            agg["locked_profit_usd"] += float(row["locked_profit_usd"] or 0.0)
        return {
            "type": "POLY_TEMPORAL_SHADOW_SUMMARY",
            "streams": sorted(by_stream),
            "by_stream": by_stream,
            "market_count": len(snapshots),
            "completed_pairs": sum(row["state"] == "COMPLETED" for row in snapshots),
            "one_sided_markets": sum(row["state"] == "ONE_SIDED" for row in snapshots),
            "expired_markets": sum(row["state"] == "EXPIRED" for row in snapshots),
            "shadow_locked_profit_usd": sum(float(row["locked_profit_usd"] or 0) for row in snapshots),
            "shadow_only": True,
            "order_entry": False,
            "realized_pnl": 0.0,
        }


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def run(
    *,
    poll_seconds: float,
    discovery_seconds: float,
    summary_seconds: float,
    min_net_edge: float,
    first_leg_max_price: float,
    once: bool,
) -> None:
    shadow = PolymarketTemporalShadow(
        min_net_edge=min_net_edge,
        first_leg_max_price=first_leg_max_price,
    )
    next_discovery = 0.0
    next_summary = 0.0

    with httpx.Client(timeout=10.0, headers={"User-Agent": "arb-temporal-shadow/1.0"}) as http:
        while True:
            now = time.time()
            if now >= next_discovery:
                try:
                    markets = discover_markets(http, horizons=shadow.horizons)
                    for event in shadow.sync_markets(markets):
                        _emit(event)
                    _emit({
                        "type": "POLY_TEMPORAL_DISCOVERY_SUMMARY",
                        "found": len(markets),
                        "streams": sorted({m.stream.name for m in markets}),
                        "shadow_only": True,
                        "order_entry": False,
                        "realized_pnl": 0.0,
                    })
                    next_discovery = now + discovery_seconds
                except Exception as exc:
                    _emit({
                        "type": "POLY_TEMPORAL_DISCOVERY_ERROR",
                        "error": type(exc).__name__,
                        "message": str(exc)[:300],
                        "shadow_only": True,
                    })
                    next_discovery = now + min(5.0, discovery_seconds)

            timestamp_ms = int(time.time() * 1000)
            for market in list(shadow.markets.values()):
                engine = shadow.engines[market.condition_id]
                if engine.completed_pair is not None or engine.expired:
                    continue
                try:
                    up_ask, up_size = fetch_best_ask(http, market.up_token_id)
                    down_ask, down_size = fetch_best_ask(http, market.down_token_id)
                    if up_size is not None and up_size < shadow.quantity:
                        up_ask = None
                    if down_size is not None and down_size < shadow.quantity:
                        down_ask = None
                    for event in shadow.observe_market(
                        market,
                        up_ask=up_ask,
                        down_ask=down_ask,
                        timestamp_ms=timestamp_ms,
                    ):
                        _emit(event)
                except Exception as exc:
                    _emit({
                        "type": "POLY_TEMPORAL_BOOK_ERROR",
                        "stream": market.stream.name,
                        "market_id": market.condition_id,
                        "error": type(exc).__name__,
                        "message": str(exc)[:300],
                        "shadow_only": True,
                    })

            if now >= next_summary:
                _emit(shadow.summary())
                next_summary = now + summary_seconds
            if once:
                return
            time.sleep(max(0.05, poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Polymarket temporal crypto shadow")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--discovery-seconds", type=float, default=20.0)
    parser.add_argument("--summary-seconds", type=float, default=10.0)
    parser.add_argument("--min-net-edge", type=float, default=0.03)
    parser.add_argument("--first-leg-max-price", type=float, default=0.45)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    run(
        poll_seconds=args.poll_seconds,
        discovery_seconds=args.discovery_seconds,
        summary_seconds=args.summary_seconds,
        min_net_edge=args.min_net_edge,
        first_leg_max_price=args.first_leg_max_price,
        once=args.once,
    )


if __name__ == "__main__":
    main()
