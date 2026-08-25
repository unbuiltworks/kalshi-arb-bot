"""Read-only single-venue BTC 15m maker shadow: Kalshi vs spot fair value.

This is deliberately NOT a Gemini<->Kalshi arbitrage or maker->hedge strategy.
Gemini is used only as one public BTC spot-price source alongside Coinbase.
The traded/simulated venue is Kalshi only.

Honesty rules:
- the fair-probability model never reads Kalshi's midpoint;
- model status is UNVALIDATED until settlement calibration says probabilities
  are calibrated;
- hypothetical fills require STRICT trade-through plus displayed queue-ahead
  exhaustion; a touch at our price never fills;
- the same pessimistic rule counts adverse/stale fills in full;
- quotes are pulled in the final 60 seconds;
- short-horizon markouts are against OUR spot-derived fair probability;
- settlement P&L/markout uses the eventual Kalshi result;
- exact live series fee_type + fee_multiplier are fetched at startup/window roll;
- no order-entry transport or write endpoint exists in this module.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import inspect
import json
import logging
import statistics
import time
from typing import Any

import httpx
import websockets

from config.settings import API_KEY_ID
from src.auth import auth_headers, load_private_key
from src.crypto_spot_fair_value import (
    D,
    MODEL_NAME,
    PessimisticQuote,
    ceil_tick,
    fair_probability_yes,
    floor_tick,
    kalshi_maker_fee,
    realized_sigma_1s,
    settlement_pnl,
)
from src.gemini_kalshi_kalshi_fees import fetch_series_fee_metadata
from src.gemini_kalshi_proxy_calibration import (
    CalibrationState,
    KALSHI_REST,
    coinbase_stream,
    composite_snapshot,
    gemini_spot_poller,
    kalshi_target,
)

logger = logging.getLogger("kalshi_arb.kalshi_btc_spot_maker_shadow")
SERIES = "KXBTC15M"
KALSHI_WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
KALSHI_WS_SIGN_PATH = "/trade-api/ws/v2"
TICK = D("0.01")
QTY = D("1")
MARKOUT_MS = (20, 50, 100, 250, 500)
CALIBRATION_CHECKPOINTS_S = (600, 300, 120, 60)
PULL_QUOTES_LAST_S = 60.0
MAX_BOOK_AGE_MS = 2000
HEARTBEAT_S = 10.0


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _expiry(market: dict[str, Any]) -> datetime | None:
    return _dt(market.get("close_time") or market.get("expiration_time") or market.get("expected_expiration_time"))


def _ws_header_kw(headers: dict[str, str]) -> dict[str, Any]:
    params = inspect.signature(websockets.connect).parameters
    return {"additional_headers" if "additional_headers" in params else "extra_headers": headers}


@dataclass
class Book:
    ticker: str
    yes: dict[Decimal, Decimal] = field(default_factory=dict)
    no: dict[Decimal, Decimal] = field(default_factory=dict)
    last_receive_ms: int = 0
    seq: int | None = None

    @staticmethod
    def _levels(rows: Any) -> dict[Decimal, Decimal]:
        out: dict[Decimal, Decimal] = {}
        if not isinstance(rows, list):
            return out
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            try:
                p, q = D(str(row[0])), D(str(row[1]))
            except Exception:
                continue
            if q > 0:
                out[p] = q
        return out

    def apply(self, payload: dict[str, Any], receive_ms: int) -> bool:
        typ = str(payload.get("type") or "")
        msg = payload.get("msg") or {}
        if str(msg.get("market_ticker") or "") != self.ticker:
            return False
        seq = payload.get("seq")
        if seq is not None:
            try:
                seq_i = int(seq)
            except Exception:
                return False
            if self.seq is not None and seq_i <= self.seq:
                return False
            self.seq = seq_i
        if typ == "orderbook_snapshot":
            self.yes = self._levels(msg.get("yes_dollars_fp"))
            self.no = self._levels(msg.get("no_dollars_fp"))
            self.last_receive_ms = receive_ms
            return True
        if typ != "orderbook_delta":
            return False
        side = str(msg.get("side") or "").lower()
        if side not in {"yes", "no"}:
            return False
        try:
            p = D(str(msg.get("price_dollars")))
            dq = D(str(msg.get("delta_fp")))
        except Exception:
            return False
        levels = self.yes if side == "yes" else self.no
        new_q = levels.get(p, D("0")) + dq
        if new_q <= 0:
            levels.pop(p, None)
        else:
            levels[p] = new_q
        self.last_receive_ms = receive_ms
        return True

    def yes_top(self) -> tuple[Decimal | None, Decimal | None]:
        bid = max(self.yes) if self.yes else None
        ask = D("1") - max(self.no) if self.no else None
        return bid, ask

    def queue_ahead(self, outcome: str, price: Decimal) -> Decimal:
        return (self.yes if outcome == "YES" else self.no).get(price, D("0"))


@dataclass(frozen=True)
class Trade:
    yes_price: Decimal
    no_price: Decimal
    quantity: Decimal
    taker_side: str
    ts_ms: int


def parse_trade(payload: dict[str, Any], ticker: str) -> Trade | None:
    if str(payload.get("type") or "") != "trade":
        return None
    msg = payload.get("msg") or {}
    if str(msg.get("market_ticker") or "") != ticker:
        return None
    try:
        yes = D(str(msg["yes_price_dollars"]))
        no = D(str(msg["no_price_dollars"]))
        qty = D(str(msg.get("count_fp") or "0"))
        ts_ms = int(msg.get("ts_ms") or int(msg.get("ts") or 0) * 1000)
    except (KeyError, TypeError, ValueError):
        return None
    taker = str(msg.get("taker_side") or "").lower()
    if qty <= 0 or taker not in {"yes", "no"}:
        return None
    return Trade(yes, no, qty, taker, ts_ms)


@dataclass
class SpotTracker:
    state: CalibrationState = field(default_factory=CalibrationState)
    one_second_prices: deque[float] = field(default_factory=lambda: deque(maxlen=301))
    latest_spot: float | None = None
    latest_receive_ms: int | None = None
    latest_source_spread: float | None = None
    latest_source_ages: dict[str, int | None] = field(default_factory=dict)
    _last_sample_ms: int = 0

    async def sampler(self) -> None:
        while True:
            now_ms = int(time.time() * 1000)
            composite, count, spread, ages = composite_snapshot(self.state.quotes, now_ms=now_ms)
            if composite is not None and count >= 2:
                self.latest_spot = float(composite)
                self.latest_receive_ms = now_ms
                self.latest_source_spread = float(spread) if spread is not None else None
                self.latest_source_ages = ages
                if now_ms - self._last_sample_ms >= 1000:
                    self.one_second_prices.append(float(composite))
                    self._last_sample_ms = now_ms
            await asyncio.sleep(0.02)

    def model(self, *, strike: Decimal, expiry: datetime) -> dict[str, Any] | None:
        if self.latest_spot is None or self.latest_receive_ms is None:
            return None
        now_ms = int(time.time() * 1000)
        if now_ms - self.latest_receive_ms > 1500:
            return None
        sigma = realized_sigma_1s(self.one_second_prices, min_returns=30)
        if sigma is None:
            return None
        seconds_left = expiry.timestamp() - now_ms / 1000.0
        p = fair_probability_yes(
            spot=self.latest_spot,
            strike=float(strike),
            seconds_to_expiry=seconds_left,
            sigma_per_sqrt_second=sigma,
        )
        if p is None:
            return None
        return {
            "ts_ms": now_ms,
            "spot": self.latest_spot,
            "strike": float(strike),
            "seconds_left": seconds_left,
            "sigma_1s": sigma,
            "fair_yes": p,
            "source_spread_dollars": self.latest_source_spread,
            "source_ages_ms": self.latest_source_ages,
            "model": MODEL_NAME,
        }


@dataclass
class ActiveQuote:
    q: PessimisticQuote
    opened_ms: int
    fair_yes_open: float
    yes_display_price: Decimal


@dataclass
class Fill:
    ticker: str
    outcome: str
    contract_price: Decimal
    yes_display_price: Decimal
    fill_ms: int
    fair_yes_fill: float
    maker_fee: Decimal
    queue_ahead_open: Decimal
    markouts: dict[int, float] = field(default_factory=dict)


@dataclass
class Stats:
    started_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    windows: int = 0
    quotes_opened: int = 0
    quotes_repriced: int = 0
    quotes_canceled: int = 0
    trades: int = 0
    fills: int = 0
    settled_fills: int = 0
    settled_net_pnl: Decimal = D("0")
    fee_failures: int = 0
    markouts: dict[int, list[float]] = field(default_factory=lambda: {h: [] for h in MARKOUT_MS})

    def summary(self, *, active: int, inventory: int, fee_meta: Any) -> dict[str, Any]:
        elapsed_days = max(1e-9, (int(time.time() * 1000) - self.started_ms) / 86_400_000)
        pnl_pc = self.settled_net_pnl / D(self.settled_fills) if self.settled_fills else None
        fill_day = self.fills / elapsed_days
        projected = (pnl_pc * D(str(fill_day))) if pnl_pc is not None else None
        return {
            "status": "KALSHI_BTC_SPOT_MAKER_SHADOW",
            "model_status": "UNVALIDATED_FAIR_VALUE_DO_NOT_TRADE",
            "decision_grade": False,
            "windows_observed": self.windows,
            "quotes_opened": self.quotes_opened,
            "quotes_repriced": self.quotes_repriced,
            "quotes_canceled": self.quotes_canceled,
            "public_trades": self.trades,
            "pessimistic_fills": self.fills,
            "settled_fills": self.settled_fills,
            "settled_net_pnl": str(self.settled_net_pnl),
            "net_pnl_per_settled_contract": str(pnl_pc) if pnl_pc is not None else None,
            "pessimistic_contracts_per_day_runrate": round(fill_day, 2),
            "projected_daily_pnl_at_observed_runrate": str(projected) if projected is not None else None,
            "active_quotes": active,
            "shadow_inventory_yes_equiv": inventory,
            "short_markout_cents": {
                str(h): round(100 * statistics.mean(v), 4) if v else None
                for h, v in self.markouts.items()
            },
            "fee_type": getattr(fee_meta, "fee_type", None),
            "fee_multiplier": getattr(fee_meta, "fee_multiplier", None),
            "fill_rule": "STRICT_TRADE_THROUGH_PLUS_DISPLAYED_QUEUE_EXHAUSTION_TOUCH_NEVER_FILLS",
            "pull_quotes_last_seconds": PULL_QUOTES_LAST_S,
            "read_only": True,
            "order_entry": False,
        }


async def discover_btc_market(http: httpx.AsyncClient) -> tuple[dict[str, Any], Decimal, datetime] | None:
    r = await http.get(
        f"{KALSHI_REST}/markets",
        params={"series_ticker": SERIES, "status": "open", "limit": 100, "mve_filter": "exclude"},
    )
    r.raise_for_status()
    now = datetime.now(timezone.utc)
    candidates = []
    for raw in r.json().get("markets") or []:
        if not isinstance(raw, dict):
            continue
        strike = kalshi_target(raw)
        expiry = _expiry(raw)
        if strike is None or expiry is None or expiry <= now:
            continue
        candidates.append((expiry, raw, strike))
    if not candidates:
        return None
    expiry, market, strike = min(candidates, key=lambda x: x[0])
    return market, strike, expiry


def subscription(ticker: str) -> dict[str, Any]:
    return {
        "id": 1,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta", "trade"],
            "market_ticker": ticker,
            "use_yes_price": False,
        },
    }


async def market_stream(ticker: str, queue: asyncio.Queue) -> None:
    private_key = load_private_key()
    headers = auth_headers(private_key, "GET", KALSHI_WS_SIGN_PATH, API_KEY_ID)
    book = Book(ticker)
    async with websockets.connect(KALSHI_WS, open_timeout=8.0, **_ws_header_kw(headers)) as ws:
        await ws.send(json.dumps(subscription(ticker)))
        async for raw in ws:
            receive_ms = int(time.time() * 1000)
            payload = json.loads(raw)
            if payload.get("type") == "error":
                raise RuntimeError(f"Kalshi WS error: {payload.get('msg')}")
            trade = parse_trade(payload, ticker)
            if trade is not None:
                await queue.put(("trade", receive_ms, trade, book))
                continue
            if book.apply(payload, receive_ms):
                await queue.put(("book", receive_ms, None, book))
    raise RuntimeError("Kalshi market-data WS ended")


def desired_quotes(
    *,
    fair_yes: float,
    half_spread: Decimal,
    book: Book,
    inventory: int,
    max_inventory: int,
) -> dict[str, tuple[Decimal, Decimal]]:
    """Return outcome -> (contract bid price, YES-equivalent display price)."""
    yes_bid_top, yes_ask_top = book.yes_top()
    if yes_bid_top is None or yes_ask_top is None or yes_bid_top >= yes_ask_top:
        return {}
    p = D(str(fair_yes))
    yes_bid = floor_tick(p - half_spread)
    yes_ask = ceil_tick(p + half_spread)
    # Never cross: hypothetical orders must remain passive.
    yes_bid = min(yes_bid, yes_ask_top - TICK)
    yes_ask = max(yes_ask, yes_bid_top + TICK)
    out: dict[str, tuple[Decimal, Decimal]] = {}
    if inventory < max_inventory and D("0") < yes_bid < D("1"):
        out["YES"] = (yes_bid, yes_bid)
    no_bid = D("1") - yes_ask
    if inventory > -max_inventory and D("0") < no_bid < D("1"):
        out["NO"] = (no_bid, yes_ask)
    return out


def refresh_quotes(
    *,
    active: dict[str, ActiveQuote],
    desired: dict[str, tuple[Decimal, Decimal]],
    book: Book,
    fair_yes: float,
    now_ms: int,
    stats: Stats,
) -> None:
    for outcome in list(active):
        target = desired.get(outcome)
        if target is None or target[0] != active[outcome].q.price:
            active.pop(outcome, None)
            stats.quotes_canceled += 1
    for outcome, (price, yes_display) in desired.items():
        if outcome in active:
            continue
        ahead = book.queue_ahead(outcome, price)
        active[outcome] = ActiveQuote(
            q=PessimisticQuote(outcome=outcome, price=price, queue_ahead=ahead),
            opened_ms=now_ms,
            fair_yes_open=fair_yes,
            yes_display_price=yes_display,
        )
        stats.quotes_opened += 1


def process_markouts(fills: list[Fill], fair_history: deque[tuple[int, float]], stats: Stats) -> None:
    if not fair_history:
        return
    for fill in fills:
        for horizon in MARKOUT_MS:
            if horizon in fill.markouts:
                continue
            due = fill.fill_ms + horizon
            sample = next(((ts, p) for ts, p in fair_history if ts >= due), None)
            if sample is None:
                continue
            _, p_yes = sample
            fair_contract = p_yes if fill.outcome == "YES" else 1.0 - p_yes
            markout = fair_contract - float(fill.contract_price)
            fill.markouts[horizon] = markout
            stats.markouts[horizon].append(markout)
            logger.info("SPOT_MAKER_MARKOUT %s", json.dumps({
                "ticker": fill.ticker,
                "outcome": fill.outcome,
                "fill_price": str(fill.contract_price),
                "yes_display_price": str(fill.yes_display_price),
                "horizon_ms": horizon,
                "fair_contract_at_markout": fair_contract,
                "markout": markout,
                "markout_cents": 100 * markout,
                "model": MODEL_NAME,
                "model_status": "UNVALIDATED",
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")))


async def settle_window(
    *,
    ticker: str,
    fills: list[Fill],
    checkpoints: dict[int, float],
    stats: Stats,
) -> None:
    async with httpx.AsyncClient(timeout=10.0) as http:
        for _ in range(180):
            r = await http.get(f"{KALSHI_REST}/markets/{ticker}")
            if r.status_code == 200:
                market = r.json().get("market") or {}
                result = str(market.get("result") or "").lower()
                if result in {"yes", "no"}:
                    result_yes = result == "yes"
                    window_pnl = D("0")
                    rows = []
                    for fill in fills:
                        pnl = settlement_pnl(
                            outcome=fill.outcome,
                            price=fill.contract_price,
                            quantity=QTY,
                            result_yes=result_yes,
                            maker_fee=fill.maker_fee,
                        )
                        window_pnl += pnl
                        rows.append({
                            "outcome": fill.outcome,
                            "contract_price": str(fill.contract_price),
                            "yes_display_price": str(fill.yes_display_price),
                            "fair_yes_at_fill": fill.fair_yes_fill,
                            "maker_fee": str(fill.maker_fee),
                            "settlement_net_pnl": str(pnl),
                            "settlement_markout_cents": 100 * float(pnl),
                        })
                    stats.settled_fills += len(fills)
                    stats.settled_net_pnl += window_pnl
                    logger.info("SPOT_MAKER_SETTLEMENT %s", json.dumps({
                        "ticker": ticker,
                        "kalshi_result": result,
                        "fills": rows,
                        "window_net_pnl": str(window_pnl),
                        "calibration_checkpoints": {str(k): v for k, v in checkpoints.items()},
                        "calibration_outcome_yes": result_yes,
                        "model": MODEL_NAME,
                        "model_status": "UNVALIDATED_PENDING_AGGREGATE_CALIBRATION",
                        "read_only": True,
                        "order_entry": False,
                    }, separators=(",", ":")))
                    return
            await asyncio.sleep(5)
    logger.warning("SPOT_MAKER_SETTLEMENT_TIMEOUT %s", json.dumps({
        "ticker": ticker, "fills": len(fills), "read_only": True, "order_entry": False,
    }, separators=(",", ":")))


async def capture_window(
    *,
    http: httpx.AsyncClient,
    tracker: SpotTracker,
    market: dict[str, Any],
    strike: Decimal,
    expiry: datetime,
    half_spread: Decimal,
    max_inventory: int,
    stats: Stats,
) -> tuple[list[Fill], dict[int, float]]:
    ticker = str(market.get("ticker") or "")
    fee_meta = await fetch_series_fee_metadata(http, SERIES)
    # Fail closed before opening even hypothetical quotes if maker fee is unmapped.
    try:
        kalshi_maker_fee(D("0.50"), QTY, fee_type=fee_meta.fee_type, fee_multiplier=D(fee_meta.fee_multiplier))
    except ValueError:
        stats.fee_failures += 1
        logger.error("SPOT_MAKER_FEE_UNMAPPED %s", json.dumps({
            "ticker": ticker, "fee_type": fee_meta.fee_type, "fee_multiplier": fee_meta.fee_multiplier,
            "read_only": True, "order_entry": False,
        }, separators=(",", ":")))
        return [], {}

    logger.info("SPOT_MAKER_WINDOW_START %s", json.dumps({
        "ticker": ticker,
        "strike": str(strike),
        "expiry": expiry.isoformat(),
        "half_spread": str(half_spread),
        "quantity": str(QTY),
        "max_inventory": max_inventory,
        "fee_type": fee_meta.fee_type,
        "fee_multiplier": fee_meta.fee_multiplier,
        "model": MODEL_NAME,
        "model_status": "UNVALIDATED_FAIR_VALUE_DO_NOT_TRADE",
        "fill_rule": "STRICT_TRADE_THROUGH_PLUS_QUEUE_EXHAUSTION_TOUCH_NEVER_FILLS",
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))

    queue: asyncio.Queue = asyncio.Queue(maxsize=100000)
    ws_task = asyncio.create_task(market_stream(ticker, queue))
    active: dict[str, ActiveQuote] = {}
    fills: list[Fill] = []
    fair_history: deque[tuple[int, float]] = deque(maxlen=5000)
    checkpoints: dict[int, float] = {}
    emitted_checkpoints: set[int] = set()
    inventory = 0
    book: Book | None = None
    next_heartbeat = time.monotonic() + HEARTBEAT_S
    stats.windows += 1

    try:
        while datetime.now(timezone.utc) < expiry:
            if ws_task.done():
                exc = ws_task.exception()
                raise RuntimeError(f"Kalshi WS stopped: {exc}")
            try:
                kind, receive_ms, trade, incoming_book = await asyncio.wait_for(queue.get(), timeout=0.02)
                book = incoming_book
            except asyncio.TimeoutError:
                kind = "tick"; receive_ms = int(time.time() * 1000); trade = None

            model = tracker.model(strike=strike, expiry=expiry)
            if model is not None:
                fair_history.append((int(model["ts_ms"]), float(model["fair_yes"])))
                seconds_left = float(model["seconds_left"])
                for cp in CALIBRATION_CHECKPOINTS_S:
                    if cp not in emitted_checkpoints and seconds_left <= cp:
                        checkpoints[cp] = float(model["fair_yes"])
                        emitted_checkpoints.add(cp)
                        logger.info("SPOT_FAIR_CALIBRATION_CHECKPOINT %s", json.dumps({
                            "ticker": ticker, "checkpoint_s": cp, **model,
                            "read_only": True, "order_entry": False,
                        }, separators=(",", ":")))
            else:
                seconds_left = max(0.0, expiry.timestamp() - time.time())

            if kind == "trade" and trade is not None:
                stats.trades += 1
                for outcome, aq in list(active.items()):
                    fill_qty = aq.q.apply_trade(
                        trade_yes_price=trade.yes_price,
                        trade_no_price=trade.no_price,
                        trade_quantity=trade.quantity,
                        taker_side=trade.taker_side,
                    )
                    if fill_qty <= 0:
                        continue
                    current_model = tracker.model(strike=strike, expiry=expiry)
                    if current_model is None:
                        # Pessimistic accounting: the fill still happened; missing
                        # fair value only blocks the markout, never deletes the fill.
                        fair_fill = aq.fair_yes_open
                    else:
                        fair_fill = float(current_model["fair_yes"])
                    try:
                        fee = kalshi_maker_fee(
                            aq.q.price, fill_qty,
                            fee_type=fee_meta.fee_type,
                            fee_multiplier=D(fee_meta.fee_multiplier),
                        )
                    except ValueError:
                        stats.fee_failures += 1
                        fee = D("0.99")  # fail painfully, never flattering
                    fill = Fill(
                        ticker=ticker,
                        outcome=outcome,
                        contract_price=aq.q.price,
                        yes_display_price=aq.yes_display_price,
                        fill_ms=receive_ms,
                        fair_yes_fill=fair_fill,
                        maker_fee=fee,
                        queue_ahead_open=aq.q.queue_ahead,
                    )
                    fills.append(fill)
                    stats.fills += 1
                    inventory += 1 if outcome == "YES" else -1
                    active.pop(outcome, None)
                    logger.info("SPOT_MAKER_PESSIMISTIC_FILL %s", json.dumps({
                        "ticker": ticker,
                        "outcome": outcome,
                        "contract_price": str(fill.contract_price),
                        "yes_display_price": str(fill.yes_display_price),
                        "trade_yes_price": str(trade.yes_price),
                        "trade_no_price": str(trade.no_price),
                        "trade_quantity": str(trade.quantity),
                        "taker_side": trade.taker_side,
                        "fair_yes_at_fill": fair_fill,
                        "maker_fee": str(fee),
                        "shadow_inventory_yes_equiv": inventory,
                        "fill_rule": "STRICT_TRADE_THROUGH_PLUS_QUEUE_EXHAUSTION",
                        "model_status": "UNVALIDATED",
                        "read_only": True,
                        "order_entry": False,
                    }, separators=(",", ":")))

            process_markouts(fills, fair_history, stats)

            # Quote only with live book + valid independent fair model, and pull all
            # quotes before the high-gamma final minute.
            now_ms = int(time.time() * 1000)
            valid = (
                book is not None
                and model is not None
                and book.last_receive_ms > 0
                and now_ms - book.last_receive_ms <= MAX_BOOK_AGE_MS
                and seconds_left > PULL_QUOTES_LAST_S
            )
            if not valid:
                if active:
                    stats.quotes_canceled += len(active)
                    active.clear()
            else:
                desired = desired_quotes(
                    fair_yes=float(model["fair_yes"]),
                    half_spread=half_spread,
                    book=book,
                    inventory=inventory,
                    max_inventory=max_inventory,
                )
                before = {k: v.q.price for k, v in active.items()}
                refresh_quotes(
                    active=active,
                    desired=desired,
                    book=book,
                    fair_yes=float(model["fair_yes"]),
                    now_ms=now_ms,
                    stats=stats,
                )
                after = {k: v.q.price for k, v in active.items()}
                stats.quotes_repriced += sum(1 for k in before if k in after and before[k] != after[k])

            if time.monotonic() >= next_heartbeat:
                logger.info("SPOT_MAKER_SHADOW_SUMMARY %s", json.dumps(
                    stats.summary(active=len(active), inventory=inventory, fee_meta=fee_meta),
                    separators=(",", ":"), default=str,
                ))
                next_heartbeat = time.monotonic() + HEARTBEAT_S
    finally:
        ws_task.cancel()
        await asyncio.gather(ws_task, return_exceptions=True)
        active.clear()

    logger.info("SPOT_MAKER_WINDOW_END %s", json.dumps({
        "ticker": ticker,
        "pessimistic_fills": len(fills),
        "shadow_inventory_yes_equiv": inventory,
        "calibration_checkpoints": checkpoints,
        "model_status": "UNVALIDATED_PENDING_SETTLEMENT_CALIBRATION",
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))
    return fills, checkpoints


async def run(*, half_spread_cents: float, max_inventory: int) -> None:
    if half_spread_cents <= 0:
        raise ValueError("half spread must be positive")
    if max_inventory < 1:
        raise ValueError("max inventory must be >=1")
    if not API_KEY_ID:
        raise RuntimeError("KALSHI_API_KEY_ID is required for read-only market-data WS")
    load_private_key()  # fail fast; credentials are only for market-data handshake

    half_spread = D(str(half_spread_cents)) / D("100")
    tracker = SpotTracker()
    stats = Stats()
    settlement_tasks: set[asyncio.Task[None]] = set()

    async with httpx.AsyncClient(timeout=15.0) as http:
        spot_tasks = [
            asyncio.create_task(coinbase_stream(tracker.state)),
            asyncio.create_task(gemini_spot_poller(http, tracker.state, 1.0)),
            asyncio.create_task(tracker.sampler()),
        ]
        try:
            while True:
                try:
                    discovered = await discover_btc_market(http)
                    if discovered is None:
                        await asyncio.sleep(2)
                        continue
                    market, strike, expiry = discovered
                    fills, checkpoints = await capture_window(
                        http=http,
                        tracker=tracker,
                        market=market,
                        strike=strike,
                        expiry=expiry,
                        half_spread=half_spread,
                        max_inventory=max_inventory,
                        stats=stats,
                    )
                    task = asyncio.create_task(settle_window(
                        ticker=str(market.get("ticker") or ""),
                        fills=fills,
                        checkpoints=checkpoints,
                        stats=stats,
                    ))
                    settlement_tasks.add(task)
                    task.add_done_callback(settlement_tasks.discard)
                    await asyncio.sleep(0.2)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("SPOT_MAKER_SHADOW_ERROR %s", json.dumps({
                        "error_type": type(exc).__name__, "error": str(exc)[:300],
                        "read_only": True, "order_entry": False,
                    }, separators=(",", ":")))
                    await asyncio.sleep(2)
        finally:
            for t in spot_tasks:
                t.cancel()
            for t in settlement_tasks:
                t.cancel()
            await asyncio.gather(*spot_tasks, *settlement_tasks, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--half-spread-cents", type=float, default=2.0)
    parser.add_argument("--max-inventory", type=int, default=5)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(half_spread_cents=args.half_spread_cents, max_inventory=args.max_inventory))


if __name__ == "__main__":
    main()
