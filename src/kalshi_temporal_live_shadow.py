"""Read-only Kalshi 15-minute temporal-pair shadow for BTC/ETH/SOL/XRP.

Uses unauthenticated public market-data GET endpoints only. No credentials,
signing, order creation, amendment, cancellation, or portfolio endpoints.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import time
from typing import Any, Iterable

import httpx

from src.temporal_binary_shadow import FIRST_TIER_ASSETS, StreamKey, TemporalMarketShadow

KALSHI_API = "https://external-api.kalshi.com/trade-api/v2"
SERIES_BY_ASSET = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "XRP": "KXXRP15M",
}
STANDARD_TAKER_RATE = 0.07


@dataclass(frozen=True)
class KalshiSeries:
    asset: str
    ticker: str
    fee_type: str
    fee_multiplier: float
    settlement_sources: tuple[tuple[str, str], ...]
    title: str


@dataclass(frozen=True)
class KalshiBinaryMarket:
    ticker: str
    event_ticker: str
    asset: str
    close_ts_ms: int | None
    title: str
    subtitle: str
    yes_ask: float | None
    yes_ask_size: float | None
    no_ask: float | None
    no_ask_size: float | None

    @property
    def stream(self) -> StreamKey:
        return StreamKey("kalshi", self.asset, 15)


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


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


def _ceil_cent(value: float) -> float:
    """q=1 cash charge: cent-align the single-fill fee conservatively."""
    return math.ceil(round(value * 100.0, 10) - 1e-12) / 100.0


def kalshi_q1_taker_fee_usd(quantity: float, price: float, fee_multiplier: float) -> float:
    """Conservative q=1 fee model including the single-fill cash rounding.

    Kalshi's quadratic trade fee is 0.07 * M * C * P * (1-P). The exchange
    computes sub-cent trade fees, but a single isolated q=1 fill still changes
    a cent-aligned cash balance. For this q=1 shadow we therefore charge the
    next whole cent, matching the conservative one-fill economics. Larger or
    fractional sizing requires explicit accumulator/rebate replay before use.
    """
    if abs(quantity - 1.0) > 1e-9:
        raise ValueError("Kalshi temporal shadow fee model is certified for q=1 only")
    if not 0.0 <= price <= 1.0:
        raise ValueError("price must be between 0 and 1")
    if fee_multiplier < 0:
        raise ValueError("fee_multiplier cannot be negative")
    raw = STANDARD_TAKER_RATE * fee_multiplier * quantity * price * (1.0 - price)
    return _ceil_cent(raw)


def fetch_series(http: httpx.Client, asset: str) -> KalshiSeries:
    asset = asset.upper()
    ticker = SERIES_BY_ASSET[asset]
    response = http.get(f"{KALSHI_API}/series/{ticker}")
    response.raise_for_status()
    payload = response.json()
    row = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(row, dict):
        raise ValueError(f"unexpected series payload for {ticker}")

    fee_type = str(row.get("fee_type") or "")
    if not fee_type.startswith("quadratic"):
        raise ValueError(f"unsupported fee type for {ticker}: {fee_type}")

    title = str(row.get("title") or "")
    if "up down" not in title.lower():
        raise ValueError(f"refusing YES->UP mapping for unexpected series title: {ticker} {title!r}")

    sources: list[tuple[str, str]] = []
    for source in row.get("settlement_sources") or []:
        if isinstance(source, dict):
            sources.append((str(source.get("name") or ""), str(source.get("url") or "")))

    return KalshiSeries(
        asset=asset,
        ticker=ticker,
        fee_type=fee_type,
        fee_multiplier=float(row.get("fee_multiplier") or 1.0),
        settlement_sources=tuple(sources),
        title=title,
    )


def _derive_asks(row: dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
    yes_ask = _float(row.get("yes_ask_dollars"))
    no_ask = _float(row.get("no_ask_dollars"))
    yes_bid = _float(row.get("yes_bid_dollars"))
    no_bid = _float(row.get("no_bid_dollars"))

    if yes_ask is None and no_bid is not None:
        yes_ask = 1.0 - no_bid
    if no_ask is None and yes_bid is not None:
        no_ask = 1.0 - yes_bid

    yes_ask_size = _float(row.get("yes_ask_size_fp"))
    # A NO ask rests as the reciprocal of the YES bid.
    no_ask_size = _float(row.get("no_ask_size_fp"))
    if no_ask_size is None:
        no_ask_size = _float(row.get("yes_bid_size_fp"))

    return yes_ask, yes_ask_size, no_ask, no_ask_size


def fetch_open_markets(
    http: httpx.Client,
    series: KalshiSeries,
    *,
    future_minutes: int = 35,
) -> list[KalshiBinaryMarket]:
    response = http.get(
        f"{KALSHI_API}/markets",
        params={"series_ticker": series.ticker, "status": "open", "limit": 1000},
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("markets") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"unexpected markets payload for {series.ticker}")

    now_ms = int(time.time() * 1000)
    max_close_ms = now_ms + future_minutes * 60 * 1000
    markets: list[KalshiBinaryMarket] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        close_ms = _iso_ms(
            row.get("close_time")
            or row.get("expected_expiration_time")
            or row.get("expiration_time")
        )
        if close_ms is not None and (close_ms < now_ms - 30_000 or close_ms > max_close_ms):
            continue
        yes_ask, yes_size, no_ask, no_size = _derive_asks(row)
        markets.append(
            KalshiBinaryMarket(
                ticker=str(row.get("ticker") or ""),
                event_ticker=str(row.get("event_ticker") or ""),
                asset=series.asset,
                close_ts_ms=close_ms,
                title=str(row.get("title") or ""),
                subtitle=str(row.get("subtitle") or ""),
                yes_ask=yes_ask,
                yes_ask_size=yes_size,
                no_ask=no_ask,
                no_ask_size=no_size,
            )
        )
    return [market for market in markets if market.ticker]


class KalshiTemporalShadow:
    def __init__(
        self,
        *,
        min_net_edge: float = 0.03,
        first_leg_max_price: float = 0.45,
        quantity: float = 1.0,
    ) -> None:
        if abs(quantity - 1.0) > 1e-9:
            raise ValueError("Kalshi live shadow is q=1 until fee rounding is replayed at size")
        self.min_net_edge = min_net_edge
        self.first_leg_max_price = first_leg_max_price
        self.quantity = quantity
        self.series: dict[str, KalshiSeries] = {}
        self.markets: dict[str, KalshiBinaryMarket] = {}
        self.engines: dict[str, TemporalMarketShadow] = {}

    def sync_series(self, series_rows: Iterable[KalshiSeries]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for series in series_rows:
            if series.asset not in self.series:
                events.append({
                    "type": "KALSHI_TEMPORAL_SERIES_CERTIFIED",
                    "stream": StreamKey("kalshi", series.asset, 15).name,
                    "series_ticker": series.ticker,
                    "series_title": series.title,
                    "fee_type": series.fee_type,
                    "fee_multiplier": series.fee_multiplier,
                    "settlement_sources": series.settlement_sources,
                    "shadow_only": True,
                    "order_entry": False,
                    "realized_pnl": 0.0,
                })
            self.series[series.asset] = series
        return events

    def sync_markets(self, markets: Iterable[KalshiBinaryMarket]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for market in markets:
            self.markets[market.ticker] = market
            if market.ticker in self.engines:
                continue
            series = self.series[market.asset]
            fee_model = lambda q, p, m=series.fee_multiplier: kalshi_q1_taker_fee_usd(q, p, m)
            self.engines[market.ticker] = TemporalMarketShadow(
                stream=market.stream,
                market_id=market.ticker,
                quantity=self.quantity,
                min_net_edge=self.min_net_edge,
                first_leg_max_price=self.first_leg_max_price,
                fee_model=fee_model,
            )
            events.append({
                "type": "KALSHI_TEMPORAL_MARKET_DISCOVERED",
                "stream": market.stream.name,
                "market_id": market.ticker,
                "event_ticker": market.event_ticker,
                "title": market.title,
                "subtitle": market.subtitle,
                "close_ts_ms": market.close_ts_ms,
                "shadow_only": True,
                "order_entry": False,
                "realized_pnl": 0.0,
            })
        return events

    def observe(self, market: KalshiBinaryMarket, timestamp_ms: int) -> list[dict[str, Any]]:
        engine = self.engines[market.ticker]
        if market.close_ts_ms is not None and timestamp_ms >= market.close_ts_ms:
            stranded = engine.expire(timestamp_ms)
            return [stranded] if stranded else []

        yes_ask = market.yes_ask
        no_ask = market.no_ask
        if market.yes_ask_size is not None and market.yes_ask_size < self.quantity:
            yes_ask = None
        if market.no_ask_size is not None and market.no_ask_size < self.quantity:
            no_ask = None
        # For this certified series YES means price up, NO means price down.
        return engine.observe(up_ask=yes_ask, down_ask=no_ask, timestamp_ms=timestamp_ms)

    def summary(self) -> dict[str, Any]:
        snapshots = [engine.snapshot() for engine in self.engines.values()]
        by_stream: dict[str, dict[str, float | int]] = {}
        for row in snapshots:
            agg = by_stream.setdefault(
                row["stream"],
                {"markets": 0, "completed": 0, "one_sided": 0, "quote_observations": 0, "locked_profit_usd": 0.0},
            )
            agg["markets"] += 1
            agg["completed"] += int(row["state"] == "COMPLETED")
            agg["one_sided"] += int(row["state"] == "ONE_SIDED")
            agg["quote_observations"] += int(row["quote_observations"])
            agg["locked_profit_usd"] += float(row["locked_profit_usd"] or 0.0)
        return {
            "type": "KALSHI_TEMPORAL_SHADOW_SUMMARY",
            "streams": sorted(by_stream),
            "by_stream": by_stream,
            "market_count": len(snapshots),
            "completed_pairs": sum(row["state"] == "COMPLETED" for row in snapshots),
            "one_sided_markets": sum(row["state"] == "ONE_SIDED" for row in snapshots),
            "shadow_locked_profit_usd": sum(float(row["locked_profit_usd"] or 0.0) for row in snapshots),
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
    shadow = KalshiTemporalShadow(
        min_net_edge=min_net_edge,
        first_leg_max_price=first_leg_max_price,
    )
    next_discovery = 0.0
    next_summary = 0.0

    with httpx.Client(timeout=10.0, headers={"User-Agent": "arb-kalshi-temporal-shadow/1.0"}) as http:
        while True:
            now = time.time()
            if now >= next_discovery:
                try:
                    series_rows = [fetch_series(http, asset) for asset in FIRST_TIER_ASSETS]
                    for event in shadow.sync_series(series_rows):
                        _emit(event)
                    discovered: list[KalshiBinaryMarket] = []
                    for series in series_rows:
                        discovered.extend(fetch_open_markets(http, series))
                    for event in shadow.sync_markets(discovered):
                        _emit(event)
                    _emit({
                        "type": "KALSHI_TEMPORAL_DISCOVERY_SUMMARY",
                        "found": len(discovered),
                        "streams": sorted({m.stream.name for m in discovered}),
                        "shadow_only": True,
                        "order_entry": False,
                        "realized_pnl": 0.0,
                    })
                    next_discovery = now + discovery_seconds
                except Exception as exc:
                    _emit({
                        "type": "KALSHI_TEMPORAL_DISCOVERY_ERROR",
                        "error": type(exc).__name__,
                        "message": str(exc)[:400],
                        "shadow_only": True,
                        "order_entry": False,
                        "realized_pnl": 0.0,
                    })
                    next_discovery = now + min(5.0, discovery_seconds)

            timestamp_ms = int(time.time() * 1000)
            # Refresh price fields from the public market list each poll. Four
            # asset series means four small unauthenticated reads per cycle.
            for asset, series in list(shadow.series.items()):
                try:
                    fresh = fetch_open_markets(http, series)
                    shadow.sync_markets(fresh)
                    for market in fresh:
                        shadow.markets[market.ticker] = market
                        for event in shadow.observe(market, timestamp_ms):
                            _emit(event)
                except Exception as exc:
                    _emit({
                        "type": "KALSHI_TEMPORAL_MARKET_ERROR",
                        "stream": StreamKey("kalshi", asset, 15).name,
                        "error": type(exc).__name__,
                        "message": str(exc)[:300],
                        "shadow_only": True,
                    })

            if now >= next_summary:
                _emit(shadow.summary())
                next_summary = now + summary_seconds
            if once:
                return
            time.sleep(max(0.10, poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Kalshi temporal crypto shadow")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--discovery-seconds", type=float, default=15.0)
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
