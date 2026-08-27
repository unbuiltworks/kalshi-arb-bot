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
WINDOW_RE = re.compile(
    r"(?P<h1>\d{1,2}):(?P<m1>\d{2})\s*(?P<a1>AM|PM)?\s*-\s*"
    r"(?P<h2>\d{1,2}):(?P<m2>\d{2})\s*(?P<a2>AM|PM)\s*ET",
    re.I,
)


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
    match = WINDOW_RE.search((text or "").replace("–", "-"))
    if not match:
        return None
    end_ampm = match.group("a2").upper()
    start_ampm = (match.group("a1") or end_ampm).upper()
    start = _clock_minutes(int(match.group("h1")), int(match.group("m1")), start_ampm)
    end = _clock_minutes(int(match.group("h2")), int(match.group("m2")), end_ampm)
    duration = (end - start) % (24 * 60)
    return duration or None


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


def parse_market(row: dict[str, Any]) -> PolyBinaryMarket | None:
    question = str(row.get("question") or row.get("title") or "")
    slug = str(row.get("slug") or "")
    joined = f"{question} {slug}"
    if "up or down" not in joined.lower():
        return None

    asset = _asset(joined)
    horizon = infer_horizon_minutes(question)
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
        question=question,
        slug=slug,
        up_token_id=mapping["UP"],
        down_token_id=mapping["DOWN"],
        end_ts_ms=_iso_ms(row.get("endDate") or row.get("endDateIso")),
    )


def discover_markets(
    http: httpx.Client,
    *,
    assets: Iterable[str] = FIRST_TIER_ASSETS,
    horizons: Iterable[int] = (5, 15),
    max_pages: int = 8,
) -> list[PolyBinaryMarket]:
    wanted_assets = {a.upper() for a in assets}
    wanted_horizons = {int(x) for x in horizons}
    found: dict[str, PolyBinaryMarket] = {}
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
        for row in payload:
            if not isinstance(row, dict):
                continue
            market = parse_market(row)
            if market and market.asset in wanted_assets and market.horizon_minutes in wanted_horizons:
                found[market.condition_id] = market
        if len(payload) < limit:
            break

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
        return {
            "type": "POLY_TEMPORAL_SHADOW_SUMMARY",
            "streams": sorted({row["stream"] for row in snapshots}),
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
    min_net_edge: float,
    first_leg_max_price: float,
    once: bool,
) -> None:
    shadow = PolymarketTemporalShadow(
        min_net_edge=min_net_edge,
        first_leg_max_price=first_leg_max_price,
    )
    next_discovery = 0.0

    with httpx.Client(timeout=10.0, headers={"User-Agent": "arb-temporal-shadow/1.0"}) as http:
        while True:
            now = time.time()
            if now >= next_discovery:
                try:
                    markets = discover_markets(http, horizons=shadow.horizons)
                    for event in shadow.sync_markets(markets):
                        _emit(event)
                    next_discovery = now + discovery_seconds
                except Exception as exc:
                    _emit({
                        "type": "POLY_TEMPORAL_DISCOVERY_ERROR",
                        "error": type(exc).__name__,
                        "message": str(exc)[:300],
                        "shadow_only": True,
                    })

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

            _emit(shadow.summary())
            if once:
                return
            time.sleep(max(0.05, poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Polymarket temporal crypto shadow")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--discovery-seconds", type=float, default=20.0)
    parser.add_argument("--min-net-edge", type=float, default=0.03)
    parser.add_argument("--first-leg-max-price", type=float, default=0.45)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    run(
        poll_seconds=args.poll_seconds,
        discovery_seconds=args.discovery_seconds,
        min_net_edge=args.min_net_edge,
        first_leg_max_price=args.first_leg_max_price,
        once=args.once,
    )


if __name__ == "__main__":
    main()
