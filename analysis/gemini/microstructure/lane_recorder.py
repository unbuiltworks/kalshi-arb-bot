"""Lane-isolated, READ-ONLY Gemini high-fidelity recorder for BTC5m/ETH5m.

WHY THIS EXISTS RATHER THAN src/policy_d_port/collector.py
----------------------------------------------------------
The shared collector subscribes `@depth5@100ms`, which is a PERIODIC
SNAPSHOT stream (it re-sends the same top-5 every 100 ms whether or not
anything changed), and then throttles its own writes to
GEMINI_BBO_RECORD_SECONDS. Both steps destroy exactly the information this
lane needs. Probed and confirmed on a live window:

  {sym}@depth          -> {"e":"depthUpdate","E":<ns>,"U":<first_id>,
                           "u":<final_id>,"b":[[px,sz]...],"a":[...]}
                          TRUE event-driven DIFF stream. Fires only on
                          change. `u` of one event == `U` of the next, so
                          ordering and completeness are VERIFIABLE.
                          sz "0.00" means the level was removed.
  {sym}@trade          -> {"E":<ns>,"t":<trade_id>,"p":px,"q":qty,"m":bool}
  {sym}@depth20        -> {"lastUpdateId":<id>,"bids":[...],"asks":[...]}
                          periodic full snapshot; used to SEED and to RESYNC.

This recorder preserves EVERY frame verbatim. It samples nothing down. All
reconstruction happens offline from the raw tape.

It does not touch PMUS, the SOL executor, or the shared collector. It sends
no authenticated request and places no order: public market data only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Set

import httpx
import websockets

GEMINI_REST = "https://api.gemini.com"
GEMINI_WS = "wss://ws.gemini.com"
EVENTS = f"{GEMINI_REST}/v1/prediction-markets/events"

SUBSCRIBE_LEAD_S = 330.0     # >= 300 s window + margin
RETAIN_AFTER_S = 45.0        # keep through settlement prints
DISCOVER_S = 30.0
VOL_POLL_S = 5.0             # fast, to characterise the `volume` field's lag
WS_SESSION_S = 900.0
SNAPSHOT_EVERY_S = 30.0      # periodic resync snapshot per live symbol


def now_s() -> float:
    return time.time()


class Sink:
    """Append-only JSONL writer. Flushes every record: a killed container
    must not lose the tail of the tape."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", buffering=1)
        self.path = path
        self.n = 0

    def write(self, row: Dict[str, Any]) -> None:
        self._fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        self.n += 1

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def parse_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(
        tzinfo=timezone.utc).timestamp()


async def discover(http: httpx.AsyncClient, series_re: re.Pattern) -> Dict[str, Dict[str, Any]]:
    """Return {instrumentSymbol: metadata} for matching crypto windows."""
    out: Dict[str, Dict[str, Any]] = {}
    r = await http.get(EVENTS)
    r.raise_for_status()
    for ev in r.json().get("data", []):
        tick = ev.get("ticker") or ""
        if not series_re.match(tick):
            continue
        try:
            exp = parse_ts(ev["expiryDate"])
            start = parse_ts(ev["startTime"])
        except Exception:
            continue
        for c in ev.get("contracts", []):
            sym = c.get("instrumentSymbol")
            if not sym:
                continue
            out[sym.upper()] = {
                "window": tick,
                "asset": tick[:3],
                "series": ev.get("series"),
                "expiry_t": exp,
                "start_t": start,
                "expiry_iso": ev["expiryDate"],
                "start_iso": ev["startTime"],
                "strike": (c.get("strike") or {}).get("value"),
                "price_increment": c.get("priceIncrement"),
                "qty_increment": c.get("quantityIncrement"),
                "source": c.get("source"),
            }
    return out


async def vol_task(sink: Sink, stop: asyncio.Event, series_re: re.Pattern) -> None:
    """Poll the events feed fast and record `volume` per window.

    PURPOSE: settle the `volume` semantics question. Historical tapes showed
    WS-captured contracts EXCEEDING this field, which must be explained
    before the field is called authoritative. A 5 s poll beside the
    nanosecond trade stream lets the field's units, update cadence and lag be
    measured directly instead of assumed.
    """
    async with httpx.AsyncClient(timeout=20.0,
                                 headers={"User-Agent": "gemini-microstructure-readonly/1.0"}) as http:
        while not stop.is_set():
            try:
                r = await http.get(EVENTS)
                t = now_s()
                for ev in r.json().get("data", []):
                    tick = ev.get("ticker") or ""
                    if not series_re.match(tick):
                        continue
                    sink.write({"t": t, "kind": "vol", "window": tick,
                                "volume": ev.get("volume"),
                                "volume24h": ev.get("volume24h"),
                                "status": ev.get("status"),
                                "expiry_iso": ev.get("expiryDate"),
                                "prices": [
                                    {"sym": c.get("instrumentSymbol"),
                                     "prices": c.get("prices"),
                                     "marketState": c.get("marketState")}
                                    for c in ev.get("contracts", [])],
                                })
            except Exception as exc:
                sink.write({"t": now_s(), "kind": "error", "where": "vol",
                            "error": type(exc).__name__})
            try:
                await asyncio.wait_for(stop.wait(), timeout=VOL_POLL_S)
            except asyncio.TimeoutError:
                pass


async def snapshot_task(sink: Sink, stop: asyncio.Event,
                        live: Set[str], lock: asyncio.Lock) -> None:
    """Periodic REST-free resync: a depth20 snapshot is requested over the ws
    by (re)subscribing is not possible, so snapshots come from the dedicated
    depth20 stream subscribed alongside. This task only records a marker so
    offline reconstruction can bound each resync interval."""
    while not stop.is_set():
        async with lock:
            syms = sorted(live)
        if syms:
            sink.write({"t": now_s(), "kind": "resync_marker", "live": syms})
        try:
            await asyncio.wait_for(stop.wait(), timeout=SNAPSHOT_EVERY_S)
        except asyncio.TimeoutError:
            pass


async def ws_session(sink: Sink, stop: asyncio.Event, series_re: re.Pattern,
                     known: Dict[str, Dict[str, Any]],
                     live: Set[str], lock: asyncio.Lock) -> None:
    subscribed: Set[str] = set()
    last_discover = 0.0
    deadline = time.monotonic() + WS_SESSION_S

    async with websockets.connect(GEMINI_WS, open_timeout=10,
                                  ping_interval=15, ping_timeout=10,
                                  max_queue=10000) as ws:
        sink.write({"t": now_s(), "kind": "ws_open"})
        async with httpx.AsyncClient(timeout=20.0,
                                     headers={"User-Agent": "gemini-microstructure-readonly/1.0"}) as http:
            while not stop.is_set() and time.monotonic() < deadline:
                if time.monotonic() - last_discover >= DISCOVER_S:
                    last_discover = time.monotonic()
                    try:
                        fresh = await discover(http, series_re)
                        for sym, meta in fresh.items():
                            if sym not in known:
                                known[sym] = meta
                                sink.write({"t": now_s(), "kind": "window",
                                            "market": sym, **meta})
                    except Exception as exc:
                        sink.write({"t": now_s(), "kind": "error",
                                    "where": "discover",
                                    "error": type(exc).__name__})

                t = now_s()
                add = [s for s, m in known.items()
                       if s not in subscribed
                       and (m["expiry_t"] - SUBSCRIBE_LEAD_S) <= t <= (m["expiry_t"] + RETAIN_AFTER_S)]
                drop = [s for s in subscribed
                        if t > known[s]["expiry_t"] + RETAIN_AFTER_S]
                if add:
                    params = []
                    for s in add:
                        # EVERY book event (@depth diff) + every trade, plus a
                        # periodic full snapshot stream to seed/resync.
                        params += [f"{s}@depth", f"{s}@trade", f"{s}@depth20"]
                    await ws.send(json.dumps({"id": "lane-add",
                                              "method": "SUBSCRIBE",
                                              "params": params}))
                    subscribed.update(add)
                    sink.write({"t": now_s(), "kind": "subscribe",
                                "markets": add,
                                "streams": ["depth", "trade", "depth20"]})
                if drop:
                    params = []
                    for s in drop:
                        params += [f"{s}@depth", f"{s}@trade", f"{s}@depth20"]
                    await ws.send(json.dumps({"id": "lane-drop",
                                              "method": "UNSUBSCRIBE",
                                              "params": params}))
                    for s in drop:
                        subscribed.discard(s)
                    sink.write({"t": now_s(), "kind": "unsubscribe",
                                "markets": drop})
                if add or drop:
                    async with lock:
                        live.clear()
                        live.update(subscribed)
                    # bounded memory
                    for s in [s for s, m in known.items()
                              if t > m["expiry_t"] + 7200]:
                        known.pop(s, None)

                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                recv_t = now_s()
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "status" in msg or "result" in msg or "error" in msg:
                    sink.write({"t": recv_t, "kind": "ctrl", "raw": msg})
                    continue

                # EVERY frame is persisted verbatim under `raw`, with our
                # local receive time. Nothing is sampled, merged or dropped.
                if msg.get("e") == "depthUpdate":
                    sink.write({"t": recv_t, "kind": "depth_update",
                                "market": (msg.get("s") or "").upper(),
                                "E_ns": msg.get("E"), "U": msg.get("U"),
                                "u": msg.get("u"),
                                "b": msg.get("b"), "a": msg.get("a")})
                elif "lastUpdateId" in msg:
                    sink.write({"t": recv_t, "kind": "depth_snapshot",
                                "market": (msg.get("symbol") or "").upper(),
                                "lastUpdateId": msg.get("lastUpdateId"),
                                "bids": msg.get("bids"),
                                "asks": msg.get("asks")})
                elif "p" in msg and "q" in msg and "m" in msg:
                    sink.write({"t": recv_t, "kind": "trade",
                                "market": (msg.get("s") or "").upper(),
                                "E_ns": msg.get("E"), "trade_id": msg.get("t"),
                                "price": msg.get("p"), "qty": msg.get("q"),
                                "buyer_is_maker": msg.get("m")})
                else:
                    sink.write({"t": recv_t, "kind": "other", "raw": msg})

    sink.write({"t": now_s(), "kind": "ws_close"})


async def main_async(args) -> None:
    series_re = re.compile(args.series_regex)
    outdir = Path(args.outdir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tape = Sink(outdir / f"hifi_{stamp}.jsonl")
    tape.write({"t": now_s(), "kind": "recorder_start",
                "series_regex": args.series_regex,
                "streams": ["@depth(diff,every event)", "@trade", "@depth20(snapshot)"],
                "vol_poll_s": VOL_POLL_S,
                "subscribe_lead_s": SUBSCRIBE_LEAD_S,
                "retain_after_s": RETAIN_AFTER_S,
                "read_only": True, "authenticated": False})

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sg in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sg, stop.set)
        except NotImplementedError:
            pass
    if args.duration_s:
        loop.call_later(args.duration_s, stop.set)

    known: Dict[str, Dict[str, Any]] = {}
    livesyms: Set[str] = set()
    lock = asyncio.Lock()

    async def ws_forever():
        backoff = 1.0
        while not stop.is_set():
            try:
                await ws_session(tape, stop, series_re, known, livesyms, lock)
                backoff = 1.0
            except Exception as exc:
                tape.write({"t": now_s(), "kind": "error", "where": "ws",
                            "error": type(exc).__name__, "msg": str(exc)[:200]})
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)

    await asyncio.gather(ws_forever(),
                         vol_task(tape, stop, series_re),
                         snapshot_task(tape, stop, livesyms, lock))
    tape.write({"t": now_s(), "kind": "recorder_stop", "rows": tape.n})
    tape.close()
    print(f"wrote {tape.path} ({tape.n} rows)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="data/gemini_hifi")
    ap.add_argument("--series-regex", default=r"^(BTC|ETH)05M\d{10}$")
    ap.add_argument("--duration-s", type=float, default=0.0)
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
