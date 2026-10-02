#!/usr/bin/env python3
"""READ-ONLY Gemini BTC15m L2 + trades recorder for the two-sided maker shadow.

Public market data only. wss://api.gemini.com/v2/marketdata needs no
credentials, imports no order path, and this file contains no mutation call.

Why this venue and this stream, having checked the alternatives:
  * the stream carries the FULL ladder (snapshot then deltas) AND the trade
    tape in the SAME message type (`l2_updates` has `changes` and `trades`),
    so book state and trade-through evidence are synchronized by
    construction. That is the precondition for a defensible maker fill, and
    it is what the PMUS gateway (a ~30s REST cache) cannot give.
  * Gemini is the venue with an actually-credited maker rebate
    (1.029% of maker notional, observed, analysis/gemini/GEMINI_P0_*).
  * no credentials are required, so nothing here is blocked by the
    environment gap that blocks the historical reconstruction.

Symbols are discovered from the PUBLIC /v1/prediction-markets/events feed and
refreshed, so new 15-minute windows are subscribed as they list. Every frame
is written with our receipt time; the venue's own timestamps are preserved
untouched inside the frame. Nothing is normalised at write time -- the
simulator rebuilds the book from these frames and must be able to see exactly
what the venue said.
"""
from __future__ import annotations

import asyncio, json, os, sys, time, urllib.request
from datetime import datetime, timezone

WS = "wss://api.gemini.com/v2/marketdata"
EVENTS = "https://api.gemini.com/v1/prediction-markets/events?limit=200"
OUT_DIR = "data/gemini_shadow"
REFRESH_S = 90


def live_symbols(prefix="BTC15M"):
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(
            EVENTS, headers={"Accept": "application/json"}), timeout=20))
    except Exception:
        return [], {}
    syms, meta = [], {}
    for e in d.get("data") or []:
        if not str(e.get("ticker", "")).startswith(prefix):
            continue
        if e.get("status") != "active":
            continue
        for c in e.get("contracts") or []:
            s = c.get("instrumentSymbol")
            if not s:
                continue
            syms.append(s)
            meta[s] = {k: c.get(k) for k in
                       ("priceIncrement", "quantityIncrement", "quantityMinimum",
                        "strike", "expiryDate", "effectiveDate", "ticker",
                        "marketState", "label")}
            meta[s]["event_ticker"] = e.get("ticker")
            meta[s]["event_expiry"] = e.get("expiryDate")
    return sorted(set(syms)), meta


async def run(seconds, prefix):
    import websockets
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = datetime.now(timezone.utc)
    path = os.path.join(OUT_DIR, f"gemini_{prefix}_{t0.strftime('%Y%m%dT%H%M%SZ')}.jsonl")
    deadline = time.time() + seconds
    subscribed = set()
    nframes = 0
    with open(path, "w") as fh:
        fh.write(json.dumps({"kind": "header", "started_utc": t0.isoformat(),
                             "ws": WS, "prefix": prefix, "read_only": True}) + "\n")
        while time.time() < deadline:
            try:
                async with websockets.connect(WS, open_timeout=15,
                                              ping_interval=20) as ws:
                    syms, meta = live_symbols(prefix)
                    if syms:
                        await ws.send(json.dumps({"type": "subscribe", "subscriptions":
                                                  [{"name": "l2", "symbols": syms}]}))
                        subscribed |= set(syms)
                        fh.write(json.dumps({"kind": "symbols", "t": datetime.now(
                            timezone.utc).isoformat(), "symbols": syms,
                            "meta": meta}) + "\n")
                    last_refresh = time.time()
                    while time.time() < deadline:
                        try:
                            m = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            break
                        fh.write(json.dumps({"kind": "frame",
                                             "t": datetime.now(timezone.utc).isoformat(),
                                             "f": json.loads(m)}) + "\n")
                        nframes += 1
                        if nframes % 200 == 0:
                            fh.flush()
                        if time.time() - last_refresh > REFRESH_S:
                            last_refresh = time.time()
                            syms, meta = live_symbols(prefix)
                            new = [s for s in syms if s not in subscribed]
                            if new:
                                await ws.send(json.dumps(
                                    {"type": "subscribe", "subscriptions":
                                     [{"name": "l2", "symbols": new}]}))
                                subscribed |= set(new)
                                fh.write(json.dumps({"kind": "symbols", "t":
                                    datetime.now(timezone.utc).isoformat(),
                                    "symbols": new, "meta": meta}) + "\n")
            except Exception as e:
                fh.write(json.dumps({"kind": "ws_error", "t": datetime.now(
                    timezone.utc).isoformat(), "error": str(e)[:200]}) + "\n")
                fh.flush()
                await asyncio.sleep(3)
        fh.flush()
    print(f"{path} frames={nframes} symbols={len(subscribed)}")
    return 0


if __name__ == "__main__":
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 3600
    pref = sys.argv[2] if len(sys.argv) > 2 else "BTC15M"
    sys.exit(asyncio.run(run(secs, pref)))
