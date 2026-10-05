"""BTC5m RESEARCH RECORDER. Read-only, unauthenticated, continuous.

PRIMARY: BTC5m. BTC15m is captured on the SAME connection when it costs
nothing to do so -- it is the same stream shape and the same REST listing, and
each duration gets its own checkpoint grid and its own output rows, so a
15-minute window can never dilute or delay a 5-minute one. Any pressure and
BTC5m wins: the checkpoint tick scans 5m markets first, and --series-regex can
exclude 15m outright.

NO ORDER PATH EXISTS IN THIS FILE. There is no credential, no signer, no
mutation endpoint, and nothing imported from src.gemini_execution. It reads
public market data.

WHAT THIS ADDS over analysis/gemini/info_edge/ie_collector.py, which already
preserves raw depth diffs with U/u sequence ids and venue E timestamps, raw
periodic snapshots, raw trades, and window metadata. Exactly four things, and
nothing is transformed away to get them:

  1. THE LIVE SETTLEMENT REFERENCE. `{agency}:{index}@indexPrice` subscribed
     per contract from that contract's OWN sourceDetails, so the index this
     window actually settles against is the one recorded. Every message is
     persisted VERBATIM in `raw`; the parsed value/timestamp sit beside it and
     are best-effort. The stream's message schema could not be read from the
     research container (developer.gemini.com is egress-denied there), so
     parsing is defensive by design and the raw row is the authority.
  2. availableAt, explicitly, alongside expiry/effective -- and sourceDetails
     (agency + index) rather than only the deprecated flat `source`.
  3. CHECKPOINT SNAPSHOTS at 240/180/120/90/60/45/30/20/15/10/5/2/1 s
     remaining, carrying the FULL YES ladder (price AND size, every level) and
     the derived NO ladder, plus the reference value as of that instant.
  4. BOOK STATE -- TWO_SIDED / ONE_SIDED / EMPTY -- written explicitly, with
     quote age and event age wherever the venue's own timestamps permit.

THE NO LADDER IS DERIVED AND SAYS SO. The venue lists one instrument per
window and publishes the YES book. Buying NO is selling YES, so
    NO ask_i = 1 - YES bid_i   with the YES bid's size
    NO bid_i = 1 - YES ask_i   with the YES ask's size
The derived ladder is recorded as a convenience under `no_ladder_derived`; the
RAW YES ladder is recorded in full beside it, so nothing downstream has to
trust the derivation.

BOOK INTEGRITY IS REPORTED, NEVER REPAIRED SILENTLY. The local book is seeded
from a snapshot and advanced by diffs. A sequence gap sets `resync_pending`
and every checkpoint emitted while it is set carries
`book_integrity: "GAP"` -- a checkpoint is never fabricated from a book we
know is stale, and it is never dropped either, because "we could not see the
book" is itself the finding that matters in this lane.

Accounting label: public market-data recording. No order was sent.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

LABEL = "GEMINI_BTC5M_RESEARCH_RECORDER_V1"
GEMINI_REST = "https://api.gemini.com"
EVENTS = f"{GEMINI_REST}/v1/prediction-markets/events"
GEMINI_WS = "wss://ws.gemini.com"

#: TWO CHECKPOINT FAMILIES, because the research frontier is the window's
#: FIRST 180 SECONDS and that region cannot be addressed from expiry alone.
#:
#:   EARLY  anchored to T, the moment the window opens -- availableAt, which
#:          is when the venue captures the reference strike. T+X is the
#:          natural coordinate here: it is the age of the market.
#:   LATE   anchored to expiry, as before, because settlement is what the
#:          late region is about.
#:
#: They are chosen NOT to overlap. For BTC5m, whose tradeable life is 300s,
#: T+60/T+120/T+180 ARE tte 240/180/120, so those three are dropped from the
#: late grid rather than emitted twice under two names.
EARLY_OFFSETS: Dict[str, Tuple[float, ...]] = {
    "BTC05M": (5, 15, 30, 45, 60, 90, 120, 150, 180),
    "BTC15M": (5, 15, 30, 45, 60, 90, 120, 150, 180),
}
CHECKPOINTS: Dict[str, Tuple[float, ...]] = {
    "BTC05M": (90, 60, 45, 30, 20, 15, 10, 5, 2, 1),
    "BTC15M": (840, 720, 600, 480, 360, 300, 240, 180, 120, 90, 60, 45, 30,
               20, 15, 10, 5, 2, 1),
}
#: fallback only, when availableAt has not been published yet: the venue makes
#: the strike available at expiry - this, measured across BTC5m windows.
DEFAULT_LIFE_S: Dict[str, float] = {"BTC05M": 300.0, "BTC15M": 900.0}
#: a checkpoint fires when tte first falls at or below the target, and only if
#: it is still within this tolerance -- so a stalled tick cannot stamp a row
#: with a checkpoint it is seconds away from.
CHECKPOINT_TOL_S = 1.5
TICK_S = 0.2
META_POLL_S = 10.0
SETTLE_POLL_S = 60.0
SUBSCRIBE_LEAD_S = 420.0
RETAIN_AFTER_S = 180.0
WS_SESSION_S = 1800.0
CA = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get(
    "SSL_CERT_FILE") or True


def now_ns() -> int:
    return time.time_ns()


def proxy_url() -> Optional[str]:
    return (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
            or None)


def ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if isinstance(CA, str) and os.path.exists(CA):
        ctx.load_verify_locations(CA)
    return ctx


def parse_ts(s: Any) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:                                          # noqa: BLE001
        return None


class Sink:
    """One append-only JSONL file, flushed per row. Crash-tolerant."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.fh = path.open("a", encoding="utf-8")
        self.n = 0
        self.kinds: Dict[str, int] = {}

    def write(self, row: Dict[str, Any]) -> None:
        self.fh.write(json.dumps(row, separators=(",", ":"),
                                 default=str) + "\n")
        self.fh.flush()
        self.n += 1
        k = str(row.get("kind"))
        self.kinds[k] = self.kinds.get(k, 0) + 1

    def close(self) -> None:
        try:
            self.fh.close()
        except Exception:                                      # noqa: BLE001
            pass


# ---------------------------------------------------------------- the book
class Book:
    """A local YES book from snapshot + diffs, with integrity reported."""

    def __init__(self) -> None:
        self.bids: Dict[str, str] = {}
        self.asks: Dict[str, str] = {}
        self.last_update_id: Optional[int] = None
        self.resync_pending = True
        self.last_event_ns: Optional[int] = None
        self.last_recv_ns: Optional[int] = None
        self.gaps = 0

    def seed(self, bids, asks, last_update_id, recv_ns) -> None:
        self.bids = {str(p): str(q) for p, q in (bids or [])}
        self.asks = {str(p): str(q) for p, q in (asks or [])}
        self.last_update_id = last_update_id
        self.resync_pending = False
        self.last_recv_ns = recv_ns

    def apply(self, U, u, b, a, event_ns, recv_ns) -> None:
        if self.last_update_id is not None and U is not None:
            if int(U) > int(self.last_update_id) + 1:
                self.gaps += 1
                self.resync_pending = True
        for side, rows in ((self.bids, b or []), (self.asks, a or [])):
            for p, q in rows:
                if str(q) in ("0", "0.0", "0.00"):
                    side.pop(str(p), None)
                else:
                    side[str(p)] = str(q)
        if u is not None:
            self.last_update_id = int(u)
        self.last_event_ns = event_ns
        self.last_recv_ns = recv_ns

    def ladder(self) -> Dict[str, List[List[str]]]:
        bids = sorted(((float(p), q) for p, q in self.bids.items()),
                      key=lambda x: -x[0])
        asks = sorted(((float(p), q) for p, q in self.asks.items()),
                      key=lambda x: x[0])
        return {"bids": [[("%g" % p), q] for p, q in bids],
                "asks": [[("%g" % p), q] for p, q in asks]}

    def state(self) -> str:
        has_b, has_a = bool(self.bids), bool(self.asks)
        return ("TWO_SIDED" if (has_b and has_a)
                else ("ONE_SIDED" if (has_b or has_a) else "EMPTY"))


def derived_no_ladder(yes: Dict[str, List[List[str]]]
                      ) -> Dict[str, List[List[str]]]:
    """NO ask = 1 - YES bid (YES bid's size); NO bid = 1 - YES ask."""
    def flip(rows):
        out = []
        for p, q in rows:
            out.append([("%g" % (1.0 - float(p))), q])
        return out
    return {"asks": flip(yes["bids"]), "bids": flip(yes["asks"])}


# ------------------------------------------------------------------- tasks
async def meta_task(sink: Sink, stop: asyncio.Event,
                    known: Dict[str, Dict[str, Any]],
                    series_re: "re.Pattern") -> None:
    """Window metadata: strike, availableAt, sourceDetails, expiry."""
    import httpx
    while not stop.is_set():
        try:
            async with httpx.AsyncClient(timeout=25.0, proxy=proxy_url(),
                                         verify=CA) as http:
                r = await http.get(EVENTS, params=[("status", "active"),
                                                   ("category", "Crypto"),
                                                   ("limit", "500")])
                data = r.json().get("data", [])
            t = now_ns()
            for ev in data:
                tick = ev.get("ticker") or ""
                if not series_re.match(tick):
                    continue
                for c in ev.get("contracts", []):
                    sym = (c.get("instrumentSymbol") or "").upper()
                    if not sym:
                        continue
                    strike = c.get("strike") or {}
                    sd = c.get("sourceDetails") or {}
                    row = {
                        "t_ns": t, "kind": "window", "market": sym,
                        "ticker": tick,
                        "duration": tick[:6],
                        "strike": strike.get("value"),
                        # REQUIREMENT 2: availableAt, under its own name
                        "availableAt": strike.get("availableAt"),
                        "strike_type": strike.get("type"),
                        # REQUIREMENT 1 (identification half): the index this
                        # contract settles against, structured, not the flat
                        # deprecated string
                        "source_agency": sd.get("agency"),
                        "source_index": sd.get("index"),
                        "source_flat_deprecated": c.get("source"),
                        "expiry": c.get("expiryDate"),
                        "effective": c.get("effectiveDate"),
                        "start": ev.get("startTime"),
                        "status": ev.get("status"),
                        "marketState": c.get("marketState"),
                        "price_increment": c.get("priceIncrement"),
                        "qty_increment": c.get("quantityIncrement"),
                        "quantity_minimum": c.get("quantityMinimum"),
                        "price_minimum": c.get("priceMinimum"),
                        "prices": c.get("prices"),
                        "raw_contract": c,
                    }
                    exp = parse_ts(c.get("expiryDate"))
                    if sym not in known:
                        if exp:
                            av = parse_ts(strike.get("availableAt"))
                            dur = tick[:6]
                            known[sym] = {
                                "expiry_t": exp,
                                "ticker": tick, "duration": dur,
                                "availableAt": strike.get("availableAt"),
                                # T, the moment the window opens. availableAt
                                # when the venue has published it; otherwise
                                # expiry - the measured life, FLAGGED as such
                                # so no row silently claims a T it inferred.
                                "t0": av if av else (
                                    exp - DEFAULT_LIFE_S.get(dur, 300.0)),
                                "t0_source": ("availableAt" if av
                                              else "expiry_minus_life"),
                                "agency": sd.get("agency"),
                                "index": sd.get("index"),
                                "fired": set(),
                            }
                        sink.write(row)
                    else:
                        known[sym].update(
                            {k: v for k, v in
                             (("agency", sd.get("agency")),
                              ("index", sd.get("index")),
                              ("availableAt", strike.get("availableAt")))
                             if v})
                        # availableAt arrives LATER than the listing, so T is
                        # upgraded from the inferred value to the published
                        # one the moment the venue publishes it.
                        av = parse_ts(strike.get("availableAt"))
                        if av and known[sym].get("t0_source") != "availableAt":
                            known[sym]["t0"] = av
                            known[sym]["t0_source"] = "availableAt"
                        sink.write({**row, "kind": "window_poll"})
        except Exception as exc:                               # noqa: BLE001
            sink.write({"t_ns": now_ns(), "kind": "error", "where": "meta",
                        "error": type(exc).__name__, "detail": str(exc)[:200]})
        try:
            await asyncio.wait_for(stop.wait(), timeout=META_POLL_S)
        except asyncio.TimeoutError:
            pass


async def settlement_task(sink: Sink, stop: asyncio.Event,
                          series_re: "re.Pattern") -> None:
    """Ground truth. Settled events LEAVE the active listing, so this polls
    the recently-settled surface rather than hoping the active one yields."""
    import httpx
    seen: Set[str] = set()
    while not stop.is_set():
        try:
            async with httpx.AsyncClient(timeout=25.0, proxy=proxy_url(),
                                         verify=CA) as http:
                r = await http.get(f"{EVENTS}/recently-settled",
                                   params=[("limit", "200")])
                data = r.json().get("data", [])
            t = now_ns()
            for ev in data:
                tick = ev.get("ticker") or ""
                if not series_re.match(tick) or tick in seen:
                    continue
                seen.add(tick)
                sink.write({"t_ns": t, "kind": "settlement", "ticker": tick,
                            "duration": tick[:6],
                            "settlement": ev.get("settlement"),
                            "raw_event": ev})
        except Exception as exc:                               # noqa: BLE001
            sink.write({"t_ns": now_ns(), "kind": "error",
                        "where": "settlement",
                        "error": type(exc).__name__, "detail": str(exc)[:200]})
        try:
            await asyncio.wait_for(stop.wait(), timeout=SETTLE_POLL_S)
        except asyncio.TimeoutError:
            pass


async def index_task(sink: Sink, stop: asyncio.Event,
                     known: Dict[str, Dict[str, Any]],
                     latest: Dict[str, Dict[str, Any]]) -> None:
    """REQUIREMENT 1: the live settlement reference, per agency:index.

    Subscribes `{agency}:{index}@indexPrice` for every distinct pair the
    contracts themselves name. EVERY message is persisted verbatim; the parse
    is best-effort and is never allowed to discard the raw row.
    """
    import websockets
    backoff = 1.0
    while not stop.is_set():
        try:
            subscribed: Set[str] = set()
            deadline = time.monotonic() + WS_SESSION_S
            async with websockets.connect(GEMINI_WS, proxy=proxy_url(),
                                          ssl=ssl_ctx(), open_timeout=15,
                                          ping_interval=15, ping_timeout=10,
                                          max_queue=20000) as ws:
                sink.write({"t_ns": now_ns(), "kind": "index_open"})
                backoff = 1.0
                while not stop.is_set() and time.monotonic() < deadline:
                    want = {"%s:%s" % (m["agency"], m["index"])
                            for m in known.values()
                            if m.get("agency") and m.get("index")}
                    add = sorted(want - subscribed)
                    if add:
                        params = ["%s@indexPrice" % k for k in add]
                        await ws.send(json.dumps({"id": "idx",
                                                  "method": "SUBSCRIBE",
                                                  "params": params}))
                        subscribed.update(add)
                        sink.write({"t_ns": now_ns(), "kind": "index_subscribe",
                                    "params": params})
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    t = now_ns()
                    try:
                        m = json.loads(raw)
                    except Exception:                          # noqa: BLE001
                        sink.write({"t_ns": t, "kind": "index_unparsed",
                                    "raw_text": str(raw)[:2000]})
                        continue
                    if isinstance(m, dict) and ("status" in m or "result" in m
                                                or "error" in m):
                        sink.write({"t_ns": t, "kind": "index_ctrl", "raw": m})
                        continue
                    # PRESERVE RAW. The parse below is a convenience and the
                    # field names are not confirmed from documentation.
                    val = None
                    ev_ns = None
                    key = None
                    if isinstance(m, dict):
                        for k in ("p", "price", "value", "indexPrice"):
                            if m.get(k) is not None:
                                val = m[k]
                                break
                        for k in ("E", "eventTime", "timestamp", "t", "time"):
                            if m.get(k) is not None:
                                ev_ns = m[k]
                                break
                        for k in ("index", "s", "symbol", "stream", "channel"):
                            if m.get(k) is not None:
                                key = m[k]
                                break
                    row = {"t_ns": t, "kind": "index_price", "raw": m,
                           "parsed_value": val, "parsed_event_time": ev_ns,
                           "parsed_key": key,
                           "parse_note": ("field names are best-effort; the "
                                          "raw message is the authority")}
                    sink.write(row)
                    if val is not None:
                        latest["ref"] = {"value": val, "event_time": ev_ns,
                                         "recv_ns": t, "key": key}
        except Exception as exc:                               # noqa: BLE001
            sink.write({"t_ns": now_ns(), "kind": "error", "where": "index",
                        "error": type(exc).__name__, "detail": str(exc)[:200]})
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)


async def book_task(sink: Sink, stop: asyncio.Event,
                    known: Dict[str, Dict[str, Any]],
                    books: Dict[str, Book]) -> None:
    """Raw depth/trade/snapshot streams, preserved, and the local book."""
    import websockets
    backoff = 1.0
    while not stop.is_set():
        try:
            subscribed: Set[str] = set()
            deadline = time.monotonic() + WS_SESSION_S
            async with websockets.connect(GEMINI_WS, proxy=proxy_url(),
                                          ssl=ssl_ctx(), open_timeout=15,
                                          ping_interval=15, ping_timeout=10,
                                          max_queue=20000) as ws:
                sink.write({"t_ns": now_ns(), "kind": "book_open"})
                backoff = 1.0
                while not stop.is_set() and time.monotonic() < deadline:
                    tnow = time.time()
                    add = [s for s, m in list(known.items())
                           if s not in subscribed
                           and (m["expiry_t"] - SUBSCRIBE_LEAD_S) <= tnow
                           <= (m["expiry_t"] + RETAIN_AFTER_S)]
                    drop = [s for s in list(subscribed)
                            if tnow > known.get(s, {}).get("expiry_t", 0)
                            + RETAIN_AFTER_S]
                    for group, method in ((add, "SUBSCRIBE"),
                                          (drop, "UNSUBSCRIBE")):
                        if not group:
                            continue
                        params: List[str] = []
                        for s in group:
                            params += ["%s@depth" % s, "%s@trade" % s,
                                       "%s@depth20" % s]
                        await ws.send(json.dumps({"id": "bk",
                                                  "method": method,
                                                  "params": params}))
                        if method == "SUBSCRIBE":
                            subscribed.update(group)
                            for s in group:
                                books.setdefault(s, Book())
                        else:
                            for s in group:
                                subscribed.discard(s)
                        sink.write({"t_ns": now_ns(),
                                    "kind": method.lower(),
                                    "markets": group})
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    t = now_ns()
                    try:
                        m = json.loads(raw)
                    except Exception:                          # noqa: BLE001
                        continue
                    if not isinstance(m, dict):
                        continue
                    if "status" in m or "result" in m or "error" in m:
                        sink.write({"t_ns": t, "kind": "book_ctrl", "raw": m})
                        continue
                    if m.get("e") == "depthUpdate":
                        sym = (m.get("s") or "").upper()
                        sink.write({"t_ns": t, "kind": "p_depth",
                                    "market": sym, "E_ns": m.get("E"),
                                    "U": m.get("U"), "u": m.get("u"),
                                    "b": m.get("b"), "a": m.get("a")})
                        bk = books.setdefault(sym, Book())
                        bk.apply(m.get("U"), m.get("u"), m.get("b"),
                                 m.get("a"), m.get("E"), t)
                    elif "lastUpdateId" in m:
                        sym = (m.get("symbol") or m.get("s") or "").upper()
                        sink.write({"t_ns": t, "kind": "p_snapshot",
                                    "market": sym,
                                    "lastUpdateId": m.get("lastUpdateId"),
                                    "bids": m.get("bids"),
                                    "asks": m.get("asks")})
                        books.setdefault(sym, Book()).seed(
                            m.get("bids"), m.get("asks"),
                            m.get("lastUpdateId"), t)
                    elif "p" in m and "q" in m:
                        sink.write({"t_ns": t, "kind": "p_trade",
                                    "market": (m.get("s") or "").upper(),
                                    "E_ns": m.get("E"), "trade_id": m.get("t"),
                                    "price": m.get("p"), "qty": m.get("q"),
                                    "buyer_is_maker": m.get("m")})
                    else:
                        sink.write({"t_ns": t, "kind": "p_other", "raw": m})
        except Exception as exc:                               # noqa: BLE001
            sink.write({"t_ns": now_ns(), "kind": "error", "where": "book",
                        "error": type(exc).__name__, "detail": str(exc)[:200]})
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)


async def checkpoint_task(sink: Sink, stop: asyncio.Event,
                          known: Dict[str, Dict[str, Any]],
                          books: Dict[str, Book],
                          latest: Dict[str, Dict[str, Any]]) -> None:
    """REQUIREMENTS 3 and 4, on a 5 Hz tick, over BOTH families.

    EARLY fires at T+X, where T is availableAt (or the flagged fallback), so
    the first 180 seconds are addressed in the window's own coordinate. LATE
    fires at tte=X from expiry. BTC5m is served first on every tick.
    """
    while not stop.is_set():
        t = now_ns()
        tnow = t / 1e9
        items = sorted(known.items(),
                       key=lambda kv: 0 if kv[1].get("duration") == "BTC05M"
                       else 1)
        for sym, meta in items:
            dur = meta.get("duration") or ""
            tte = meta["expiry_t"] - tnow
            t0 = meta.get("t0")
            age = None if t0 is None else tnow - t0
            due: List[Tuple[str, str, float]] = []
            # EARLY: the market's age has just reached T+X
            if age is not None:
                for off in EARLY_OFFSETS.get(dur, ()):
                    key = "E%g" % off
                    if key in meta["fired"]:
                        continue
                    if age >= off and (age - off) <= CHECKPOINT_TOL_S:
                        due.append((key, "EARLY_FROM_T0", off))
            # LATE: tte has just fallen to X
            for target in CHECKPOINTS.get(dur, ()):
                key = "L%g" % target
                if key in meta["fired"]:
                    continue
                if tte <= target and (target - tte) <= CHECKPOINT_TOL_S:
                    due.append((key, "LATE_FROM_EXPIRY", target))
            for key, kind, target in due:
                meta["fired"].add(key)
                bk = books.get(sym)
                yes = bk.ladder() if bk else {"bids": [], "asks": []}
                ref = latest.get("ref") or {}
                sink.write({
                    "t_ns": t, "kind": "checkpoint",
                    "market": sym, "ticker": meta.get("ticker"),
                    "duration": dur,
                    # the label says which coordinate this row lives in, so
                    # nothing downstream has to guess
                    "checkpoint_label": ("T+%gs" % target
                                         if kind == "EARLY_FROM_T0"
                                         else "tte%gs" % target),
                    "checkpoint_family": kind,
                    "checkpoint_s": target,
                    "tte_s_actual": round(tte, 3),
                    "elapsed_since_t0_s": (None if age is None
                                           else round(age, 3)),
                    "t0_epoch": t0,
                    "t0_source": meta.get("t0_source"),
                    "expiry_epoch": meta["expiry_t"],
                    "availableAt": meta.get("availableAt"),
                    # REQUIREMENT 4
                    "book_state": bk.state() if bk else "EMPTY",
                    "book_integrity": ("GAP" if (bk and bk.resync_pending)
                                       else ("OK" if bk else "NO_BOOK")),
                    "book_gaps_so_far": (bk.gaps if bk else None),
                    "quote_age_s": (
                        None if not (bk and bk.last_recv_ns)
                        else round((t - bk.last_recv_ns) / 1e9, 4)),
                    "event_age_s": _event_age_s(bk, t),
                    "event_age_note": ("venue E is ms on some streams and ns "
                                       "on others; the magnitude test picks "
                                       "the unit and the raw E is preserved "
                                       "on every p_depth row"),
                    # REQUIREMENT 3: full ladders, price AND size
                    "yes_ladder": yes,
                    "yes_levels": {"bids": len(yes["bids"]),
                                   "asks": len(yes["asks"])},
                    "no_ladder_derived": derived_no_ladder(yes),
                    "no_ladder_note": ("DERIVED: NO ask = 1 - YES bid at the "
                                       "YES bid's size. The raw YES ladder "
                                       "above is the authority."),
                    "best_yes_bid": (yes["bids"][0][0] if yes["bids"]
                                     else None),
                    "best_yes_ask": (yes["asks"][0][0] if yes["asks"]
                                     else None),
                    "reference": {
                        "value": ref.get("value"),
                        "event_time": ref.get("event_time"),
                        "age_s": (None if not ref.get("recv_ns")
                                  else round((t - ref["recv_ns"]) / 1e9, 4)),
                        "key": ref.get("key"),
                        "agency": meta.get("agency"),
                        "index": meta.get("index"),
                    },
                    "provenance": {
                        "recorder": LABEL,
                        "book_source": ("%s@depth20 seed + %s@depth diffs"
                                        % (sym, sym)),
                        "reference_source": ("%s:%s@indexPrice"
                                             % (meta.get("agency"),
                                                meta.get("index"))),
                        "metadata_source": EVENTS,
                    },
                })
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_S)
        except asyncio.TimeoutError:
            pass


def _event_age_s(bk: Optional[Book], t: int) -> Optional[float]:
    """Venue event age, with the ms/ns unit DECIDED by magnitude.

    A venue E in milliseconds is ~1e12; in nanoseconds ~1e18. Guessing wrong
    turns a 40 ms age into 40 000 s, so the unit is inferred per value and the
    raw E stays on every p_depth row regardless.
    """
    if not (bk and bk.last_event_ns):
        return None
    e = int(bk.last_event_ns)
    e_ns = e * 1_000_000 if e < 10 ** 15 else e
    return round((t - e_ns) / 1e9, 4)


# -------------------------------------------------------------- validation
#: WINDOW-AWARE. The old validator proved rows of each kind existed somewhere
#: in the file, which is not the question. The question is whether the FIRST
#: COMPLETED BTC5m WINDOW actually contains early two-sided books with sizes
#: and a live index stream -- because that is the region the research needs
#: and it has never once been observed.
REQUIRED_FOR_VALIDATION = {
    "a_completed_btc5m_window": (
        "one BTC5m window observed from an EARLY checkpoint through a LATE "
        "one, so the whole tradeable life is covered"),
    "early_checkpoints_present": "checkpoint rows in the T+X family",
    "early_two_sided_book": (
        "at least one EARLY checkpoint whose book_state is TWO_SIDED -- the "
        "entire premise of moving the frontier to the first 180 seconds"),
    "early_ladder_has_sizes": (
        "an EARLY checkpoint carrying a priced level WITH a size on BOTH "
        "sides"),
    "index_reference_stream": "at least one index_price row",
    "index_reference_at_a_checkpoint": (
        "a checkpoint whose reference.value is populated, i.e. the stream and "
        "the checkpoint actually met"),
    "venue_timestamps": "a p_depth row carrying a venue E",
    "availableAt_seen": "a window row carrying availableAt",
    "t0_from_availableAt": (
        "an EARLY checkpoint whose t0_source is availableAt, not the inferred "
        "fallback -- otherwise T+X is our arithmetic rather than the venue's"),
}


def validate(path: Path) -> Dict[str, Any]:
    """Prove the requirements are PERSISTED, per window, from the file."""
    got = {k: False for k in REQUIRED_FOR_VALIDATION}
    kinds: Dict[str, int] = {}
    examples: Dict[str, Any] = {}
    per_window: Dict[str, Dict[str, Any]] = {}
    states_early: Dict[str, int] = {}
    states_late: Dict[str, int] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            k = str(r.get("kind"))
            kinds[k] = kinds.get(k, 0) + 1
            if k == "index_price":
                got["index_reference_stream"] = True
                examples.setdefault("index_price", r)
            elif k == "p_depth" and r.get("E_ns") is not None:
                got["venue_timestamps"] = True
            elif k == "window" and r.get("availableAt"):
                got["availableAt_seen"] = True
            elif k == "checkpoint":
                mk = str(r.get("market"))
                w = per_window.setdefault(
                    mk, {"duration": r.get("duration"), "early": 0,
                         "late": 0, "early_two_sided": 0,
                         "early_with_sizes": 0, "ref_at_checkpoint": 0,
                         "t0_from_availableAt": 0, "labels": []})
                early = r.get("checkpoint_family") == "EARLY_FROM_T0"
                w["labels"].append(r.get("checkpoint_label"))
                st = r.get("book_state")
                if early:
                    w["early"] += 1
                    got["early_checkpoints_present"] = True
                    states_early[st] = states_early.get(st, 0) + 1
                    if st == "TWO_SIDED":
                        w["early_two_sided"] += 1
                    if r.get("t0_source") == "availableAt":
                        w["t0_from_availableAt"] += 1
                    yl = r.get("yes_ladder") or {}
                    sized = {}
                    for side in ("bids", "asks"):
                        sized[side] = any(
                            len(l) >= 2 and l[1] not in (None, "")
                            for l in (yl.get(side) or []))
                    if sized["bids"] and sized["asks"]:
                        w["early_with_sizes"] += 1
                        examples.setdefault("early_two_sided_ladder", {
                            "market": mk,
                            "label": r.get("checkpoint_label"),
                            "book_state": st, "yes_ladder": yl,
                            "quote_age_s": r.get("quote_age_s"),
                            "reference": r.get("reference")})
                else:
                    w["late"] += 1
                    states_late[st] = states_late.get(st, 0) + 1
                if (r.get("reference") or {}).get("value") is not None:
                    w["ref_at_checkpoint"] += 1
    btc5 = {m: w for m, w in per_window.items()
            if (w.get("duration") or "") == "BTC05M"}
    completed = {m: w for m, w in btc5.items()
                 if w["early"] > 0 and w["late"] > 0}
    if completed:
        got["a_completed_btc5m_window"] = True
        if any(w["early_two_sided"] > 0 for w in completed.values()):
            got["early_two_sided_book"] = True
        if any(w["early_with_sizes"] > 0 for w in completed.values()):
            got["early_ladder_has_sizes"] = True
        if any(w["ref_at_checkpoint"] > 0 for w in completed.values()):
            got["index_reference_at_a_checkpoint"] = True
        if any(w["t0_from_availableAt"] > 0 for w in completed.values()):
            got["t0_from_availableAt"] = True
    return {
        "path": str(path), "rows": sum(kinds.values()), "kinds": kinds,
        "checks": got,
        "missing": sorted(k for k, v in got.items() if not v),
        "pass": all(got.values()),
        "requirements": REQUIRED_FOR_VALIDATION,
        "btc5m_windows_seen": len(btc5),
        "btc5m_windows_completed_early_and_late": len(completed),
        "early_book_states": states_early,
        "late_book_states": states_late,
        "per_window": per_window,
        "examples": examples,
        "reading": ("the decisive line is early_book_states: if TWO_SIDED is "
                    "absent there, the first 180 seconds are NOT two-sided "
                    "and the frontier moves again -- which would itself be "
                    "the finding, not a recorder fault"),
    }


async def main_async(args) -> int:
    series_re = re.compile(args.series_regex)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sink = Sink(Path(args.outdir) / ("btc5m_rec_%s.jsonl" % stamp))
    sink.write({"t_ns": now_ns(), "kind": "recorder_start", "label": LABEL,
                "series_regex": args.series_regex,
                "checkpoints": {k: list(v) for k, v in CHECKPOINTS.items()},
                "primary": "BTC05M",
                "duration_s": args.duration_s,
                "no_order_path": True})
    known: Dict[str, Dict[str, Any]] = {}
    books: Dict[str, Book] = {}
    latest: Dict[str, Dict[str, Any]] = {}
    stop = asyncio.Event()

    def _sig(*_a):
        stop.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(s, _sig)
        except (NotImplementedError, RuntimeError):             # pragma: no cover
            pass
    tasks = [asyncio.create_task(meta_task(sink, stop, known, series_re)),
             asyncio.create_task(settlement_task(sink, stop, series_re)),
             asyncio.create_task(index_task(sink, stop, known, latest)),
             asyncio.create_task(book_task(sink, stop, known, books)),
             asyncio.create_task(checkpoint_task(sink, stop, known, books,
                                                 latest))]
    if args.duration_s > 0:
        try:
            await asyncio.wait_for(stop.wait(), timeout=args.duration_s)
        except asyncio.TimeoutError:
            stop.set()
    else:
        await stop.wait()
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    sink.write({"t_ns": now_ns(), "kind": "recorder_stop",
                "rows": sink.n, "kinds": sink.kinds})
    sink.close()
    print(json.dumps({"label": LABEL, "path": str(sink.path),
                      "rows": sink.n, "kinds": sink.kinds}, sort_keys=True))
    if args.validate_out:
        v = validate(sink.path)
        Path(args.validate_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.validate_out).write_text(
            json.dumps(v, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8")
        print(json.dumps({"validation_pass": v["pass"],
                          "missing": v["missing"],
                          "kinds": v["kinds"]}, sort_keys=True))
        return 0 if v["pass"] else 2
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="data/gemini_btc5m_research")
    ap.add_argument("--series-regex", default=r"^BTC(05|15)M\d{10}$")
    ap.add_argument("--duration-s", type=float, default=0.0)
    ap.add_argument("--validate-out", default=None)
    ap.add_argument("--validate-only", default=None,
                    help="validate an existing jsonl and exit")
    a = ap.parse_args(argv)
    if a.validate_only:
        v = validate(Path(a.validate_only))
        print(json.dumps(v, indent=2, sort_keys=True, default=str))
        return 0 if v["pass"] else 2
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    raise SystemExit(main())
