"""Read-only PMUS book tape collector, for ADAPTED execution-sweep input.

OWNER 2026-09-17. The ADAPTED sweep needs real book state: what the touch was,
how fast it moved, whether a proposed maker price would have crossed, and
whether a complement stayed priceable. The V1/V1.1 shadows already record that
-- ~296k rows -- but onto a Railway volume this container cannot reach, and the
export routes all cost either a redeploy or a credential. PMUS's book endpoints
are PUBLIC, so this collects the same state directly and perturbs nothing.

WHAT THIS IS NOT. It records the BOOK, never a fill. Nothing here may be called
FILLED. The sweep labels outcomes EXECUTABLE or RESTABLE from this tape, and
calibrates actual fill probability only against real prints and our own live
fills. A tape that let us assume fills would be worse than no tape, because it
would make every candidate look good.

READ-ONLY AND UNAUTHENTICATED BY CONSTRUCTION: public gateway endpoints only,
no account state, no credentials, no orders. It cannot touch the live executor.
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

GATEWAY = "https://gateway.polymarket.us"
LIVE_EVENTS = ("/v1/events?live=true&active=true&closed=false"
               "&categories=sports&limit=500")

#: THE ONE SPORTS ASSUMPTION IN THIS FILE, AND ITS ESCAPE HATCH.
#:
#: `LIVE_EVENTS` pins `categories=sports`, so the tape could only ever see the
#: sports slate. PMUS launched recurring BTC up/down series on 2026-09-22
#: (`btc-updown-15m`, `btc-updown-1h`) with the same book endpoints, and those
#: markets are invisible to sports discovery. They are also SHORT: a 15-minute
#: market's whole life is shorter than this collector's 300 s re-slate, so a
#: series universe additionally has to roll on its own clock.
#:
#: Nothing else in the file is sports-specific -- `snapshot()` is already
#: venue-generic -- so the admission is a second slug source, not a second
#: collector.
SERIES_RESLATE_S = 45.0

#: A slug that has just left the universe is POLLED ON, not dropped, until a
#: terminal row for it is on the tape or this many seconds have passed.
#:
#: WHY. `series_slugs()` excludes `closed` events, so a window leaves the
#: universe at the first reslate after its boundary -- 0 to SERIES_RESLATE_S
#: seconds later, at random. The venue publishes the terminal bbo state some
#: seconds AFTER the boundary (6s to 30s+ observed). Whichever lands first
#: decides whether the close is ever recorded, and the preregistered
#: completeness rule excludes any window whose last tape state is still OPEN --
#: so a lost race silently destroys a window of evidence. Measured 2026-09-27:
#: 2115z's terminal row arrived at 21:30:30 and its reslate at 21:30:35, 5s to
#: spare; 2130z's reslate came at 21:45:19 and the close was never seen. Five of
#: sixteen windows were lost this way, including three consecutively.
DRAIN_S = 240.0
#: a slug re-emits at least this often even when nothing changed, so a gap in
#: the tape distinguishes lost observation from a quiet book.
HEARTBEAT_S = 30.0

#: how far ahead of its start a window is worth polling
LEAD_S = 60.0
UA = "pmus-book-tape (read-only research collector)"


def _get(path: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(GATEWAY + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, context=ssl.create_default_context(),
                                timeout=timeout) as r:
        return json.loads(r.read())


def live_slugs(cap: int = 60) -> list:
    """Sports markets the venue currently serves as live. The sweep is about
    the regime we actually trade, so the universe is the live slate rather
    than a hand-picked list."""
    out = []
    try:
        ev = _get(LIVE_EVENTS)
    except Exception:                                    # noqa: BLE001
        return out
    for e in (ev.get("events") or []):
        for m in (e.get("markets") or []):
            s = m.get("slug")
            if s:
                out.append(str(s))
        if len(out) >= cap:
            break
    return out[:cap]


def series_slugs(series_slug: str, *, get=None, now=None) -> list:
    """Every market of a recurring series that is open or about to open.

    A 15-minute window is created well in advance and expires on a hard clock,
    so "the one trading right now" is a property of the wall clock, not of a
    `live=true` flag the venue only sets for sports. Markets that have already
    closed are excluded; the current window and the next two are kept, so a
    rollover is never missed between re-slates.
    """
    get = _get if get is None else get
    now = datetime.now(timezone.utc) if now is None else now
    try:
        sid = None
        off = 0
        while off < 2000 and sid is None:
            d = get("/v1/series?limit=100&offset=%d" % off) or {}
            rows = d.get("series") or []
            if not rows:
                break
            for r in rows:
                if str(r.get("slug")) == series_slug:
                    sid = str(r.get("id"))
                    break
            off += len(rows)
        if sid is None:
            return []
        evs, off = [], 0
        while off < 4000:
            d = get("/v1/events?series_id=%s&limit=100&offset=%d" % (sid, off)) or {}
            rows = d.get("events") or []
            if not rows:
                break
            evs += rows
            off += len(rows)
            if len(rows) < 100:
                break
    except Exception:                                    # noqa: BLE001
        return []
    live = []
    for e in evs:
        if e.get("closed"):
            continue
        try:
            start = datetime.fromisoformat(
                str(e.get("startTime")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        live.append((start, e))
    live.sort(key=lambda r: r[0])
    # ONLY WHAT IS ACTUALLY TRADING. The venue pre-creates windows hours
    # ahead, and their book/bbo endpoints 404 until the window opens. Polling
    # them spends two thirds of the call budget on 404s and fills the tape
    # with errors that are not errors, so the universe is bounded to the
    # window in progress plus the one about to start.
    out = []
    for start, e in live:
        if start > now + timedelta(seconds=LEAD_S):
            break
        for m in (e.get("markets") or []):
            if m.get("slug"):
                out.append(str(m["slug"]))
    return out


def book_snapshot(slug: str) -> Optional[dict]:
    """The full ladder, for depth. Separate from `snapshot` because the BBO
    endpoint answers the liveness question on its own and costs one call; a
    depth question is worth a second call only when it is actually asked."""
    try:
        bk = (_get("/v1/markets/%s/book" % slug) or {}).get("marketData") or {}
    except Exception as exc:                             # noqa: BLE001
        return {"t": datetime.now(timezone.utc).isoformat(), "slug": slug,
                "kind": "book_error", "error": "%s:%s" % (
                    type(exc).__name__, str(exc)[:80])}

    def side(rows):
        out = []
        for r in rows or []:
            try:
                out.append([str(r["px"]["value"]), str(r["qty"])])
            except (KeyError, TypeError):
                continue
        return out

    return {
        "t": datetime.now(timezone.utc).isoformat(),
        "slug": slug, "kind": "book",
        "bids": side(bk.get("bids")),
        "offers": side(bk.get("offers")),
        "state": bk.get("state"),
        "transact_time": bk.get("transactTime"),
        "labels": "BOOK STATE ONLY -- never a fill, never realized",
    }


def snapshot(slug: str) -> Optional[dict]:
    """One book observation. `offers: []` is recorded as an empty side, not as
    a missing one -- a one-sided book is exactly the condition that stranded
    the 2026-09-17 residual, so it has to survive into the tape."""
    try:
        bbo = (_get("/v1/markets/%s/bbo" % slug) or {}).get("marketData") or {}
    except Exception as exc:                             # noqa: BLE001
        return {"t": datetime.now(timezone.utc).isoformat(), "slug": slug,
                "kind": "error", "error": "%s:%s" % (type(exc).__name__,
                                                     str(exc)[:80])}
    bid = (bbo.get("bestBid") or {}).get("value") if bbo.get("bestBid") else None
    ask = (bbo.get("bestAsk") or {}).get("value") if bbo.get("bestAsk") else None
    return {
        "t": datetime.now(timezone.utc).isoformat(),
        "slug": slug, "kind": "bbo",
        "bid": bid, "ask": ask,
        "bid_depth": bbo.get("bidDepth"), "ask_depth": bbo.get("askDepth"),
        "bid_shares": bbo.get("bidShares"), "ask_shares": bbo.get("askShares"),
        "last": (bbo.get("lastTradePx") or {}).get("value")
        if bbo.get("lastTradePx") else None,
        "shares_traded": bbo.get("sharesTraded"),
        "state": bbo.get("state"),
        "one_sided": (bid is None) != (ask is None),
        "two_sided": bool(bid) and bool(ask),
        "labels": "BOOK STATE ONLY -- never a fill, never realized",
    }


#: hard ceiling on a one-time dump, so a mistyped range cannot flood the logs
DUMP_MAX_ROWS = 20000


def dump_existing(out_dir: Path, since: str = "", until: str = "",
                  max_rows: int = DUMP_MAX_ROWS,
                  slug_contains: str = "") -> dict:
    """Print a BOUNDED slice of the tape ALREADY on disk, then return.

    WHY THIS EXISTS. This module's own header records the problem: the dense
    tape lands "onto a Railway volume this container cannot reach, and the
    export routes all cost either a redeploy or a credential." Measured on
    2026-10-25, storage runs at one row every 1.09 s (2,191,674 rows) while the
    stdout echo is deduped to a ~30 s heartbeat, and 80 of 81 consecutive
    echoed snapshots differ -- so the echo is lossy and the analysis needs the
    file.

    This reads the file and writes to stdout. It opens no socket, takes no
    credential, never writes to the tape, and cannot place an order. It is
    bounded twice over -- by the time range and by `max_rows` -- so a one-time
    dump cannot turn into a sustained log-volume increase.
    """
    files = sorted(out_dir.glob("pmus_*.jsonl"))
    sent = scanned = 0
    stopped = None
    for fp in files:
        if sent >= max_rows:
            stopped = "max_rows"
            break
        try:
            fh = fp.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            print("PMUS_TAPE_DUMP_ERR %s" % json.dumps(
                {"file": fp.name, "error": type(exc).__name__}), flush=True)
            continue
        with fh:
            for line in fh:
                scanned += 1
                line = line.strip()
                if not line:
                    continue
                if slug_contains and slug_contains not in line:
                    continue
                if since or until:
                    # the written row carries an ISO "t"; compare as strings,
                    # which is correct for ISO-8601 in a fixed timezone
                    try:
                        t = json.loads(line).get("t") or ""
                    except Exception:            # noqa: BLE001
                        continue
                    if since and t < since:
                        continue
                    if until and t > until:
                        continue
                print("PMUS_TAPE_DUMP %s" % line, flush=True)
                sent += 1
                if sent >= max_rows:
                    stopped = "max_rows"
                    break
    out = {"kind": "tape_dump_done", "files": [f.name for f in files],
           "scanned": scanned, "emitted": sent, "max_rows": max_rows,
           "since": since, "until": until, "slug_contains": slug_contains,
           "stopped": stopped,
           "labels": ("READ-ONLY replay of rows already on disk. No socket, no "
                      "credential, no write to the tape, no order path. Bounded "
                      "by the range AND by max_rows.")}
    print("PMUS_TAPE_DUMP_DONE %s" % json.dumps(out), flush=True)
    return out


def run(out_dir: Path, minutes: float, interval_s: float = 5.0,
        cap: int = 60, series: Optional[str] = None,
        with_book: bool = False, echo_rows: bool = False) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = ("pmus_%s_" % series.replace("-", "")) if series else "pmus_book_"
    path = out_dir / ("%s%s.jsonl" % (tag, stamp))
    # minutes <= 0 == CONTINUOUS, the convention every sibling collector in
    # deploy/ already uses (--hours 0). Without it a long-running deployment
    # would exit on its first loop test and the restart policy would spin.
    forever = minutes <= 0
    end = float("inf") if forever else (time.time() + minutes * 60.0)

    def universe():
        return series_slugs(series) if series else live_slugs(cap)

    reslate = SERIES_RESLATE_S if series else 300.0
    slugs, refreshed, rows, cycles = universe(), time.time(), 0, 0
    # slug -> deadline. A slug that leaves the universe keeps being polled until
    # its terminal state is recorded or its deadline passes. See DRAIN_S.
    draining: dict = {}
    terminal_seen: set = set()
    last_traded: dict = {}
    echoed: dict = {}

    def put(fh, obj):
        """Write a row, and echo it to stdout when asked.

        RETRIEVABILITY. Rows land on the container filesystem, which nothing
        outside the container can read: there is no shell and, where no volume
        is attached, nothing survives a restart either. A tape nobody can read
        back is not evidence. stdout IS retrievable -- it is how the 1615z and
        1630z decision states were rescued -- so echoing each row makes the
        tape scrapeable into git, which is durable and outside Railway
        entirely.
        """
        line = json.dumps(obj)
        fh.write(line + "\n")
        if not echo_rows:
            return
        # CHANGE-ONLY ECHO. The ladder is frequently byte-identical between
        # consecutive 2 s observations, and echoing every repeat makes the tape
        # too large to retrieve through the only channel that can retrieve it.
        # A state's ladder IS the last observed one, so suppressing an
        # unchanged repeat loses nothing -- the reader carries it forward. A
        # row is still emitted at least every HEARTBEAT_S per slug, so a gap in
        # the tape means a real gap in observation and not merely a quiet book,
        # which is what the completeness metric has to be able to tell apart.
        k = "%s|%s" % (obj.get("slug"), obj.get("kind"))
        body = {kk: vv for kk, vv in obj.items() if kk != "t"}
        prev_body, prev_t = echoed.get(k, (None, 0.0))
        now_s = time.time()
        if body == prev_body and (now_s - prev_t) < HEARTBEAT_S:
            return
        echoed[k] = (body, now_s)
        print("PMUS_TAPE_ROW %s" % line, flush=True)
    with path.open("a", encoding="utf-8") as fh:
        put(fh, {
            "t": datetime.now(timezone.utc).isoformat(), "kind": "tape_start",
            "gateway": GATEWAY, "interval_s": interval_s, "cap": cap,
            "series": series, "with_book": with_book,
            "reslate_s": reslate,
            "slugs": len(slugs), "slug_list": list(slugs),
            "minutes": minutes,
            "provenance": "public PMUS gateway, read-only, no credentials",
        })
        while time.time() < end:
            if time.time() - refreshed > reslate:
                fresh = universe()
                if fresh:
                    if series and set(fresh) != set(slugs):
                        put(fh, {
                            "t": datetime.now(timezone.utc).isoformat(),
                            "kind": "reslate", "series": series,
                            "was": list(slugs), "now": list(fresh)})
                    # KEEP POLLING WHAT JUST LEFT. A window drops out of the
                    # universe before the venue publishes its close, so the
                    # departure starts a drain rather than ending the coverage.
                    for gone in set(slugs) - set(fresh):
                        if gone not in terminal_seen:
                            draining.setdefault(gone, time.time() + DRAIN_S)
                    slugs = fresh
                refreshed = time.time()
            now_m = time.time()
            for s in list(draining):
                if s in terminal_seen or now_m > draining[s]:
                    put(fh, {
                        "t": datetime.now(timezone.utc).isoformat(),
                        "kind": "drain_end", "slug": s,
                        "terminal_recorded": s in terminal_seen,
                        "labels": ("terminal_recorded false means the close was "
                                   "never observed and the window fails the "
                                   "completeness rule"),
                    })
                    draining.pop(s, None)
            for s in list(slugs) + [x for x in draining if x not in slugs]:
                row = snapshot(s)
                if row:
                    # PMUS serves NO public trade-print endpoint -- /trades,
                    # /history, /ohlc, /candles and /price-history all 404. The
                    # only print evidence the venue exposes is the cumulative
                    # sharesTraded counter, so the delta between consecutive
                    # observations is recorded and NAMED as a lower bound on
                    # traded volume. It is never called a trade count and never
                    # a fill.
                    prev = last_traded.get(s)
                    cur = row.get("shares_traded")
                    try:
                        d = (float(cur) - float(prev)) if (
                            cur is not None and prev is not None) else None
                    except (TypeError, ValueError):
                        d = None
                    row["shares_traded_delta_lower_bound"] = (
                        None if d is None else round(d, 4))
                    row["shares_traded_prev"] = prev
                    if cur is not None:
                        last_traded[s] = cur
                    # A terminal state is what the completeness rule needs. Note
                    # it BEFORE the dedup in put(): the drain ends on the state
                    # having been observed, not on the row having been new.
                    st = row.get("state")
                    if st and st != "MARKET_STATE_OPEN":
                        terminal_seen.add(s)
                    put(fh, row)
                    rows += 1
                if with_book:
                    brow = book_snapshot(s)
                    if brow:
                        put(fh, brow)
                        rows += 1
            fh.flush()
            cycles += 1
            print("PMUS_BOOK_TAPE %s" % json.dumps(
                {"cycle": cycles, "slugs": len(slugs), "rows": rows,
                 "file": path.name}), flush=True)
            time.sleep(interval_s)
    print("PMUS_BOOK_TAPE_DONE %s" % json.dumps(
        {"rows": rows, "cycles": cycles, "file": str(path)}), flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--cap", type=int, default=60)
    ap.add_argument("--out-dir", default="data/pmus_book_tape")
    ap.add_argument("--series", default=None,
                    help="recurring series slug (e.g. btc-updown-15m). "
                         "Default None keeps the sports live slate.")
    ap.add_argument("--with-book", action="store_true",
                    help="also record the full ladder, for depth")
    ap.add_argument("--dump-existing", action="store_true",
                    help="before collecting, print a BOUNDED slice of the tape "
                         "already on disk as PMUS_TAPE_DUMP, so it can be "
                         "retrieved where the volume is unreachable")
    ap.add_argument("--dump-since", default="",
                    help="ISO lower bound for --dump-existing (inclusive)")
    ap.add_argument("--dump-until", default="",
                    help="ISO upper bound for --dump-existing (inclusive)")
    ap.add_argument("--dump-max", type=int, default=DUMP_MAX_ROWS,
                    help="hard row ceiling for --dump-existing")
    ap.add_argument("--dump-slug", default="",
                    help="only dump rows whose line contains this substring")
    ap.add_argument("--dump-only", action="store_true",
                    help="exit after the dump instead of starting collection")
    ap.add_argument("--echo-rows", action="store_true",
                    help="also print every row to stdout as PMUS_TAPE_ROW, so "
                         "the tape can be retrieved where no volume exists")
    a = ap.parse_args()
    if a.dump_existing:
        dump_existing(Path(a.out_dir), since=a.dump_since, until=a.dump_until,
                      max_rows=a.dump_max, slug_contains=a.dump_slug)
        if a.dump_only:
            return 0
    return run(Path(a.out_dir), a.minutes, a.interval, a.cap,
               series=a.series, with_book=a.with_book,
               echo_rows=a.echo_rows)


if __name__ == "__main__":
    raise SystemExit(main())
