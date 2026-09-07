"""Venue-served GAME PROGRESS, so market selection can see how close a
market is to settling.

WHY THIS EXISTS. The ranker already scores a ``residual_duration``
component, but its only input was ``market_state.endDate``. Measured live
2026-09-07T02:47Z on the three CFB markets the campaign traded tonight,
every one of them served::

    gameStartTime 2026-09-06T23:30:00Z
    endDate       2026-09-20T23:30:00Z

``endDate`` is a fixed gameStartTime + 14d CONTRACT EXPIRY, identical
across markets and unrelated to when the game -- and therefore the
position -- actually resolves. Fed through the old component
(``1 - hours/24``) it is 0.0 for every live sports market, so the
component was a constant and selection was effectively horizon-blind.
That matters now because the measured binding constraint on the live
campaign is capital velocity under HOLD-to-settlement, not size.

WHAT THE VENUE ACTUALLY SERVES. ``/v1/events?live=true&...&categories=sports``
returns, per live event: ``period`` ("Q4", "HT", "S2", "Top 3rd", "Map 2"),
``elapsed`` (game clock), ``score``, ``ended``, and
``eventState.type`` ("football", "baseball", "tennis", "esports", ...)
with a per-sport sub-state. This module reads that ONE bounded endpoint,
caches it, and maps market slug -> progress.

DISCIPLINE.
  * Progress is reported ONLY from tokens the venue serves. An
    unrecognised period yields proximity None with the reason attached;
    it never guesses, and None leaves the ranker exactly where it is
    today (neutral) rather than inventing a horizon.
  * Proximity is a POSITION IN THE GAME, not a settlement timestamp.
    The venue serves no settlement time for live sports and this module
    does not manufacture one. Nothing here is used as evidence that a
    market has settled -- that stays the settlement auditor's
    venue-confirmed POSITION_RESOLUTION, unchanged.
  * Read-only, keyless, GET only. Never raises: on any failure the table
    is empty and every caller falls back to its existing behaviour.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.request
from typing import Any, Mapping, Optional

GATEWAY = "https://gateway.polymarket.us"
LIVE_EVENTS_PATH = ("/v1/events?live=true&active=true&closed=false"
                    "&categories=sports")
EVENTS_PAGE_LIMIT = 500
LIVE_EVENTS_MAX_PAGES = 4
CA_ENV = "PMUS_CA_BUNDLE"
TTL_S = float(os.getenv("PMUS_EVENT_PROGRESS_TTL_S", "60"))

_CACHE: dict = {}


def _ssl_context() -> ssl.SSLContext:
    ca = os.getenv(CA_ENV, "").strip()
    return ssl.create_default_context(cafile=ca) if ca \
        else ssl.create_default_context()


def http_get_json(path: str, timeout: float = 15.0):
    req = urllib.request.Request(GATEWAY + path,
                                 headers={"User-Agent": "pmus-event-progress"})
    with urllib.request.urlopen(req, context=_ssl_context(),
                                timeout=timeout) as r:
        return json.loads(r.read())


def fetch_live_events(get_json=None, max_pages: int = LIVE_EVENTS_MAX_PAGES,
                      page_size: int = EVENTS_PAGE_LIMIT) -> tuple[list, dict]:
    """The venue's own live set, bounded and fail-visible. Mirrors the
    collector's contract; kept self-contained because the probe image
    does not carry the collector."""
    get_json = http_get_json if get_json is None else get_json
    rows: list = []
    pages = 0
    err = None
    for page in range(max_pages):
        try:
            resp = get_json(f"{LIVE_EVENTS_PATH}&limit={page_size}"
                            f"&offset={page * page_size}")
        except Exception as exc:
            err = f"{type(exc).__name__}:{str(exc)[:100]}"
            break
        evs = resp if isinstance(resp, list) else (
            resp.get("events") if isinstance(resp, Mapping) else None)
        if not isinstance(evs, list):
            err = "unexpected_response_shape"
            break
        pages += 1
        rows.extend(e for e in evs if isinstance(e, Mapping)
                    and e.get("live") is True)
        if len(evs) < page_size:
            break
    return rows, {"live_events": len(rows), "pages": pages,
                  "hit_cap": pages >= max_pages, "error": err}


# Position in the game for the period tokens the venue actually serves.
# These are ORDERINGS, which the token itself settles; they are not
# durations and not settlement times.
_QUARTERS = {"Q1": 0.10, "Q2": 0.30, "Q3": 0.62, "Q4": 0.85, "OT": 0.95}
_BREAK = {"HT": 0.50, "HALFTIME": 0.50}
_FINAL = {"FT": 1.0, "FINAL": 1.0, "ENDED": 1.0}
_TENNIS = {"S1": 0.20, "S2": 0.45, "S3": 0.65, "S4": 0.85, "S5": 0.95}
_SOCCER_MINUTE = re.compile(r"^(\d{1,3})'")
_BASEBALL = re.compile(r"^(TOP|BOT|BOTTOM|MID|END)\s+(\d{1,2})")


def proximity(period: Optional[str], sport_type: Optional[str] = None,
              ended: Optional[bool] = None) -> tuple:
    """(0..1 position in the game, basis) or (None, why-not).

    Higher = closer to the end of the game. Every branch below is keyed on
    a token the venue itself serves, and the ordering is settled by the
    token, not by an assumed duration. A token this function does not
    recognise returns None so the caller keeps whatever neutral behaviour
    it already had -- it never extrapolates.

    The game CLOCK ("elapsed": "12:26") is carried through as evidence but
    is deliberately NOT scored: measured live on cfb-lou-miss 2026-09-07
    it ran 12:45 -> 12:26 over five minutes of wall clock, i.e. it counts
    DOWN and is time REMAINING in the period, the opposite of what its
    name suggests. One measurement is not enough to score money on, so it
    stays evidence until it is confirmed across sports.
    """
    if ended is True:
        return 1.0, "venue_ended_flag"
    tok = (period or "").strip()
    if not tok:
        return None, "no_period_served"
    up = tok.upper()
    st = (sport_type or "").strip().lower() or "unknown"
    if up in _FINAL:
        return _FINAL[up], "period_token/final"
    if up in _BREAK:
        return _BREAK[up], "period_token/halftime/%s" % st
    if up in _QUARTERS:
        return _QUARTERS[up], "period_token/quarters/%s" % st
    if up in _TENNIS:
        return _TENNIS[up], "period_token/sets/%s" % st
    m = _SOCCER_MINUTE.match(up)
    if m:
        return min(1.0, round(int(m.group(1)) / 95.0, 4)), "match_minute/%s" % st
    m = _BASEBALL.match(up)
    if m:
        return min(1.0, round(int(m.group(2)) / 9.0, 4)), "inning/%s" % st
    # esports series ("Map 2", "Game 1"): the series length is NOT served,
    # so position in it is genuinely unknown. Say so rather than guess.
    if up.startswith("MAP ") or up.startswith("GAME "):
        return None, "unknown_series_length"
    return None, "unrecognised_period_token"


def progress_table(rows, now: Optional[float] = None) -> dict:
    """market_slug -> the venue-served progress of the event it belongs to."""
    now = time.time() if now is None else now
    out: dict = {}
    for e in rows:
        if not isinstance(e, Mapping):
            continue
        es = e.get("eventState") or {}
        st = es.get("type")
        period = e.get("period") or es.get("period")
        ended = bool(e.get("ended") or es.get("ended"))
        prox, basis = proximity(period, st, ended)
        start = e.get("startTime") or e.get("startDate")
        elapsed_h = None
        if start:
            try:
                from datetime import datetime
                t = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
                elapsed_h = round((now - t.timestamp()) / 3600.0, 3)
            except (TypeError, ValueError):
                elapsed_h = None
        entry = {
            "event_slug": e.get("slug"), "sport_type": st,
            "period": period, "clock": e.get("elapsed") or es.get("elapsed"),
            "score": e.get("score"), "ended": ended,
            "hours_since_event_start": elapsed_h,
            "game_proximity": prox, "proximity_basis": basis,
            "labels": "venue-served game position; NOT a settlement time",
        }
        for m in (e.get("markets") or []):
            slug = m.get("slug") if isinstance(m, Mapping) else None
            if slug:
                out[str(slug)] = entry
    return out


def table(get_json=None, now: Optional[float] = None,
          ttl_s: float = TTL_S) -> tuple:
    """Cached (table, diag). Never raises; an empty table is a valid,
    visible outcome and leaves every caller on its existing path."""
    now = time.time() if now is None else now
    hit = _CACHE.get("t")
    if hit and now - hit[0] < ttl_s:
        return hit[1], hit[2]
    try:
        rows, diag = fetch_live_events(get_json)
        tbl = progress_table(rows, now)
    except Exception as exc:                    # pragma: no cover - defensive
        return {}, {"error": f"{type(exc).__name__}:{str(exc)[:100]}"}
    diag = dict(diag)
    diag["markets_with_progress"] = len(tbl)
    diag["recognised_proximity"] = sum(
        1 for v in tbl.values() if v["game_proximity"] is not None)
    _CACHE["t"] = (now, tbl, diag)
    return tbl, diag
