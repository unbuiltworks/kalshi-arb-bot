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

#: BTC5m is primary and listed first so the tick serves it first.
CHECKPOINTS: Dict[str, Tuple[float, ...]] = {
    "BTC05M": (240, 180, 120, 90, 60, 45, 30, 20, 15, 10, 5, 2, 1),
    "BTC15M": (840, 720, 600, 480, 360, 300, 240, 180, 120, 90, 60, 45, 30,
               20, 15, 10, 5, 2, 1),
}
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
                            known[sym] = {
                                "expiry_t": exp,
                                "ticker": tick, "duration": tick[:6],
                                "availableAt": strike.get("availableAt"),
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
    """REQUIREMENTS 3 and 4, on a 5 Hz tick. BTC5m is served first."""
    while not stop.is_set():
        t = now_ns()
        tnow = t / 1e9
        items = sorted(known.items(),
                       key=lambda kv: 0 if kv[1].get("duration") == "BTC05M"
                       else 1)
        for sym, meta in items:
            grid = CHECKPOINTS.get(meta.get("duration") or "", ())
            if not grid:
                continue
            tte = meta["expiry_t"] - tnow
            for target in grid:
                if target in meta["fired"]:
                    continue
                if tte <= target and (target - tte) <= CHECKPOINT_TOL_S:
                    meta["fired"].add(target)
                    bk = books.get(sym)
                    yes = bk.ladder() if bk else {"bids": [], "asks": []}
                    ref = latest.get("ref") or {}
                    sink.write({
                        "t_ns": t, "kind": "checkpoint",
                        "market": sym, "ticker": meta.get("ticker"),
                        "duration": meta.get("duration"),
                        "checkpoint_s": target,
                        "tte_s_actual": round(tte, 3),
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
                        "event_age_s": (
                            None if not (bk and bk.last_event_ns)
                            else round((t - int(bk.last_event_ns) * 1_000_000)
                                       / 1e9, 4)
                            if int(bk.last_event_ns) < 10 ** 15
                            else round((t - int(bk.last_event_ns)) / 1e9, 4)),
                        "event_age_note": ("venue E is ms on some streams and "
                                           "ns on others; the magnitude test "
                                           "picks the unit and the raw E is "
                                           "preserved on every p_depth row"),
                        # REQUIREMENT 3: full ladders, price AND size
                        "yes_ladder": yes,
                        "yes_levels": {"bids": len(yes["bids"]),
                                       "asks": len(yes["asks"])},
                        "no_ladder_derived": derived_no_ladder(yes),
                        "no_ladder_note": ("DERIVED: NO ask = 1 - YES bid at "
                                           "the YES bid's size. The raw YES "
                                           "ladder above is the authority."),
                        "best_yes_bid": (yes["bids"][0][0] if yes["bids"]
                                         else None),
                        "best_yes_ask": (yes["asks"][0][0] if yes["asks"]
                                         else None),
                        "reference": {
                            "value": ref.get("value"),
                            "event_time": ref.get("event_time"),
                            "age_s": (None if not ref.get("recv_ns")
                                      else round((t - ref["recv_ns"]) / 1e9,
                                                 4)),
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
                    break
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_S)
        except asyncio.TimeoutError:
            pass


# -------------------------------------------------------------- validation
REQUIRED_FOR_VALIDATION = {
    "book_with_sizes": "a checkpoint or snapshot carrying a priced level WITH a size",
    "index_reference": "at least one index_price row",
    "venue_timestamps": "a p_depth row carrying a venue E",
    "checkpoint_rows": "at least one checkpoint row",
    "book_state_labelled": "a checkpoint whose book_state is set",
    "availableAt_seen": "a window row carrying availableAt",
}


def validate(path: Path) -> Dict[str, Any]:
    """Prove the four requirements are actually PERSISTED, from the file."""
    got = {k: False for k in REQUIRED_FOR_VALIDATION}
    kinds: Dict[str, int] = {}
    examples: Dict[str, Any] = {}
    states: Dict[str, int] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            k = str(r.get("kind"))
            kinds[k] = kinds.get(k, 0) + 1
            if k == "index_price":
                got["index_reference"] = True
                examples.setdefault("index_price", r)
            if k == "p_depth" and r.get("E_ns") is not None:
                got["venue_timestamps"] = True
            if k == "window" and r.get("availableAt"):
                got["availableAt_seen"] = True
            if k == "checkpoint":
                got["checkpoint_rows"] = True
                if r.get("book_state"):
                    got["book_state_labelled"] = True
                    states[r["book_state"]] = states.get(r["book_state"],
                                                         0) + 1
                yl = (r.get("yes_ladder") or {})
                for side in ("bids", "asks"):
                    for lvl in (yl.get(side) or []):
                        if len(lvl) >= 2 and lvl[1] not in (None, ""):
                            got["book_with_sizes"] = True
                            examples.setdefault("level", lvl)
                examples.setdefault("checkpoint", {
                    kk: r.get(kk) for kk in
                    ("market", "checkpoint_s", "book_state", "quote_age_s",
                     "yes_levels", "book_integrity", "reference")})
            if k == "p_snapshot" and (r.get("bids") or r.get("asks")):
                got["book_with_sizes"] = True
    return {"path": str(path), "rows": sum(kinds.values()), "kinds": kinds,
            "checks": got, "book_states": states,
            "missing": sorted(k for k, v in got.items() if not v),
            "pass": all(got.values()),
            "requirements": REQUIRED_FOR_VALIDATION, "examples": examples}


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
