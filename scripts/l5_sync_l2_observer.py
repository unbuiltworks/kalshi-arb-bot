"""SYNCHRONIZED L5 + native Polymarket L2 observation on BTC 5-minute markets.

THE ONE QUESTION THIS EXISTS TO ANSWER. When consecutive L5 fills walk .31 ->
.32 -> .33, did the book move through those levels sequentially (REACTIVE
repricing), or were several levels already resting and swept by one move
(LADDER)? `analysis/l5/requote/L5_NEXT_ACTION_PREDICTORS.md` established that
fill-only history CANNOT separate these, and they imply completely different
bots.

READ ONLY. No credentials, no orders, no strategy. It cannot trade: there is no
transport, no signer and no POST anywhere in it.

REUSE, NOT DUPLICATION. Window enumeration, slug construction, the gamma/CLOB
book lookup, the activity endpoint and fill dedupe all come from the existing
5m coverage collector unchanged:

    src.l5_5m_coverage.recorder   window_start, slug, duration, dedupe_fills
    src.l5_5m_coverage.run        GAMMA, CLOB, ACTIVITY, L5, UA, _get

WHAT IS NEW, and the only reason a new file exists: the existing collector polls
the REST book at 1 Hz, which cannot resolve a sub-second sweep. This subscribes
to the NATIVE CLOB websocket so book and trade events arrive at the venue's own
resolution.

    wss://ws-subscriptions-clob.polymarket.com/ws/market

TIME IS KEPT TWICE, ALWAYS. Every record carries `recv_utc`/`recv_mono` (OUR
receipt) separately from whatever timestamp the venue put inside the payload.
Activity receipt time is NEVER L5's decision time: the previous pass measured a
median collection lag of 7,993 s on the batch feed, and reading receipt time as
decision time is the specific error that invites.

ACTIVITY CADENCE. 2 s, with limit=100. L5 runs ~5 fills/min, so 100 rows is many
minutes of overlap and no fill can be missed between polls; the dedupe is by
content, not by cursor. Faster than this buys no information because fills carry
their own venue timestamps, and it risks a rate-limit that would present as a
quiet venue -- the failure the existing collector documents.

    python3 scripts/l5_sync_l2_observer.py [windows]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, ".")

from src.l5_5m_coverage import recorder as R                     # noqa: E402
from src.l5_5m_coverage import run as C                          # noqa: E402

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
OUT_DIR = "analysis/l5/sync_l2"
HORIZON = "5m"
ASSET = "btc"
PRE_ROLL_S = 45.0          # open capture before the window starts
TAIL_S = 20.0              # keep capturing just past the close
ACTIVITY_POLL_S = 2.0
WINDOW_S = R.HORIZONS[HORIZON]

_STOP = threading.Event()


def stamp() -> Dict[str, Any]:
    """OUR receipt time, kept separate from any venue timestamp."""
    return {"recv_utc": datetime.now(timezone.utc).isoformat(),
            "recv_mono": time.monotonic(), "recv_epoch": time.time()}


def token_ids(slug: str) -> Dict[str, Any]:
    """The market's CLOB token ids, plus the raw gamma metadata."""
    m = C._get(C.GAMMA.format(slug=slug))
    meta = (m or [None])[0] if isinstance(m, list) else None
    if not meta:
        return {"ok": False, "reason": "gamma served no market for this slug"}
    try:
        tids = json.loads(meta.get("clobTokenIds") or "[]")
    except ValueError:
        return {"ok": False, "reason": "clobTokenIds not parseable"}
    if len(tids) < 2:
        return {"ok": False, "reason": f"expected 2 token ids, got {len(tids)}"}
    try:
        outcomes = json.loads(meta.get("outcomes") or "[]")
    except ValueError:
        outcomes = []
    return {"ok": True, "token_ids": [str(t) for t in tids],
            "outcomes": outcomes, "condition_id": meta.get("conditionId"),
            "question": meta.get("question")}


class Writer:
    """One JSONL per window. Append-only, flushed per record."""

    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.fh = open(path, "a")
        self.lock = threading.Lock()
        self.counts: Dict[str, int] = {}

    def put(self, kind: str, payload: Any, **extra) -> None:
        rec = {"kind": kind, **stamp(), **extra, "payload": payload}
        with self.lock:
            self.fh.write(json.dumps(rec, default=str) + "\n")
            self.fh.flush()
            self.counts[kind] = self.counts.get(kind, 0) + 1

    def close(self) -> None:
        with self.lock:
            self.fh.close()


async def ws_capture(w: Writer, tids: List[str], until: float) -> None:
    """Subscribe to the native market channel and record every frame verbatim."""
    import websockets
    backoff = 1.0
    while time.time() < until and not _STOP.is_set():
        try:
            async with websockets.connect(WS_URL, open_timeout=20,
                                          max_size=8 * 1024 * 1024) as ws:
                await ws.send(json.dumps({"assets_ids": tids,
                                          "type": "market"}))
                w.put("ws_subscribed", {"assets_ids": tids})
                backoff = 1.0
                while time.time() < until and not _STOP.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        msg = {"unparsed": str(raw)[:4000]}
                    w.put("ws", msg)
        except Exception as exc:                                 # noqa: BLE001
            w.put("ws_error", {"error": f"{type(exc).__name__}: {exc}"[:400]})
            await asyncio.sleep(backoff)
            backoff = min(20.0, backoff * 2)


def activity_capture(w: Writer, until: float) -> None:
    """Poll L5's public activity. Records the RAW rows; dedupe is by content.

    A failed read is recorded as a failure, never as an empty venue -- that
    conflation is what hid a 400 for four windows in the earlier collector.
    """
    seen = set()
    while time.time() < until and not _STOP.is_set():
        a = C._get(C.ACTIVITY.format(user=C.L5))
        if a is None:
            w.put("activity_read_failed", {"reason": "read returned None"})
        else:
            fresh = []
            for r in (a or []):
                key = (str(r.get("transactionHash")), str(r.get("conditionId")),
                       str(r.get("timestamp")), str(r.get("price")),
                       str(r.get("size")), str(r.get("outcome")),
                       str(r.get("side")))
                if key in seen:
                    continue
                seen.add(key)
                fresh.append(r)
            w.put("activity", fresh, n_new=len(fresh), n_returned=len(a or []))
        time.sleep(ACTIVITY_POLL_S)


def rest_book_snapshot(w: Writer, slug: str, cache: Dict[str, Any]) -> None:
    """One REST book read, as an independent cross-check on the WS state."""
    book, src_ts = C.book_for(slug, cache)
    w.put("rest_book", book, venue_ts=src_ts,
          reason=None if book else "venue served no two-sided book")


def observe_window(start: int) -> Dict[str, Any]:
    slug = R.slug(ASSET, start, HORIZON)
    ids = token_ids(slug)
    path = os.path.join(OUT_DIR, f"{slug}.jsonl")
    w = Writer(path)
    w.put("window_open", {"slug": slug, "window_start_epoch": start,
                          "window_end_epoch": start + WINDOW_S,
                          "horizon": HORIZON, "ids": ids,
                          "l5_wallet": C.L5, "l5_handle": C.L5_HANDLE,
                          "activity_poll_s": ACTIVITY_POLL_S,
                          "ws_url": WS_URL})
    if not ids.get("ok"):
        w.put("window_abandoned", {"reason": ids.get("reason")})
        w.close()
        return {"slug": slug, "ok": False, "reason": ids.get("reason"),
                "path": path}
    until = start + WINDOW_S + TAIL_S
    th = threading.Thread(target=activity_capture, args=(w, until), daemon=True)
    th.start()
    cache: Dict[str, Any] = {}

    def snapshots() -> None:
        """A REST book read every 10s, as an independent cross-check on the WS
        reconstruction. Written as a plain loop: the comprehension this replaced
        computed its iteration count once and was easy to misread."""
        while time.time() < until and not _STOP.is_set():
            rest_book_snapshot(w, slug, cache)
            time.sleep(10.0)

    snap = threading.Thread(target=snapshots, daemon=True)
    snap.start()
    try:
        asyncio.run(ws_capture(w, ids["token_ids"], until))
    except Exception as exc:                                     # noqa: BLE001
        w.put("ws_fatal", {"error": f"{type(exc).__name__}: {exc}"[:400]})
    th.join(timeout=5.0)
    counts = dict(w.counts)
    w.put("window_close", {"counts": counts})
    w.close()
    return {"slug": slug, "ok": True, "path": path, "counts": counts}


def main(argv) -> int:
    n = int(argv[1]) if len(argv) > 1 else 4
    done = []
    for k in range(n):
        if _STOP.is_set():
            break
        start = R.window_start(time.time(), 0, HORIZON)
        wait = (start - PRE_ROLL_S) - time.time()
        if wait > 0:
            time.sleep(wait)
        elif time.time() > start + WINDOW_S * 0.5:
            #: more than half the window already gone: take the NEXT one rather
            #: than record a partial that cannot answer the sequencing question
            start = R.window_start(time.time(), 1, HORIZON)
            time.sleep(max(0.0, (start - PRE_ROLL_S) - time.time()))
        res = observe_window(start)
        done.append(res)
        print("WINDOW_DONE " + json.dumps(res, default=str), flush=True)
    print("OBSERVER_DONE " + json.dumps({"windows": done}, default=str),
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
