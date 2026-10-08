"""READ-ONLY: is PMUS still serving a BTC15m market, and under what identity?

On 2026-10-08T11:03Z five BTC15m windows x five PMUS-native routes returned
TWENTY-FIVE 404s, including cpc-btc-updown-15m-2026-10-08-1100z while it was
LIVE. Ten hours earlier the venue resolved ...-0100z as market_id 1274078.

A 404 is not a delisting, so this does not infer one. It asks the venue three
independent questions and reports what comes back:

  1. the KNOWN-GOOD slug (...-0100z) on the same native routes. If that still
     resolves, only NEW windows are missing; if it 404s too, the identity
     itself has changed or gone.
  2. DISCOVERY WITHOUT ASSUMING THE SLUG FORMAT: /v1/series paged, matched on
     title/slug text rather than on a remembered id, then /v1/events for each
     matching series paged TO THE END -- the repository already records that a
     single unpaginated page answers for a series' FIRST hundred windows and
     silently reports "not listed" for every current one.
  3. the live and active catalogues, /v1/events?live=true&active=true and
     /v1/markets?active=true, scanned for anything BTC/updown shaped.

For whatever it finds it reports the venue's OWN identifiers, titles,
outcomes, status flags and start/end times, so a changed identity can be
recognised rather than guessed at.

IT CANNOT PLACE AN ORDER. The only transport it builds is
`pmus_auth.ReadOnlyTransport`, which raises on any method but GET. It touches
no selector, no spec, no latch and no risk accounting, and writes nothing.

Output is ONE compact JSON line: Railway drops messages above 500 logs/sec,
and the earlier pretty-printed probes lost their decisive lines that way.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from src import pmus_auth as A

KNOWN_GOOD = "cpc-btc-updown-15m-2026-10-08-0100z"
NEEDLES = ("btc", "updown", "up-down", "15m", "bitcoin")
MAX_PAGES = 80
PAGE = 100


def get(t, path, base=A.GATEWAY):
    try:
        return {"ok": True, "body": t.request("GET", path, base=base)}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False,
                "err": "%s:%s" % (type(exc).__name__,
                                  getattr(exc, "code", "") or str(exc)[:40])}


def status_of(t, path, base=A.GATEWAY):
    r = get(t, path, base)
    if not r["ok"]:
        return r["err"]
    b = r["body"]
    if not b:
        return "EMPTY"
    return "OK:" + ",".join(sorted(b.keys())[:5]) if isinstance(b, dict) \
        else "OK:%s" % type(b).__name__


def looks_btc(*vals) -> bool:
    blob = " ".join(str(v or "").lower() for v in vals)
    return ("btc" in blob or "bitcoin" in blob) and (
        "updown" in blob or "up-down" in blob or "up/down" in blob
        or "15m" in blob)


def slim_event(e: Dict[str, Any]) -> Dict[str, Any]:
    mkts = e.get("markets") or []
    return {"slug": e.get("slug"), "id": e.get("id"),
            "title": str(e.get("title") or "")[:70],
            "startTime": e.get("startTime"), "endTime": e.get("endTime"),
            "active": e.get("active"), "closed": e.get("closed"),
            "archived": e.get("archived"), "status": e.get("status"),
            "n_markets": len(mkts),
            "markets": [{"slug": m.get("slug"), "id": m.get("id"),
                         "status": m.get("status"),
                         "active": m.get("active"), "closed": m.get("closed"),
                         "tick": m.get("orderPriceMinTickSize"),
                         "minQty": m.get("minimumTradeQty"),
                         "outcomes": m.get("outcomes")}
                        for m in mkts[:2]]}


def page_all(t, fmt: str, key: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i in range(MAX_PAGES):
        r = get(t, fmt % (PAGE, i * PAGE))
        if not r["ok"]:
            out.append({"_page_error": r["err"], "_offset": i * PAGE})
            break
        rows = (r["body"] or {}).get(key) or []
        if not rows:
            break
        out.extend(rows)
    return out


def main() -> int:
    t = A.ReadOnlyTransport()
    now = int(time.time())
    out: Dict[str, Any] = {
        "probe": "PMUS_BTC15M_AVAILABILITY",
        "READ_ONLY": True, "cannot_place_an_order": True,
        "now_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }

    # (1) the KNOWN-GOOD window, same native routes
    ev = KNOWN_GOOD[len("cpc-"):]
    out["known_good"] = {
        "slug": KNOWN_GOOD,
        "bbo_gateway": status_of(t, "/v1/markets/%s/bbo" % KNOWN_GOOD),
        "bbo_api": status_of(t, "/v1/markets/%s/bbo" % KNOWN_GOOD, A.API),
        "book_gateway": status_of(t, "/v1/markets/%s/book" % KNOWN_GOOD),
        "market_slug_singular": status_of(
            t, "/v1/market/slug/%s" % KNOWN_GOOD),
        "event_slug": status_of(t, "/v1/events/slug/%s" % ev),
    }

    # (2) discovery, WITHOUT assuming the slug format
    series = page_all(t, "/v1/series?limit=%d&offset=%d", "series")
    out["series_total"] = len([s for s in series if "_page_error" not in s])
    matched = [s for s in series
               if looks_btc(s.get("title"), s.get("slug"), s.get("name"))]
    out["btc_series"] = [{"id": s.get("id"), "slug": s.get("slug"),
                          "title": str(s.get("title") or "")[:70],
                          "active": s.get("active"),
                          "closed": s.get("closed")} for s in matched[:8]]
    out["series_page_errors"] = [s for s in series if "_page_error" in s][:2]

    # every matching series' events, paged TO THE END; newest are last
    out["series_events"] = {}
    for s in matched[:3]:
        sid = s.get("id")
        rows = page_all(
            t, "/v1/events?series_id=" + str(sid) + "&limit=%d&offset=%d",
            "events")
        real = [r for r in rows if "_page_error" not in r]
        out["series_events"][str(sid)] = {
            "n_events": len(real),
            "page_errors": [r for r in rows if "_page_error" in r][:2],
            "newest": [slim_event(e) for e in real[-4:]],
            "oldest_slug": (real[0].get("slug") if real else None),
        }

    # (2b) WHAT THE VENUE HAS FROM NOW ON, time-sorted. The newest-by-id
    # view above is CREATION order, which is not time order, so it cannot
    # answer "is there a tradeable window coming". This sorts series 375's
    # events by startTime, keeps those not yet ended, and asks /bbo whether
    # each actually has a book.
    s375 = [s for s in matched if str(s.get("id")) == "375"]
    out["upcoming"] = []
    if s375:
        rows = page_all(t, "/v1/events?series_id=375&limit=%d&offset=%d",
                        "events")
        real = [r for r in rows if "_page_error" not in r]
        dated = []
        for e in real:
            st = str(e.get("startTime") or "")
            if len(st) >= 19:
                dated.append((st, e))
        dated.sort(key=lambda x: x[0])
        nowstamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 900))
        fut = [e for (st, e) in dated if st[:19] >= nowstamp][:6]
        out["n_upcoming_events"] = len(
            [1 for (st, e) in dated if st[:19] >= nowstamp])
        for e in fut:
            row = slim_event(e)
            mslug = (row["markets"][0]["slug"] if row["markets"] else None)
            row["bbo"] = (status_of(t, "/v1/markets/%s/bbo" % mslug)
                          if mslug else "NO_MARKET_IN_EVENT")
            out["upcoming"].append(row)

    # (3) the live and active catalogues
    live = get(t, "/v1/events?live=true&active=true&closed=false&limit=200")
    if live["ok"]:
        rows = (live["body"] or {}).get("events") or []
        hits = [e for e in rows if looks_btc(e.get("title"), e.get("slug"))]
        out["live_events"] = {"n": len(rows), "btc_hits": len(hits),
                              "btc": [slim_event(e) for e in hits[:4]],
                              "sample_slugs": [e.get("slug")
                                               for e in rows[:5]]}
    else:
        out["live_events"] = {"err": live["err"]}

    mk = get(t, "/v1/markets?active=true&closed=false&limit=200")
    if mk["ok"]:
        rows = (mk["body"] or {}).get("markets") or []
        hits = [m for m in rows if looks_btc(m.get("title"), m.get("slug"))]
        out["active_markets"] = {
            "n": len(rows), "btc_hits": len(hits),
            "btc": [{"slug": m.get("slug"), "id": m.get("id"),
                     "title": str(m.get("title") or "")[:70],
                     "status": m.get("status"),
                     "outcomes": m.get("outcomes"),
                     "tick": m.get("orderPriceMinTickSize"),
                     "minQty": m.get("minimumTradeQty")} for m in hits[:4]],
            "sample_slugs": [m.get("slug") for m in rows[:5]]}
    else:
        out["active_markets"] = {"err": mk["err"]}

    print("AVAIL:" + json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
