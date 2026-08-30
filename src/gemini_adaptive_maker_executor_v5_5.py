"""Gemini adaptive maker V5.5 incident hardening.

V5.4's first bounded continuous session exposed two reconciliation hazards that only
appear after campaign 1:

1. The long approval id was embedded in ``trade_id`` and then truncated to 24
   characters before the campaign number. Campaigns therefore reused YES/NO/grace/
   FOK clientOrderIds. REST reconciliation for campaign 3 could read campaign 2's
   filled order and manufacture a ghost residual.
2. A definite non-200 rescue FOK acknowledgement preserved only the HTTP-like
   status. The safe error code/message were discarded and ``pending_rescue`` stayed
   durable even though a definite rejection means that order was not accepted.

V5.5 changes no admission economics and grants no new permission. It wraps the
reviewed V5.4 campaign with a compact deterministic approval id so campaign identity
survives every venue clientOrderId limit, and patches bounded FOK rejection handling
to persist only a small allow-listed error summary. Ambiguous timeouts remain
ambiguous and fail closed exactly as before.
"""
from __future__ import annotations

import hashlib
import time
from decimal import Decimal as D
from typing import Any, Mapping

from src import gemini_adaptive_maker_executor_v5_3 as v53
from src import gemini_adaptive_maker_executor_v5_4 as v54
from src import gemini_q10_production_executor_v5_2 as v52

ZERO = D("0")


def compact_approval_id(raw: str) -> str:
    """Return a short deterministic id that leaves room for campaign identity.

    ``gadv53-{compact}-{campaign}`` must fit V5.3's 24-character trade-id ceiling
    through campaign 20. A hash is used instead of truncating the human approval id,
    because truncation was the source of the collision incident.
    """
    digest = hashlib.blake2s(str(raw).encode("utf-8"), digest_size=5).hexdigest()
    return f"v55-{digest}"


def campaign_trade_id(raw_approval_id: str, campaign_no: int) -> str:
    if not 1 <= int(campaign_no) <= 999:
        raise ValueError("campaign number out of supported range")
    value = f"gadv53-{compact_approval_id(raw_approval_id)}-{int(campaign_no)}"
    if len(value) > 24:
        raise ValueError("V5.5 campaign trade id exceeds venue-safe internal limit")
    return value


def campaign_client_ids(raw_approval_id: str, campaign_no: int) -> dict[str, str]:
    trade_id = campaign_trade_id(raw_approval_id, campaign_no)
    return {
        "trade_id": trade_id,
        "yes": f"{trade_id}-yes"[:36],
        "no": f"{trade_id}-no"[:36],
        "grace": f"{trade_id}-grace"[:36],
        "fok1": f"{trade_id}-v5fok-1"[:36],
        "fok2": f"{trade_id}-v5fok-2"[:36],
    }


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:240]


def sanitized_ack_error(ack: Mapping[str, Any] | None) -> dict[str, Any]:
    """Allow-list non-sensitive exchange rejection diagnostics only."""
    if not isinstance(ack, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key in ("status", "code", "message", "reason", "type"):
        if key in ack:
            out[key] = _safe_scalar(ack.get(key))
    nested = ack.get("error")
    if isinstance(nested, Mapping):
        safe_nested = {
            key: _safe_scalar(nested.get(key))
            for key in ("code", "message", "reason", "type")
            if key in nested
        }
        if safe_nested:
            out["error"] = safe_nested
    return out


async def bounded_fok_v55(
    maker: Any,
    *,
    cache: Any,
    symbol: str,
    filled_outcome: str,
    filled_price: D,
    quantity: D,
    increment: D,
    trade_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """V5.2 bounded FOK with definite-reject observability and state cleanup."""
    v5 = v52.v5
    rescue = v52.rescue
    if not (ZERO < quantity <= v52.Q):
        return {"status": "INVALID_RESCUE_QUANTITY", "hard_halt": True, "flattened": False}

    for attempt in range(1, v5.RESCUE_ATTEMPTS + 1):
        try:
            snap = await cache.wait_fresh(timeout=0.75, max_age_ms=v5.BOOK_MAX_AGE_MS)
        except Exception as exc:
            return {
                "status": "RESCUE_BOOK_NOT_FRESH",
                "hard_halt": True,
                "flattened": False,
                "error_type": type(exc).__name__,
                "attempt": attempt,
            }

        route = rescue.best_route_after_fill(
            filled_outcome=filled_outcome,
            filled_price=filled_price,
            quantity=quantity,
            yes_bid=snap.bid,
            yes_bid_size=snap.bid_size,
            yes_ask=snap.ask,
            yes_ask_size=snap.ask_size,
            max_loss=v5.max_rescue_loss(),
            increment=increment,
        )
        v5.v1.emit(
            "V5_RESCUE_ROUTE",
            attempt=attempt,
            filled_outcome=filled_outcome,
            filled_price=str(filled_price),
            route=route.as_dict(),
            book_age_ms=round(snap.age_ms, 3),
            order_entry=False,
        )
        if not route.safe:
            return {
                "status": "RESCUE_BOUND_NO_LONGER_AVAILABLE",
                "hard_halt": True,
                "flattened": False,
                "attempt": attempt,
                "route": route.as_dict(),
            }

        client_id = f"{trade_id}-v5fok-{attempt}"[:36]
        params = {
            "symbol": symbol,
            "side": route.side,
            "type": "LIMIT",
            "timeInForce": "FOK",
            "price": str(route.limit_price),
            "quantity": str(quantity),
            "eventOutcome": route.outcome,
            "clientOrderId": client_id,
        }
        v5._heartbeat(
            state,
            stage="RESCUE_FOK_PENDING",
            pending_rescue={
                "client_order_id": client_id,
                "attempt": attempt,
                "route": route.as_dict(),
                "quantity": str(quantity),
            },
        )
        t0 = time.perf_counter_ns()
        try:
            ack = await maker.request("order.place", params, timeout=3.0)
        except Exception as exc:
            # Timeout/transport failure remains ambiguous. Do not clear pending state
            # and never send another full-size FOK automatically.
            return {
                "status": "RESCUE_FOK_ACK_AMBIGUOUS",
                "hard_halt": True,
                "flattened": False,
                "attempt": attempt,
                "error_type": type(exc).__name__,
            }

        ack_ms = (time.perf_counter_ns() - t0) / 1_000_000
        safe_error = sanitized_ack_error(ack)
        v5.v1.emit(
            "V5_RESCUE_FOK_ACK",
            attempt=attempt,
            client_order_id=client_id,
            ack_status=ack.get("status"),
            ack_error=safe_error,
            ack_latency_ms=round(ack_ms, 3),
            route=route.as_dict(),
            order_entry=True,
        )
        if int(ack.get("status", 0)) != 200:
            # A definite exchange rejection is not an unknown order. Clear the
            # pending mutation while retaining the hard halt; no retry is attempted.
            v5._heartbeat(
                state,
                stage="RESCUE_FOK_REJECTED",
                pending_rescue=None,
                rescue_last_client_id=client_id,
                rescue_last_fill="0",
                rescue_reject=safe_error,
            )
            return {
                "status": "RESCUE_FOK_REJECTED",
                "hard_halt": True,
                "flattened": False,
                "attempt": attempt,
                "ack_status": ack.get("status"),
                "ack_error": safe_error,
            }

        row = await v5.v1.base._find_row(client_id, symbol)
        if row is None:
            v5._heartbeat(
                state,
                stage="RESCUE_FOK_STATUS_UNKNOWN",
                rescue_last_client_id=client_id,
                rescue_last_fill=None,
            )
            return {
                "status": "RESCUE_FOK_STATUS_UNKNOWN",
                "hard_halt": True,
                "flattened": False,
                "attempt": attempt,
                "client_order_id": client_id,
            }

        filled = v5.v1.base._filled(row)
        v5._heartbeat(
            state,
            rescue_last_client_id=client_id,
            rescue_last_fill=str(filled),
            pending_rescue=None,
        )
        if filled == quantity:
            return {
                "status": f"RESCUE_{route.action}_FILLED",
                "hard_halt": False,
                "flattened": True,
                "attempt": attempt,
                "route": route.as_dict(),
                "quantity": str(quantity),
            }
        if filled != ZERO:
            return {
                "status": "RESCUE_FOK_PARTIAL_AMBIGUITY",
                "hard_halt": True,
                "flattened": False,
                "attempt": attempt,
                "filled": str(filled),
            }
        await __import__("asyncio").sleep(0.03)

    return {
        "status": "RESCUE_FOK_DID_NOT_FILL_WITHIN_BOUND",
        "hard_halt": True,
        "flattened": False,
        "attempts": v5.RESCUE_ATTEMPTS,
    }


async def run_campaign_v55(*, guard: Any, campaign_no: int) -> dict[str, Any]:
    """Run one V5.4 campaign with collision-proof compact approval identity."""
    original_clean = v53.v5.v1._clean
    original_fok = v52.bounded_fok_v52

    def compact_clean(value: str, limit: int = 18) -> str:
        del limit
        return compact_approval_id(value)

    v53.v5.v1._clean = compact_clean
    v52.bounded_fok_v52 = bounded_fok_v55
    try:
        return await v54.run_campaign_v54(guard=guard, campaign_no=campaign_no)
    finally:
        v53.v5.v1._clean = original_clean
        v52.bounded_fok_v52 = original_fok


def install() -> None:
    """Install V5.4 economics/risk first, then V5.5 continuous-session patches."""
    v54.install()
    v52.bounded_fok_v52 = bounded_fok_v55
    v53.v5._run_campaign = run_campaign_v55
