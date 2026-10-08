"""READ-ONLY WATCHER: when does a BTC15m window become genuinely tradeable?

On 2026-10-08 the armed cohort refused every candidate window because PMUS
served no metadata for any of them, and a follow-up probe found the series
alive (375 `btc-updown-15m`, 1,522 events, identical
`cpc-btc-updown-15m-YYYY-MM-DD-HHMMz` identity) but every market reading
`MARKET_STATUS_HALTED` with `closed: true`, and this morning's windows never
minted at all. So the blocker is venue-side availability, and the question
worth watching is narrow: WHEN is a window present, canonical, un-halted and
actually serving a book?

FOUR CONDITIONS, CHECKED IN TIERS so the cheap ones gate the expensive ones
and the poll costs about four requests a minute:

  C1 PRESENT    the event's market row exists in the venue catalogue.
  C2 CANONICAL  under the slug `pmus_identity.slug_for` builds from the
                venue's own 900s grid -- the slug is never guessed, so a row
                coming back for it IS canonical confirmation.
  C3 TRADEABLE  that row is not MARKET_STATUS_HALTED, not `closed`, not
                inactive. THIS IS THE LEADING INDICATOR and is checked first:
                measured on 2026-10-08, all 41 upcoming 15m windows are
                pre-created and HALTED, and `/v1/markets/{slug}/bbo` 404s
                precisely BECAUSE they are halted. So polling the book first
                would report "absent" forever and miss the un-halting.
  C4 BOOK       `/bbo` answers, and `PmusDecisionFeed.health()` says
                two-sided, fresher than the frozen 2.0s bound, and sourced
                from the authenticated socket.

A CONTROL RUNS EVERY POLL. `cpc-btc-updown-1h-...` was measured OPEN while
every 15m window was halted, so the 1h window is checked too. A watcher that
can never fire is worse than none; the control proves the detector sees
`MARKET_STATUS_OPEN` when it is really there.

C4 DELIBERATELY REUSES E1'S OWN GATE rather than reimplementing freshness, so
"serving a valid book" means exactly what E1 means by it. If this watcher says
C4 passed, E1's E7/E8 would pass on the same book.

IT CANNOT PLACE AN ORDER. REST goes through `pmus_auth.ReadOnlyTransport`,
which raises on any method but GET; AuthBook is a read-only socket. It never
imports the E1 runner, never reads an arming key, consumes no window, claims
no latch slot and touches no risk accounting.

TRANSITIONS ONLY. Per-slug state is remembered and a line is written only when
it CHANGES, to `/state/pmus-ws/btc15m_watch.jsonl`, append-only, so a restart
inherits what was already seen. Steady state is silent.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from analysis.l5.e1_cohort import pmus_book as PBK
from analysis.l5.e1_cohort import pmus_identity as PI
from src import pmus_auth as A

SERIES_ID = "375"
SERIES_SLUG = "btc-updown-15m"
WINDOW_S = PI.WINDOW_S
LOOKAHEAD = int(os.environ.get("WATCH_LOOKAHEAD", "4"))
POLL_S = float(os.environ.get("WATCH_POLL_S", "60"))
CATALOGUE_EVERY_S = float(os.environ.get("WATCH_CATALOGUE_EVERY_S", "900"))
MAX_RUNTIME_S = float(os.environ.get("WATCH_MAX_RUNTIME_S", "72000"))
OUT = os.environ.get("WATCH_OUT", "/state/pmus-ws/btc15m_watch.jsonl")

HALTED = "MARKET_STATUS_HALTED"


def emit(rec: Dict[str, Any]) -> None:
    rec = dict(rec)
    rec.setdefault("unix", time.time())
    rec.setdefault("utc", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    line = json.dumps(rec, default=str)
    print("WATCH:" + line, flush=True)
    try:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "a", encoding="utf8") as fh:
            fh.write(line + "\n")
    except Exception as exc:                                   # noqa: BLE001
        print("WATCH_WRITE_FAILED:%s" % type(exc).__name__, flush=True)


def _get(t, path, base=A.GATEWAY):
    try:
        return True, t.request("GET", path, base=base)
    except Exception as exc:                                   # noqa: BLE001
        return False, "%s:%s" % (type(exc).__name__,
                                 getattr(exc, "code", "") or str(exc)[:40])


def candidate_slugs(now: float) -> List[Dict[str, Any]]:
    ws0 = PI.window_start(now)
    out = []
    for k in range(LOOKAHEAD):
        ws = ws0 + k * WINDOW_S
        out.append({"slug": PI.slug_for(ws), "window_start": ws,
                    "window_end": ws + WINDOW_S,
                    "is_open_now": ws <= now < ws + WINDOW_S})
    return out


def market_row(t, slug: str) -> Dict[str, Any]:
    """C3's source: the event's own market row for this exact slug."""
    ok, body = _get(t, "/v1/events/slug/%s" % slug[len("cpc-"):])
    if not ok:
        return {"ok": False, "why": body}
    ev = (body or {}).get("event") or body or {}
    for m in (ev.get("markets") or []):
        if str(m.get("slug")) == slug:
            return {"ok": True, "id": m.get("id"), "status": m.get("status"),
                    "active": m.get("active"), "closed": m.get("closed"),
                    "tick": m.get("orderPriceMinTickSize"),
                    "minQty": m.get("minimumTradeQty"),
                    "outcomes": m.get("outcomes")}
    return {"ok": False, "why": "SLUG_NOT_IN_EVENT_MARKETS"}


def status_permits(row: Dict[str, Any]) -> Dict[str, Any]:
    why = []
    if str(row.get("status")) == HALTED:
        why.append("status=%s" % HALTED)
    if row.get("closed") is True:
        why.append("closed=true")
    if row.get("active") is False:
        why.append("active=false")
    return {"permits": not why, "blockers": why}


def check_book(feed, pin: Dict[str, Any]) -> Dict[str, Any]:
    """C4, through E1's OWN gate. No reimplemented freshness rule."""
    try:
        feed.start()
        h = feed.health(pin)
        out = {"ok": bool(h.get("ok")), "why": h.get("why"),
               "source": h.get("source"),
               "timing_authority": h.get("timing_authority"),
               "stream_age_s": h.get("stream_age_s")}
        if out["ok"]:
            b = feed.book(pin)
            yes = (b.get("yes") or b.get("first_leg") or {})
            no = (b.get("no") or b.get("complement") or {})
            out["yes"] = {"bid": yes.get("best_bid"), "ask": yes.get("best_ask")}
            out["no"] = {"bid": no.get("best_bid"), "ask": no.get("best_ask")}
            out["choice"] = (b.get("first_leg_side_choice") or {}).get("side")
        return out
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "why": "%s:%s" % (type(exc).__name__,
                                               str(exc)[:80])}


def stage_of(present: bool, permits: Optional[bool],
             bbo_ok: Optional[bool], book_ok: Optional[bool]) -> str:
    if not present:
        return "ABSENT_FROM_CATALOGUE"
    if not permits:
        return "CATALOGUE_HALTED"
    if not bbo_ok:
        return "STATUS_OPEN_NO_BBO"
    if not book_ok:
        return "BBO_OK_NO_AUTHBOOK"
    return "TRADEABLE_WITH_BOOK"


def hour_slug(now: float) -> str:
    """The 1h control window: `cpc-btc-updown-1h-YYYY-MM-DD-HH00z`."""
    hs = int(now) - (int(now) % 3600)
    return "cpc-btc-updown-1h-" + time.strftime(
        "%Y-%m-%d-%H00", time.gmtime(hs)) + "z"


def catalogue_tail(t) -> Dict[str, Any]:
    """Low-frequency: what has the venue minted furthest ahead? Paged to the
    end, because one page answers for the series' FIRST hundred windows."""
    rows: List[Dict[str, Any]] = []
    for i in range(80):
        ok, body = _get(t, "/v1/events?series_id=%s&limit=100&offset=%d"
                        % (SERIES_ID, i * 100))
        if not ok:
            return {"err": body, "at_offset": i * 100}
        page = (body or {}).get("events") or []
        if not page:
            break
        rows.extend(page)
    dated = sorted((str(e.get("startTime") or ""), e) for e in rows
                   if str(e.get("startTime") or ""))
    return {"n_events": len(rows),
            "latest_start": (dated[-1][0] if dated else None),
            "latest_slug": (dated[-1][1].get("slug") if dated else None)}


def main() -> int:
    t = A.ReadOnlyTransport()
    feed = PBK.PmusDecisionFeed()
    started = time.time()
    seen: Dict[str, str] = {}
    announced = False
    last_catalogue = 0.0
    emit({"event": "WATCH_START", "series_id": SERIES_ID,
          "series_slug": SERIES_SLUG, "poll_s": POLL_S,
          "lookahead_windows": LOOKAHEAD,
          "max_runtime_s": MAX_RUNTIME_S,
          "READ_ONLY": True, "cannot_place_an_order": True,
          "e1_untouched_and_disarmed": True,
          "c4_uses": "analysis.l5.e1_cohort.pmus_book:PmusDecisionFeed.health"})

    while time.time() - started < MAX_RUNTIME_S:
        loop_at = time.time()
        try:
            if loop_at - last_catalogue >= CATALOGUE_EVERY_S:
                last_catalogue = loop_at
                emit({"event": "CATALOGUE_TAIL", **catalogue_tail(t)})

            # the 1h CONTROL, so a detector that never fires is visible
            ctrl_slug = hour_slug(loop_at)
            ctrl_row = market_row(t, ctrl_slug)
            ctrl_perm = status_permits(ctrl_row) if ctrl_row.get("ok") else {
                "permits": False, "blockers": [str(ctrl_row.get("why"))]}
            ctrl_stage = ("CONTROL_OPEN" if ctrl_perm.get("permits")
                          else "CONTROL_NOT_OPEN")
            if seen.get("__control__") != ctrl_stage:
                seen["__control__"] = ctrl_stage
                emit({"event": "CONTROL", "slug": ctrl_slug,
                      "to": ctrl_stage, "market": ctrl_row,
                      "C3": ctrl_perm,
                      "why": ("the 1h series was OPEN while every 15m window "
                              "was halted; this proves the status detector "
                              "fires on a real MARKET_STATUS_OPEN")})

            for cand in candidate_slugs(loop_at):
                slug = cand["slug"]
                # C1+C2+C3 FIRST: the catalogue row and its status. This is
                # the leading indicator -- /bbo 404s *because* a window is
                # halted, so book-first polling would never see the change.
                row = market_row(t, slug)
                present = bool(row.get("ok"))
                perm = status_permits(row) if present else {
                    "permits": False, "blockers": [str(row.get("why"))]}
                bbo_ok: Optional[bool] = None
                book: Dict[str, Any] = {}
                if perm.get("permits"):
                    okb, bbo = _get(t, "/v1/markets/%s/bbo" % slug)
                    bbo_ok = bool(okb and bbo)
                    if bbo_ok:
                        book = check_book(feed, {"slug": slug})
                stage = stage_of(present, perm.get("permits"), bbo_ok,
                                 book.get("ok"))
                if seen.get(slug) == stage:
                    continue                      # steady state: say nothing
                prev, seen[slug] = seen.get(slug), stage
                rec = {"event": "TRANSITION", "slug": slug,
                       "from": prev, "to": stage,
                       "window_start": cand["window_start"],
                       "window_open_now": cand["is_open_now"],
                       "C1_present_in_catalogue": present,
                       "C2_canonical_slug_builder":
                           "pmus_identity:slug_for",
                       "C3": perm or None, "market": row or None,
                       "C4_bbo_ok": bbo_ok, "C4_authbook": book or None}
                emit(rec)
                if stage == "TRADEABLE_WITH_BOOK" and not announced:
                    announced = True
                    emit({"event": "FIRST_TRADEABLE_WINDOW",
                          "slug": slug, "market_id": row.get("id"),
                          "window_start": cand["window_start"],
                          "window_open_now": cand["is_open_now"],
                          "status": row.get("status"),
                          "closed": row.get("closed"),
                          "active": row.get("active"),
                          "tick": row.get("tick"),
                          "minQty": row.get("minQty"),
                          "outcomes": row.get("outcomes"),
                          "book": book,
                          "ALL_FOUR_CONDITIONS_MET": True,
                          "E1_STILL_DISARMED": True,
                          "REQUIRES": "explicit owner authorization to arm"})
        except Exception as exc:                               # noqa: BLE001
            emit({"event": "POLL_ERROR",
                  "err": "%s:%s" % (type(exc).__name__, str(exc)[:120])})
        slept = time.time() - loop_at
        time.sleep(max(1.0, POLL_S - slept))

    emit({"event": "WATCH_END", "reason": "MAX_RUNTIME_REACHED",
          "ran_s": round(time.time() - started, 1),
          "first_tradeable_seen": announced})
    return 0


if __name__ == "__main__":
    sys.exit(main())
