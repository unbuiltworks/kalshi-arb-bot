"""Durable READ-ONLY live-sports habitat + frozen-L5-shadow service.

ONE process, four collector threads, one analysis emitter. Exists because the
research container recycled three times in two hours (10:18Z, 11:24Z, 11:50Z on
2026-09-20), each time silently killing every detached collector and costing
data the NFL PRE->LIVE experiment cannot recover.

HARD PROPERTIES, by construction:
  * PUBLIC GETs ONLY. gateway.polymarket.us, gamma-api/clob/data-api
    polymarket.com. No credentials are read, held or accepted.
  * NO ORDER TRANSPORT. This module imports no executor, no session, no
    signer. The only writes are appends to local JSONL.
  * NO ARMING. Nothing here can be armed and there is no flag that would.
  * MODEL A UNTOUCHED. The frozen policy is imported and called, never edited.
  * APPEND-ONLY to L5_LIVE_ROOT, which must be a mounted volume to be durable.
  * GAP MARKERS RETAINED. Every thread restart and every process start writes
    a COLLECTOR_GAP marker into the affected stream, so no analysis can ever
    silently bridge an outage.

Results leave this service as structured heartbeat log lines, the same way the
other shadows report, because the volume is not readable from outside.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import queue
import ssl
import sys
import threading
import time
import traceback
import urllib.request

ROOT = os.getenv("L5_LIVE_ROOT", "/state/l5-live")
GATEWAY = "https://gateway.polymarket.us"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"

PMUS_BOOK_CAD = float(os.getenv("L5_PMUS_BOOK_CAD", "8"))
INTL_BOOK_CAD = float(os.getenv("L5_INTL_BOOK_CAD", "8"))
PMUS_FLOW_CAD = float(os.getenv("L5_PMUS_FLOW_CAD", "8"))
INTL_FLOW_CAD = float(os.getenv("L5_INTL_FLOW_CAD", "20"))
REDISCOVER_S = float(os.getenv("L5_REDISCOVER_S", "180"))
HEARTBEAT_S = float(os.getenv("L5_HEARTBEAT_S", "60"))
ANALYSIS_S = float(os.getenv("L5_ANALYSIS_S", "300"))
TOTAL_CAP = int(os.getenv("L5_PMUS_CAP", "300"))
LIVE_PER_EVENT = int(os.getenv("L5_LIVE_PER_EVENT", "6"))
INTL_PER_EVENT = int(os.getenv("L5_INTL_PER_EVENT", "4"))
INTL_CAP = int(os.getenv("L5_INTL_CAP", "150"))
BAND_LO, BAND_HI = 0.30, 0.70
# PREREGISTERED DIAGNOSTIC STRATUM retained for continuity. The earlier
# rationale that v2 COMPLETE was confined to p10-25c was falsified by the
# canonical audit; rows remain explicitly labelled so this diagnostic sample
# can never be confused with the [0.30,0.70] transfer cohort.
DIAG_LO, DIAG_HI = 0.10, 0.25

# Falls back to the SYSTEM TRUST STORE, never to None and never by raising.
# Pinning this path without a fallback is how three prospective windows
# recorded 361 executions with an empty leg list: the path is absent in a
# deployed image, the context raised, and a bare `except` swallowed it.
# tests/test_chain_ca_fallback.py exists to stop it recurring.
_CA = "/root/.ccr/ca-bundle.crt"
_CTX = (ssl.create_default_context(cafile=_CA) if os.path.exists(_CA)
        else ssl.create_default_context())
_LOCK = threading.Lock()
_FILES: dict = {}
_COUNTS: dict = collections.defaultdict(int)
_NFL_PRE_BASELINE_SLUGS: set[str] = set()


def load_nfl_pre_baseline() -> set[str]:
    """Recover previously observed NFL PRE control slugs from durable storage.

    This guarantees same-market PRE->LIVE continuity even across a process
    restart: once a slug has been observed in nfl_pre_control, the LIVE
    discovery path keeps that exact slug if the venue still serves a two-sided
    book for it.
    """
    p = os.path.join(ROOT, "pmus_books.jsonl")
    out: set[str] = set()
    if not os.path.exists(p):
        return out
    try:
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                if '"nfl_pre_control"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("stratum") == "nfl_pre_control" and row.get("slug"):
                    out.add(str(row["slug"]))
    except OSError:
        return out
    return out


def log(kind: str, **kw) -> None:
    print("L5_LIVE %s" % json.dumps({"kind": kind, **kw}, default=str),
          flush=True)


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()


def get(url: str, timeout: float = 25.0):
    req = urllib.request.Request(
        url, headers={"User-Agent": "l5-research-readonly/1.0",
                      "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return json.loads(r.read().decode())


def qf(v):
    v = v.get("value") if isinstance(v, dict) else v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def kickoff_within_hours(value, hours: float = 12.0) -> bool:
    """True for an event whose scheduled start is from now through +hours.

    Used only to guarantee today's NFL PRE control survives the generic
    pregame cap. It is an observation-priority rule, never an execution gate.
    """
    if not value:
        return False
    try:
        s = str(value).replace("Z", "+00:00")
        k = dt.datetime.fromisoformat(s)
        if k.tzinfo is None:
            k = k.replace(tzinfo=dt.timezone.utc)
        delta_s = (k - dt.datetime.now(dt.timezone.utc)).total_seconds()
        return 0.0 <= delta_s <= hours * 3600.0
    except (TypeError, ValueError):
        return False


def stream(name: str):
    with _LOCK:
        fh = _FILES.get(name)
        if fh is None:
            os.makedirs(ROOT, exist_ok=True)
            fh = open(os.path.join(ROOT, name + ".jsonl"), "a", buffering=1)
            _FILES[name] = fh
        return fh


def write(name: str, row: dict) -> None:
    fh = stream(name)
    with _LOCK:
        fh.write(json.dumps(row, default=str) + "\n")
        _COUNTS[name] += 1


def gap(name: str, reason: str, **kw) -> None:
    write(name, {"_marker": "COLLECTOR_GAP", "t": time.time(),
                 "reason": reason, **kw})


def rank(k) -> tuple:
    k = str(k or "")
    for i, pat in enumerate((
            "moneyline", "full_game_winner", "full_time_winner",
            "match_winner", "team_full_game_spread", "team_full_game_total",
            "team_points_full_game_total", "game_full_game_total",
            "first_half_spread", "first_half_total",
            "second_half_spread", "second_half_total")):
        if pat in k:
            return (i, k)
    return (99, k)


# --------------------------------------------------------------- PMUS books
def pmus_discover() -> tuple:
    """LIVE first, today's NFL PRE control second, then generic pregame.

    Returns `(selection, livemap)`. The liveness map is built from the very
    events this pass already fetched. It used to be built by a SECOND pass over
    `/v2/leagues`, which returns 50 leagues against the 266 this pass reaches
    through `/v2/sports` -- so an event in any of the other 216 was absent from
    the map, and `pmus_flow_loop` reads absence as not-live. That silently
    stopped the flow stream for 2h12m overnight on 2026-09-21 while every other
    thread stayed healthy; see analysis/l5/PMUS_FLOW_INCIDENT_20260921.md.
    Deriving both from one pass makes the disagreement impossible to express,
    and deletes a whole network pass rather than adding one.

    ?limit is mandatory: without it the endpoint silently returns only ten
    events per league. NFL PRE is explicitly prioritized so the durable
    collector cannot lose the same-market PRE->LIVE control to TOTAL_CAP.
    """
    try:
        # /v2/leagues is an incomplete legacy index (~50 slugs). The venue's
        # own /v2/sports payload is the authoritative discovery surface and
        # currently exposes hundreds of operational league slugs, including
        # ATP/WTA/CFB/Dota2 habitats seen by the independent PMUS surface tape.
        sports_payload = get(GATEWAY + "/v2/sports")
        leagues = []
        def collect_leagues(obj):
            if isinstance(obj, dict):
                # Only objects carried in an explicit leagues collection are
                # admitted below; recurse so wrapper shapes remain harmless.
                for k, v in obj.items():
                    if k == "leagues" and isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict) and item.get("slug"):
                                if item.get("isOperational", True):
                                    leagues.append(str(item["slug"]))
                            elif isinstance(item, str):
                                leagues.append(item)
                    else:
                        collect_leagues(v)
            elif isinstance(obj, list):
                for item in obj:
                    collect_leagues(item)
        collect_leagues(sports_payload)
        leagues = list(dict.fromkeys(leagues))
        if not leagues:
            raise ValueError("no leagues discovered from /v2/sports")
    except Exception:
        return [], {}
    live, nfl_live_control, nfl_pre, pre, diag = [], [], [], [], []
    livemap: dict = {}
    for lg in leagues:
        try:
            ev = get(GATEWAY + "/v2/leagues/%s/events?limit=500" % lg)
        except Exception:
            continue
        for e in (ev.get("events") or []):
            st = e.get("eventState") or {}
            is_live = bool(st.get("live")) and not st.get("ended")
            # Recorded for EVERY event this pass reaches, not only the ones
            # that survive the market filters below -- the flow selector keys
            # on the event, so an event dropped for having no two-sided market
            # must still be answerable as live/not-live rather than missing.
            livemap[e.get("slug")] = (is_live, st.get("period"), st.get("score"))
            fam = collections.defaultdict(list)
            for m in (e.get("markets") or []):
                if not m.get("slug"):
                    continue
                b, a = qf(m.get("bestBidQuote")), qf(m.get("bestAskQuote"))
                if b is None or a is None or not (0 < b < a < 1):
                    continue
                mid = (b + a) / 2
                row = {"league": lg, "event": e.get("slug"), "slug": m["slug"],
                       "type": m.get("marketType"),
                       "sports_type": m.get("sportsMarketType"),
                       "line": m.get("line"),
                       "kickoff_utc": e.get("startTime") or e.get("startDate"),
                       "mid": mid}
                if is_live and row["slug"] in _NFL_PRE_BASELINE_SLUGS:
                    nfl_live_control.append(
                        {**row, "stratum": "nfl_pre_live_control"})
                if is_live and DIAG_LO <= mid <= DIAG_HI:
                    diag.append({**row, "stratum": "p10_25_diagnostic"})
                fam[m.get("sportsMarketType") or m.get("marketType")].append(
                    (abs(mid - 0.5), row, mid))
            picked = []
            for k in sorted(fam, key=rank):
                for _, row, mid in sorted(fam[k], key=lambda r: r[0])[:2]:
                    picked.append((row, mid))
            if is_live:
                for row, _mid in picked[:LIVE_PER_EVENT]:
                    live.append({**row, "stratum": "live"})
            else:
                for row, mid in picked[:9]:
                    if BAND_LO <= mid <= BAND_HI:
                        base = {**row, "stratum": "pre_band"}
                        pre.append(base)
                        if lg == "nfl" and kickoff_within_hours(
                                row.get("kickoff_utc"), 12.0):
                            nfl_pre.append(
                                {**row, "stratum": "nfl_pre_control"})
    seen, out = set(), []
    # NFL PRE must be ahead of generic PRE so today's same-market control
    # cannot be crowded out by other leagues under TOTAL_CAP.
    # Exact NFL PRE slugs go first once their event is LIVE, guaranteeing
    # same-market continuity instead of allowing a moving line/priority choice
    # to silently swap the market under test at kickoff.
    for r in nfl_live_control + live + diag + nfl_pre + pre:
        if r["slug"] in seen:
            continue
        seen.add(r["slug"])
        out.append(r)
        if len(out) >= max(
                TOTAL_CAP,
                len(nfl_live_control) + len(live) + len(diag) + len(nfl_pre)):
            break
    return out, livemap


def top(L):
    if not L:
        return None, None
    px = qf(L[0].get("px"))
    try:
        qy = float(L[0].get("qty"))
    except (TypeError, ValueError):
        qy = None
    return px, qy


def pmus_books_loop(state: dict) -> None:
    sweep = 0
    while True:
        t0 = time.time()
        sweep += 1
        sel = state.get("pmus") or []
        livemap = state.get("pmus_live") or {}
        batch = [r for r in sel
                 if livemap.get(r["event"], (False,))[0] or sweep % 4 == 0]
        qq: queue.Queue = queue.Queue()
        for r in batch:
            qq.put(r)

        def worker():
            while True:
                try:
                    r = qq.get_nowait()
                except queue.Empty:
                    return
                try:
                    b = get(GATEWAY + "/v1/markets/%s/book" % r["slug"]
                            )["marketData"]
                except Exception:
                    continue
                bp, bq = top(b.get("bids"))
                ap, aq = top(b.get("offers"))
                lv, per, sc = livemap.get(r["event"], (False, None, None))
                write("pmus_books", {
                    "venue": "PMUS", "t": t0, "cad": PMUS_BOOK_CAD,
                    "sweep_s": state.get("pmus_sweep_s"),
                    "slug": r["slug"], "league": r["league"],
                    "event": r["event"], "type": r["type"],
                    "sports_type": r["sports_type"], "line": r.get("line"),
                    "kickoff_utc": r.get("kickoff_utc"),
                    "stratum": r.get("stratum"),
                    "live": lv, "period": per, "score": sc,
                    "bid": bp, "ask": ap, "bid_qty": bq, "ask_qty": aq})
        ths = [threading.Thread(target=worker) for _ in range(14)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        state["pmus_sweep_s"] = round(time.time() - t0, 2)
        d = PMUS_BOOK_CAD - (time.time() - t0)
        if d > 0:
            time.sleep(d)


# ---------------------------------------------------------------- PMUS flow
def pmus_flow_loop(state: dict) -> None:
    sweep = 0
    while True:
        t0 = time.time()
        sweep += 1
        livemap = state.get("pmus_live") or {}
        # LIVE flow stays at 8s. Today's NFL PRE control is sampled every
        # fourth sweep (~32s) so PRE compatible-flow can be compared with the
        # same markets after kickoff without materially increasing load.
        sel = [
            r for r in (state.get("pmus") or [])
            if livemap.get(r["event"], (False,))[0]
            or (r.get("stratum") == "nfl_pre_control" and sweep % 4 == 0)
        ]
        # Published so the heartbeat can tell "selected nothing to poll" apart
        # from "writer stuck". Both freeze rows_written identically, which is
        # what made the 2026-09-21 flow gap read as a stall for two hours.
        state["pmus_flow_selected"] = len(sel)
        qq: queue.Queue = queue.Queue()
        for r in sel:
            qq.put(r)

        def worker():
            while True:
                try:
                    r = qq.get_nowait()
                except queue.Empty:
                    return
                try:
                    b = get(GATEWAY + "/v1/markets/%s/bbo" % r["slug"]
                            )["marketData"]
                except Exception:
                    continue
                write("pmus_flow", {
                    "venue": "PMUS", "t": iso(t0), "slug": r["slug"],
                    "league": r["league"], "event": r["event"],
                    "sports_type": r["sports_type"],
                    "stratum": r.get("stratum"),
                    "bid": qf(b.get("bestBid")), "ask": qf(b.get("bestAsk")),
                    "shares_traded_cum": qf(b.get("sharesTraded")),
                    "last_trade_px": qf(b.get("lastTradePx")),
                    "open_interest": qf(b.get("openInterest")),
                    "trade_provenance": "VOLUME_DELTA_CAUSAL_NO_HINDSIGHT"})
        ths = [threading.Thread(target=worker) for _ in range(10)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        d = PMUS_FLOW_CAD - (time.time() - t0)
        if d > 0:
            time.sleep(d)


# ----------------------------------------------------------- INTERNATIONAL
def intl_discover() -> tuple:
    """Live slate by the venue's OWN scoreboard flag. startDate is
    market-creation time here and marks season-long futures as started, so it
    is never used for liveness."""
    evs, off = [], 0
    while off < 800:
        try:
            p = get(GAMMA + "/events?live=true&closed=false&limit=100"
                    "&offset=%d" % off)
        except Exception:
            break
        if not p:
            break
        evs += p
        off += 100
        if len(p) < 100:
            break
    sel, cmap, seen = [], {}, set()
    for e in evs:
        if e.get("id") in seen or e.get("ended"):
            continue
        seen.add(e.get("id"))
        sp = e.get("sport")
        sp = sp.get("sport") if isinstance(sp, dict) else sp
        fam = collections.defaultdict(list)
        for m in (e.get("markets") or []):
            if m.get("closed") or not m.get("acceptingOrders"):
                continue
            try:
                b = float(m.get("bestBid") or 0)
                a = float(m.get("bestAsk") or 0)
            except (TypeError, ValueError):
                continue
            if not (0 < b < a < 1):
                continue
            try:
                toks = json.loads(m.get("clobTokenIds") or "[]")
            except Exception:
                continue
            if not toks:
                continue
            if m.get("conditionId"):
                cmap[m["conditionId"]] = {
                    "slug": m.get("slug"), "league": sp or "?",
                    "event": e.get("slug"),
                    "sports_type": m.get("sportsMarketType")}
            fam[m.get("sportsMarketType") or "?"].append(
                (abs((b + a) / 2 - 0.5), m, toks[0]))
        pick = []
        for k in sorted(fam, key=rank):
            for _, m, tok in sorted(fam[k], key=lambda r: r[0])[:2]:
                pick.append((m, tok))
        for m, tok in pick[:INTL_PER_EVENT]:
            sel.append({"venue": "INTL", "event": e.get("slug"),
                        "league": sp or "?", "slug": m.get("slug"),
                        "token": tok, "condition": m.get("conditionId"),
                        "sports_type": m.get("sportsMarketType"),
                        "period": e.get("period"), "score": e.get("score"),
                        "kickoff_utc": m.get("gameStartTime")})
    return sel[:INTL_CAP], cmap


def intl_books_loop(state: dict) -> None:
    while True:
        t0 = time.time()
        sel = state.get("intl") or []
        qq: queue.Queue = queue.Queue()
        for r in sel:
            qq.put(r)

        def worker():
            while True:
                try:
                    r = qq.get_nowait()
                except queue.Empty:
                    return
                try:
                    bk = get(CLOB + "/book?token_id=%s" % r["token"])
                except Exception:
                    continue

                def best(L, hi):
                    b = None
                    for x in L or []:
                        try:
                            p = float(x["price"])
                            s = float(x["size"])
                        except (TypeError, ValueError, KeyError):
                            continue
                        if b is None or (p > b[0] if hi else p < b[0]):
                            b = (p, s)
                    return b or (None, None)
                bid, bq = best(bk.get("bids"), True)
                ask, aq = best(bk.get("asks"), False)
                write("intl_books", {
                    "venue": "INTL", "t": t0, "cad": INTL_BOOK_CAD,
                    "sweep_s": state.get("intl_sweep_s"),
                    "slug": r["slug"], "league": r["league"],
                    "event": r["event"], "type": r["sports_type"],
                    "sports_type": r["sports_type"],
                    "kickoff_utc": r.get("kickoff_utc"),
                    "live": True, "period": r.get("period"),
                    "score": r.get("score"),
                    "bid": bid, "ask": ask, "bid_qty": bq, "ask_qty": aq})
        ths = [threading.Thread(target=worker) for _ in range(12)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        state["intl_sweep_s"] = round(time.time() - t0, 2)
        d = INTL_BOOK_CAD - (time.time() - t0)
        if d > 0:
            time.sleep(d)


def intl_flow_loop(state: dict) -> None:
    """REAL prints from the global tape. One call returns ~1000 trades
    spanning ~48s across ~212 markets, so the whole venue is covered by a
    single request. Direction is KNOWN, never inferred."""
    seen: collections.deque = collections.deque(maxlen=80000)
    seenset: set = set()
    sweeps = 0
    errors = 0
    while True:
        t0 = time.time()
        sweeps += 1
        try:
            tr = get(DATA + "/trades?limit=1000")
        except Exception:
            # Swallowed with no counter, this is the silent-failure shape:
            # a tape that stops answering looks exactly like a quiet market.
            errors += 1
            tr = []
        cmap = state.get("intl_cond") or {}
        written = 0
        for x in (tr if isinstance(tr, list) else []):
            ident = cmap.get(x.get("conditionId"))
            if ident is None:
                continue
            k = (x.get("transactionHash"), x.get("timestamp"),
                 x.get("size"), x.get("price"), x.get("asset"))
            if k in seenset:
                continue
            seenset.add(k)
            seen.append(k)
            if len(seen) == seen.maxlen:
                seenset.discard(seen.popleft())
            write("intl_flow", {
                "venue": "INTL", "observed_t": iso(t0), "t": x.get("timestamp"),
                "slug": ident["slug"], "league": ident["league"],
                "event": ident["event"], "sports_type": ident["sports_type"],
                "condition": x.get("conditionId"), "asset": x.get("asset"),
                "price": x.get("price"), "size": x.get("size"),
                "side": x.get("side"), "outcome": x.get("outcome"),
                "wallet": x.get("proxyWallet"),
                "trade_provenance": "REAL_PRINT_DIRECTION_KNOWN"})
            written += 1
        # The intl_flow analogue of pmus_flow_selected. A FLAT rows_written on
        # this stream has two causes and they are not the same fact: the loop
        # ran and no tape trade fell in a watched condition, or the loop is
        # stuck. `sweeps` rising with `selected` 0 is the first; `sweeps`
        # frozen is the second. This ambiguity cost two hours of misdiagnosis
        # on pmus_flow (see analysis/l5/PMUS_FLOW_INCIDENT_20260921.md) and the
        # same blind spot was still open here.
        state["intl_flow_sweeps"] = sweeps
        state["intl_flow_selected"] = written
        state["intl_flow_conditions"] = len(cmap)
        state["intl_flow_tape"] = len(tr) if isinstance(tr, list) else 0
        state["intl_flow_fetch_errors"] = errors
        d = INTL_FLOW_CAD - (time.time() - t0)
        if d > 0:
            time.sleep(d)


# -------------------------------------------- prospective L5 full-L2 recorder
# Deployed HERE rather than as its own service because the durable volume, the
# supervisor and the 24/7 restart policy already exist on this container, and
# because nothing else it needs is anywhere else. It writes to its own
# subdirectory of ROOT and shares no file, no token set and no state key with
# the collector threads.
#
# THE ISOLATION PROPERTY OF THIS IMAGE IS UNCHANGED. l5_prospective_recorder
# imports the standard library ONLY (bisect, concurrent, datetime, json, os,
# ssl, threading, time, typing, urllib). No executor, session, signer,
# credential reader or order transport enters the image, because none is
# copied into it and none is importable from what is.
PROSPECTIVE_ON = os.getenv("L5_PROSPECTIVE", "1") not in ("0", "false", "")
PROSPECTIVE_CAD = float(os.getenv("L5_PROSPECTIVE_CAD", "3"))


def prospective_loop(state: dict) -> None:
    from src import l5_prospective_recorder as P

    root = os.path.join(ROOT, "prospective")
    rec = P.Recorder(root)
    drv = P.Driver(rec)
    drv.seed()
    log("prospective_seeded", root=root,
        tokens=len(rec.tokens), backlog_marked=rec.counts.get("seeded_backlog"),
        note="backlog marked seen, not emitted: the first emitted event is the "
             "first fill we were already polling the book for")
    state["prospective_counts"] = rec.counts
    while True:
        t0 = time.time()
        for e in drv.step(t0):
            _COUNTS["prospective_events"] += 1
            log("prospective_event", slug=e.get("slug"),
                outcome=e.get("outcome"), action=e.get("action"),
                price=e.get("price"), size=e.get("size"),
                maker=(None if e.get("is_taker") is None
                       else not e["is_taker"]),
                pre_book=bool(e.get("pre_fill_book")),
                trail=e.get("queue_trail_snapshots"),
                scorable_hint=(e.get("is_taker") is False
                               and bool(e.get("pre_fill_book"))),
                ahead_first=e.get("depth_ahead_at_first_look"),
                ahead_last=e.get("depth_ahead_at_last_look"),
                vanished=e.get("depth_ahead_disappeared"),
                gap_s=e.get("unobserved_interval_s"))
        state["prospective_tokens"] = len(rec.tokens)
        d = PROSPECTIVE_CAD - (time.time() - t0)
        if d > 0:
            time.sleep(d)


# ------------------------------------------------------------- discovery
def discovery_loop(state: dict) -> None:
    while True:
        try:
            sel, lm = pmus_discover()
            # Published together or not at all. Keeping the previous selection
            # alive against a NEW liveness map (or the reverse) is what let the
            # two disagree; they are now one observation of the slate.
            if sel:
                state["pmus"] = sel
                state["pmus_live"] = lm
            isel, cmap = intl_discover()
            if isel:
                state["intl"] = isel
            if cmap:
                state["intl_cond"] = cmap
            log("discovery",
                pmus_markets=len(state.get("pmus") or []),
                pmus_live_events=sum(1 for v in (state.get("pmus_live") or {})
                                     .values() if v[0]),
                pmus_diag_stratum=sum(1 for r in (state.get("pmus") or [])
                                      if r.get("stratum") ==
                                      "p10_25_diagnostic"),
                intl_markets=len(state.get("intl") or []),
                intl_conditions=len(state.get("intl_cond") or {}))
        except Exception as e:
            log("discovery_error", err=type(e).__name__, detail=str(e)[:200])
        time.sleep(REDISCOVER_S)


# ------------------------------------------------------------- supervisor
def supervise(name: str, fn, state: dict, stream_name: str) -> None:
    restarts = 0
    while True:
        try:
            fn(state)
        except Exception as e:
            restarts += 1
            gap(stream_name, "thread crashed and was restarted",
                thread=name, restarts=restarts, err=type(e).__name__)
            log("thread_restart", thread=name, restarts=restarts,
                err=type(e).__name__, tb=traceback.format_exc()[-400:])
            time.sleep(5.0)


def heartbeat_loop(state: dict, t_start: float) -> None:
    while True:
        time.sleep(HEARTBEAT_S)
        try:
            sizes = {}
            for n in ("pmus_books", "pmus_flow", "intl_books", "intl_flow"):
                p = os.path.join(ROOT, n + ".jsonl")
                sizes[n] = os.path.getsize(p) if os.path.exists(p) else 0
            for n in ("events", "raw_books", "raw_prints", "raw_fills"):
                p = os.path.join(ROOT, "prospective", n + ".jsonl")
                sizes["prospective_" + n] = (os.path.getsize(p)
                                             if os.path.exists(p) else 0)
            try:
                st = os.statvfs(ROOT)
                free_gb = round(st.f_bavail * st.f_frsize / 2 ** 30, 2)
            except (OSError, AttributeError):
                free_gb = None
            log("heartbeat",
                uptime_s=round(time.time() - t_start, 1),
                root=ROOT,
                rows_written=dict(_COUNTS),
                file_bytes=sizes,
                pmus_markets=len(state.get("pmus") or []),
                intl_markets=len(state.get("intl") or []),
                pmus_sweep_s=state.get("pmus_sweep_s"),
                intl_sweep_s=state.get("intl_sweep_s"),
                # A frozen rows_written means one of two different things.
                # These disambiguate them without reading discovery lines.
                pmus_flow_selected=state.get("pmus_flow_selected"),
                pmus_live_events=sum(1 for v in (state.get("pmus_live") or {})
                                     .values() if v[0]),
                # intl_flow's own version of the same disambiguation:
                # sweeps rising with selected 0 is a quiet slate; sweeps
                # frozen is a stuck loop.
                intl_flow_sweeps=state.get("intl_flow_sweeps"),
                intl_flow_selected=state.get("intl_flow_selected"),
                intl_flow_conditions=state.get("intl_flow_conditions"),
                intl_flow_tape=state.get("intl_flow_tape"),
                intl_flow_fetch_errors=state.get("intl_flow_fetch_errors"),
                volume_free_gb=free_gb,
                prospective_tokens=state.get("prospective_tokens"),
                prospective=dict(state.get("prospective_counts") or {}),
                labels=("READ-ONLY. Public GETs only. No credentials, no order "
                        "transport, no arming. Nothing here is submitted, "
                        "filled, settled or realized."))
        except Exception as e:
            log("heartbeat_error", err=type(e).__name__)


def main() -> int:
    os.makedirs(ROOT, exist_ok=True)
    global _NFL_PRE_BASELINE_SLUGS
    _NFL_PRE_BASELINE_SLUGS = load_nfl_pre_baseline()
    t_start = time.time()
    for n in ("pmus_books", "pmus_flow", "intl_books", "intl_flow",
              "prospective"):
        gap(n, "service start / restart", pid=os.getpid())
    # Every other _COUNTS key is created by the gap() writes above, under the
    # lock. This one is created by the first event, which could land while the
    # heartbeat is iterating -- "dictionary changed size during iteration",
    # swallowed into a heartbeat_error, at the one moment the heartbeat is most
    # worth reading. Create it up front instead.
    _COUNTS["prospective_events"] += 0
    # The 2026-09-21 flow gap is marked IN THE STREAM, not only in a report, so
    # an analysis that never reads analysis/ still cannot treat that window as
    # zero flow. Re-written on every start: these are append-only observations
    # and `incident_id` makes duplicates trivially collapsible.
    write("pmus_flow", {
        "_marker": "PRESERVED_INCIDENT",
        "incident_id": "PMUS_FLOW_LIVEMAP_COVERAGE_20260921",
        "t": time.time(),
        "window_start_utc": "2026-09-21T03:55:13Z",
        "window_end_utc": "2026-09-21T06:07:17Z",
        "duration_s": 7924,
        "deployment": "62a54463-b441-4f3a-a723-9545e6f368c9",
        "rows_written_frozen_at": 1220964,
        "file_bytes_frozen_at": 516596932,
        "cause": ("the liveness map was built from /v2/leagues (50 leagues) "
                  "while discovery sampled /v2/sports (266); the flow selector "
                  "reads an absent event as not-live, and every live event on "
                  "the venue was in an uncovered league"),
        "not_a_writer_freeze": True,
        "treatment": "CENSORED -- absence of observations, never zero flow",
        "report": "analysis/l5/PMUS_FLOW_INCIDENT_20260921.md"})
    log("start", root=ROOT, pid=os.getpid(),
        nfl_pre_baseline_slugs=len(_NFL_PRE_BASELINE_SLUGS),
        note="durable replacement for the recycling research container")
    state: dict = {"pmus": [], "pmus_live": {}, "intl": [], "intl_cond": {}}
    try:
        state["pmus"], state["pmus_live"] = pmus_discover()
        isel, cmap = intl_discover()
        state["intl"], state["intl_cond"] = isel, cmap
    except Exception as e:
        log("initial_discovery_error", err=type(e).__name__)
    threads = [
        ("discovery", discovery_loop, "pmus_books"),
        ("pmus_books", pmus_books_loop, "pmus_books"),
        ("pmus_flow", pmus_flow_loop, "pmus_flow"),
        ("intl_books", intl_books_loop, "intl_books"),
        ("intl_flow", intl_flow_loop, "intl_flow"),
    ]
    if PROSPECTIVE_ON:
        # Supervised like the rest: a read-only recorder self-recovers and runs
        # 24/7. It carries its own stream name so a crash of ITS loop cannot
        # write a gap marker into a collector stream it does not own.
        threads.append(("prospective", prospective_loop, "prospective"))
    for name, fn, sname in threads:
        threading.Thread(target=supervise, args=(name, fn, state, sname),
                         daemon=True).start()
    threading.Thread(target=heartbeat_loop, args=(state, t_start),
                     daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
