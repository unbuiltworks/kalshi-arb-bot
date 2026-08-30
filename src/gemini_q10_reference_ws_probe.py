"""Read-only probe for Gemini's event reference-price WebSocket stream.

Uses public event discovery to locate the current BTC 5-minute event's
sourceDetails and subscribes to `{agency}:{index}@indexPrice` on wss://ws.gemini.com.
No credentials or order entry.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Mapping

import httpx
import websockets

REST = "https://api.gemini.com"
WS = "wss://ws.gemini.com"


def _source_details(row: Mapping[str, Any]) -> tuple[str, str] | None:
    details = row.get("sourceDetails")
    if not isinstance(details, Mapping):
        return None
    agency = str(details.get("agency") or "").strip()
    index = str(details.get("index") or "").strip()
    return (agency, index) if agency and index else None


async def current_btc5_source() -> tuple[str, str, str]:
    async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as http:
        response = await http.get(f"{REST}/v1/prediction-markets/events", params={
            "status":"active", "category":"Crypto", "limit":200, "offset":0,
        })
        response.raise_for_status()
        rows = response.json().get("data") or []
        candidates = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            ticker = str(row.get("ticker") or "").upper()
            if not ticker.startswith("BTC05M"):
                continue
            source = _source_details(row)
            if source is not None:
                candidates.append((str(row.get("expiryDate") or ""), ticker, source[0], source[1]))
        if not candidates:
            raise RuntimeError("no active BTC5 event with sourceDetails")
        candidates.sort()
        _, ticker, agency, index = candidates[0]
        return ticker, agency, index


async def probe(max_messages: int = 5, timeout_s: float = 5.0) -> dict[str, Any]:
    ticker, agency, index = await current_btc5_source()
    stream = f"{agency}:{index}@indexPrice"
    started = time.monotonic()
    frames = []
    async with websockets.connect(WS, open_timeout=3.0, close_timeout=1.0, ping_interval=10) as ws:
        connected_ms = (time.monotonic() - started) * 1000.0
        await ws.send(json.dumps({"id":"1","method":"SUBSCRIBE","params":[stream]}))
        deadline = time.monotonic() + timeout_s
        while len(frames) < max_messages and time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, deadline-time.monotonic()))
            except asyncio.TimeoutError:
                break
            received = time.monotonic()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"raw": str(raw)[:500]}
            frames.append({"received_after_connect_ms":round((received-started)*1000.0,3),"payload":payload})
    return {
        "classification":"READ_ONLY_PUBLIC_GEMINI_REFERENCE_WS_PROBE",
        "order_entry":False,
        "credentials_loaded":False,
        "ticker":ticker,
        "agency":agency,
        "index":index,
        "stream":stream,
        "connect_ms":round(connected_ms,3),
        "frames":frames,
    }


async def main() -> None:
    print("GEMINI_REFERENCE_WS_PROBE=" + json.dumps(await probe(), separators=(",",":"), default=str))


if __name__ == "__main__":
    asyncio.run(main())
