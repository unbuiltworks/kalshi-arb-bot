"""Run the PMUS sports surface against live markets.

THE TAPE SOURCE IS THE WHOLE POINT, BECAUSE IT DECIDES HOW FAR THE LADDER GOES.

  AUTHENTICATED   the WS recorder's per-market state, held IN-PROCESS. Carries
                  a real trade tape, so it can reach TRADE_FLOW and therefore
                  EVIDENCED_FILL -- but only code running inside the recorder
                  can pass it, which is why it was never deployable on its own.
  TRADES_HTTP     the recorder's /status book JOINED to its /trades tail over
                  Railway's private network. The same authenticated tape, per
                  PRICE, reachable from another service. This is the only
                  NETWORK source that reaches EVIDENCED_FILL.
  STATUS_HTTP     /status alone. AUTHENTICATED BOOK and trade AGGREGATES only
                  (`trades_1h` a count, `two_sided_trades` a bool), which
                  cannot say a trade happened AT OUR PRICE. Stops at
                  BOOK_REACHED and says so.
  PUBLIC          the gateway book only. Reaches BOOK_REACHED and stops, and
                  SAYS it stops -- `trade_flow_source: "UNAVAILABLE"` on every
                  row, so a public run can never be mistaken for evidence of
                  no interaction.

UNREADABLE IS NOT EMPTY, at every level. A market with no tape carries
`trades: None`, never []: None stops the ladder at BOOK_REACHED, while []
asserts that nobody traded and would manufacture a confident no-fill. The
/trades fetch returns None as a whole when the endpoint is disabled,
unreachable or malformed, so one bad read cannot silently clear the slate.

The public mode is not a degraded substitute; it is how the surface is
validated against real books without credentials, and how the missing input is
isolated rather than hidden. `sharesTraded` is deliberately NOT used as a flow
proxy: it is cumulative per market and cannot say a trade happened at our
price, which is exactly the claim VOLUME_AHEAD_CLEARED depends on.

Read-only in both modes. No order transport, no credential of its own.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_l5_surface_shadow as SS

LABEL = "PMUS_L5_SURFACE_RUNNER_V1"

AUTHENTICATED = "AUTHENTICATED_WS"
PUBLIC = "PUBLIC_BOOK_ONLY"
#: the recorder's existing read-only HTTP handoff, over Railway's private
#: network. AUTHENTICATED BOOK, and no trade tape -- see below.
STATUS_HTTP = "STATUS_HTTP"
#: the same handoff PLUS the recorder's /trades tail, which carries the
#: authenticated per-market trade tape. This is the only NETWORK source that
#: can reach TRADE_FLOW, and therefore the only deployable way to reach
#: EVIDENCED_FILL: AUTHENTICATED requires the recorder's state in-process, so
#: it can only run inside the recorder itself.
TRADES_HTTP = "TRADES_HTTP"

#: sources whose trade evidence is the authenticated tape
AUTHENTICATED_FLOW = (AUTHENTICATED, TRADES_HTTP)

#: the recorder serves this unconditionally on a daemon thread; status_port()
#: falls back to PORT and then 8080, so it is listening even unconfigured.
DEFAULT_STATUS_URL = ("http://pmus-ws-evidence-recorder.railway.internal:8080"
                      "/status")
DEFAULT_TRADES_URL = ("http://pmus-ws-evidence-recorder.railway.internal:8080"
                      "/trades")

#: the recorder serves the newest TRADES_PER_MARKET (200) rows of a 500-deep
#: ring. A tail AT that cap may be truncated, and the ladder has to be told so
#: -- counting rows cannot detect it, because the cap is below RING_CAPACITY.
SERVED_TAIL_CAP = 200


def _f(v: Any) -> Optional[str]:
    return None if v is None else str(v)


def market_from_gateway(slug: str, get_json) -> Optional[Dict[str, Any]]:
    """Public book -> the recorder's state shape, with NO trade tape.

    `trades` is None, not [], because None means "unreadable" to the ladder
    and [] would assert nobody traded -- the claim that manufactures no-fills.
    """
    try:
        bbo = (get_json("/v1/markets/%s/bbo" % slug) or {}).get("marketData") or {}
        md = (get_json("/v1/markets/%s/book" % slug) or {}).get("marketData") or {}
    except Exception:                                          # noqa: BLE001
        return None
    bids, offers = md.get("bids") or [], md.get("offers") or []

    def top(levels):
        if not levels:
            return None, None
        lv = levels[0]
        return (lv.get("px") or {}).get("value"), lv.get("qty")

    b_px, b_qty = top(bids)
    a_px, a_qty = top(offers)
    return {
        "bid": _f((bbo.get("bestBid") or {}).get("value") or b_px),
        "ask": _f((bbo.get("bestAsk") or {}).get("value") or a_px),
        "bid_qty": _f(b_qty), "ask_qty": _f(a_qty),
        "trades": None,                       # UNREADABLE, never empty
        "state": bbo.get("state"),
        "n_bid_levels": len(bids), "n_ask_levels": len(offers),
    }


def markets_from_status(body: str) -> Dict[str, Dict[str, Any]]:
    """The recorder's status document -> per-market state, by slug.

    WHAT THIS SOURCE CAN AND CANNOT REACH. The status document carries the
    AUTHENTICATED book per market -- bid, bid_qty, ask, ask_qty -- which is
    strictly better than the public gateway: same venue, authenticated, and
    the recorder's own WS state rather than a REST sample. It carries trades
    only as AGGREGATES (`trades_1h`, a count; `two_sided_trades`, a bool;
    slot counts), and VOLUME_AHEAD_CLEARED needs volume traded THROUGH a
    PRICE. So `trades` is None here -- UNREADABLE, never [] -- and the ladder
    correctly stops at BOOK_REACHED and says TRADE_FLOW_NOT_OBSERVABLE.

    Reaching TRADE_FLOW from this source needs the recorder to export the
    per-market trade tail; that is the minimum additive change, and it is a
    proposal rather than something done here.

    The document is newline-delimited and appended to, so the LAST parseable
    line is the newest.
    """
    newest = None
    for line in (body or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            newest = json.loads(line)
        except ValueError:
            continue
    if not isinstance(newest, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    # the same row shape appears in all three lists; later ones do not
    # overwrite earlier, because they are the same rows
    for key in ("top_markets", "eligible_markets", "spring_watch"):
        for r in (newest.get(key) or []):
            slug = r.get("slug")
            if not slug or slug in out:
                continue
            out[slug] = {
                "bid": _f(r.get("bid")), "ask": _f(r.get("ask")),
                "bid_qty": _f(r.get("bid_qty")), "ask_qty": _f(r.get("ask_qty")),
                "trades": None,            # UNREADABLE, never empty
                "both_sides_served": r.get("both_sides_served"),
                "authenticated_age_s": r.get("last_authenticated_update_age_s"),
            }
    return out


def fetch_status(url: str, timeout: float = 10.0) -> Optional[str]:
    """GET the recorder's status document. A failure is None, not {}."""
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "l5-surface"},
                                     method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return fh.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def fetch_trades(url: str, timeout: float = 10.0
                 ) -> Optional[Dict[str, Dict[str, Any]]]:
    """GET the recorder's /trades tail -> {slug: {trades, ring_full}}.

    UNREADABLE IS None, NEVER {}. A 404 (the endpoint disabled), a timeout or
    a malformed body all mean we could not read the tape -- and {} would tell
    every market that nobody traded, which is the one error that manufactures
    confident no-fills across the whole slate at once.
    """
    body = fetch_status(url, timeout=timeout)
    if body is None:
        return None
    newest = None
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            newest = json.loads(line)
        except ValueError:
            continue
    if not isinstance(newest, dict):
        return None
    markets = newest.get("markets")
    if not isinstance(markets, Mapping):
        return None
    out: Dict[str, Dict[str, Any]] = {}
    for slug, rec in markets.items():
        if not isinstance(rec, Mapping):
            continue
        rows = rec.get("trades")
        out[str(slug)] = {
            "trades": list(rows) if isinstance(rows, list) else None,
            "ring_full": bool(rec.get("ring_full")),
            "bad_trade_rows": rec.get("bad_trade_rows"),
        }
    return out


def join_trades(served: Dict[str, Dict[str, Any]],
                trades_doc: Optional[Mapping[str, Mapping[str, Any]]]
                ) -> Dict[str, Dict[str, Any]]:
    """Attach the authenticated tape to the book rows, market by market.

    A market the tape document does not mention keeps `trades: None`. It is
    not [] and never becomes []: the recorder publishes an entry for every
    market it subscribes to, so an ABSENT slug means our view of that market
    is incomplete, while a PRESENT slug carrying [] is a real, subscribed
    silence the ladder may read as no flow.
    """
    for slug, m in served.items():
        rec = (trades_doc or {}).get(slug)
        if not isinstance(rec, Mapping):
            continue                       # stays None -- UNREADABLE
        rows = rec.get("trades")
        if not isinstance(rows, list):
            continue                       # stays None -- UNREADABLE
        m["trades"] = rows
        m["ring_full"] = bool(rec.get("ring_full"))
        m["served_cap"] = SERVED_TAIL_CAP
    return served


def market_from_recorder(state: Mapping[str, Any]) -> Dict[str, Any]:
    """The WS recorder's per-market state, used as-is.

    Its trade tape is the authenticated one; `pmus_prints` applies the
    coverage rules (a wrapped 500-trade ring vouches for nothing)."""
    return {
        "bid": _f(state.get("bid")), "ask": _f(state.get("ask")),
        "bid_qty": _f(state.get("bid_qty")),
        "ask_qty": _f(state.get("ask_qty")),
        "trades": state.get("trades"),
        "both_sides_served": state.get("both_sides_served"),
    }


def resync(surfaces: Dict[str, Any], served: Mapping[str, Any], *,
           cap: int, spec: Any) -> Dict[str, int]:
    """Follow the live slate instead of freezing the one we started on.

    A sports slate turns over: games end, new ones start, and a run pinned to
    its first `cap` slugs spends the rest of the day observing settled
    markets.

    SURFACES FOR SLUGS STILL PRESENT ARE KEPT. Rebuilding one that never went
    away would discard its placement history and the queue positions captured
    at placement -- and queue-ahead-at-placement is the whole basis of
    EVIDENCED_FILL. A resync that reset them would quietly convert a fill
    question into a fresh-quote question every few minutes.
    """
    added = [x for x in list(served)[:cap] if x not in surfaces]
    gone = [x for x in surfaces if x not in served]
    for x in added:
        surfaces[x] = SS.MarketSurface(x, spec)
    for x in gone:
        surfaces.pop(x, None)
    return {"added": len(added), "retired": len(gone),
            "watching": len(surfaces)}


def discover(cap: int = 64) -> List[str]:
    """Live sports slugs, unsigned. Two-sided is decided per observation by
    the surface itself, not pre-filtered here, so a market that becomes
    two-sided mid-run is not excluded by a stale screen."""
    from src.pmus_sports_collector import (http_get_json, fetch_live_events,
                                           live_event_targets)
    events, _ = fetch_live_events(http_get_json)
    return [t["slug"] for t in live_event_targets(events, prefixes=())][:cap]


def run(out_dir: str, *, minutes: float = 30.0, interval: float = 5.0,
        cap: int = 40, source: str = PUBLIC,
        status_url: str = DEFAULT_STATUS_URL,
        trades_url: str = DEFAULT_TRADES_URL,
        states: Optional[Mapping[str, Mapping[str, Any]]] = None) -> int:
    # LAZY, AND ONLY FOR THE SOURCES THAT NEED IT. The gateway client is the
    # PUBLIC path's book reader and the discovery helper; TRADES_HTTP and
    # STATUS_HTTP read the recorder instead and never touch it. Importing it
    # unconditionally would drag it into images that carry neither it nor any
    # reason for it -- l5-exec-shadow runs this stream with a deliberately
    # narrow COPY list, and every module in that list is one more thing to
    # justify.
    http_get_json = None
    if source == PUBLIC:
        from src.pmus_sports_collector import http_get_json    # noqa: F811

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "pmus_surface_%s.jsonl"
                        % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    fh = open(path, "a", encoding="utf-8")

    def emit(row: Mapping[str, Any]) -> None:
        fh.write(json.dumps(row, default=str) + "\n")
        fh.flush()

    spec = SS.spec()
    if source in (STATUS_HTTP, TRADES_HTTP):
        first = markets_from_status(fetch_status(status_url) or "")
        slugs = list(first)[:cap]
    elif states is not None:
        slugs = list(states)
    else:
        slugs = discover(cap)                      # PUBLIC only; imports there
    surfaces: Dict[str, SS.MarketSurface] = {
        s: SS.MarketSurface(s, spec) for s in slugs}

    emit({"kind": "session_start", "label": LABEL, "t": time.time(),
          "spec_label": spec.label, "spec_sha256": spec.spec_sha256(),
          "trade_flow_source": (AUTHENTICATED
                                if source in AUTHENTICATED_FLOW
                                else "UNAVAILABLE"),
          "status_url": (status_url
                         if source in (STATUS_HTTP, TRADES_HTTP) else None),
          "trades_url": (trades_url if source == TRADES_HTTP else None),
          "served_tail_cap": (SERVED_TAIL_CAP if source == TRADES_HTTP
                              else None),
          "source": source, "slugs": slugs,
          "labels": ("a PUBLIC run cannot reach TRADE_FLOW: the gateway "
                     "serves no print tape and sharesTraded is cumulative per "
                     "market. It is not used as a flow proxy. A STATUS_HTTP "
                     "run cannot reach it either -- that document carries the "
                     "authenticated BOOK and only trade AGGREGATES. "
                     "TRADES_HTTP adds the recorder's /trades tail, which is "
                     "per PRICE, and is the only network source that can "
                     "reach TRADE_FLOW and therefore EVIDENCED_FILL.")})

    deadline = time.time() + minutes * 60.0
    obs = 0
    # the newest tape-coverage facts, kept for the stdout heartbeat. The
    # evidence rows go to the volume, which nothing outside the container can
    # read -- so the few facts that say whether this stream is WORKING have to
    # reach the log too, or "is the tape arriving?" is unanswerable from
    # outside without shelling into the service.
    cov: Dict[str, Any] = {"tape": "NOT_YET_READ"}
    while time.time() < deadline:
        t0 = time.time()
        served: Dict[str, Dict[str, Any]] = {}
        if source in (STATUS_HTTP, TRADES_HTTP):
            body = fetch_status(status_url)
            if body is None:
                emit({"kind": "status_unreachable", "t": time.time(),
                      "url": status_url,
                      "labels": ("UNREACHABLE, not empty: no market is "
                                 "observed this cycle and none is scored")})
            else:
                served = markets_from_status(body)
            if source == TRADES_HTTP and served:
                doc = fetch_trades(trades_url)
                if doc is None:
                    # the books are still usable; the ladder simply stops at
                    # BOOK_REACHED this cycle, which is what trades=None means
                    cov = {"tape": "UNREACHABLE", "url": trades_url}
                    emit({"kind": "trades_unreachable", "t": time.time(),
                          "url": trades_url,
                          "labels": ("UNREADABLE, not empty: every market "
                                     "keeps trades=None, so this cycle can "
                                     "reach BOOK_REACHED and stops there. An "
                                     "empty tape would instead assert that "
                                     "nobody traded.")})
                else:
                    served = join_trades(served, doc)
                    covered = sum(1 for m in served.values()
                                  if m.get("trades") is not None)
                    wrapped = sum(1 for m in served.values()
                                  if m.get("ring_full"))
                    cov = {"tape": "SERVED", "markets_in_tape": len(doc),
                           "joined": covered,
                           "book_only": len(served) - covered,
                           "ring_full": wrapped,
                           "prints": sum(len(m.get("trades") or [])
                                         for m in served.values()
                                         if m.get("trades") is not None)}
                    # summarised on the progress cadence, not every cycle: at
                    # 5s intervals a per-cycle row is ~17k lines a day of
                    # counts, and the LADDER rows are the evidence. An
                    # unreachable tape stays unthrottled -- that one is a
                    # condition, not a summary.
                    coverage_due = (obs % 6 == 0)
                    if coverage_due:
                        emit({"kind": "trades_served", "t": time.time(),
                              "markets_in_tape": len(doc),
                              "markets_joined": covered,
                              "markets_book_only": len(served) - covered,
                              "ring_full_markets": wrapped,
                              "prints_total": sum(
                                  len(m.get("trades") or [])
                                  for m in served.values()
                                  if m.get("trades") is not None),
                              "labels": ("markets_book_only are UNREADABLE "
                                         "tape, not silent markets")})
        # follow the slate on the progress cadence, not every cycle
        if source in (STATUS_HTTP, TRADES_HTTP) and served and obs % 60 == 0:
            delta = resync(surfaces, served, cap=cap, spec=spec)
            if delta["added"] or delta["retired"]:
                emit({"kind": "slate_resync", "t": time.time(), **delta,
                      "labels": ("surfaces for slugs still present are KEPT; "
                                 "only genuinely gone markets are retired")})

        for slug, surf in list(surfaces.items()):
            if source == AUTHENTICATED:
                st = (states or {}).get(slug)
                mkt = market_from_recorder(st) if st else None
            elif source in (STATUS_HTTP, TRADES_HTTP):
                mkt = served.get(slug)
            else:
                mkt = market_from_gateway(slug, http_get_json)
            if mkt is None:
                surf.empty_reason["unreadable"] += 1
                continue
            for row in SS.step(surf, mkt, time.time(), tte_s=3600.0):
                emit(row)
        obs += 1
        if obs % 6 == 0:
            rep = SS.report(surfaces)
            emit({"kind": "progress", "t": time.time(), "cycles": obs, **rep})
            # ONE compact line to stdout: whether the tape arrived, and how
            # far the ladder has actually climbed. Ladder counts are reported
            # as measured -- EVIDENCED_FILL is never inferred from the others.
            print("PMUS_SURFACE " + json.dumps(
                {"kind": "heartbeat", "cycles": obs, "source": source,
                 "watching": len(surfaces), "tape_join": cov,
                 "per_side": rep.get("per_side"),
                 "tape": rep.get("tape"), "verdict": rep.get("verdict")},
                default=str), flush=True)
        sleep = interval - (time.time() - t0)
        if sleep > 0:
            time.sleep(sleep)

    final = SS.report(surfaces)
    emit({"kind": "session_end", "t": time.time(), "cycles": obs, **final})
    fh.close()
    print("PMUS_SURFACE " + json.dumps({"ok": True, "tape": path,
                                        "cycles": obs, **final},
                                       default=str), flush=True)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default="data/pmus_l5_surface")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--cap", type=int, default=40)
    ap.add_argument("--source", choices=(PUBLIC, AUTHENTICATED, STATUS_HTTP,
                                        TRADES_HTTP), default=PUBLIC)
    ap.add_argument("--status-url", default=DEFAULT_STATUS_URL)
    ap.add_argument("--trades-url", default=DEFAULT_TRADES_URL)
    a = ap.parse_args(argv)
    return run(a.out_dir, minutes=a.minutes, interval=a.interval, cap=a.cap,
               source=a.source, status_url=a.status_url,
               trades_url=a.trades_url)


if __name__ == "__main__":
    raise SystemExit(main())
