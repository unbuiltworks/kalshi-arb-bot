"""Measure how late each PUBLIC surface tells us about an L5 fill.

THE QUESTION. The FIDELITY runner reports a ~28.5 s feed lag from twelve
samples, and that single number is currently doing a lot of work: it is the
reason estimand B (arbitrary-state WAIT/ACT) is served as NOT READY. A number
that load-bearing has to be measured properly, over time, per surface, and with
its own error budget stated.

WHAT IS MEASURED. For every L5 fill, per surface:

    lag = our_first_observation_wallclock  -  source_event_timestamp

recorded as a raw observation, never as a running average. Five surfaces:

  activity_user   data-api /activity?user=L5     identity, 1 s timestamps
  trades_user     data-api /trades?user=L5       identity, 1 s timestamps
  trades_global   data-api /trades (unfiltered)  identity, 1 s timestamps
  clob_book_rest  clob /book?token_id=...        ANONYMOUS, ms timestamps
  clob_ws         wss market channel             ANONYMOUS, ms timestamps

The identity-bearing surfaces are joined to each other by `transactionHash`,
which is an exact key, so "the same event arrived here at X and there at Y" is
a fact rather than a fuzzy match.

THE MEASUREMENT'S OWN ERROR BUDGET, stated rather than buried:

  * POLL PHASE. A surface polled every P seconds is discovered on average P/2
    late purely from phase. Reported as `poll_interval_s` per surface so the
    bias is subtractable; it is NOT silently subtracted here.
  * SOURCE QUANTIZATION. data-api timestamps are INTEGER SECONDS. A fill at
    t=1.9 s is stamped 1, so the source clock alone contributes up to +1 s of
    apparent lag. CLOB timestamps are milliseconds and do not have this.
  * CLOCK SKEW. Our clock versus the venue's is measured directly from the
    HTTP `Date` response header on every poll, and reported alongside, so a
    skewed container cannot masquerade as venue latency.

WHAT THIS CANNOT DO. It cannot prove a faster feed exists; it can only find
one. If the fastest IDENTITY-bearing surface is still ~30 s late, that is an
OBSERVABILITY_LIMIT on realtime side/timing fidelity and is to be labelled as
one, not worked around by assuming a private feed.

READ ONLY. Public endpoints, no credentials, no order path.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
UA = {"User-Agent": "l5-latency-probe/1.0 (read-only research)"}
DATA = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

_lock = threading.Lock()


def _ca() -> ssl.SSLContext:
    for p in (os.environ.get("SSL_CERT_FILE"), "/root/.ccr/ca-bundle.crt"):
        if p and os.path.exists(p):
            return ssl.create_default_context(cafile=p)
    return ssl.create_default_context()


def get(url: str, timeout: float = 20.0) -> Dict[str, Any]:
    """GET with the receive instant and the server's own clock."""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout, context=_ca()) as fh:
            body = fh.read().decode("utf-8")
            date = fh.headers.get("Date")
        recv = time.time()
        skew = None
        if date:
            try:
                skew = recv - parsedate_to_datetime(date).timestamp()
            except Exception:
                skew = None
        return {"ok": True, "recv": recv, "body": body, "skew_s": skew}
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}


def emit(out: str, rec: Dict[str, Any]) -> None:
    with _lock:
        with open(out, "a") as fh:
            fh.write(json.dumps(rec) + "\n")


# ------------------------------------------------------------ REST surfaces

SURFACES = {
    "activity_user": (lambda: f"{DATA}/activity?user={ADDR}&limit=500&type=TRADE", 1.0),
    "trades_user":   (lambda: f"{DATA}/trades?user={ADDR}&limit=500", 1.0),
    "trades_global": (lambda: f"{DATA}/trades?limit=500", 1.0),
}


def poll_rest(name: str, out: str, stop: threading.Event) -> None:
    url_fn, interval = SURFACES[name]
    seen: set = set()
    primed = False
    while not stop.is_set():
        t_start = time.time()
        r = get(url_fn())
        if r["ok"]:
            try:
                rows = json.loads(r["body"])
            except ValueError:
                rows = []
            fresh = []
            for x in rows if isinstance(rows, list) else []:
                if str(x.get("proxyWallet", "")).lower() != ADDR:
                    continue
                th = x.get("transactionHash")
                key = (th, x.get("asset"), x.get("side"), x.get("size"),
                       x.get("price"), x.get("timestamp"))
                k = json.dumps(key, sort_keys=True)
                if k in seen:
                    continue
                seen.add(k)
                fresh.append((k, x))
            # the FIRST poll sees the whole backlog; those are not discoveries
            if primed:
                for k, x in fresh:
                    src = float(x.get("timestamp") or 0)
                    emit(out, {
                        "kind": "obs", "surface": name, "poll_interval_s": interval,
                        "source_ts": src, "observed_at": r["recv"],
                        "lag_s": round(r["recv"] - src, 3),
                        "clock_skew_s": (round(r["skew_s"], 3)
                                         if r["skew_s"] is not None else None),
                        "tx": x.get("transactionHash"), "asset": x.get("asset"),
                        "cid": x.get("conditionId"), "slug": x.get("slug"),
                        "side": x.get("side"), "size": x.get("size"),
                        "price": x.get("price"), "event_key": k,
                    })
            else:
                primed = True
                emit(out, {"kind": "primed", "surface": name,
                           "backlog": len(fresh), "at": r["recv"]})
        else:
            emit(out, {"kind": "err", "surface": name, "err": r["err"],
                       "at": time.time()})
        stop.wait(max(0.0, interval - (time.time() - t_start)))


# ------------------------------------------------------- CLOB book (anonymous)

def live_tokens() -> List[Dict[str, str]]:
    """clobTokenIds for the BTC 15m/5m windows currently open."""
    out: List[Dict[str, str]] = []
    now = int(time.time())
    for horizon, dur in (("15m", 900), ("5m", 300)):
        start = (now // dur) * dur
        slug = f"btc-updown-{horizon}-{start}"
        r = get(f"{GAMMA}/markets?slug={slug}")
        if not r["ok"]:
            continue
        try:
            ms = json.loads(r["body"])
        except ValueError:
            continue
        for m in ms if isinstance(ms, list) else []:
            t = m.get("clobTokenIds")
            t = json.loads(t) if isinstance(t, str) else t
            for tid in (t or [])[:1]:
                out.append({"slug": slug, "token": tid,
                            "cid": m.get("conditionId", "")})
    return out


def poll_book(out: str, stop: threading.Event, interval: float = 1.0) -> None:
    toks, refreshed = live_tokens(), time.time()
    last_hash: Dict[str, str] = {}
    while not stop.is_set():
        t_start = time.time()
        if t_start - refreshed > 120:
            toks, refreshed = live_tokens(), t_start
        for t in toks:
            r = get(f"{CLOB}/book?token_id={t['token']}")
            if not r["ok"]:
                continue
            try:
                b = json.loads(r["body"])
            except ValueError:
                continue
            h = b.get("hash")
            if h and h != last_hash.get(t["token"]):
                last_hash[t["token"]] = h
                src = float(b.get("timestamp") or 0) / 1000.0
                emit(out, {
                    "kind": "obs", "surface": "clob_book_rest",
                    "poll_interval_s": interval, "source_ts": src,
                    "observed_at": r["recv"], "lag_s": round(r["recv"] - src, 3),
                    "clock_skew_s": (round(r["skew_s"], 3)
                                     if r["skew_s"] is not None else None),
                    "slug": t["slug"], "cid": t["cid"], "hash": h,
                    "identity_bearing": False,
                })
        stop.wait(max(0.0, interval - (time.time() - t_start)))


# ------------------------------------------------------------ CLOB websocket

def run_ws(out: str, stop: threading.Event) -> None:
    import asyncio
    try:
        import websockets
    except ImportError:
        emit(out, {"kind": "err", "surface": "clob_ws",
                   "err": "websockets not installed", "at": time.time()})
        return

    async def go() -> None:
        while not stop.is_set():
            toks = [t["token"] for t in live_tokens()]
            if not toks:
                await asyncio.sleep(5)
                continue
            try:
                async with websockets.connect(WS_URL, open_timeout=20,
                                              ssl=_ca()) as ws:
                    await ws.send(json.dumps({"assets_ids": toks,
                                              "type": "market"}))
                    opened = time.time()
                    while not stop.is_set() and time.time() - opened < 120:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=15)
                        except asyncio.TimeoutError:
                            continue
                        recv = time.time()
                        try:
                            d = json.loads(raw)
                        except ValueError:
                            continue
                        for ev in (d if isinstance(d, list) else [d]):
                            if not isinstance(ev, dict):
                                continue
                            ts = ev.get("timestamp")
                            if not ts:
                                continue
                            src = float(ts) / 1000.0
                            emit(out, {
                                "kind": "obs", "surface": "clob_ws",
                                "poll_interval_s": 0.0, "source_ts": src,
                                "observed_at": recv,
                                "lag_s": round(recv - src, 3),
                                "clock_skew_s": None,
                                "event_type": ev.get("event_type"),
                                "asset": ev.get("asset_id"),
                                "cid": ev.get("market"),
                                "identity_bearing": False,
                            })
            except Exception as e:
                emit(out, {"kind": "err", "surface": "clob_ws",
                           "err": f"{type(e).__name__}: {e}", "at": time.time()})
                await asyncio.sleep(3)

    asyncio.run(go())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--out", default="analysis/l5/latency/observations.jsonl")
    a = ap.parse_args(argv)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    stop = threading.Event()
    threads = [threading.Thread(target=poll_rest, args=(n, a.out, stop),
                                daemon=True) for n in SURFACES]
    threads.append(threading.Thread(target=poll_book, args=(a.out, stop),
                                    daemon=True))
    threads.append(threading.Thread(target=run_ws, args=(a.out, stop),
                                    daemon=True))
    emit(a.out, {"kind": "run_start", "at": time.time(),
                 "minutes": a.minutes, "surfaces": list(SURFACES) +
                 ["clob_book_rest", "clob_ws"]})
    for t in threads:
        t.start()
    stop.wait(a.minutes * 60)
    stop.set()
    for t in threads:
        t.join(timeout=10)
    emit(a.out, {"kind": "run_end", "at": time.time()})
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
