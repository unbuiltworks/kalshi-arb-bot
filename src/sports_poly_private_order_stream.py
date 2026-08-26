"""Private Polymarket order-state stream used by the structural q1 executor.

The REST order API remains the mutation and final-reconciliation authority. This
module replaces high-frequency status polling while a maker is resting with the
exchange's authenticated private order WebSocket.

An empty account is allowed to subscribe without receiving an initial order
snapshot.  In that case the first newly-created live order becomes a bounded
bootstrap proof: until the private stream actually observes that order, exact REST
is sampled at a low rate.  If the stream has not observed the order within three
seconds, one final exact read is performed and the attempt raises so the caller
scoped-cancels and hard-halts.  This prevents an apparently connected but silent
WebSocket from leaving a filled maker unhedged for the full resting TTL.

No credentials are stored here; the SDK reads the same environment credentials
already used by the live executor.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation
import os
import time
from typing import Any, Mapping

from polymarket_us import PolymarketUS

D = Decimal
TERMINAL_STATES = {
    "ORDER_STATE_FILLED",
    "ORDER_STATE_CANCELED",
    "ORDER_STATE_REJECTED",
    "ORDER_STATE_EXPIRED",
}
START_SNAPSHOT_GRACE_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_WS_SNAPSHOT_GRACE_SECONDS", "1.5"))
BOOTSTRAP_REST_INTERVAL_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_WS_BOOTSTRAP_REST_INTERVAL_SECONDS", "0.75"))
BOOTSTRAP_TIMEOUT_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_WS_BOOTSTRAP_TIMEOUT_SECONDS", "3.0"))


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row.get(key) is not None:
            return row.get(key)
    return None


def _decimal(value: Any) -> D | None:
    if isinstance(value, Mapping):
        value = value.get("value")
    if value in (None, ""):
        return None
    try:
        return D(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _unwrap_order(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    nested = value.get("order")
    if isinstance(nested, Mapping):
        return normalize_order(nested)
    return normalize_order(value)


def normalize_order(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize snake/camel SDK payloads to the shape the v4 engine consumes."""
    out = dict(raw)
    aliases = {
        "marketSlug": ("marketSlug", "market_slug"),
        "cumQuantity": ("cumQuantity", "cum_quantity"),
        "leavesQuantity": ("leavesQuantity", "leaves_quantity"),
        "avgPx": ("avgPx", "avg_px"),
        "commissionNotionalTotalCollected": (
            "commissionNotionalTotalCollected",
            "commission_notional_total_collected",
        ),
    }
    for target, keys in aliases.items():
        value = _first(raw, *keys)
        if value is not None:
            out[target] = value

    qty = _decimal(_first(raw, "quantity", "qty"))
    leaves = _decimal(_first(raw, "leavesQuantity", "leaves_quantity"))
    cum = _decimal(_first(raw, "cumQuantity", "cum_quantity"))
    if cum is None and qty is not None and leaves is not None:
        derived = qty - leaves
        if derived >= 0:
            out["cumQuantity"] = str(derived)
    return out


def _looks_like_order(row: Mapping[str, Any]) -> bool:
    return bool(
        str(_first(row, "id", "orderId", "order_id") or "")
        and _first(row, "state") is not None
        and _first(row, "marketSlug", "market_slug") is not None
    )


def _walk_orders(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        if _looks_like_order(value):
            row = normalize_order(value)
            if not row.get("id"):
                row["id"] = _first(value, "orderId", "order_id")
            found.append(row)
        for child in value.values():
            found.extend(_walk_orders(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.extend(_walk_orders(child))
    return found


class PrivateOrderStream:
    """Small cache around Polymarket US SUBSCRIPTION_TYPE_ORDER events."""

    def __init__(self, *, key_id: str, secret_key: str, timeout: float = 8.0) -> None:
        self.client = PolymarketUS(key_id=key_id, secret_key=secret_key, timeout=timeout)
        self.ws: Any = None
        self.orders: dict[str, dict[str, Any]] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._snapshot = asyncio.Event()
        self._healthy = False
        self._subscribed = False
        self._initial_snapshot_seen = False
        self._error_type = ""
        self._updates = 0
        self._last_update_mono = 0.0
        self._stream_seen_ids: set[str] = set()
        self._bootstrap_started: dict[str, float] = {}
        self._last_bootstrap_rest: dict[str, float] = {}

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def subscribed(self) -> bool:
        return self._subscribed

    @property
    def initial_snapshot_seen(self) -> bool:
        return self._initial_snapshot_seen

    @property
    def updates(self) -> int:
        return self._updates

    @property
    def error_type(self) -> str:
        return self._error_type

    def seen(self, order_id: str) -> bool:
        return order_id in self._stream_seen_ids

    def _event_for(self, order_id: str) -> asyncio.Event:
        event = self._events.get(order_id)
        if event is None:
            event = asyncio.Event()
            self._events[order_id] = event
        return event

    def _merge(self, order: Mapping[str, Any], *, source: str) -> None:
        oid = str(_first(order, "id", "orderId", "order_id") or "")
        if not oid:
            return
        prior = self.orders.get(oid, {})
        merged = dict(prior)
        merged.update(normalize_order(order))
        merged["id"] = oid
        self.orders[oid] = merged
        if source == "stream":
            self._stream_seen_ids.add(oid)
            self._updates += 1
            self._last_update_mono = time.monotonic()
        self._event_for(oid).set()

    def _on_snapshot(self, payload: Any) -> None:
        for order in _walk_orders(payload):
            self._merge(order, source="stream")
        self._healthy = True
        self._initial_snapshot_seen = True
        self._snapshot.set()

    def _on_update(self, payload: Any) -> None:
        for order in _walk_orders(payload):
            self._merge(order, source="stream")
        self._healthy = True
        # Some empty-account subscriptions may provide no snapshot.  A valid
        # first update is equally strong proof that the subscription is live.
        self._snapshot.set()

    def _on_error(self, error: Any) -> None:
        self._healthy = False
        self._error_type = type(error).__name__ if not isinstance(error, Mapping) else "WebSocketErrorPayload"
        self._snapshot.set()
        for event in self._events.values():
            event.set()

    async def start(self, *, snapshot_timeout: float = START_SNAPSHOT_GRACE_SECONDS) -> None:
        self.ws = self.client.ws.private()
        self.ws.on("order_snapshot", self._on_snapshot)
        self.ws.on("order_update", self._on_update)
        self.ws.on("error", self._on_error)
        await self.ws.connect()
        await self.ws.subscribe("arb-structural-orders", "SUBSCRIPTION_TYPE_ORDER")
        self._subscribed = True
        self._healthy = True
        try:
            await asyncio.wait_for(self._snapshot.wait(), timeout=snapshot_timeout)
        except asyncio.TimeoutError:
            # Empty accounts are allowed to have no initial order snapshot.  Do
            # not declare the stream proven; the first created order must do that
            # under the bounded bootstrap logic in wait_for_update().
            if self._healthy:
                self._error_type = "NO_INITIAL_SNAPSHOT_BOOTSTRAP_REQUIRED"
                return
            raise RuntimeError(f"private order websocket unhealthy: {self._error_type or 'unknown'}")
        if not self._healthy:
            raise RuntimeError(f"private order websocket unhealthy: {self._error_type or 'unknown'}")

    def register_fallback(self, order_id: str, fallback: Mapping[str, Any]) -> None:
        if not order_id or not isinstance(fallback, Mapping):
            return
        row = dict(fallback)
        row.setdefault("id", order_id)
        prior = self.orders.get(order_id, {})
        merged = dict(row)
        # Stream state must win over the create-response fallback.
        merged.update(prior)
        self.orders[order_id] = normalize_order(merged)

    def get(self, order_id: str) -> dict[str, Any] | None:
        row = self.orders.get(order_id)
        return dict(row) if row else None

    async def _bootstrap_rest_refresh(self, order_id: str, *, force: bool = False) -> dict[str, Any] | None:
        now = time.monotonic()
        last = self._last_bootstrap_rest.get(order_id, 0.0)
        if not force and now - last < BOOTSTRAP_REST_INTERVAL_SECONDS:
            return self.get(order_id)
        self._last_bootstrap_rest[order_id] = now
        try:
            detail = await asyncio.to_thread(self.client.orders.retrieve, order_id)
        except Exception as exc:
            raise RuntimeError(f"private ws bootstrap exact read failed: {type(exc).__name__}") from exc
        order = _unwrap_order(detail)
        if order:
            order.setdefault("id", order_id)
            self._merge(order, source="rest")
        return self.get(order_id)

    @staticmethod
    def _has_fill_or_terminal(order: Mapping[str, Any] | None) -> bool:
        if not isinstance(order, Mapping):
            return False
        cum = _decimal(order.get("cumQuantity")) or D("0")
        state = str(order.get("state") or "")
        return cum > 0 or state in TERMINAL_STATES

    async def wait_for_update(self, order_id: str, timeout: float) -> dict[str, Any] | None:
        event = self._event_for(order_id)
        event.clear()
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

        current = self.get(order_id)
        if self.seen(order_id):
            return current
        if not self._healthy:
            return current

        now = time.monotonic()
        started = self._bootstrap_started.setdefault(order_id, now)
        current = await self._bootstrap_rest_refresh(order_id)
        if self._has_fill_or_terminal(current):
            return current
        if self.seen(order_id):
            return self.get(order_id)

        if now - started >= BOOTSTRAP_TIMEOUT_SECONDS:
            current = await self._bootstrap_rest_refresh(order_id, force=True)
            if self._has_fill_or_terminal(current):
                return current
            if self.seen(order_id):
                return self.get(order_id)
            raise RuntimeError("private order websocket did not observe new order during bootstrap")
        return current

    async def wait_terminal(self, order_id: str, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        event = self._event_for(order_id)
        while time.monotonic() < deadline:
            event.clear()
            current = self.get(order_id)
            if current is not None and str(current.get("state") or "") in TERMINAL_STATES:
                return current
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                break
        current = self.get(order_id)
        if current is not None and str(current.get("state") or "") in TERMINAL_STATES:
            return current
        return None

    async def close(self) -> None:
        self._healthy = False
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
        try:
            self.client.close()
        except Exception:
            pass
