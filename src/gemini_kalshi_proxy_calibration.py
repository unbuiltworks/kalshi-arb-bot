"""Read-only BTC 15m settlement-proxy calibration for Gemini <-> Kalshi.

This service deliberately contains no order-entry code.  It observes the active
BTC 15-minute Gemini/Kalshi pair, records free public spot-price checkpoints near
expiry, then joins those checkpoints to the eventual venue settlements.

The goal is empirical: learn how much safety margin a free public-price composite
needs before the different Kaiko (Gemini) and CF Benchmarks (Kalshi) settlement
indexes can be treated as a controlled basis-risk trade.  It never upgrades the
pair to risk-free arbitrage.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import time
from typing import Any

import httpx
import websockets

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_proxy_calibration")

GEMINI_PREDICTION_REST = "https://api.gemini.com/v1/prediction-markets"
GEMINI_SPOT_REST = "https://api.gemini.com/v2/ticker/BTCUSD"
COINBASE_WS = "wss://advanced-trade-ws.coinbase.com"
KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_SERIES = "KXBTC15M"
CHECKPOINTS_S = (60, 30, 10, 5, 3, 2, 1)


@dataclass(frozen=True)
class SpotQuote:
    source: str
    price: Decimal
    received_ms: int


@dataclass(frozen=True)
class Pair:
    gemini_event_ticker: str
    gemini_symbol: str
    gemini_expiry: datetime
    gemini_strike: Decimal
    gemini_source_agency: str
    gemini_source_index: str
    kalshi_ticker: str
    kalshi_expiry: datetime
    kalshi_target: Decimal | None
    kalshi_strike_type: str


@dataclass
class Checkpoint:
    checkpoint_s: int
    receive_ms: int
    seconds_to_expiry: float
    coinbase_price: Decimal | None
    coinbase_age_ms: int | None
    gemini_spot_price: Decimal | None
    gemini_spot_age_ms: int | None
    composite_price: Decimal | None
    source_count: int
    source_spread_dollars: Decimal | None
    gemini_distance_dollars: Decimal | None
    gemini_distance_bps: Decimal | None
    kalshi_distance_dollars: Decimal | None
    kalshi_distance_bps: Decimal | None


@dataclass
class CalibrationState:
    quotes: dict[str, SpotQuote] = field(default_factory=dict)


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return D(str(value))
    except (InvalidOperation, ValueError):
        return None


def _contract(event: dict[str, Any]) -> dict[str, Any] | None:
    contracts = event.get("contracts") or []
    for row in contracts:
        if not isinstance(row, dict):
            continue
        strike = row.get("strike") or {}
        if isinstance(strike, dict) and str(strike.get("type") or "").lower() == "reference":
            if row.get("instrumentSymbol") or row.get("instrument_symbol"):
                return row
    return None


def _btc_15m_gemini(event: dict[str, Any], now: datetime) -> tuple[datetime, dict[str, Any], Decimal] | None:
    source = event.get("sourceDetails") or event.get("source_details") or {}
    index = str(source.get("index") or event.get("source") or "") if isinstance(source, dict) else ""
    if "BTCUSD" not in index.upper():
        return None
    contract = _contract(event)
    if contract is None:
        return None
    strike = contract.get("strike") or {}
    strike_value = _decimal(strike.get("value")) if isinstance(strike, dict) else None
    available = _dt(strike.get("availableAt")) if isinstance(strike, dict) else None
    expiry = _dt(contract.get("expiryDate") or event.get("expiryDate"))
    if strike_value is None or available is None or expiry is None or expiry <= now:
        return None
    duration_s = (expiry - available).total_seconds()
    if not 14 * 60 <= duration_s <= 16 * 60:
        return None
    return expiry, contract, strike_value


def _kalshi_expiry(market: dict[str, Any]) -> datetime | None:
    return _dt(
        market.get("close_time")
        or market.get("expiration_time")
        or market.get("expected_expiration_time")
    )


def kalshi_target(market: dict[str, Any]) -> Decimal | None:
    """Extract an unambiguous numeric threshold; fail closed if metadata conflicts."""
    vals: list[Decimal] = []
    for key in ("floor_strike", "cap_strike"):
        value = _decimal(market.get(key))
        if value is not None:
            vals.append(value)
    unique = sorted(set(vals))
    if len(unique) == 1:
        return unique[0]
    functional = _decimal(market.get("functional_strike"))
    if functional is not None and (not unique or functional in unique):
        return functional
    return None


def composite_snapshot(
    quotes: dict[str, SpotQuote], *, now_ms: int, max_age_ms: int = 1500
) -> tuple[Decimal | None, int, Decimal | None, dict[str, int | None]]:
    """Return a two-source median/midpoint composite, failing closed with <2 fresh sources."""
    fresh: list[SpotQuote] = []
    ages: dict[str, int | None] = {"coinbase": None, "gemini_spot": None}
    for source in ages:
        quote = quotes.get(source)
        if quote is None:
            continue
        age = max(0, now_ms - quote.received_ms)
        ages[source] = age
        if age <= max_age_ms:
            fresh.append(quote)
    if len(fresh) < 2:
        return None, len(fresh), None, ages
    prices = sorted(q.price for q in fresh)
    if len(prices) % 2:
        composite = prices[len(prices) // 2]
    else:
        i = len(prices) // 2
        composite = (prices[i - 1] + prices[i]) / D("2")
    spread = prices[-1] - prices[0]
    return composite, len(fresh), spread, ages


def _distance(price: Decimal | None, target: Decimal | None) -> tuple[Decimal | None, Decimal | None]:
    if price is None or target is None or target == 0:
        return None, None
    dollars = price - target
    return dollars, dollars / target * D("10000")


def _checkpoint(pair: Pair, state: CalibrationState, checkpoint_s: int, now_ms: int) -> Checkpoint:
    composite, count, spread, ages = composite_snapshot(state.quotes, now_ms=now_ms)
    gd, gb = _distance(composite, pair.gemini_strike)
    kd, kb = _distance(composite, pair.kalshi_target)
    coinbase = state.quotes.get("coinbase")
    gemini = state.quotes.get("gemini_spot")
    seconds_left = pair.gemini_expiry.timestamp() - now_ms / 1000
    return Checkpoint(
        checkpoint_s=checkpoint_s,
        receive_ms=now_ms,
        seconds_to_expiry=seconds_left,
        coinbase_price=coinbase.price if coinbase else None,
        coinbase_age_ms=ages["coinbase"],
        gemini_spot_price=gemini.price if gemini else None,
        gemini_spot_age_ms=ages["gemini_spot"],
        composite_price=composite,
        source_count=count,
        source_spread_dollars=spread,
        gemini_distance_dollars=gd,
        gemini_distance_bps=gb,
        kalshi_distance_dollars=kd,
        kalshi_distance_bps=kb,
    )


def _json_decimal(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _checkpoint_dict(pair: Pair, c: Checkpoint) -> dict[str, Any]:
    payload = {
        "asset": "BTC",
        "window_end_utc": pair.gemini_expiry,
        "gemini_event_ticker": pair.gemini_event_ticker,
        "gemini_symbol": pair.gemini_symbol,
        "gemini_source_agency": pair.gemini_source_agency,
        "gemini_source_index": pair.gemini_source_index,
        "gemini_strike": pair.gemini_strike,
        "kalshi_ticker": pair.kalshi_ticker,
        "kalshi_target": pair.kalshi_target,
        "kalshi_strike_type": pair.kalshi_strike_type,
        **c.__dict__,
        "settlement_status": "INDEX_MISMATCH_CALIBRATION_ONLY",
        "read_only": True,
        "order_entry": False,
    }
    return {k: _json_decimal(v) for k, v in payload.items()}


async def discover_pair(http: httpx.AsyncClient, *, max_expiry_skew_s: float = 30.0) -> Pair | None:
    now = datetime.now(timezone.utc)
    g_response = await http.get(
        f"{GEMINI_PREDICTION_REST}/events",
        params={"status": "active", "category": "crypto", "limit": 500},
    )
    g_response.raise_for_status()
    candidates: list[tuple[datetime, dict[str, Any], dict[str, Any], Decimal]] = []
    for event in g_response.json().get("data", []):
        if not isinstance(event, dict):
            continue
        parsed = _btc_15m_gemini(event, now)
        if parsed is not None:
            expiry, contract, strike = parsed
            candidates.append((expiry, event, contract, strike))
    if not candidates:
        return None
    g_expiry, event, contract, g_strike = min(candidates, key=lambda row: row[0])

    k_response = await http.get(
        f"{KALSHI_REST}/markets",
        params={"series_ticker": KALSHI_SERIES, "status": "open", "limit": 100, "mve_filter": "exclude"},
    )
    k_response.raise_for_status()
    kalshi: list[tuple[float, datetime, dict[str, Any]]] = []
    for market in k_response.json().get("markets", []):
        if not isinstance(market, dict):
            continue
        expiry = _kalshi_expiry(market)
        if expiry is None:
            continue
        skew = abs((expiry - g_expiry).total_seconds())
        if skew <= max_expiry_skew_s:
            kalshi.append((skew, expiry, market))
    if not kalshi:
        return None
    _, k_expiry, market = min(kalshi, key=lambda row: row[0])

    source = event.get("sourceDetails") or event.get("source_details") or {}
    agency = str(source.get("agency") or "") if isinstance(source, dict) else ""
    index = str(source.get("index") or event.get("source") or "") if isinstance(source, dict) else str(event.get("source") or "")
    return Pair(
        gemini_event_ticker=str(event.get("ticker") or ""),
        gemini_symbol=str(contract.get("instrumentSymbol") or contract.get("instrument_symbol") or ""),
        gemini_expiry=g_expiry,
        gemini_strike=g_strike,
        gemini_source_agency=agency,
        gemini_source_index=index,
        kalshi_ticker=str(market.get("ticker") or ""),
        kalshi_expiry=k_expiry,
        kalshi_target=kalshi_target(market),
        kalshi_strike_type=str(market.get("strike_type") or ""),
    )


async def coinbase_stream(state: CalibrationState) -> None:
    while True:
        try:
            async with websockets.connect(COINBASE_WS, open_timeout=8.0) as ws:
                await ws.send(json.dumps({"type": "subscribe", "product_ids": ["BTC-USD"], "channel": "ticker"}))
                await ws.send(json.dumps({"type": "subscribe", "channel": "heartbeats"}))
                async for raw in ws:
                    now_ms = int(time.time() * 1000)
                    payload = json.loads(raw)
                    if payload.get("channel") != "ticker":
                        continue
                    for event in payload.get("events") or []:
                        for ticker in event.get("tickers") or []:
                            if ticker.get("product_id") != "BTC-USD":
                                continue
                            bid = _decimal(ticker.get("best_bid"))
                            ask = _decimal(ticker.get("best_ask"))
                            price = (bid + ask) / D("2") if bid is not None and ask is not None else _decimal(ticker.get("price"))
                            if price is not None:
                                state.quotes["coinbase"] = SpotQuote("coinbase", price, now_ms)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("PROXY_COINBASE_RECONNECT %s", json.dumps({"error_type": type(exc).__name__, "error": str(exc)}))
            await asyncio.sleep(0.5)


async def gemini_spot_poller(http: httpx.AsyncClient, state: CalibrationState, poll_s: float) -> None:
    while True:
        try:
            response = await http.get(GEMINI_SPOT_REST, headers={"Cache-Control": "no-cache"})
            response.raise_for_status()
            payload = response.json()
            bid = _decimal(payload.get("bid"))
            ask = _decimal(payload.get("ask"))
            price = (bid + ask) / D("2") if bid is not None and ask is not None else _decimal(payload.get("close"))
            if price is not None:
                state.quotes["gemini_spot"] = SpotQuote("gemini_spot", price, int(time.time() * 1000))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("PROXY_GEMINI_SPOT_ERROR %s", json.dumps({"error_type": type(exc).__name__, "error": str(exc)}))
        await asyncio.sleep(poll_s)


async def _event_settlement(http: httpx.AsyncClient, event_ticker: str) -> Decimal | None:
    response = await http.get(f"{GEMINI_PREDICTION_REST}/events/{event_ticker}")
    if response.status_code != 200:
        return None
    event = response.json()
    settlement = event.get("settlement") or {}
    value = _decimal(settlement.get("value")) if isinstance(settlement, dict) else None
    if value is not None:
        return value
    contract = _contract(event)
    return _decimal(contract.get("settlementValue")) if contract else None


async def _kalshi_settlement(http: httpx.AsyncClient, ticker: str) -> tuple[str | None, Decimal | None]:
    response = await http.get(f"{KALSHI_REST}/markets/{ticker}")
    if response.status_code != 200:
        return None, None
    market = response.json().get("market") or {}
    result = str(market.get("result") or "").lower() or None
    expiration_value = _decimal(market.get("expiration_value"))
    return result, expiration_value


async def wait_for_settlement(
    http: httpx.AsyncClient, pair: Pair, checkpoints: list[Checkpoint], *, timeout_s: float = 900
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        g_value, k = await asyncio.gather(
            _event_settlement(http, pair.gemini_event_ticker),
            _kalshi_settlement(http, pair.kalshi_ticker),
        )
        k_result, k_value = k
        if g_value is not None and k_result in {"yes", "no"}:
            rows: list[dict[str, Any]] = []
            g_outcome = "yes" if g_value > pair.gemini_strike else "no"
            for c in checkpoints:
                error = c.composite_price - g_value if c.composite_price is not None else None
                predicted = None
                if c.composite_price is not None:
                    predicted = "yes" if c.composite_price > pair.gemini_strike else "no"
                rows.append({
                    "checkpoint_s": c.checkpoint_s,
                    "composite_price": str(c.composite_price) if c.composite_price is not None else None,
                    "proxy_error_vs_gemini_settlement_dollars": str(error) if error is not None else None,
                    "abs_proxy_error_dollars": str(abs(error)) if error is not None else None,
                    "proxy_gemini_outcome": predicted,
                    "proxy_gemini_outcome_correct": predicted == g_outcome if predicted else None,
                })
            payload = {
                "asset": "BTC",
                "window_end_utc": pair.gemini_expiry.isoformat(),
                "gemini_event_ticker": pair.gemini_event_ticker,
                "gemini_symbol": pair.gemini_symbol,
                "gemini_source_index": pair.gemini_source_index,
                "gemini_strike": str(pair.gemini_strike),
                "gemini_settlement_value": str(g_value),
                "gemini_outcome": g_outcome,
                "kalshi_ticker": pair.kalshi_ticker,
                "kalshi_target": str(pair.kalshi_target) if pair.kalshi_target is not None else None,
                "kalshi_result": k_result,
                "kalshi_expiration_value": str(k_value) if k_value is not None else None,
                "outcomes_agree": g_outcome == k_result,
                "checkpoints": rows,
                "classification": "INDEX_MISMATCH_CALIBRATION_ONLY",
                "read_only": True,
                "order_entry": False,
            }
            logger.info("PROXY_SETTLEMENT %s", json.dumps(payload, separators=(",", ":")))
            return
        await asyncio.sleep(5)
    logger.warning("PROXY_SETTLEMENT_TIMEOUT %s", json.dumps({
        "gemini_event_ticker": pair.gemini_event_ticker,
        "kalshi_ticker": pair.kalshi_ticker,
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


async def run(*, seconds: float, spot_poll_s: float) -> None:
    state = CalibrationState()
    async with httpx.AsyncClient(timeout=10.0) as http:
        feeds = [
            asyncio.create_task(coinbase_stream(state), name="coinbase_public_ticker"),
            asyncio.create_task(gemini_spot_poller(http, state, spot_poll_s), name="gemini_public_spot"),
        ]
        stop_at = time.monotonic() + seconds if seconds > 0 else None
        last_pair_key: tuple[str, str] | None = None
        try:
            while stop_at is None or time.monotonic() < stop_at:
                pair = await discover_pair(http)
                if pair is None:
                    await asyncio.sleep(2)
                    continue
                pair_key = (pair.gemini_event_ticker, pair.kalshi_ticker)
                if pair_key == last_pair_key:
                    await asyncio.sleep(1)
                    continue
                last_pair_key = pair_key
                logger.info("PROXY_PAIR %s", json.dumps({
                    "gemini_event_ticker": pair.gemini_event_ticker,
                    "gemini_symbol": pair.gemini_symbol,
                    "gemini_expiry": pair.gemini_expiry.isoformat(),
                    "gemini_strike": str(pair.gemini_strike),
                    "gemini_source_agency": pair.gemini_source_agency,
                    "gemini_source_index": pair.gemini_source_index,
                    "kalshi_ticker": pair.kalshi_ticker,
                    "kalshi_expiry": pair.kalshi_expiry.isoformat(),
                    "kalshi_target": str(pair.kalshi_target) if pair.kalshi_target is not None else None,
                    "kalshi_strike_type": pair.kalshi_strike_type,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")))

                emitted: set[int] = set()
                checkpoints: list[Checkpoint] = []
                while datetime.now(timezone.utc) < pair.gemini_expiry:
                    now_ms = int(time.time() * 1000)
                    seconds_left = pair.gemini_expiry.timestamp() - now_ms / 1000
                    for checkpoint_s in CHECKPOINTS_S:
                        if checkpoint_s not in emitted and seconds_left <= checkpoint_s:
                            c = _checkpoint(pair, state, checkpoint_s, now_ms)
                            checkpoints.append(c)
                            emitted.add(checkpoint_s)
                            logger.info("PROXY_CHECKPOINT %s", json.dumps(
                                _checkpoint_dict(pair, c), separators=(",", ":")
                            ))
                    await asyncio.sleep(0.05 if seconds_left <= 10 else 0.25)

                await wait_for_settlement(http, pair, checkpoints)
        finally:
            for task in feeds:
                task.cancel()
            await asyncio.gather(*feeds, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=0, help="0 runs continuously")
    parser.add_argument("--spot-poll-s", type=float, default=1.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(seconds=args.seconds, spot_poll_s=args.spot_poll_s))


if __name__ == "__main__":
    main()
