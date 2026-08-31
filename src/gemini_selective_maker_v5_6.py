"""Gemini BTC15m selective maker V5.6.

Live strategy port of the frozen L5 tape-contra rule onto Gemini BTC 15-minute
Up/Down contracts.  V5.6 deliberately separates strategy selection from the
V5.5 execution/risk substrate:

- rolling 30-trade public tape, share weighted;
- quote the maker outcome opposite a >10% tape imbalance;
- selected maker quote must be >=30c;
- no per-trade pair-edge requirement;
- integer q=1..10 must be valid on the live contract grid;
- a total rescue failure must fit the configured per-campaign loss ceiling;
- the full possible maker fill must be flattenable now inside the normal rescue cap;
- MOC maker-only entry, exact cancel/reconcile, V5.3 grace, V5.5 bounded FOK;
- stop opening new exposure 90s before BTC15m expiry;
- persistent V5.4 UTC-day risk ledger and an independent session drawdown stop.

Liquidity rewards and maker rebates are never included in admission or realized P&L.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import json
import os
import re
import time
from typing import Any, Mapping

import httpx
import websockets

from src import gemini_adaptive_maker_contract_grid_v5_3 as grid
from src import gemini_adaptive_maker_daily_risk_v5_4 as daily
from src import gemini_adaptive_maker_executor_v5_3 as v53
from src import gemini_adaptive_maker_executor_v5_5 as v55
from src import gemini_q10_rescue_policy as rescue
from src.btc_spot_toxicity_guard import BtcSpotGuard
from src.gemini_prediction_depth_cache_v5_3 import DepthSnapshot, GeminiDepth5Cache

ZERO = D("0")
ONE = D("1")
REST = "https://api.gemini.com"
WS = "wss://ws.gemini.com"
EVENT_RE = re.compile(r"^BTC15M(\d{10})$")
TAPE_WINDOW = 30
TAPE_THRESHOLD = D("0.10")
PRICE_FLOOR = D("0.30")
STOP_NEW_SECONDS = 90.0
MAX_WINDOW_SECONDS = 900.0
SIGNAL_WAIT_POLL = 0.10
QUOTE_CHECK_SECONDS = 0.05
QUOTE_REPRICE_MIN_AGE = 2.0
MAX_SESSION_SECONDS_ENV = "GEMINI_SELECTIVE_V5_6_MAX_SESSION_SECONDS"
MAX_WINDOWS_ENV = "GEMINI_SELECTIVE_V5_6_MAX_WINDOWS"
SESSION_LOSS_ENV = "GEMINI_SELECTIVE_V5_6_MAX_SESSION_LOSS_USD"
DEFAULT_MAX_SESSION_SECONDS = 3600.0
DEFAULT_MAX_WINDOWS = 4
DEFAULT_SESSION_LOSS = D("5.00")
CLASSIFICATION = "LIVE_GEMINI_BTC15M_SELECTIVE_MAKER_V5_6_OPEN_NOT_REALIZED"


def emit(stage: str, **fields: Any) -> None:
    v53.v5.v1.emit(f"V5_6_{stage}", **fields)


def _positive_float_env(name: str, default: float, *, low: float, high: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not (low <= value <= high):
        raise RuntimeError(f"{name} must be between {low} and {high}")
    return value


def max_session_seconds() -> float:
    return _positive_float_env(MAX_SESSION_SECONDS_ENV, DEFAULT_MAX_SESSION_SECONDS, low=60.0, high=14400.0)


def max_windows() -> int:
    n = int(os.getenv(MAX_WINDOWS_ENV, str(DEFAULT_MAX_WINDOWS)).strip())
    if not 1 <= n <= 20:
        raise RuntimeError(f"{MAX_WINDOWS_ENV} must be between 1 and 20")
    return n


def max_session_loss() -> D:
    value = D(os.getenv(SESSION_LOSS_ENV, str(DEFAULT_SESSION_LOSS)))
    if value <= ZERO:
        raise RuntimeError(f"{SESSION_LOSS_ENV} must be positive")
    return value


def _iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


@dataclass(frozen=True)
class Event:
    ticker: str
    symbol: str
    expiry_ms: int
    quantity_minimum: D
    quantity_increment: D
    price_increment: D

    @property
    def seconds_to_expiry(self) -> float:
        return (self.expiry_ms - int(time.time() * 1000)) / 1000.0


async def discover_btc15m() -> Event | None:
    """Return the current BTC15m window, never a future pre-open contract."""
    found: list[Event] = []
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True,
                                 headers={"User-Agent": "arb-gemini-btc15m-selective-v5.6"}) as http:
        offset = 0
        while offset < 5000:
            response = await http.get(
                f"{REST}/v1/prediction-markets/events",
                params=[("status", "active"), ("category", "Crypto"), ("limit", "500"), ("offset", str(offset))],
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") or [] if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                ticker = str(row.get("ticker") or "").strip().upper()
                if not EVENT_RE.match(ticker):
                    continue
                for contract in row.get("contracts") or []:
                    if not isinstance(contract, Mapping):
                        continue
                    symbol = str(contract.get("instrumentSymbol") or "").strip()
                    state = str(contract.get("marketState") or contract.get("status") or "").lower()
                    if not symbol.upper().endswith("-UP") or state not in {"open", "active"}:
                        continue
                    expiry = _iso_ms(contract.get("expiryDate") or row.get("expiryDate"))
                    if expiry is None:
                        continue
                    found.append(Event(
                        ticker=ticker,
                        symbol=symbol,
                        expiry_ms=expiry,
                        quantity_minimum=D(str(contract.get("quantityMinimum") or "1")),
                        quantity_increment=D(str(contract.get("quantityIncrement") or "1")),
                        price_increment=D(str(contract.get("priceIncrement") or "0.01")),
                    ))
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            total = int((pagination or {}).get("total") or len(rows))
            offset += len(rows)
            if not rows or offset >= total:
                break
    eligible = [e for e in found if STOP_NEW_SECONDS < e.seconds_to_expiry <= MAX_WINDOW_SECONDS]
    return min(eligible, key=lambda x: x.expiry_ms) if eligible else None


def maker_outcome_from_trade(data: Mapping[str, Any]) -> str | None:
    """Map YES-instrument aggressor metadata to the maker's economic outcome."""
    if "m" not in data:
        return None
    raw = data.get("m")
    buyer_maker = raw if isinstance(raw, bool) else str(raw).strip().lower() in {"1", "true", "yes"}
    return "YES" if buyer_maker else "NO"


class GeminiTradeTape30:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.rows: deque[tuple[str, D]] = deque(maxlen=TAPE_WINDOW)
        self._updated = asyncio.Event()
        self.connected = False
        self.last_error: str | None = None

    def add(self, outcome: str, quantity: D) -> None:
        q = D(str(quantity))
        if outcome not in {"YES", "NO"} or q <= ZERO:
            return
        self.rows.append((outcome, q))
        self._updated.set()

    @property
    def ready(self) -> bool:
        return len(self.rows) == TAPE_WINDOW

    def signed_imbalance(self) -> D | None:
        if not self.ready:
            return None
        yes = sum((q for outcome, q in self.rows if outcome == "YES"), ZERO)
        no = sum((q for outcome, q in self.rows if outcome == "NO"), ZERO)
        total = yes + no
        return None if total <= ZERO else (yes - no) / total

    def contra_outcome(self) -> str | None:
        signed = self.signed_imbalance()
        if signed is None:
            return None
        if signed > TAPE_THRESHOLD:
            return "NO"
        if signed < -TAPE_THRESHOLD:
            return "YES"
        return None

    def snapshot(self) -> dict[str, Any]:
        yes = sum((q for outcome, q in self.rows if outcome == "YES"), ZERO)
        no = sum((q for outcome, q in self.rows if outcome == "NO"), ZERO)
        signed = self.signed_imbalance()
        return {"n": len(self.rows), "yes_quantity": str(yes), "no_quantity": str(no),
                "signed_yes_imbalance": None if signed is None else str(signed),
                "contra_outcome": self.contra_outcome()}

    async def wait_ready(self, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ready:
                return True
            self._updated.clear()
            try:
                await asyncio.wait_for(self._updated.wait(), timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            except asyncio.TimeoutError:
                pass
        return self.ready

    async def run(self, stop: asyncio.Event) -> None:
        backoff = 0.10
        while not stop.is_set():
            try:
                async with websockets.connect(WS, open_timeout=4.0, ping_interval=15, ping_timeout=8,
                                              close_timeout=1, max_queue=10000) as ws:
                    await ws.send(json.dumps({"id": f"v56-trade-{self.symbol}", "method": "SUBSCRIBE",
                                              "params": [f"{self.symbol}@trade"]}, separators=(",", ":")))
                    self.connected = True
                    self.last_error = None
                    backoff = 0.10
                    while not stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        try:
                            data = json.loads(raw)
                        except Exception:
                            continue
                        if not isinstance(data, Mapping) or str(data.get("s") or "") not in {"", self.symbol}:
                            continue
                        outcome = maker_outcome_from_trade(data)
                        try:
                            q = D(str(data.get("q") or "0"))
                        except Exception:
                            continue
                        if outcome is not None and q > ZERO:
                            self.add(outcome, q)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                self.last_error = f"{type(exc).__name__}:{str(exc)[:160]}"
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(2.0, backoff * 2)
        self.connected = False


@dataclass(frozen=True)
class SelectivePlan:
    outcome: str
    quote: D
    quantity: D
    catastrophic_loss: D
    rescue_route: rescue.RescueRoute
    tape_signed_imbalance: D

    def as_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "quote": str(self.quote), "quantity": str(self.quantity),
                "catastrophic_loss": str(self.catastrophic_loss), "rescue_route": self.rescue_route.as_dict(),
                "tape_signed_imbalance": str(self.tape_signed_imbalance), "price_floor": str(PRICE_FLOOR),
                "tape_threshold": str(TAPE_THRESHOLD), "liquidity_rewards_modeled": False,
                "maker_rebates_modeled": False}


def quote_for_outcome(snap: DepthSnapshot, outcome: str) -> D:
    if outcome == "YES":
        return snap.bid
    if outcome == "NO":
        return ONE - snap.ask
    raise ValueError("outcome must be YES or NO")


def select_safe_plan(event: Event, *, snap: DepthSnapshot, tape: GeminiTradeTape30) -> SelectivePlan | None:
    outcome = tape.contra_outcome()
    signed = tape.signed_imbalance()
    if outcome is None or signed is None:
        return None
    quote = quote_for_outcome(snap, outcome)
    if quote < PRICE_FLOOR or quote >= ONE:
        return None
    for q in sorted(grid.valid_integer_quantities(event), reverse=True):
        quantity = D(q)
        catastrophic = grid.single_leg_settlement_loss(price=quote, quantity=quantity)
        if catastrophic > v53.v5.max_campaign_loss():
            continue
        route = rescue.best_route_after_fill(
            filled_outcome=outcome, filled_price=quote, quantity=quantity,
            yes_bid=snap.bid, yes_bid_size=snap.bid_size, yes_ask=snap.ask, yes_ask_size=snap.ask_size,
            max_loss=v53.v5.max_rescue_loss(), increment=event.price_increment,
        )
        if route.safe:
            return SelectivePlan(outcome, quote, quantity, catastrophic, route, signed)
    return None


def _compact_run_id(raw: str) -> str:
    return hashlib.blake2s(str(raw).encode("utf-8"), digest_size=4).hexdigest()


def quote_trade_id(raw_approval_id: str, window_no: int, quote_no: int) -> str:
    if not (1 <= window_no <= 99 and 1 <= quote_no <= 999):
        raise ValueError("selective maker id counters outside supported range")
    value = f"s6-{_compact_run_id(raw_approval_id)}-{window_no}-{quote_no}"
    if len(value) > 24:
        raise ValueError("selective maker trade id too long")
    return value


async def _daily_gate() -> tuple[D, dict[str, Any], daily.DailyRiskDecision]:
    available, flat = await v53.v5.v1._preflight_flat()
    decision = daily.check(current_available_usd=available, loss_limit_usd=v53.v5.max_daily_loss(),
                           account_flat=(flat.get("orders") == 0 and flat.get("positions") == 0),
                           allow_rollover=True, persist_observation=True)
    emit("GLOBAL_DAILY_RISK", **decision.as_dict(), order_entry=False)
    return available, flat, decision


async def _close_task(stop: asyncio.Event | None, task: asyncio.Task | None) -> None:
    if stop is not None:
        stop.set()
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _wait_flat_with_heartbeat(state: dict[str, Any], *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = await v53.v5.v1._flat_snapshot()
        if not snap["orders"] and not snap["positions"]:
            return True
        v53.v5._heartbeat(state, stage="SETTLEMENT_WAIT", flat=snap)
        await asyncio.sleep(2.0)
    return False


async def _finish_residual(maker: Any, *, cache: GeminiDepth5Cache, guard: BtcSpotGuard, event: Event,
                           plan: SelectivePlan, filled_quantity: D, filled_price: D,
                           trade_id: str, state: dict[str, Any]) -> dict[str, Any]:
    grace = await v53.calm_maker_grace_v53(
        maker, cache=cache, guard=guard, symbol=event.symbol, filled_outcome=plan.outcome,
        filled_price=filled_price, quantity=filled_quantity, increment=event.price_increment,
        trade_id=trade_id, state=state,
    )
    if grace.get("hard_halt"):
        return {"status": str(grace.get("status") or "GRACE_HARD_HALT"), "hard_halt": True, "completion": grace}
    remaining = D(str(grace.get("remaining") or filled_quantity))
    if remaining == ZERO:
        return {"status": "GRACE_COMPLETED_PAIR", "hard_halt": False, "completion": grace, "matched": True}
    fok = await v55.bounded_fok_v55(
        maker, cache=cache, symbol=event.symbol, filled_outcome=plan.outcome,
        filled_price=filled_price, quantity=remaining, increment=event.price_increment,
        trade_id=trade_id, state=state,
    )
    action = str((fok.get("route") or {}).get("action") or "")
    return {"status": str(fok.get("status") or "FOK_UNKNOWN"), "hard_halt": bool(fok.get("hard_halt")),
            "completion": grace, "rescue": fok,
            "matched": bool(action == "COMPLETE" and fok.get("flattened")),
            "flattened": bool(action == "UNWIND" and fok.get("flattened"))}


async def run_window(*, guard: BtcSpotGuard, window_no: int) -> dict[str, Any]:
    available, flat, risk_decision = await _daily_gate()
    if risk_decision.blocked:
        return {"status": risk_decision.status, "hard_halt": True, "order_entry": False}
    spot = guard.decision()
    if not spot.ready or spot.dangerous:
        return {"status": "SPOT_DANGEROUS_PRE_WINDOW", "hard_halt": False, "order_entry": False}
    event = await discover_btc15m()
    if event is None:
        return {"status": "NO_CURRENT_BTC15M_WINDOW", "hard_halt": False, "order_entry": False}

    cache = GeminiDepth5Cache(event.symbol, stale_ms=750)
    tape = GeminiTradeTape30(event.symbol)
    cache_stop, tape_stop = asyncio.Event(), asyncio.Event()
    cache_task = asyncio.create_task(cache.run(cache_stop))
    tape_task = asyncio.create_task(tape.run(tape_stop))
    maker = v53.v5.v1.base.GeminiMakerSession()
    state: dict[str, Any] = {}
    quote_no = 0
    any_order_entry = False
    try:
        warm_timeout = max(1.0, event.seconds_to_expiry - STOP_NEW_SECONDS)
        if not await tape.wait_ready(timeout=warm_timeout):
            emit("WINDOW_NO_TAPE", ticker=event.ticker, symbol=event.symbol,
                 seconds_to_expiry=round(event.seconds_to_expiry, 3), tape=tape.snapshot(), order_entry=False)
            return {"status": "WINDOW_NO_30_TRADE_TAPE", "hard_halt": False, "order_entry": False}
        await maker.connect()
        approval = os.environ[v53.v5.v1.ARM_ID_ENV]

        while event.seconds_to_expiry > STOP_NEW_SECONDS:
            if guard.decision().dangerous:
                return {"status": "SPOT_DANGEROUS_BETWEEN_QUOTES", "hard_halt": False, "order_entry": any_order_entry}
            try:
                snap = await cache.wait_fresh(timeout=1.0, max_age_ms=v53.v5.BOOK_MAX_AGE_MS)
            except Exception:
                await asyncio.sleep(SIGNAL_WAIT_POLL)
                continue
            plan = select_safe_plan(event, snap=snap, tape=tape)
            if plan is None:
                await asyncio.sleep(SIGNAL_WAIT_POLL)
                continue
            _, _, mutation_risk = await _daily_gate()
            if mutation_risk.blocked:
                return {"status": mutation_risk.status, "hard_halt": True, "order_entry": any_order_entry}

            quote_no += 1
            trade_id = quote_trade_id(approval, window_no, quote_no)
            quote_id = f"{trade_id}-quote"[:36]
            qty = plan.quantity
            state = {"classification": CLASSIFICATION, "run_id": _compact_run_id(approval), "trade_id": trade_id,
                     "campaign_no": window_no, "quote_no": quote_no, "ticker": event.ticker, "symbol": event.symbol,
                     "selected_outcome": plan.outcome, "quote_id": quote_id, "quote_price": str(plan.quote),
                     "quantity": str(qty), "price_increment": str(event.price_increment), "max_quantity": "10",
                     "max_rescue_loss": str(v53.v5.max_rescue_loss()), "max_campaign_loss": str(v53.v5.max_campaign_loss()),
                     "available_usd_before": str(available), "selective_plan": plan.as_dict(),
                     "owned_client_ids": [quote_id], "liquidity_rewards_modeled": False,
                     "maker_rebates_modeled": False, "status": "PRE_MUTATION", "open": True,
                     "realized_pnl": None, "rewards_realized": None}
            v53.v5._heartbeat(state, stage="PRE_MUTATION")
            emit("QUOTE_START", ticker=event.ticker, symbol=event.symbol,
                 seconds_to_expiry=round(event.seconds_to_expiry, 3), plan=plan.as_dict(), tape=tape.snapshot(),
                 flat=flat, order_entry=False)

            pre_sequence = snap.sequence
            ack, row, ack_ms = await v53.v5.v1._post(
                maker, symbol=event.symbol, outcome=plan.outcome, price=plan.quote, quantity=qty, client_id=quote_id)
            any_order_entry = True
            safe_ack = v55.sanitized_ack_error(ack)
            emit("MAKER_POST", client_order_id=quote_id, outcome=plan.outcome, price=str(plan.quote), quantity=str(qty),
                 ack_status=ack.get("status"), ack_error=safe_ack, ack_latency_ms=round(ack_ms, 3), order_entry=True)
            if int(ack.get("status", 0)) != 200:
                v53.v5._heartbeat(state, stage="MAKER_REJECTED", open=False, maker_reject=safe_ack)
                await asyncio.sleep(0.10)
                continue
            if row is None:
                v53.v5._heartbeat(state, stage="MAKER_ACK_ROW_UNRESOLVED", open=True)
                return {"status": "MAKER_ACK_ROW_UNRESOLVED", "hard_halt": True, "order_entry": True}

            try:
                post_snap = await cache.wait_newer(pre_sequence, timeout=0.75, max_age_ms=v53.v5.BOOK_MAX_AGE_MS)
            except Exception:
                post_snap = None
            external = None if post_snap is None else post_snap.externalized(
                own_yes_price=plan.quote if plan.outcome == "YES" else None,
                own_yes_quantity=qty if plan.outcome == "YES" else ZERO,
                own_no_price=plan.quote if plan.outcome == "NO" else None,
                own_no_quantity=qty if plan.outcome == "NO" else ZERO)
            if external is None:
                resolved, filled, cancel = await v53._cancel_one_exact(maker, row=row, client_id=quote_id, symbol=event.symbol)
                if resolved is None:
                    v53.v5._heartbeat(state, stage="POST_DEPTH_CANCEL_RECONCILE_AMBIGUOUS", open=True)
                    return {"status": "POST_DEPTH_CANCEL_RECONCILE_AMBIGUOUS", "hard_halt": True,
                            "order_entry": True, "cancel": cancel}
                if filled == ZERO:
                    v53.v5._heartbeat(state, stage="POST_DEPTH_NOT_CONFIRMED_ZERO_FILL", open=False)
                    continue
                final_row = resolved
            else:
                post_route = rescue.best_route_after_fill(
                    filled_outcome=plan.outcome, filled_price=plan.quote, quantity=qty,
                    yes_bid=external.bid, yes_bid_size=external.bid_size, yes_ask=external.ask, yes_ask_size=external.ask_size,
                    max_loss=v53.v5.max_rescue_loss(), increment=event.price_increment)
                if not post_route.safe:
                    resolved, filled, cancel = await v53._cancel_one_exact(maker, row=row, client_id=quote_id, symbol=event.symbol)
                    if resolved is None:
                        v53.v5._heartbeat(state, stage="POST_RESCUE_CANCEL_RECONCILE_AMBIGUOUS", open=True)
                        return {"status": "POST_RESCUE_CANCEL_RECONCILE_AMBIGUOUS", "hard_halt": True,
                                "order_entry": True, "cancel": cancel}
                    if filled == ZERO:
                        v53.v5._heartbeat(state, stage="POST_RESCUE_BROKE_ZERO_FILL", open=False)
                        continue
                    final_row = resolved
                else:
                    pull_reason = None
                    posted_at = time.monotonic()
                    last_heartbeat = 0.0
                    while event.seconds_to_expiry > STOP_NEW_SECONDS:
                        now = time.monotonic()
                        if now - last_heartbeat >= 1.0:
                            v53.v5._heartbeat(state, stage="SELECTIVE_QUOTE_RESTING")
                            last_heartbeat = now
                        if maker.reader is None or maker.reader.done():
                            pull_reason = "MAKER_SESSION_LOST"; break
                        spot_now = guard.decision()
                        if not spot_now.ready or spot_now.dangerous:
                            pull_reason = "SPOT_TOXIC"; break
                        if tape.contra_outcome() != plan.outcome:
                            pull_reason = "TAPE_SIGNAL_CHANGED"; break
                        try:
                            snap_now = await cache.wait_fresh(timeout=0.20, max_age_ms=v53.v5.BOOK_MAX_AGE_MS)
                        except Exception:
                            pull_reason = "BOOK_STALE"; break
                        external_now = snap_now.externalized(
                            own_yes_price=plan.quote if plan.outcome == "YES" else None,
                            own_yes_quantity=qty if plan.outcome == "YES" else ZERO,
                            own_no_price=plan.quote if plan.outcome == "NO" else None,
                            own_no_quantity=qty if plan.outcome == "NO" else ZERO)
                        if external_now is None:
                            pull_reason = "EXTERNAL_RESCUE_DEPTH_GONE"; break
                        route_now = rescue.best_route_after_fill(
                            filled_outcome=plan.outcome, filled_price=plan.quote, quantity=qty,
                            yes_bid=external_now.bid, yes_bid_size=external_now.bid_size,
                            yes_ask=external_now.ask, yes_ask_size=external_now.ask_size,
                            max_loss=v53.v5.max_rescue_loss(), increment=event.price_increment)
                        if not route_now.safe:
                            pull_reason = "RESCUE_BOUND_BROKE"; break
                        desired = quote_for_outcome(snap_now, plan.outcome)
                        if desired > plan.quote and now - posted_at >= QUOTE_REPRICE_MIN_AGE and desired >= PRICE_FLOOR:
                            pull_reason = "MORE_COMPETITIVE_QUOTE_AVAILABLE"; break
                        try:
                            item = await asyncio.wait_for(maker.events.get(), timeout=QUOTE_CHECK_SECONDS)
                        except asyncio.TimeoutError:
                            continue
                        if not isinstance(item, Mapping) or str(item.get("c") or "") != quote_id:
                            continue
                        status = str(item.get("X") or "").upper()
                        event_fill = D(str(item.get("Z") or "0"))
                        if event_fill > ZERO and status in {"PARTIALLY_FILLED", "FILLED"}:
                            pull_reason = "FIRST_FILL"; break

                    resolved, filled, cancel = await v53._cancel_one_exact(maker, row=row, client_id=quote_id, symbol=event.symbol)
                    emit("QUOTE_PULLED", reason=pull_reason or "STOP_NEW_EXPOSURE", client_order_id=quote_id,
                         filled=str(filled), quantity=str(qty), cancel=cancel, order_entry=True)
                    if resolved is None:
                        v53.v5._heartbeat(state, stage="QUOTE_CANCEL_RECONCILE_AMBIGUOUS", open=True)
                        return {"status": "QUOTE_CANCEL_RECONCILE_AMBIGUOUS", "hard_halt": True, "order_entry": True}
                    if pull_reason == "FIRST_FILL" and filled == ZERO:
                        v53.v5._heartbeat(state, stage="RECONCILE_AMBIGUOUS", open=True,
                                          reconcile_reason="WS_FIRST_FILL_CONTRADICTED_BY_ZERO_RECONCILE")
                        return {"status": "RECONCILE_AMBIGUOUS", "hard_halt": True, "order_entry": True}
                    if filled == ZERO:
                        v53.v5._heartbeat(state, stage="ZERO_FILL_REQUOTE", open=False)
                        continue
                    final_row = resolved

            filled = v53.v5.v1.base._filled(final_row)
            if not (ZERO < filled <= qty):
                v53.v5._heartbeat(state, stage="SELECTED_QUANTITY_BOUND_VIOLATION", open=True,
                                  reconciled_fill=str(filled))
                return {"status": "SELECTED_QUANTITY_BOUND_VIOLATION", "hard_halt": True, "order_entry": True}
            filled_price = v53.v5._fill_price(final_row, plan.quote)
            v53.v5._heartbeat(state, stage="RESIDUAL_IDENTIFIED", fill_outcome=plan.outcome,
                              residual_quantity=str(filled), residual_fill_price=str(filled_price))
            finish = await _finish_residual(maker, cache=cache, guard=guard, event=event, plan=plan,
                                            filled_quantity=filled, filled_price=filled_price,
                                            trade_id=trade_id, state=state)
            v53.v5._heartbeat(state, stage=finish["status"], selective_finish=finish)
            if finish.get("hard_halt"):
                return {"status": finish["status"], "hard_halt": True, "order_entry": True, "finish": finish}
            wait_timeout = max(30.0, event.seconds_to_expiry + 180.0)
            if not await _wait_flat_with_heartbeat(state, timeout=wait_timeout):
                v53.v5._heartbeat(state, stage="SETTLEMENT_DID_NOT_CLEAR_WITHIN_BOUND", open=True)
                return {"status": "SETTLEMENT_DID_NOT_CLEAR_WITHIN_BOUND", "hard_halt": True,
                        "order_entry": True, "finish": finish}
            settled_available = D(str(await v53.v5.v1.base._gemini_available_usd()))
            v53.v5._heartbeat(state, stage="SETTLED_ACCOUNT_FLAT", open=False,
                              available_usd_after=str(settled_available),
                              settled_at_utc=datetime.now(timezone.utc).isoformat())
            return {"status": "SETTLED_ACCOUNT_FLAT", "hard_halt": False, "order_entry": True,
                    "finish": finish, "available_usd_after": str(settled_available)}

        return {"status": "WINDOW_ENDED_NO_FILL", "hard_halt": False, "order_entry": any_order_entry,
                "ticker": event.ticker}
    finally:
        try:
            if maker.ws is not None:
                await maker.request("order.cancel_session", {}, timeout=1.5)
        except Exception:
            pass
        try:
            await maker.close()
        except Exception:
            pass
        await _close_task(cache_stop, cache_task)
        await _close_task(tape_stop, tape_task)


async def run() -> dict[str, Any]:
    """Run one bounded continuous V5.6 session; never auto-restart."""
    v55.install()
    v53.v5._config_ok()
    existing = v53.v5.v1._read_state()
    if existing and bool(existing.get("open")):
        try:
            _, flat = await v53.v5.v1._preflight_flat()
        except Exception as exc:
            result = {"status": "OPEN_STATE_RECOVERY_REQUIRED", "hard_halt": True,
                      "new_campaign_started": False, "error_type": type(exc).__name__}
            print("GEMINI_SELECTIVE_MAKER_V5_6_RUNTIME=" + json.dumps(result, separators=(",", ":")), flush=True)
            return result
        existing.update(open=False, status="STALE_OPEN_STATE_ACCOUNT_FLAT_CLOSED",
                        reconciled_at_utc=datetime.now(timezone.utc).isoformat(), flat=flat)
        v53.v5.v1._write_state(existing)

    if not v53.v5.live_armed():
        try:
            available, flat, risk = await _daily_gate()
        except Exception as exc:
            result = {"status": "DISARMED_PREFLIGHT_ERROR", "armed": False, "order_entry": False,
                      "error_type": type(exc).__name__}
        else:
            result = {"status": "DISARMED_READY", "armed": False, "order_entry": False,
                      "available_usd": str(available), "flat": flat, "daily_risk": risk.as_dict(),
                      "strategy": "BTC15M_TAPE_CONTRA_30_THRESHOLD_0.10_PRICE_FLOOR_0.30"}
        print("GEMINI_SELECTIVE_MAKER_V5_6_RUNTIME=" + json.dumps(result, separators=(",", ":"), default=str), flush=True)
        return result

    start_available, _, risk = await _daily_gate()
    if risk.blocked:
        return {"status": risk.status, "hard_halt": True, "order_entry": False}
    stop = asyncio.Event()
    guard = BtcSpotGuard()
    spot_task = asyncio.create_task(guard.run_coinbase(stop))
    results: list[dict[str, Any]] = []
    deadline = time.monotonic() + max_session_seconds()
    try:
        if not await v53.v5.v1._warm_spot(guard):
            return {"status": "SPOT_GUARD_NOT_READY", "hard_halt": True, "order_entry": False}
        window_no = 0
        while window_no < max_windows() and time.monotonic() < deadline:
            current_available, _ = await v53.v5.v1._preflight_flat()
            session_drawdown = max(ZERO, start_available - current_available)
            if session_drawdown >= max_session_loss():
                emit("SESSION_LOSS_STOP", start_available=str(start_available), current_available=str(current_available),
                     drawdown=str(session_drawdown), loss_stop=str(max_session_loss()), order_entry=False)
                break
            tentative_window = window_no + 1
            result = await run_window(guard=guard, window_no=tentative_window)
            if result.get("status") == "NO_CURRENT_BTC15M_WINDOW":
                await asyncio.sleep(1.0)
                continue
            window_no = tentative_window
            results.append(result)
            emit("WINDOW_RESULT", window_no=window_no, result=result, order_entry=bool(result.get("order_entry")))
            if result.get("hard_halt"):
                break
        final_available, final_flat = await v53.v5.v1._preflight_flat()
        result = {"status": "V5_6_SESSION_COMPLETE" if not any(r.get("hard_halt") for r in results)
                  else "V5_6_SESSION_HARD_HALTED",
                  "windows_attempted": len(results), "results": results,
                  "start_available_usd": str(start_available), "final_available_usd": str(final_available),
                  "session_account_delta": str(final_available - start_available), "final_flat": final_flat,
                  "order_entry": any(bool(r.get("order_entry")) for r in results),
                  "liquidity_rewards_modeled": False, "maker_rebates_modeled": False}
        print("GEMINI_SELECTIVE_MAKER_V5_6_RUNTIME=" + json.dumps(result, separators=(",", ":"), default=str), flush=True)
        return result
    finally:
        stop.set()
        spot_task.cancel()
        await asyncio.gather(spot_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run())
