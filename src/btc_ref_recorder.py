"""External BTC reference recorder (read-only, public endpoints, no credentials).

WHY THIS EXISTS. The pre-fill cancellability diagnostic (4cfc293f) could not run
its external-BTC leg at all: the only BTC series in the repo is
data/btc15m/coinbase_1m.jsonl -- 1-MINUTE candles, 60,000 ms, one to three
orders of magnitude coarser than the 100ms..5s horizons the question needs. No
reachable venue serves sub-minute BTC HISTORY. A realtime feed, however, does
give sub-second resolution, so the reference has to be recorded FORWARD from now
alongside the PMUS tape rather than reconstructed backwards.

SOURCE. Coinbase Exchange public WebSocket wss://ws-feed.exchange.coinbase.com,
channels `matches` (every execution) and `ticker` (best bid/ask on change).
Measured here: venue timestamps carry MICROSECOND precision
(2026-10-02T12:35:59.201661Z), which is finer than the PMUS book cadence
(inter-arrival p50 0.101 s), so the reference is not the limiting resolution.

Coinbase is a PROXY, not the settlement source. PMUS BTC Up/Down settles off its
own reference; nothing produced from this file is a settlement replay. It is
used only to ask whether external price information moved BEFORE a PMUS fill.

TIMESTAMP DISCIPLINE -- the point of the whole file. Every row carries BOTH:

  ev        the venue's own event timestamp, verbatim as the string it sent
  ev_ns     that timestamp parsed to integer ns, for arithmetic only
  t_ns      LOCAL receipt time, time.time_ns(), taken immediately on recv

Lead/lag between this feed and PMUS must be MEASURED from these two clocks, not
assumed. Venue-vs-venue comparison on `ev` assumes both venues' clocks agree;
receipt-vs-receipt on `t_ns` assumes both transports are equally fast. Neither
assumption is granted, so both are preserved and their difference (`skew_ns`,
emitted on every row) is itself an observable. A `clock` row is emitted every
30 s carrying the same quantity sampled independently, so clock drift over a
session can be separated from per-message transport jitter.

Rows:
  {"kind":"ref_trade","ev","ev_ns","t_ns","skew_ns","px","qty","side","tid"}
      side is the Coinbase MAKER side convention as sent; recorded verbatim and
      not reinterpreted here.
  {"kind":"ref_bbo","ev","ev_ns","t_ns","skew_ns","bid","ask","px"}
  {"kind":"clock","t_ns","ev_ns","skew_ns","n_trade","n_bbo"}
  {"kind":"error","t_ns","where","error"}            reconnects are not silent

Self-recovering: reconnects with backoff, and forces a reconnect on a silently
half-dead socket (recv times out forever with no exception).

Observational only. No orders, no credentials, no account state.
Run: python3 -m src.btc_ref_recorder --hours 6
"""
from __future__ import annotations

import argparse, asyncio, json, time
from datetime import datetime, timezone
from pathlib import Path

import websockets

WS = "wss://ws-feed.exchange.coinbase.com"
PRODUCT = "BTC-USD"
OUTDIR = Path("data/btc_ref")
SILENT_RECONNECT_S = 30.0
CLOCK_EMIT_S = 30.0


def parse_ev_ns(s: str | None) -> int | None:
    """Coinbase sends RFC3339 with 6 (sometimes 9) fractional digits."""
    if not s:
        return None
    try:
        head, _, frac = s.rstrip("Z").partition(".")
        base = int(datetime.strptime(head, "%Y-%m-%dT%H:%M:%S")
                   .replace(tzinfo=timezone.utc).timestamp())
        return base * 1_000_000_000 + int((frac + "000000000")[:9])
    except Exception:
        return None


class Sink:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def write(self, ev: dict) -> None:
        self._fh.write(json.dumps(ev, separators=(",", ":")) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


async def record(sink: Sink, stop: asyncio.Event) -> dict:
    n = {"ref_trade": 0, "ref_bbo": 0, "reconnect": 0}
    backoff = 1.0
    last_clock = 0.0
    while not stop.is_set():
        try:
            async with websockets.connect(WS, open_timeout=10, ping_interval=15,
                                          ping_timeout=10, max_queue=10000) as ws:
                await ws.send(json.dumps({"type": "subscribe",
                                          "product_ids": [PRODUCT],
                                          "channels": ["matches", "ticker"]}))
                backoff = 1.0
                last_msg = time.monotonic()
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        last_msg = time.monotonic()
                    except asyncio.TimeoutError:
                        if time.monotonic() - last_msg > SILENT_RECONNECT_S:
                            sink.write({"kind": "error", "t_ns": time.time_ns(),
                                        "where": "ws", "error": "SilentFeedReconnect"})
                            n["reconnect"] += 1
                            break
                        continue
                    t_ns = time.time_ns()
                    try:
                        m = json.loads(raw)
                    except Exception:
                        continue
                    ty = m.get("type")
                    ev = m.get("time")
                    ev_ns = parse_ev_ns(ev)
                    skew = (t_ns - ev_ns) if ev_ns is not None else None
                    if ty in ("match", "last_match"):
                        sink.write({"kind": "ref_trade", "ev": ev, "ev_ns": ev_ns,
                                    "t_ns": t_ns, "skew_ns": skew,
                                    "px": m.get("price"), "qty": m.get("size"),
                                    "side": m.get("side"),
                                    "tid": m.get("trade_id")})
                        n["ref_trade"] += 1
                    elif ty == "ticker":
                        sink.write({"kind": "ref_bbo", "ev": ev, "ev_ns": ev_ns,
                                    "t_ns": t_ns, "skew_ns": skew,
                                    "bid": m.get("best_bid"), "ask": m.get("best_ask"),
                                    "px": m.get("price")})
                        n["ref_bbo"] += 1
                    now = time.monotonic()
                    if now - last_clock > CLOCK_EMIT_S:
                        last_clock = now
                        sink.write({"kind": "clock", "t_ns": t_ns, "ev_ns": ev_ns,
                                    "skew_ns": skew, "n_trade": n["ref_trade"],
                                    "n_bbo": n["ref_bbo"]})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            sink.write({"kind": "error", "t_ns": time.time_ns(), "where": "connect",
                        "error": f"{type(e).__name__}: {e}"})
            n["reconnect"] += 1
            await asyncio.sleep(min(backoff, 30.0))
            backoff *= 2
    return n


async def main_async(hours: float) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sink = Sink(OUTDIR / f"btcref_{stamp}.jsonl")
    stop = asyncio.Event()
    task = asyncio.create_task(record(sink, stop))
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=hours * 3600)
    except asyncio.TimeoutError:
        stop.set()
        n = await task
        print(json.dumps(n))
    finally:
        sink.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args()
    asyncio.run(main_async(a.hours))


if __name__ == "__main__":
    main()
