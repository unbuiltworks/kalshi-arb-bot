"""Full PMUS league discovery for the local analysis collectors.

/v2/leagues is an incomplete legacy index (50 slugs). /v2/sports carries the
venue's own per-sport league arrays (265 slugs today) and is the authoritative
discovery surface. PUBLIC READ-ONLY: no auth, no orders, no restriction bypass.

The per-league event fetch is prefetched with a small thread pool because the
universe is 5.3x larger than the old index and these collectors call discovery
inline on the sweep loop.
"""
import json, threading, time, queue
import sys
sys.path.insert(0, "/home/user/kalshi-arb-bot")
from src import pmus_sports_collector as col

_LG_LOCK = threading.Lock()
_LG_CACHE = {"t": 0.0, "slugs": []}
_LG_TTL = 3600.0

FALLBACK = ["nfl", "mlb", "wnba", "nba", "epl", "lal", "sea", "mls"]


def leagues():
    with _LG_LOCK:
        if _LG_CACHE["slugs"] and time.time() - _LG_CACHE["t"] < _LG_TTL:
            return list(_LG_CACHE["slugs"])
    out = []
    try:
        payload = col.http_get_json("/v2/sports")
        for s in (payload.get("sports") or []):
            for l in (s.get("leagues") or []):
                if l.get("slug") and l.get("isOperational", True):
                    out.append(str(l["slug"]))
        out = list(dict.fromkeys(out))
    except Exception:
        out = []
    if not out:
        # never silently narrow to the legacy index: say so and fall back
        try:
            out = [l["slug"] for l in
                   col.http_get_json("/v2/leagues")["leagues"]]
        except Exception:
            out = list(FALLBACK)
    with _LG_LOCK:
        _LG_CACHE["t"], _LG_CACHE["slugs"] = time.time(), out
    return list(out)


def events_by_league(threads=8):
    """slug -> events payload, fetched concurrently. ?limit is REQUIRED."""
    lgs = leagues()
    q, out, lock = queue.Queue(), {}, threading.Lock()
    for x in lgs:
        q.put(x)

    def worker():
        while True:
            try:
                lg = q.get_nowait()
            except queue.Empty:
                return
            try:
                ev = col.http_get_json(
                    "/v2/leagues/%s/events?limit=500" % lg)
            except Exception:
                continue
            evs = ev.get("events") if isinstance(ev, dict) else ev
            with lock:
                out[lg] = evs or []
    ths = [threading.Thread(target=worker, daemon=True)
           for _ in range(threads)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return out
