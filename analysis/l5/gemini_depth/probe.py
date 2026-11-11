"""Is Gemini's book shallow, stale, badly parsed, or just fast? Measure it.

THE OBSERVATION THAT STARTED THIS: manually refreshing the Gemini UI changes
the displayed book. That is a claim about our DATA PATH, and it had to be
settled before any capacity conclusion, because four of the five candidate
explanations would invalidate one:

  1. genuinely one executable contract
  2. best-level depth only, with size behind it
  3. a stale or cached snapshot
  4. incomplete book parsing / truncated retrieval
  5. rapidly changing genuine liquidity

This probe runs both data paths against the SAME contract at the same instants
and records enough to separate them:

  REST   every request timestamped on send and receive, with the venue's own
         per-level timestamps, so `snapshot_age_s` is measured and not assumed.
         Response headers are captured once per window: no Cache-Control, no
         Age, no CDN header is served, which rules cache staleness out at the
         HTTP layer rather than by assertion.
  WS     `wss://api.gemini.com/v1/marketdata/{symbol}` maintained continuously,
         so the comparison is against the book as the venue actually has it
         rather than against another sample of it.
  limit=1  the OLD collector path, sampled periodically, to quantify exactly
         how much of the book that truncation hides.

TIME-WEIGHTED AVAILABILITY IS THE POINT. A snapshot answers "was q executable
at 13:33:07.2". For a venue whose touch is cancelled and replaced 25ms later,
the answerable question is "what FRACTION OF THE TIME is q executable, and at
what VWAP" -- so the fast sampler reads the maintained book at 2 Hz and the
statistics below are time-weighted rather than snapshot counts.

NO ARTIFICIAL DELAYS AND NO UI AUTOMATION. Every REST call is a fresh
unauthenticated GET issued as fast as the loop reaches it; nothing is slowed
down to make a book look better or worse.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

from src.l5_shadow import depth as DEP
from src.l5_shadow import venues as V
from src.l5_shadow.gemini_ws import GeminiFeed

REST = "https://api.gemini.com/v1/book/{symbol}"
UA = {"User-Agent": "l5-gemini-depth-probe/0.1 (read-only research)"}
CA_BUNDLE = "/root/.ccr/ca-bundle.crt"
QTY = (D("1"), D("2"), D("5"), D("10"), D("20"))
# offsets from the touch at which cumulative depth is accumulated, so any
# ceiling can be applied afterwards without re-running
OFFSETS = (D("0"), D("0.01"), D("0.02"), D("0.05"))

FAST_HZ = 2.0            # maintained-book sampling; costs no network
REST_EVERY_S = 3.0       # paired fresh REST call
TRUNC_EVERY_S = 30.0     # the old limit=1 path, for comparison
ROTATE_EVERY_S = 20.0    # pick up new windows as they open
SAMPLES = "samples.jsonl"
PAIRED = "paired.jsonl"
EVENTS = "events.jsonl"


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    try:
        c.load_verify_locations(CA_BUNDLE)
    except OSError:
        pass
    return c


def rest_book(symbol: str, qs: str = "") -> Dict[str, Any]:
    """A fresh REST book with both ends of the request timestamped."""
    t0 = time.time()
    try:
        req = urllib.request.Request(REST.format(symbol=symbol) + qs,
                                     headers=UA, method="GET")
        with urllib.request.urlopen(req, timeout=15, context=_ctx()) as fh:
            body = fh.read().decode("utf-8")
            hdr = {k.lower(): v for k, v in fh.headers.items()}
            t1 = time.time()
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"ok": False, "t_request": t0, "t_response": time.time(),
                "err": f"{type(e).__name__}: {e}"}
    try:
        j = json.loads(body)
    except ValueError as e:
        return {"ok": False, "t_request": t0, "t_response": t1,
                "err": f"unparseable: {e}"}
    bids, asks = j.get("bids") or [], j.get("asks") or []
    ts = [float(x["timestamp"]) for x in list(bids) + list(asks)
          if isinstance(x, dict) and x.get("timestamp")]
    venue_ts = max(ts) if ts else None
    return {"ok": True, "t_request": t0, "t_response": t1,
            "rtt_s": round(t1 - t0, 4),
            "venue_ts": venue_ts,
            # measured, not assumed: response time minus the venue's own
            # newest level stamp
            "snapshot_age_s": (round(t1 - venue_ts, 3) if venue_ts else None),
            "bids": bids, "asks": asks,
            "price_key": "price", "size_key": "amount",
            "cache_headers": {k: hdr[k] for k in
                              ("cache-control", "age", "expires", "etag",
                               "cf-cache-status", "x-cache", "via", "pragma")
                              if k in hdr},
            "server": hdr.get("server"), "date": hdr.get("date")}


def summarise(levels: Sequence) -> Dict[str, Any]:
    """Compact, ceiling-free description of one side's ladder.

    The q-ladder VWAPs carry no ceiling so that any ceiling can be applied
    later; `cum_at_offset` carries the cumulative size within a few ticks of
    the touch, which is what a ceiling near the touch actually consumes.
    """
    if not levels:
        return {"levels": 0, "best": None, "best_qty": None, "total": "0",
                "by_qty": {str(q): {"filled": "0", "vwap": None} for q in QTY},
                "cum_at_offset": {str(o): "0" for o in OFFSETS}}
    best = D(str(levels[0][0]))
    by_qty = {}
    for q in QTY:
        r = DEP.vwap_for_qty(levels, q)
        by_qty[str(q)] = {"filled": r["filled"], "vwap": r["vwap"],
                          "complete": r["complete"],
                          "levels_used": r["levels_used"]}
    cum = {}
    for o in OFFSETS:
        cap = best + o
        cum[str(o)] = str(sum((D(str(s)) for p, s in levels
                               if D(str(p)) <= cap), D("0")))
    return {"levels": len(levels), "best": str(best),
            "best_qty": str(levels[0][1]),
            "total": str(sum((D(str(s)) for _, s in levels), D("0"))),
            "by_qty": by_qty, "cum_at_offset": cum}


def ladders(raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        return {"asks": DEP.parse_book(raw, "asks"),
                "bids": DEP.parse_book(raw, "bids")}
    except DEP.BookParseError as e:
        return {"asks": None, "bids": None, "parse_error": str(e)}


def append(out_dir: str, name: str, rec: Dict[str, Any]) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name), "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


def live_symbols() -> List[Dict[str, str]]:
    out = []
    for w in V.live_windows():
        sym = V.gemini_symbol_for(w["market_key"])
        if sym:
            out.append({"symbol": sym, "cohort": w["cohort"],
                        "market_key": w["market_key"],
                        "window_end": w["window_end"]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-gemini-depth")
    ap.add_argument("--hours", type=float, default=0.0)
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)

    feed = GeminiFeed()
    print("GEMINI_DEPTH boot: WS + paired REST, no artificial delay, "
          "no UI automation", flush=True)
    watching: Dict[str, dict] = {}
    last_rot = last_rest = last_trunc = 0.0
    deadline = time.time() + a.hours * 3600 if a.hours else None
    n_fast = n_rest = 0
    hdr_logged: set = set()
    prev: Dict[str, Dict[str, Any]] = {}

    while deadline is None or time.time() < deadline:
        now = time.time()
        if now - last_rot > ROTATE_EVERY_S:
            last_rot = now
            for w in live_symbols():
                if w["symbol"] not in watching:
                    watching[w["symbol"]] = w
                    feed.watch(w["symbol"])
                    print(f"GEMINI_DEPTH watch {w['symbol']} ({w['cohort']})",
                          flush=True)
            for sym, w in list(watching.items()):
                if w["window_end"] + 60 < now:
                    feed.drop(sym)
                    watching.pop(sym, None)

        # ---- the RAW event stream, drained every cycle.
        # Snapshots cannot distinguish an order that rested untouched from one
        # cancelled and re-placed identically, so flow -- how much NEW eligible
        # liquidity actually arrived -- is only answerable from the events.
        for sym, w in list(watching.items()):
            try:
                bk = feed.watch(sym)
                for e in bk.drain_events():
                    append(a.out_dir, EVENTS, {**e, "cohort": w["cohort"],
                                               "window_end": w["window_end"]})
            except Exception:                                 # noqa: BLE001
                pass

        # ---- fast: the maintained book, no network, time-weighted
        for sym, w in list(watching.items()):
            b = feed.book(sym)
            row = {"kind": "ws", "t": now, "symbol": sym, "cohort": w["cohort"],
                   "tte_s": round(w["window_end"] - now, 1)}
            if b is None:
                row.update(ready=False,
                           reason="book not ready or socket down: UNKNOWN")
            else:
                lad = ladders(b)
                row.update(ready=True, age_s=b["age_s"],
                           venue_ts_ms=b["venue_ts_ms"],
                           socket_sequence=b["socket_sequence"],
                           gaps=b["gaps"], reconnects=b["reconnects"],
                           counts=b["counts"],
                           ask=summarise(lad["asks"] or []),
                           bid=summarise(lad["bids"] or []))
                # touch persistence: has the top of book changed since the
                # previous sample, and how long has this state held?
                key = (row["ask"]["best"], row["ask"]["best_qty"])
                p = prev.get(sym)
                if p and p["key"] == key:
                    row["touch_held_s"] = round(now - p["since"], 3)
                else:
                    prev[sym] = {"key": key, "since": now}
                    row["touch_held_s"] = 0.0
                    row["touch_changed"] = True
            append(a.out_dir, SAMPLES, row)
            n_fast += 1

        # ---- paired: a FRESH REST call against the same maintained book
        if now - last_rest > REST_EVERY_S:
            last_rest = now
            for sym, w in list(watching.items()):
                r = rest_book(sym)
                ws_after = feed.book(sym)
                if r["ok"] and r.get("cache_headers") is not None \
                        and sym not in hdr_logged:
                    hdr_logged.add(sym)
                    print("GEMINI_DEPTH rest_headers " + json.dumps(
                        {"symbol": sym, "server": r.get("server"),
                         "cache_headers": r["cache_headers"],
                         "reads": ("empty cache_headers means the venue serves "
                                   "no cache directive at all")}), flush=True)
                rec = {"kind": "paired", "t": now, "symbol": sym,
                       "cohort": w["cohort"],
                       "tte_s": round(w["window_end"] - now, 1),
                       "rest": {k: r.get(k) for k in
                                ("ok", "t_request", "t_response", "rtt_s",
                                 "venue_ts", "snapshot_age_s", "err")}}
                if r["ok"]:
                    rl = ladders(r)
                    rec["rest"]["ask"] = summarise(rl["asks"] or [])
                    rec["rest"]["bid"] = summarise(rl["bids"] or [])
                if ws_after:
                    wl = ladders(ws_after)
                    rec["ws"] = {"age_s": ws_after["age_s"],
                                 "socket_sequence": ws_after["socket_sequence"],
                                 "ask": summarise(wl["asks"] or []),
                                 "bid": summarise(wl["bids"] or [])}
                    if r["ok"]:
                        rec["agree"] = {
                            "best_ask": (rec["rest"]["ask"]["best"]
                                         == rec["ws"]["ask"]["best"]),
                            "best_ask_qty": (rec["rest"]["ask"]["best_qty"]
                                             == rec["ws"]["ask"]["best_qty"]),
                            "total_ask": (rec["rest"]["ask"]["total"]
                                          == rec["ws"]["ask"]["total"]),
                            "n_ask_levels": (rec["rest"]["ask"]["levels"]
                                             - rec["ws"]["ask"]["levels"])}
                else:
                    rec["ws"] = None
                append(a.out_dir, PAIRED, rec)
                n_rest += 1

        # ---- the old truncated path, so its cost is a number not a story
        if now - last_trunc > TRUNC_EVERY_S:
            last_trunc = now
            for sym, w in list(watching.items()):
                full = rest_book(sym)
                trunc = rest_book(sym, "?limit_bids=1&limit_asks=1")
                rec = {"kind": "truncation", "t": now, "symbol": sym,
                       "cohort": w["cohort"]}
                for label, r in (("full", full), ("limit1", trunc)):
                    if r["ok"]:
                        lz = ladders(r)
                        rec[label] = {"t_request": r["t_request"],
                                      "snapshot_age_s": r["snapshot_age_s"],
                                      "ask": summarise(lz["asks"] or []),
                                      "bid": summarise(lz["bids"] or [])}
                    else:
                        rec[label] = {"err": r.get("err")}
                append(a.out_dir, PAIRED, rec)

        if n_fast and n_fast % 200 == 0:
            print("GEMINI_DEPTH " + json.dumps(
                {"kind": "progress", "utc": round(now, 1),
                 "ws_samples": n_fast, "paired_rest": n_rest,
                 "watching": sorted(watching)}), flush=True)
        time.sleep(max(0.0, (1.0 / FAST_HZ) - (time.time() - now)))
    feed.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
