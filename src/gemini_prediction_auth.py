"""Gemini Prediction Markets authentication helpers.

This module contains authentication/transport plumbing only. It never submits an
order. Credentials are read from environment variables and are never logged.

Gemini time-based API keys use an epoch-seconds nonce. REST private calls use
Gemini's standard signed payload headers; Prediction Markets WebSocket auth uses
the same account-scoped key and time-based nonce.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import os
import threading
import time
from typing import Any

import httpx
import websockets
from dotenv import load_dotenv

load_dotenv()

GEMINI_REST = "https://api.gemini.com"
GEMINI_WS = "wss://ws.gemini.com"
MAX_NONCE_FUTURE_S = 25

_nonce_lock = threading.Lock()
_last_nonce = 0


def next_nonce() -> int:
    """Return a monotonic epoch-seconds nonce for a Gemini time-based API key.

    Gemini rejects time-based nonces that drift more than roughly 30 seconds from
    server time. When multiple private requests occur inside one second, advance
    by one second to preserve monotonicity, but fail closed before local request
    volume can push the nonce outside the accepted clock window.
    """
    global _last_nonce
    with _nonce_lock:
        now = int(time.time())
        candidate = max(now, _last_nonce + 1)
        if candidate > now + MAX_NONCE_FUTURE_S:
            raise RuntimeError("Gemini time-based nonce rate exceeded")
        _last_nonce = candidate
        return candidate


def credentials() -> tuple[str, str]:
    return (
        os.getenv("GEMINI_API_KEY", "").strip(),
        os.getenv("GEMINI_API_SECRET", "").strip(),
    )


def has_credentials() -> bool:
    key, secret = credentials()
    return bool(key and secret)


def is_account_scoped_key(api_key: str | None = None) -> bool:
    key = (api_key if api_key is not None else credentials()[0]).strip()
    return key.startswith("account-")


def websocket_auth_headers(
    api_key: str,
    api_secret: str,
    *,
    nonce: int | None = None,
) -> dict[str, str]:
    """Build Gemini Prediction Markets authenticated WebSocket handshake headers."""
    value = int(nonce if nonce is not None else next_nonce())
    payload = base64.b64encode(str(value).encode("ascii")).decode("ascii")
    signature = hmac.new(
        api_secret.encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha384,
    ).hexdigest()
    return {
        "X-GEMINI-APIKEY": api_key,
        "X-GEMINI-NONCE": str(value),
        "X-GEMINI-PAYLOAD": payload,
        "X-GEMINI-SIGNATURE": signature,
    }


def rest_auth_headers(
    api_key: str,
    api_secret: str,
    path: str,
    *,
    nonce: int | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build Gemini private REST HMAC headers.

    The signed payload is base64(JSON({request, nonce, ...params})). The JSON is
    carried in X-GEMINI-PAYLOAD, not emitted to logs by this module.
    """
    value = int(nonce if nonce is not None else next_nonce())
    body: dict[str, Any] = {"request": path, "nonce": value}
    if params:
        body.update(params)
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    payload = base64.b64encode(raw).decode("ascii")
    signature = hmac.new(
        api_secret.encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha384,
    ).hexdigest()
    return {
        "X-GEMINI-APIKEY": api_key,
        "X-GEMINI-PAYLOAD": payload,
        "X-GEMINI-SIGNATURE": signature,
        "Content-Type": "text/plain",
        "Cache-Control": "no-cache",
    }


async def private_rest_request(
    method: str,
    path: str,
    *,
    api_key: str | None = None,
    api_secret: str | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> httpx.Response:
    key, secret = credentials()
    key = api_key or key
    secret = api_secret or secret
    if not key or not secret:
        raise RuntimeError("Gemini API credentials are not configured")
    headers = rest_auth_headers(key, secret, path, params=params)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.request(
            method.upper(),
            f"{GEMINI_REST}{path}",
            headers=headers,
            content=b"",
        )


def _websocket_header_kwarg() -> str:
    """Handle both websockets 12.x and newer keyword names."""
    params = inspect.signature(websockets.connect).parameters
    if "additional_headers" in params:
        return "additional_headers"
    if "extra_headers" in params:
        return "extra_headers"
    raise RuntimeError("Installed websockets.connect has no supported header argument")


async def authenticated_ws_connect(
    *,
    api_key: str | None = None,
    api_secret: str | None = None,
    timeout: float = 5.0,
):
    """Open an authenticated Gemini WebSocket connection; never places an order."""
    key, secret = credentials()
    key = api_key or key
    secret = api_secret or secret
    if not key or not secret:
        raise RuntimeError("Gemini API credentials are not configured")
    headers = websocket_auth_headers(key, secret)
    kwargs: dict[str, Any] = {"open_timeout": timeout, _websocket_header_kwarg(): headers}
    return await websockets.connect(GEMINI_WS, **kwargs)


async def verify_authenticated_ws(timeout: float = 5.0) -> bool:
    """Verify authenticated account-stream access without submitting any order."""
    ws = await authenticated_ws_connect(timeout=timeout)
    try:
        request_id = "readiness-positions"
        await ws.send(json.dumps({
            "id": request_id,
            "method": "SUBSCRIBE",
            "params": ["positions@account"],
        }))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            msg = json.loads(raw)
            if str(msg.get("id", "")) == request_id:
                return int(msg.get("status", 0)) == 200
        return False
    finally:
        await ws.close()
