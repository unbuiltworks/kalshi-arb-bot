"""AWS-local durable budget/replay fallback for the one-shot Gemini BTC5m q=1 canary.

Used only when the central Command Center ledger is unavailable. This module:
- blocks if this host already has an unresolved local canary reservation;
- queries Gemini directly for existing short-crypto active orders/positions;
- fsyncs an append-only event journal and an atomic open-reservation file before
  any order-entry session is allowed to connect;
- never calls a venue mutation endpoint;
- stores Command-Center-compatible events for later replay.

It is intentionally conservative and one-shot. It does not classify local events
as realized P&L.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from src.gemini_prediction_auth import private_rest_request

D = Decimal
STATE_DIR = Path(os.getenv("GEMINI_SHORT_CRYPTO_STATE_DIR", "/state"))
JOURNAL = STATE_DIR / "gemini_btc5m_q1_events.jsonl"
OPEN_STATE = STATE_DIR / "gemini_btc5m_q1_open.json"
STRATEGY = "crypto_gemini_btc5m_two_sided_maker_q1"
CLASSIFICATION = "GEMINI_SHORT_BINARY_TWO_SIDED_MAKER_Q1_CANARY_LOCAL_LEDGER"
RESERVED_USD = D("2")
SHORT_PREFIXES = (
    "GEMI-BTC05M", "GEMI-BTC15M",
    "GEMI-ETH05M", "GEMI-ETH15M",
    "GEMI-SOL05M", "GEMI-SOL15M",
    "GEMI-XRP05M", "GEMI-XRP15M",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


def _short_symbol(value: Any) -> bool:
    symbol = str(value or "").strip().upper()
    return any(symbol.startswith(prefix) for prefix in SHORT_PREFIXES)


def _ensure_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _append(event: Mapping[str, Any]) -> None:
    _ensure_dir()
    line = json.dumps(dict(event), separators=(",", ":"), sort_keys=True, default=str) + "\n"
    fd = os.open(JOURNAL, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_open(payload: Mapping[str, Any]) -> None:
    _ensure_dir()
    tmp = OPEN_STATE.with_suffix(".tmp")
    raw = json.dumps(dict(payload), separators=(",", ":"), sort_keys=True, default=str) + "\n"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        os.write(fd, raw.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, OPEN_STATE)
    dfd = os.open(STATE_DIR, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _clear_open() -> None:
    if OPEN_STATE.exists():
        OPEN_STATE.unlink()
        dfd = os.open(STATE_DIR, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def local_open_state() -> dict[str, Any] | None:
    if not OPEN_STATE.exists():
        return None
    try:
        payload = json.loads(OPEN_STATE.read_text())
    except Exception as exc:
        raise RuntimeError(f"local canary state unreadable: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("local canary state malformed")
    return payload


async def _post(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = await private_rest_request("POST", path, params=params, timeout=5.0)
    if response.status_code != 200:
        raise RuntimeError(f"Gemini account audit failed path={path} http={response.status_code}")
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"Gemini account audit malformed path={path}")
    return body


async def account_exposure_snapshot() -> dict[str, Any]:
    """Read-only Gemini recovery snapshot; blocks only short-duration crypto exposure."""
    active: list[dict[str, Any]] = []
    offset = 0
    while True:
        body = await _post(
            "/v1/prediction-markets/orders/active",
            {"limit": 100, "offset": offset},
        )
        rows = body.get("orders") if isinstance(body.get("orders"), list) else []
        active.extend(row for row in rows if isinstance(row, dict) and _short_symbol(row.get("symbol")))
        if len(rows) < 100:
            break
        offset += len(rows)
        if offset >= 5000:
            raise RuntimeError("Gemini active-order audit pagination exceeded conservative cap")

    positions_body = await _post(
        "/v1/prediction-markets/positions",
        {"limit": 1000, "offset": 0, "sort": "+expiryDate"},
    )
    positions = positions_body.get("positions") if isinstance(positions_body.get("positions"), list) else []
    short_positions: list[dict[str, Any]] = []
    for row in positions:
        if not isinstance(row, dict) or not _short_symbol(row.get("symbol")):
            continue
        try:
            qty = D(str(row.get("totalQuantity") or "0"))
        except Exception:
            qty = D("0")
        if qty > 0:
            short_positions.append(row)

    return {
        "active_short_crypto_orders": len(active),
        "active_short_crypto_positions": len(short_positions),
        "order_symbols": sorted({str(r.get("symbol") or "") for r in active}),
        "position_symbols": sorted({str(r.get("symbol") or "") for r in short_positions}),
    }


async def assert_clean_account() -> dict[str, Any]:
    local = local_open_state()
    if local is not None:
        raise RuntimeError(f"unresolved AWS-local canary reservation blocks live run: {local.get('trade_id')}")
    snapshot = await account_exposure_snapshot()
    if snapshot["active_short_crypto_orders"] or snapshot["active_short_crypto_positions"]:
        raise RuntimeError(
            "existing Gemini short-crypto exposure blocks canary: "
            f"orders={snapshot['active_short_crypto_orders']} positions={snapshot['active_short_crypto_positions']}"
        )
    return snapshot


def _event_base(*, trade_id: str, symbol: str, event_ticker: str, event_type: str, status: str) -> dict[str, Any]:
    return {
        "event_id": _safe_id(f"{trade_id}:{event_type.lower()}:{status.lower()}"),
        "trade_id": trade_id,
        "event_type": event_type,
        "event_ts": _now(),
        "strategy": STRATEGY,
        "mode": "LIVE_PROBE",
        "market": symbol,
        "asset": event_ticker,
        "venues": ["gemini"],
        "side": "TWO_SIDED_MAKER",
        "status": status,
        "metadata": {
            "classification": CLASSIFICATION,
            "ledger_backend": "AWS_LOCAL_FSYNC_JOURNAL",
            "command_center_replay_required": True,
            "realized": False,
        },
    }


async def reserve(*, symbol: str, event_ticker: str, trade_id: str,
                  yes_quote: str, no_quote: str, modeled_pair_edge: str) -> None:
    snapshot = await assert_clean_account()
    event = _event_base(
        trade_id=trade_id, symbol=symbol, event_ticker=event_ticker,
        event_type="CAPITAL_RESERVED", status="CAPITAL_RESERVED",
    )
    event.update({"amount": str(RESERVED_USD), "legs_required": 2, "legs_filled": 0})
    event["metadata"].update({
        "reserved_usd": str(RESERVED_USD), "q": 1,
        "yes_quote": yes_quote, "no_quote": no_quote,
        "modeled_pair_edge": modeled_pair_edge,
        "gemini_account_snapshot_before_reserve": snapshot,
        "guaranteed_profit": False,
    })
    _append(event)
    _write_open({
        "trade_id": trade_id,
        "symbol": symbol,
        "event_ticker": event_ticker,
        "reserved_usd": str(RESERVED_USD),
        "reserved_at": event["event_ts"],
        "status": "CAPITAL_RESERVED",
    })


async def release(*, symbol: str, event_ticker: str, trade_id: str, reason: str) -> None:
    event = _event_base(
        trade_id=trade_id, symbol=symbol, event_ticker=event_ticker,
        event_type="CAPITAL_RELEASED", status="CAPITAL_RELEASED",
    )
    event.update({"amount": "0", "legs_required": 2, "legs_filled": 0})
    event["event_id"] = _safe_id(f"{trade_id}:capital-released:{reason}")
    event["metadata"].update({"reserved_usd": "0", "release_reason": reason})
    _append(event)
    _clear_open()


async def result(*, symbol: str, event_ticker: str, trade_id: str,
                 status: str, legs_filled: int, metadata: Mapping[str, Any]) -> None:
    event = _event_base(
        trade_id=trade_id, symbol=symbol, event_ticker=event_ticker,
        event_type="EXECUTION_RESULT", status=status,
    )
    event.update({"quantity": "1", "legs_required": 2, "legs_filled": int(legs_filled)})
    event["event_id"] = _safe_id(f"{trade_id}:execution-result:{status}")
    event["metadata"].update({"guaranteed_profit": False, **dict(metadata)})
    _append(event)
    current = local_open_state() or {"trade_id": trade_id, "symbol": symbol, "event_ticker": event_ticker}
    current.update({"status": status, "updated_at": event["event_ts"], "legs_filled": int(legs_filled)})
    _write_open(current)
