"""Gemini Prediction Markets authentication helpers.

This module contains authentication/transport plumbing only. It never submits an
order. Credentials are read from environment variables and are never logged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

GEMINI_REST = "https://api.gemini.com"
MAX_NONCE_FUTURE_S = 25
_nonce_lock = threading.Lock()
_last_nonce = 0


def next_nonce() -> int:
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


def rest_auth_headers(api_key: str, api_secret: str, path: str, *, nonce: int | None = None, params: dict[str, Any] | None = None) -> dict[str, str]:
    value = int(nonce if nonce is not None else next_nonce())
    body: dict[str, Any] = {"request": path, "nonce": value}
    if params:
        body.update(params)
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    payload = base64.b64encode(raw).decode("ascii")
    signature = hmac.new(api_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha384).hexdigest()
    return {
        "X-GEMINI-APIKEY": api_key,
        "X-GEMINI-PAYLOAD": payload,
        "X-GEMINI-SIGNATURE": signature,
        "Content-Type": "text/plain",
        "Cache-Control": "no-cache",
    }


async def private_rest_request(method: str, path: str, *, api_key: str | None = None, api_secret: str | None = None, params: dict[str, Any] | None = None, timeout: float = 10.0) -> httpx.Response:
    key, secret = credentials()
    key = api_key or key
    secret = api_secret or secret
    if not key or not secret:
        raise RuntimeError("Gemini API credentials are not configured")
    headers = rest_auth_headers(key, secret, path, params=params)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.request(method.upper(), f"{GEMINI_REST}{path}", headers=headers, content=b"")
