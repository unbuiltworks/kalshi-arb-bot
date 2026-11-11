"""One-shot READ-ONLY Polymarket US live-discovery diagnostic.

Runs independently of the authenticated recorder and never mutates its
subscription plan.  It measures the documented v1 sports discovery surfaces so
we can reconcile the operator-visible live-event count against the legacy
/v2/leagues + global-market join before changing admission logic.

Public GETs only. No credentials. No order/execution imports.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from src import pmus_sports_collector as col

EVENT_PAGE = 100
EVENT_MAX_PAGES = 5
MARKET_PAGE = 500
MARKET_MAX_PAGES = 10


def _rows(resp: Any, key: str) -> list[dict]:
    if isinstance(resp, list):
        return [x for x in resp if isinstance(x, Mapping)]
    if isinstance(resp, Mapping):
        xs = resp.get(key) or []
        return [x for x in xs if isinstance(x, Mapping)]
    return []


def _page(path: str, key: str, page_size: int, max_pages: int) -> tuple[list[dict], int, bool, str | None]:
    out: list[dict] = []
    pages = 0
    err = None
    for i in range(max_pages):
        try:
            resp = col.http_get_json(f"{path}&limit={page_size}&offset={i * page_size}")
        except Exception as exc:  # diagnostic must never affect recorder
            err = f"{type(exc).__name__}:{str(exc)[:120]}"
            break
        rows = _rows(resp, key)
        out.extend(rows)
        pages += 1
        if len(rows) < page_size:
            return out, pages, False, err
        time.sleep(0.06)
    return out, pages, pages >= max_pages, err


def _norm_ts(v: Any) -> str | None:
    if not v:
        return None
    s = str(v).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return s


def _market_count(ev: Mapping[str, Any]) -> int | None:
    mc = ev.get("marketCounts")
    if isinstance(mc, Mapping):
        for k in ("numMarkets", "markets", "total"):
            try:
                if mc.get(k) is not None:
                    return int(mc[k])
            except Exception:
                pass
    return None


def collect() -> dict:
    # Official docs expose live=true + categories=sports on /v1/events and
    # categories=sports on /v1/markets.  Keep both bounded and observable.
    events, epages, ecap, eerr = _page(
        "/v1/events?live=true&active=true&closed=false&categories=sports",
        "events", EVENT_PAGE, EVENT_MAX_PAGES)
    markets, mpages, mcap, merr = _page(
        "/v1/markets?active=true&closed=false&categories=sports",
        "markets", MARKET_PAGE, MARKET_MAX_PAGES)

    live = [e for e in events if e.get("live") is True]
    starts_exact = {str(e.get("startTime") or e.get("startDate"))
                    for e in live if e.get("startTime") or e.get("startDate")}
    starts_norm = {_norm_ts(x) for x in starts_exact}
    m_with_start = [m for m in markets if m.get("gameStartTime")]
    exact = [m for m in m_with_start if str(m.get("gameStartTime")) in starts_exact]
    norm = [m for m in m_with_start if _norm_ts(m.get("gameStartTime")) in starts_norm]

    stypes: dict[str, int] = {}
    for m in markets:
        typ = (m.get("sportsMarketTypeV2") or m.get("sportsMarketType") or
               m.get("marketType") or "UNKNOWN")
        stypes[str(typ)] = stypes.get(str(typ), 0) + 1

    embedded = sum(len(e.get("markets") or []) for e in live
                   if isinstance(e.get("markets") or [], list))
    declared = [x for x in (_market_count(e) for e in live) if x is not None]

    def ev_sample(e: Mapping[str, Any]) -> dict:
        state = e.get("eventState") if isinstance(e.get("eventState"), Mapping) else {}
        return {"id": e.get("id"), "slug": e.get("slug"),
                "live": e.get("live"), "ended": e.get("ended"),
                "startTime": e.get("startTime") or e.get("startDate"),
                "period": e.get("period") or state.get("period"),
                "score": e.get("score") or state.get("score"),
                "embedded_markets": len(e.get("markets") or [])
                    if isinstance(e.get("markets") or [], list) else None,
                "declared_markets": _market_count(e)}

    # per-event detail probe (issue #167 comment 5525317831): does the
    # single-event surface return the COMPLETE market set for a live
    # event? Compare embedded/declared vs by-id and by-slug responses on
    # the two live events with the largest declared market counts.
    detail = []
    probes = sorted(live, key=lambda e: -(_market_count(e) or 0))[:2]
    for e in probes:
        row = {"slug": e.get("slug"), "declared": _market_count(e),
               "list_embedded": len(e.get("markets") or [])
               if isinstance(e.get("markets") or [], list) else None}
        for label, path in (("by_id", f"/v1/events/{e.get('id')}"),
                            ("by_slug", f"/v1/events/slug/{e.get('slug')}")):
            try:
                resp = col.http_get_json(path)
                ev = resp.get("event") if isinstance(resp, Mapping) and \
                    isinstance(resp.get("event"), Mapping) else resp
                ms = ev.get("markets") if isinstance(ev, Mapping) else None
                row[label] = {"markets": len(ms) if isinstance(ms, list)
                              else None,
                              "keys": sorted(ev.keys())[:12]
                              if isinstance(ev, Mapping) else None}
            except Exception as exc:
                row[label] = {"error":
                              f"{type(exc).__name__}:{str(exc)[:100]}"}
            time.sleep(0.06)
        detail.append(row)

    return {
        "t": datetime.now(timezone.utc).isoformat(),
        "mode": "READ_ONLY_LIVE_DISCOVERY_DIAGNOSTIC",
        "event_detail_probe": detail,
        "events": {"returned": len(events), "live_true": len(live),
                   "pages": epages, "hit_cap": ecap, "error": eerr,
                   "embedded_markets": embedded,
                   "declared_market_sum": sum(declared) if declared else None,
                   "sample": [ev_sample(e) for e in live[:4]]},
        "sports_markets": {"returned": len(markets), "pages": mpages,
                           "hit_cap": mcap, "error": merr,
                           "with_gameStartTime": len(m_with_start),
                           "exact_live_start_matches": len(exact),
                           "normalized_live_start_matches": len(norm),
                           "types": dict(sorted(stypes.items())),
                           "first": ({"slug": markets[0].get("slug"),
                                      "gameStartTime": markets[0].get("gameStartTime")}
                                     if markets else None),
                           "last": ({"slug": markets[-1].get("slug"),
                                     "gameStartTime": markets[-1].get("gameStartTime")}
                                    if markets else None)},
        "note": "diagnostic only; does not alter recorder subscriptions or any execution gate",
    }


def main() -> int:
    try:
        print("PMUS_LIVE_DISCOVERY_DIAG " + json.dumps(
            collect(), separators=(",", ":"), default=str), flush=True)
        return 0
    except Exception as exc:
        print("PMUS_LIVE_DISCOVERY_DIAG " + json.dumps({
            "mode": "READ_ONLY_LIVE_DISCOVERY_DIAGNOSTIC",
            "error": f"{type(exc).__name__}:{str(exc)[:160]}"}), flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
