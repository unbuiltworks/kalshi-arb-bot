"""One-shot q=1 maker-triggered hedge LIVE_PROBE for Gemini <-> Kalshi 15m.

This is deliberately a mechanics probe, NOT certified arbitrage. The current
Gemini/Kalshi 15-minute contracts remain INDEX_MISMATCH_RESEARCH_ONLY.

Live order entry requires all of:
- ENABLE_GEMINI_KALSHI_LIVE_PROBES=YES
- MAKER_LIVE_PROBE_LIVE=YES
- MAKER_LIVE_PROBE_ARM=YES
- a non-expired MAKER_LIVE_PROBE_ARM_DEADLINE_UTC
- a unique MAKER_LIVE_PROBE_RUN_ID
- a persistent Railway volume for the one-shot claim/result

The run posts at most one q<=1 Kalshi post-only GTC maker order. It never submits
the Gemini leg first. A confirmed Kalshi fill triggers an immediate Gemini FOK
hedge. If that hedge fails unambiguously, the exact unmatched Kalshi quantity is
flattened with reduce-only IOC and the run hard-stops. Ambiguous Gemini execution
also hard-stops rather than guessing.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import re
import time
import uuid
from typing import Any

import httpx
import websockets

from config.settings import API_KEY_ID
from src.auth import auth_headers, load_private_key
from src.gemini_kalshi_btc_ws_shadow import KALSHI_WS, KALSHI_WS_SIGN_PATH, _ws_header_kw
from src.gemini_kalshi_live_probe_guard import live_probe_authorized
from src.gemini_kalshi_live_service import _preflight
from src.gemini_kalshi_maker_shadow import (
    ASSET_SERIES,
    D,
    MakerStats,
    PairDiscoveryCache,
    TopOfBook,
    _gemini_stream,
    _kalshi_stream,
    desired_maker_price,
    fetch_series_fee_metadata,
    maker_side_book,
    max_safe_maker_price,
    quote_economics,
)
from src.gemini_kalshi_probe_transport import GeminiTradingSession, KalshiV2Executor

logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_live_probe_q1")

ASSET = "XRP"
TARGET_PROFIT = D("0.03")
QTY = D("1")
POLICY = "MAX_SAFE"
SETTLEMENT_STATUS = "INDEX_MISMATCH_RESEARCH_ONLY"
MAX_OPEN_SNAPSHOT_SKEW_MS = 50
MAX_OPEN_BOOK_AGE_MS = 250
MAX_RESTING_GEMINI_AGE_MS = 250
MAX_RESTING_KALSHI_AGE_MS = 500
REST_SECONDS = 10.0
HEDGE_LIMIT_CUSHION = D("0.03")
VOLUME_ENV = "RAILWAY_VOLUME_MOUNT_PATH"
RUN_ID_ENV = "MAKER_LIVE_PROBE_RUN_ID"
LIVE_ENV = "MAKER_LIVE_PROBE_LIVE"
ARM_ENV = "MAKER_LIVE_PROBE_ARM"
DEADLINE_ENV = "MAKER_LIVE_PROBE_ARM_DEADLINE_UTC"


@dataclass(frozen=True)
class Candidate:
    direction: str
    maker_outcome: str
    hedge_outcome: str
    maker_price: Decimal
    hedge_price: Decimal
    locked_profit: Decimal
    best_bid: Decimal
    best_ask: Decimal
    queue_ahead: Decimal
    observed_ms: int


def _yes(name: str) -> bool:
    return os.getenv(name, "NO").strip().upper() == "YES"


def _clean_run_id(raw: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", str(raw).strip()).strip("-")
    if not value or len(value) > 32:
        raise ValueError("maker live probe run id must be 1-32 safe characters")
    return value


def _parse_deadline(raw: str) -> datetime:
    if not raw.strip():
        raise ValueError(f"{DEADLINE_ENV} is required when armed")
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _state_dir(*, required: bool) -> Path | None:
    raw = os.getenv(VOLUME_ENV, "").strip()
    if not raw:
        if required:
            raise RuntimeError("armed maker live probe requires a Railway persistent volume")
        return None
    path = Path(raw)
    if not path.is_absolute():
        raise RuntimeError("Railway volume mount path must be absolute")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _paths(run_id: str, *, required: bool = True) -> tuple[Path, Path, Path] | None:
    root = _state_dir(required=required)
    if root is None:
        return None
    safe_id = _clean_run_id(run_id)
    return (
        root / f"maker-{safe_id}.arm",
        root / f"maker-{safe_id}.attempt.json",
        root / f"maker-{safe_id}.result.json",
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"), default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _live_software_authorized() -> bool:
    return bool(_yes(LIVE_ENV) and _yes(ARM_ENV) and live_probe_authorized(True))


def _maybe_create_arm(run_id: str) -> Path | None:
    if not _live_software_authorized():
        return None
    deadline = _parse_deadline(os.getenv(DEADLINE_ENV, ""))
    if datetime.now(timezone.utc) > deadline:
        raise RuntimeError("maker live probe arm deadline has expired")
    paths = _paths(run_id, required=True)
    assert paths is not None
    arm, claim, result = paths
    if claim.exists() or result.exists():
        raise RuntimeError("maker live probe run id was already claimed/completed")
    if not arm.exists():
        fd = os.open(arm, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (run_id + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    return arm


def _claim_before_order(run_id: str) -> Path:
    paths = _paths(run_id, required=True)
    assert paths is not None
    arm, claim, result = paths
    if result.exists() or claim.exists() or not arm.exists():
        raise PermissionError("maker live probe one-shot authorization unavailable")
    try:
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PermissionError("maker live probe run already claimed") from exc
    try:
        payload = {
            "run_id": run_id,
            "claimed_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "ATTEMPT_CLAIMED_BEFORE_KALSHI_POST",
            "asset": ASSET,
            "quantity_max": "1",
            "target_profit": str(TARGET_PROFIT),
            "settlement_status": SETTLEMENT_STATUS,
        }
        os.write(fd, json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        arm.unlink()
    except FileNotFoundError:
        pass
    return result


def kalshi_maker_payload(
    *, ticker: str, direction: str, maker_price: Decimal, client_order_id: str,
) -> dict[str, Any]:
    if D(maker_price) <= 0 or D(maker_price) >= 1:
        raise ValueError("maker price must be inside (0,1)")
    if direction == "KALSHI_YES_GEMINI_NO":
        side = "bid"
        yes_price = D(maker_price)
    elif direction == "GEMINI_YES_KALSHI_NO":
        side = "ask"
        yes_price = D("1") - D(maker_price)
    else:
        raise ValueError(f"unsupported direction={direction}")
    return {
        "ticker": ticker,
        "client_order_id": client_order_id,
        "side": side,
        "count": "1.00",
        "price": str(yes_price.quantize(D("0.0001"))),
        "time_in_force": "good_till_canceled",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": True,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }


def gemini_hedge_payload(
    *, symbol: str, direction: str, ask_price: Decimal, quantity: Decimal,
    client_order_id: str, cushion: Decimal = HEDGE_LIMIT_CUSHION,
) -> dict[str, Any]:
    if D(quantity) <= 0 or D(quantity) > QTY:
        raise ValueError("hedge quantity must be in (0,1]")
    if direction == "KALSHI_YES_GEMINI_NO":
        outcome = "NO"
    elif direction == "GEMINI_YES_KALSHI_NO":
        outcome = "YES"
    else:
        raise ValueError(f"unsupported direction={direction}")
    limit = min(D("0.99"), D(ask_price) + D(cushion))
    if limit <= 0:
        raise ValueError("invalid Gemini hedge limit")
    return {
        "symbol": symbol,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "FOK",
        "price": str(limit.quantize(D("0.0001"))),
        "quantity": str(D(quantity)),
        "eventOutcome": outcome,
        "clientOrderId": client_order_id,
    }


def candidate_from_books(*, gemini: TopOfBook, kalshi: TopOfBook, fee_meta, now_ms: int) -> Candidate | None:
    choices: list[Candidate] = []
    for direction in ("GEMINI_YES_KALSHI_NO", "KALSHI_YES_GEMINI_NO"):
        maker_outcome = "NO" if direction == "GEMINI_YES_KALSHI_NO" else "YES"
        hedge_outcome = "YES" if direction == "GEMINI_YES_KALSHI_NO" else "NO"
        best_bid, bid_size, best_ask = maker_side_book(kalshi, maker_outcome)
        economic_max = max_safe_maker_price(
            direction=direction,
            gemini=gemini,
            fee_meta=fee_meta,
            target_profit=TARGET_PROFIT,
        )
        if economic_max is None:
            continue
        maker_price = desired_maker_price(
            policy=POLICY,
            best_bid=best_bid,
            best_ask=best_ask,
            economic_max=economic_max,
        )
        if maker_price is None:
            continue
        econ = quote_economics(
            direction=direction,
            maker_price=maker_price,
            gemini=gemini,
            fee_meta=fee_meta,
        )
        if econ is None or econ.locked_profit < TARGET_PROFIT:
            continue
        queue_ahead = bid_size if maker_price == best_bid else D("0")
        choices.append(Candidate(
            direction=direction,
            maker_outcome=maker_outcome,
            hedge_outcome=hedge_outcome,
            maker_price=maker_price,
            hedge_price=econ.hedge_price,
            locked_profit=econ.locked_profit,
            best_bid=best_bid,
            best_ask=best_ask,
            queue_ahead=queue_ahead,
            observed_ms=now_ms,
        ))
    if not choices:
        return None
    # Prefer a quote that improves the displayed bid (zero observed queue ahead),
    # then larger modeled profit. This is a one-shot mechanics probe, not optimization.
    return max(choices, key=lambda c: (c.queue_ahead == 0, c.locked_profit, c.maker_price))


async def _account_stream(ticker: str, queue: asyncio.Queue, ready: asyncio.Event) -> None:
    private_key = load_private_key()
    headers = auth_headers(private_key, "GET", KALSHI_WS_SIGN_PATH, API_KEY_ID)
    async with websockets.connect(KALSHI_WS, open_timeout=5.0, **_ws_header_kw(headers)) as ws:
        await ws.send(json.dumps({
            "id": 91,
            "cmd": "subscribe",
            "params": {"channels": ["fill", "user_orders"], "market_tickers": [ticker]},
        }))
        subscribed: set[str] = set()
        async for raw in ws:
            payload = json.loads(raw)
            typ = str(payload.get("type") or "")
            if typ == "error":
                raise RuntimeError(f"Kalshi private WS error: {payload.get('msg')}")
            if typ == "subscribed":
                channel = str((payload.get("msg") or {}).get("channel") or "")
                subscribed.add(channel)
                if {"fill", "user_orders"}.issubset(subscribed):
                    ready.set()
                continue
            if typ in {"fill", "user_order"}:
                await queue.put(payload)


async def _cancel_order(kalshi: KalshiV2Executor, order_id: str) -> dict[str, Any]:
    response = await kalshi.client.delete(f"/portfolio/events/orders/{order_id}")
    body: Any = None
    try:
        body = response.json()
    except Exception:
        body = {"text": response.text[:200]}
    if response.status_code not in {200, 404}:
        raise RuntimeError(f"Kalshi cancel failed HTTP {response.status_code}: {body}")
    return {"http_status": response.status_code, "body": body}


async def _fills_for_order(kalshi: KalshiV2Executor, order_id: str) -> list[dict[str, Any]]:
    response = await kalshi.client.get("/portfolio/fills", params={"order_id": order_id, "limit": 100})
    response.raise_for_status()
    return list(response.json().get("fills") or [])


def _filled_qty(rows: list[dict[str, Any]]) -> Decimal:
    total = D("0")
    for row in rows:
        total += D(str(row.get("count_fp") or "0"))
    return total


async def _flatten_kalshi_qty(
    kalshi: KalshiV2Executor, original_payload: dict[str, Any], quantity: Decimal,
) -> dict[str, Any]:
    if quantity <= 0:
        return {"status": "NOT_NEEDED", "quantity": "0"}
    if original_payload["side"] == "bid":
        side, price = "ask", "0.0100"
    else:
        side, price = "bid", "0.9900"
    payload = {
        "ticker": original_payload["ticker"],
        "client_order_id": f"makerflat-{uuid.uuid4().hex[:20]}",
        "side": side,
        "count": str(quantity),
        "price": price,
        "time_in_force": "immediate_or_cancel",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": True,
        "subaccount": 0,
        "exchange_index": 0,
    }
    result = await kalshi.place(payload)
    return result.as_dict()


async def _hedge_after_fill(
    *, gemini: GeminiTradingSession, kalshi: KalshiV2Executor,
    candidate: Candidate, gemini_book: TopOfBook | None, symbol: str,
    kalshi_payload: dict[str, Any], fill_qty: Decimal, run_id: str,
) -> dict[str, Any]:
    if fill_qty <= 0:
        raise ValueError("fill_qty must be positive")
    if gemini_book is None:
        flatten = await _flatten_kalshi_qty(kalshi, kalshi_payload, fill_qty)
        return {"outcome": "NO_GEMINI_BOOK_FLATTENED", "gemini": None, "flatten": flatten, "hard_halt": True}
    if candidate.hedge_outcome == "YES":
        ask_price = gemini_book.yes_ask
        ask_size = gemini_book.yes_ask_size
    else:
        ask_price = gemini_book.no_ask
        ask_size = gemini_book.no_ask_size
    if ask_size < fill_qty:
        flatten = await _flatten_kalshi_qty(kalshi, kalshi_payload, fill_qty)
        return {"outcome": "INSUFFICIENT_GEMINI_DEPTH_FLATTENED", "gemini": None, "flatten": flatten, "hard_halt": True}

    payload = gemini_hedge_payload(
        symbol=symbol,
        direction=candidate.direction,
        ask_price=ask_price,
        quantity=fill_qty,
        client_order_id=f"makerhedge-{run_id}-g",
    )
    result = await gemini.place_fok(payload, terminal_timeout=2.0)
    if result.status == "UNKNOWN":
        return {"outcome": "GEMINI_HEDGE_AMBIGUOUS", "gemini": result.as_dict(), "flatten": None, "hard_halt": True}
    gemini_filled = D(result.filled_quantity)
    if gemini_filled == fill_qty:
        return {"outcome": "MAKER_FILLED_HEDGE_FILLED", "gemini": result.as_dict(), "flatten": None, "hard_halt": False}
    residual = max(D("0"), fill_qty - gemini_filled)
    flatten = await _flatten_kalshi_qty(kalshi, kalshi_payload, residual)
    return {
        "outcome": "GEMINI_HEDGE_NOT_FULLY_FILLED_KALSHI_RESIDUAL_FLATTENED",
        "gemini": result.as_dict(),
        "flatten": flatten,
        "hard_halt": True,
    }


async def _wait_candidate(*, pair, fee_meta, timeout_s: float = 120.0):
    queue: asyncio.Queue = asyncio.Queue(maxsize=50_000)
    stats = MakerStats(asset=ASSET)
    tasks = [
        asyncio.create_task(_gemini_stream(pair.gemini_symbol, queue, stats)),
        asyncio.create_task(_kalshi_stream(pair.kalshi_ticker, queue, stats)),
    ]
    g: TopOfBook | None = None
    k: TopOfBook | None = None
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            kind, receive_ms, payload = await asyncio.wait_for(queue.get(), timeout=min(1.0, max(0.01, deadline - time.monotonic())))
            if kind == "gemini":
                g = payload
            elif kind == "kalshi":
                k = payload
            if g is None or k is None:
                continue
            now_ms = int(time.time() * 1000)
            if abs(g.ts_ms - k.ts_ms) > MAX_OPEN_SNAPSHOT_SKEW_MS:
                continue
            if max(now_ms - g.ts_ms, now_ms - k.ts_ms) > MAX_OPEN_BOOK_AGE_MS:
                continue
            candidate = candidate_from_books(gemini=g, kalshi=k, fee_meta=fee_meta, now_ms=now_ms)
            if candidate is not None:
                return candidate, g, k, queue, tasks
        return None, g, k, queue, tasks
    except Exception:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def run_once() -> dict[str, Any]:
    run_id = _clean_run_id(os.getenv(RUN_ID_ENV, "")) if os.getenv(RUN_ID_ENV, "").strip() else "disarmed"
    armed = _live_software_authorized()
    logger.warning(
        "MAKER_LIVE_PROBE_BOOT %s",
        json.dumps({
            "mode": "LIVE_PROBE",
            "asset": ASSET,
            "quantity_max": "1",
            "target_profit": str(TARGET_PROFIT),
            "policy": POLICY,
            "settlement_status": SETTLEMENT_STATUS,
            "live_software_authorized": armed,
            "order_entry": False,
        }, separators=(",", ":")),
    )
    if not armed:
        return {"outcome": "DISARMED", "order_entry": False, "settlement_status": SETTLEMENT_STATUS}

    arm_path = _maybe_create_arm(run_id)
    if arm_path is None:
        raise RuntimeError("maker live probe could not create persistent arm")

    gemini = GeminiTradingSession()
    kalshi = KalshiV2Executor()
    market_tasks: list[asyncio.Task] = []
    account_task: asyncio.Task | None = None
    result_path: Path | None = None
    try:
        await gemini.connect()
        preflight = await _preflight(kalshi, live_requested=True, token_path=arm_path)
        if not bool(preflight.get("armed")):
            raise RuntimeError("maker live probe preflight is not armed")

        async with httpx.AsyncClient(timeout=20.0) as http:
            fee_meta = await fetch_series_fee_metadata(http, ASSET_SERIES[ASSET])
            # Fail closed unless maker fee mapping is understood by shared economics.
            _ = max_safe_maker_price(
                direction="GEMINI_YES_KALSHI_NO",
                gemini=TopOfBook(D("0.49"), D("1"), D("0.51"), D("1"), D("0.49"), D("0.51"), D("1"), int(time.time()*1000)),
                fee_meta=fee_meta,
                target_profit=TARGET_PROFIT,
            )
            discovery = PairDiscoveryCache(ttl_s=1.0)
            pair = None
            pair_deadline = time.monotonic() + 30.0
            while pair is None and time.monotonic() < pair_deadline:
                pair = await discovery.current_pair(http, ASSET)
                if pair is None:
                    await asyncio.sleep(0.25)
            if pair is None:
                raise RuntimeError("no current XRP pair discovered")

        candidate, g, k, market_queue, market_tasks = await _wait_candidate(pair=pair, fee_meta=fee_meta)
        if candidate is None:
            return {"outcome": "NO_QUALIFYING_MAKER_CANDIDATE", "order_entry": False, "settlement_status": SETTLEMENT_STATUS}

        account_queue: asyncio.Queue = asyncio.Queue()
        account_ready = asyncio.Event()
        account_task = asyncio.create_task(_account_stream(pair.kalshi_ticker, account_queue, account_ready))
        await asyncio.wait_for(account_ready.wait(), timeout=5.0)

        kalshi_client_id = f"makerprobe-{run_id}-k"
        k_payload = kalshi_maker_payload(
            ticker=pair.kalshi_ticker,
            direction=candidate.direction,
            maker_price=candidate.maker_price,
            client_order_id=kalshi_client_id,
        )

        # One-shot claim occurs immediately before the first mutation.
        result_path = _claim_before_order(run_id)
        response = await kalshi.client.post("/portfolio/events/orders", json=k_payload)
        if response.status_code != 201:
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"text": response.text[:300]}
            result = {
                "outcome": "KALSHI_POST_REJECTED",
                "http_status": response.status_code,
                "response": body,
                "candidate": candidate.__dict__,
                "settlement_status": SETTLEMENT_STATUS,
                "mode": "LIVE_PROBE",
            }
            _write_json_atomic(result_path, result)
            return result

        created = response.json()
        order_id = str(created.get("order_id") or "")
        if not order_id:
            raise RuntimeError("Kalshi create-order response missing order_id")
        immediate_fill = D(str(created.get("fill_count") or "0"))
        logger.warning("MAKER_LIVE_PROBE_KALSHI_POSTED %s", json.dumps({
            "run_id": run_id,
            "order_id": order_id,
            "client_order_id": kalshi_client_id,
            "ticker": pair.kalshi_ticker,
            "direction": candidate.direction,
            "maker_price": str(candidate.maker_price),
            "modeled_locked_profit": str(candidate.locked_profit),
            "queue_ahead": str(candidate.queue_ahead),
            "post_only": True,
            "time_in_force": "good_till_canceled",
            "fill_count_on_ack": str(immediate_fill),
            "settlement_status": SETTLEMENT_STATUS,
        }, separators=(",", ":")))

        if immediate_fill > 0:
            hedge = await _hedge_after_fill(
                gemini=gemini, kalshi=kalshi, candidate=candidate, gemini_book=g,
                symbol=pair.gemini_symbol, kalshi_payload=k_payload,
                fill_qty=min(QTY, immediate_fill), run_id=run_id,
            )
            result = {"outcome": hedge["outcome"], "kalshi_order": created, "hedge": hedge, "mode": "LIVE_PROBE", "settlement_status": SETTLEMENT_STATUS}
            _write_json_atomic(result_path, result)
            return result

        started = time.monotonic()
        fill_qty = D("0")
        cancel_reason: str | None = None
        while time.monotonic() - started < REST_SECONDS:
            market_get = asyncio.create_task(market_queue.get())
            account_get = asyncio.create_task(account_queue.get())
            done, pending = await asyncio.wait({market_get, account_get}, timeout=0.25, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if not done:
                now_ms = int(time.time() * 1000)
            else:
                task = next(iter(done))
                item = task.result()
                if isinstance(item, tuple) and len(item) == 3:
                    kind, receive_ms, payload = item
                    if kind == "gemini":
                        g = payload
                    elif kind == "kalshi":
                        k = payload
                    now_ms = receive_ms
                else:
                    evt = item
                    typ = str(evt.get("type") or "")
                    msg = evt.get("msg") or {}
                    if typ == "fill" and str(msg.get("order_id") or "") == order_id:
                        fill_qty += D(str(msg.get("count_fp") or "0"))
                        maker_flag = not bool(msg.get("is_taker"))
                        logger.warning("MAKER_LIVE_PROBE_FILL %s", json.dumps({
                            "run_id": run_id, "order_id": order_id,
                            "fill_qty": str(fill_qty), "is_maker": maker_flag,
                            "yes_price": msg.get("yes_price_dollars"),
                            "fee_cost": msg.get("fee_cost"),
                        }, separators=(",", ":")))
                        break
                    if typ == "user_order" and str(msg.get("client_order_id") or "") == kalshi_client_id:
                        observed_fill = D(str(msg.get("fill_count_fp") or "0"))
                        if observed_fill > fill_qty:
                            fill_qty = observed_fill
                        if fill_qty > 0:
                            break
                    now_ms = int(time.time() * 1000)

            if fill_qty > 0:
                break
            if g is None or k is None:
                cancel_reason = "MISSING_BOOK"
                break
            g_age = max(0, now_ms - int(g.ts_ms))
            k_age = max(0, now_ms - int(k.ts_ms))
            if g_age > MAX_RESTING_GEMINI_AGE_MS:
                cancel_reason = "GEMINI_FEED_STALE"
                break
            if k_age > MAX_RESTING_KALSHI_AGE_MS:
                cancel_reason = "KALSHI_FEED_STALE"
                break
            econ = quote_economics(
                direction=candidate.direction,
                maker_price=candidate.maker_price,
                gemini=g,
                fee_meta=fee_meta,
            )
            if econ is None or econ.locked_profit < TARGET_PROFIT:
                cancel_reason = "TARGET_NO_LONGER_HOLDS"
                break

        if fill_qty <= 0:
            cancel_reason = cancel_reason or "REST_TIMEOUT"
            cancel = await _cancel_order(kalshi, order_id)
            # Always reconcile fills after cancel to close the fill/cancel race.
            fills = await _fills_for_order(kalshi, order_id)
            fill_qty = min(QTY, _filled_qty(fills))
            if fill_qty <= 0:
                result = {
                    "outcome": "MAKER_ORDER_CANCELED_NO_FILL",
                    "cancel_reason": cancel_reason,
                    "cancel": cancel,
                    "kalshi_order": created,
                    "mode": "LIVE_PROBE",
                    "settlement_status": SETTLEMENT_STATUS,
                }
                _write_json_atomic(result_path, result)
                return result
            logger.warning("MAKER_LIVE_PROBE_CANCEL_RACE_FILL run_id=%s order_id=%s fill_qty=%s", run_id, order_id, fill_qty)

        hedge = await _hedge_after_fill(
            gemini=gemini, kalshi=kalshi, candidate=candidate, gemini_book=g,
            symbol=pair.gemini_symbol, kalshi_payload=k_payload,
            fill_qty=min(QTY, fill_qty), run_id=run_id,
        )
        # Cancel any unfilled remainder after a partial maker fill.
        try:
            await _cancel_order(kalshi, order_id)
        except Exception as exc:
            logger.error("MAKER_LIVE_PROBE_REMAINDER_CANCEL_ERROR type=%s error=%s", type(exc).__name__, str(exc)[:180])
        result = {
            "outcome": hedge["outcome"],
            "kalshi_order": created,
            "kalshi_fill_qty": str(fill_qty),
            "hedge": hedge,
            "mode": "LIVE_PROBE",
            "counts_as_realized_arb": False,
            "settlement_status": SETTLEMENT_STATUS,
        }
        _write_json_atomic(result_path, result)
        return result
    finally:
        for task in market_tasks:
            task.cancel()
        await asyncio.gather(*market_tasks, return_exceptions=True)
        if account_task is not None:
            account_task.cancel()
            await asyncio.gather(account_task, return_exceptions=True)
        await gemini.close()
        await kalshi.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    result = asyncio.run(run_once())
    logger.warning("MAKER_LIVE_PROBE_RESULT %s", json.dumps(result, separators=(",", ":"), default=str))
    if result.get("hedge", {}).get("hard_halt"):
        raise SystemExit(70)


if __name__ == "__main__":
    main()
