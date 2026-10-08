"""READ-ONLY: why will PMUS not identify a BTC15m window?

On 2026-10-08 the armed cohort refused every candidate window with

    SELECTOR_REFUSED: PMUS would not confirm the identity of
    cpc-btc-updown-15m-2026-10-08-1045z: market metadata not served ...
    neither /v1/market/slug/ nor /v1/events/slug/btc-updown-15m-... returned it

including 1045z, which was ALREADY LIVE at the time. Nine hours earlier the
venue had resolved cpc-btc-updown-15m-2026-10-08-0100z fine (market_id
1274078).

`l5_pmus_transport.resolve_market`'s own docstring records the distinction
this probe exists to measure: `/v1/markets/{slug}/bbo` has answered 200 for a
BTC15m window while the singular `/v1/market/slug/{slug}` 404s, and that
mismatch once failed 35 consecutive windows. So a window can be TRADEABLE
while being UNIDENTIFIABLE by the resolver.

This asks all five routes for several windows around now and reports the
status of each, so the answer is a measurement rather than a guess.

IT CANNOT PLACE AN ORDER. The only transport it builds is
`pmus_auth.ReadOnlyTransport`, which raises on any method but GET, so this
process cannot construct a mutating request at all. It consumes no window,
claims no latch slot and touches no risk accounting.
"""
from __future__ import annotations

import glob
import json
import os
import time

from src import pmus_auth as A

SESSION = os.environ.get("PMUS_E1_SESSION_ID", "")
STATE = "/state/pmus-ws"
WINDOW_S = 900


def _try(t, path, base):
    try:
        got = t.request("GET", path, base=base)
    except Exception as exc:                                   # noqa: BLE001
        code = getattr(exc, "code", "")
        return "%s:%s %s" % (type(exc).__name__, code, str(exc)[:70])
    if not got:
        return "EMPTY"
    if isinstance(got, dict):
        keys = sorted(got.keys())[:6]
        return "OK keys=%s" % (keys,)
    return "OK %s" % type(got).__name__


def main() -> int:
    t = A.ReadOnlyTransport()
    now = int(time.time())
    base_ws = now - (now % WINDOW_S)
    out = {
        "probe": "E1_WINDOW_IDENTITY",
        "READ_ONLY": True,
        "transport": "src.pmus_auth.ReadOnlyTransport",
        "cannot_place_an_order": True,
        "now_unix": now,
        "now_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "windows": [],
    }
    for off in (-1800, -900, 0, 900, 1800):
        ws = base_ws + off
        stamp = time.strftime("%Y-%m-%d-%H%M", time.gmtime(ws)) + "z"
        slug = "cpc-btc-updown-15m-" + stamp
        event = slug[len("cpc-"):]
        out["windows"].append({
            "slug": slug,
            "window_start": ws,
            "window_utc": time.strftime("%H:%MZ", time.gmtime(ws)),
            "is_live_now": ws <= now < ws + WINDOW_S,
            "bbo_gateway": _try(t, "/v1/markets/%s/bbo" % slug, A.GATEWAY),
            "bbo_api": _try(t, "/v1/markets/%s/bbo" % slug, A.API),
            "book_gateway": _try(t, "/v1/markets/%s/book" % slug, A.GATEWAY),
            "market_slug_singular": _try(
                t, "/v1/market/slug/%s" % slug, A.GATEWAY),
            "event_slug": _try(t, "/v1/events/slug/%s" % event, A.GATEWAY),
        })

    # the durable cohort state, so we know whether anything was consumed
    rows = []
    for p in glob.glob(STATE + "/**/e1.cohort_progress.jsonl", recursive=True):
        for line in open(p, encoding="utf8"):
            if line.strip():
                rows.append(json.loads(line))
    mine = [r for r in rows if r.get("session_id") == SESSION]
    out["cohort"] = {
        "session_id_present": bool(SESSION),
        "my_rows": mine,
        "my_windows_opened": len([r for r in mine
                                  if r.get("event") == "WINDOW_OPENED"]),
        "rows_per_session": {},
        "submitted_markers": [
            p[len(STATE):] for p in
            glob.glob(STATE + "/**/*.submitted", recursive=True)],
    }
    for r in rows:
        k = str(r.get("session_id"))
        out["cohort"]["rows_per_session"][k] = \
            out["cohort"]["rows_per_session"].get(k, 0) + 1
    print("PROBE_BEGIN")
    print(json.dumps(out, indent=1, default=str))
    print("PROBE_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
