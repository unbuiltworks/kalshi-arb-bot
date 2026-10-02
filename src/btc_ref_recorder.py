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

SERVICE MODE (Railway collector). With --forever (or BTC_REF_FOREVER=1) this
runs continuously and persists TWICE, deliberately:

  1. VOLUME, the durable store. Raw rows append to $BTC_REF_ROOT (default
     /state/btc-ref) in a per-UTC-day file, so a restart APPENDS to the same
     day's file rather than starting a new one -- the data survives restart
     because it is on the mounted volume, not in the container. Each process
     start writes a `session` row carrying its provenance (source URL, product,
     commit, service role, pid, start time), so a restart is visible in the data
     instead of being silently stitched over.
  2. LOG, the harvest path. Batched rows are echoed as BTC_REF_ROWS lines, the
     same transport the PMUS tape is harvested through. Every emission states
     `emitted` and `pending`, because the PMUS recorder's book cap was hit on
     every emission and the truncation was invisible until counted. A cap here
     is therefore never silent.

Observational only. No orders, no credentials, no account state. The Coinbase
public feed requires no authentication, so this service holds no secret at all.
Run: python3 -m src.btc_ref_recorder --hours 6
     python3 -m src.btc_ref_recorder --forever
"""
from __future__ import annotations

import argparse, asyncio, json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

import websockets

WS = "wss://ws-feed.exchange.coinbase.com"
PRODUCT = "BTC-USD"
OUTDIR = Path(os.environ.get("BTC_REF_ROOT") or "data/btc_ref")
SILENT_RECONNECT_S = 30.0
CLOCK_EMIT_S = 30.0
LOG_EMIT_S = float(os.environ.get("BTC_REF_LOG_EMIT_S") or 5.0)
LOG_CHUNK = int(os.environ.get("BTC_REF_LOG_CHUNK") or 120)
SERVICE_ROLE = os.environ.get("BTC_REF_SERVICE_ROLE") or "btc-ref-recorder"
EXPECT_ROLE = os.environ.get("BTC_REF_EXPECT_ROLE") or ""


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


def commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True,
                              timeout=5).stdout.strip() or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def emit_log(rows: list, kind: str, seq: int) -> int:
    """Echo rows to stdout for the log-harvest path, chunked and counted."""
    i = 0
    while i < len(rows):
        chunk = rows[i:i + LOG_CHUNK]
        i += len(chunk)
        seq += 1
        print("BTC_REF_ROWS " + json.dumps(
            {"kind": kind, "seq": seq, "emitted": len(chunk),
             "pending": len(rows) - i, "role": SERVICE_ROLE, "rows": chunk},
            separators=(",", ":")), flush=True)
    return seq


class Sink:
    """Append-only, one file per UTC day, so a restart continues the same file."""

    def __init__(self, path: Path, daily: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._dir = path.parent
        self._daily = daily
        self._day = None
        self._fh = None if daily else path.open("a", encoding="utf-8")
        if daily:
            self._roll()

    def _roll(self) -> None:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        if day != self._day:
            if self._fh:
                self._fh.close()
            self._day = day
            self._fh = (self._dir / f"btcref_{day}.jsonl").open("a", encoding="utf-8")

    def write(self, ev: dict) -> None:
        if self._daily:
            self._roll()
        self._fh.write(json.dumps(ev, separators=(",", ":")) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


async def record(sink: Sink, stop: asyncio.Event) -> dict:
    n = {"ref_trade": 0, "ref_bbo": 0, "reconnect": 0}
    backoff = 1.0
    last_clock = 0.0
    last_log = time.monotonic()
    pend: list = []
    seq = 0
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
                        row = {"kind": "ref_trade", "ev": ev, "ev_ns": ev_ns,
                               "t_ns": t_ns, "skew_ns": skew,
                               "px": m.get("price"), "qty": m.get("size"),
                               "side": m.get("side"), "tid": m.get("trade_id")}
                        sink.write(row); pend.append(row)
                        n["ref_trade"] += 1
                    elif ty == "ticker":
                        row = {"kind": "ref_bbo", "ev": ev, "ev_ns": ev_ns,
                               "t_ns": t_ns, "skew_ns": skew,
                               "bid": m.get("best_bid"), "ask": m.get("best_ask"),
                               "px": m.get("price")}
                        sink.write(row); pend.append(row)
                        n["ref_bbo"] += 1
                    now = time.monotonic()
                    if pend and now - last_log >= LOG_EMIT_S:
                        seq = emit_log(pend, "btc_ref_rows", seq)
                        pend = []
                        last_log = now
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


async def main_async(hours: float, forever: bool) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sink = (Sink(OUTDIR / "x.jsonl", daily=True) if forever
            else Sink(OUTDIR / f"btcref_{stamp}.jsonl"))
    prov = {"kind": "session", "t_ns": time.time_ns(), "started_utc": stamp,
            "source": WS, "product": PRODUCT, "channels": ["matches", "ticker"],
            "commit": commit(), "role": SERVICE_ROLE, "pid": os.getpid(),
            "root": str(OUTDIR), "forever": forever,
            "auth": "none -- public feed, no credential"}
    sink.write(prov)
    print("BTC_REF_SESSION " + json.dumps(prov, separators=(",", ":")), flush=True)
    stop = asyncio.Event()
    task = asyncio.create_task(record(sink, stop))
    try:
        if forever:
            await task
        else:
            await asyncio.wait_for(asyncio.shield(task), timeout=hours * 3600)
    except asyncio.TimeoutError:
        stop.set()
        n = await task
        print(json.dumps(n))
    finally:
        sink.close()


def main() -> None:
    # Role tripwire, same contract as deploy/pmus_ws_recorder: if a branch push
    # ever cross-builds services, the wrong worker exits loudly instead of
    # quietly recording the wrong thing.
    if EXPECT_ROLE and SERVICE_ROLE != EXPECT_ROLE:
        print(f"SERVICE_ROLE_MISMATCH role={SERVICE_ROLE} expected={EXPECT_ROLE}",
              flush=True)
        sys.exit(2)
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--forever", action="store_true",
                    default=bool(os.environ.get("BTC_REF_FOREVER")))
    a = ap.parse_args()
    asyncio.run(main_async(a.hours, a.forever))


if __name__ == "__main__":
    main()
