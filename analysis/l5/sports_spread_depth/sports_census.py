"""Sports spread-and-depth census. READ ONLY. NO MONEY. No order path.

WHY THIS EXISTS. Two-sided maker spread capture pays exactly the spread
when a pair fills, so it is viable only where `spread > adverse selection`.
BTC5m is 1c against a measured 4.87c and is closed: the ratio is 4.9x the
wrong way and no parameter changes it. Sports quote far wider -- 8-25c has
been seen -- but two things were never measured there: whether the depth at
those wide touches is real, and what adverse selection actually is.

WHAT THE HANDOFF GOT WRONG, MEASURED 2026-11-13 AND THE REASON THIS FILE
LOOKS DIFFERENT FROM `straddle_shadow.py`:

  * `/v1/markets/{slug}/bbo` was said to 404 while OPEN. It does NOT on
    sports -- it answers 200 with `state: MARKET_STATE_OPEN`, and it
    carries `sharesTraded`. The 404 is a `cpc-` crypto-window behaviour
    that was generalised to the whole venue.
  * `/v1/markets/{slug}/book` was not mentioned at all. It serves the FULL
    depth ladder on an open sports market, sell side under `offers`.
  * so `volume-while-fillable`, which on BTC5m "CANNOT be measured on a
    live window from public data", IS measurable here. That quantity is
    the one that decides whether size fills, and it is why this census can
    answer a question the BTC5m shadow had to leave as null.
  * the live-slate query was called unsolved. It is not:
    `/v1/events?live=true&active=true&closed=false` returns the live slate
    (76 events / 598 markets when this was written) and
    `/v1/markets?active=true&closed=false` serves the open universe. Both
    were already in `src/pmus_sports_collector.py`.

DEPTH CUTS BOTH WAYS, which is the thing the "1-3 shares" worry misses. A
thin touch caps capacity, but a thick touch is a QUEUE we join at the back:
resting behind 16,000 shares at the bid is not a fill, it is a wait. So
this records depth as queue-ahead AND as capacity, and the analyzer reports
the joint distribution rather than either alone. Spread alone decides
nothing; `spread x depth` is the instruction and the queue is why.

DISCIPLINE CARRIED FROM THE BTC5M COLLECTOR, where seven defects were found
by running it and none by reviewing it:

  heartbeat      emitted from the first cycle, so a dead collector says so
                 instead of looking idle.
  never fake a 0 an unmeasured quantity is `None` with a reason beside it.
                 A rate-limited poll reporting "no volume" and a real zero
                 must never be the same row.
  429 is not an  the venue asking us to slow down is handled apart from a
  error          transport failure, and backs off instead of retrying.
  read density   every row carries its gap from the previous read, because a
                 sparse tape under-counts fills and that must be visible
                 rather than averaged in.
  no order path  there is no order path in this file. It holds no
                 credentials and GETs only.

Usage:
  python3 -I .../sports_census.py --snapshot            # one pass, now
  python3 -I .../sports_census.py --record --minutes 45 --out TAPE.jsonl
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Optional

UTC = dt.timezone.utc
GATEWAY = os.environ.get("PMUS_PUBLIC_GATEWAY", "https://gateway.polymarket.us")
TAG = "SPORTS_SPREAD_DEPTH"

#: Sports families. `aec-` moneyline, `a5c-` 5-inning, `atc-` totals,
#: `asc-`/`tsc-` the other two families that appear in the credited-rebate
#: ledger. Crypto (`cpc-`) is EXCLUDED by prefix and that exclusion is
#: reported, not silent: it is the pond this whole move is away from.
SPORTS_PREFIXES = ("aec-", "a5c-", "asc-", "tsc-", "atc-")
CRYPTO_PREFIXES = ("cpc-", "cpce", "cpcs", "cpcx", "cpag", "cpau")

#: steady polling is fine; bursts buy 429s. The public recorder sustained
#: 2s for 27 hours, but it polled ONE market -- here each market costs two
#: calls, so the budget is per-request and the cycle length follows from it.
BUDGET_RPS = float(os.environ.get("CENSUS_BUDGET_RPS", "6.0"))
CALLS_PER_MARKET = 2                     # bbo + book
CALLS_PER_MARKET_BOOK_ONLY = 1           # book alone; see observe_book_only
MIN_CYCLE_S = 15.0
GAP_WARN_S = 30.0


def emit(kind: str, payload: Mapping[str, Any]) -> None:
    print("%s %s" % (kind, json.dumps(payload, sort_keys=True, default=str)),
          flush=True)


class RateLimited(Exception):
    """The venue asked us to slow down. Not a failure; must not be retried
    immediately, and must never be recorded as a zero."""


def _get(path: str, timeout: float = 15.0, retries: int = 2) -> Any:
    req = urllib.request.Request(GATEWAY + path,
                                 headers={"Accept": "application/json"})
    ctx = ssl.create_default_context()
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout,
                                        context=ctx) as r:      # noqa: S310
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                if attempt == retries:
                    raise RateLimited("429") from exc
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RateLimited("429")


def _num(v: Any) -> Optional[float]:
    """A venue number, which may be bare or wrapped in {value, currency}."""
    if isinstance(v, Mapping):
        v = v.get("value")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_sports(slug: str) -> bool:
    return bool(slug) and slug.startswith(SPORTS_PREFIXES)


def discover() -> "tuple[list, dict]":
    """The live sports slate, plus what was excluded and why.

    Uses the live-events query the handoff called unsolved. Returns the
    slugs AND a coverage block, because "43 sports markets" means nothing
    without "out of 598, 553 of them crypto or other".
    """
    doc = _get("/v1/events?live=true&active=true&closed=false&limit=500")
    events = doc.get("events") or []
    sports, crypto, other = [], 0, 0
    meta: dict = {}
    for e in events:
        for m in (e.get("markets") or []):
            slug = m.get("slug") or ""
            if is_sports(slug):
                sports.append(slug)
                meta[slug] = {"event": e.get("slug"),
                              "title": e.get("title"),
                              "league": (e.get("primaryTag") or {}).get("slug")
                              if isinstance(e.get("primaryTag"), Mapping)
                              else e.get("primaryTag"),
                              "market_type": m.get("marketType"),
                              "start": e.get("startDate")}
            elif slug.startswith(CRYPTO_PREFIXES):
                crypto += 1
            else:
                other += 1
    slugs = sorted(set(sports))
    cov = {"live_events": len(events), "sports_markets": len(slugs),
           "excluded_crypto": crypto, "excluded_other": other,
           "prefixes_kept": list(SPORTS_PREFIXES),
           "leagues": sorted({(meta[s] or {}).get("league") or "?"
                              for s in slugs})}
    return slugs, cov


def observe(slug: str) -> dict:
    """One market, one poll: the touch, the depth behind it, and the tape.

    Two calls. `bbo` carries the quote, the state and `sharesTraded`;
    `book` carries the ladder. Both are GET. Anything unreadable comes back
    flagged, never as a zero -- a market we could not read is not a market
    with no depth.
    """
    row: dict = {"slug": slug, "utc": dt.datetime.now(UTC).isoformat()}
    try:
        bbo = _get("/v1/markets/%s/bbo" % slug)
        md = bbo.get("marketData") or {}
    except RateLimited:
        return {**row, "readable": False, "why": "RATE_LIMITED"}
    except Exception as exc:                                   # noqa: BLE001
        return {**row, "readable": False,
                "why": "%s:%s" % (type(exc).__name__,
                                  getattr(exc, "code", "") or "")}
    bid, ask = _num(md.get("bestBid")), _num(md.get("bestAsk"))
    row.update({
        "readable": True,
        "state": md.get("state"),
        "bid": bid, "ask": ask,
        "shares_traded": _num(md.get("sharesTraded")),
        "open_interest": _num(md.get("openInterest")),
        "last_trade_px": _num(md.get("lastTradePx")),
        "settlement_px": _num(md.get("settlementPx")),
    })
    #: the spread is the whole edge, so it is computed once, here, and
    #: only when both sides exist. A one-sided book has NO spread, which is
    #: different from a spread of 0.
    row["spread"] = (None if bid is None or ask is None or ask < bid
                     else round(ask - bid, 4))
    #: a resting buy on both sides at the touch costs bid + (1 - ask).
    #: That IS 1 - spread, and it is the one equation this census serves.
    row["maker_pair_cost"] = (None if row["spread"] is None
                              else round(bid + (1.0 - ask), 4))
    try:
        book = (_get("/v1/markets/%s/book" % slug).get("marketData") or {})
    except RateLimited:
        #: the quote survived and the ladder did not. Say exactly that.
        return {**row, "depth_available": False, "why_depth": "RATE_LIMITED"}
    except Exception as exc:                                   # noqa: BLE001
        return {**row, "depth_available": False,
                "why_depth": type(exc).__name__}
    bids = book.get("bids") or []
    offers = book.get("offers") or []        # PMUS serves sell depth here
    row["depth_available"] = True
    row["bid_touch_qty"] = _num((bids[0] or {}).get("qty")) if bids else None
    row["ask_touch_qty"] = _num((offers[0] or {}).get("qty")) if offers else None
    row["bid_levels"] = len(bids)
    row["ask_levels"] = len(offers)
    #: QUEUE AHEAD is the number that decides whether we ever fill, and it
    #: is the touch depth seen from the other end: joining a 16,000-share
    #: bid means 16,000 shares must trade before we do. Recorded under its
    #: own name so no reader can mistake capacity for accessibility.
    row["queue_ahead_bid"] = row["bid_touch_qty"]
    row["queue_ahead_ask"] = row["ask_touch_qty"]
    return row


#: ONE CALL INSTEAD OF TWO, which doubles the sampling rate for free and
#: was found by reading what `/book` actually returns. Its `stats` block
#: carries `sharesTraded`, `lastTradePx`, `openInterest` and the session
#: high/low -- so the ladder AND the volume come from a single GET, and
#: `bbo` is not needed at all for the census.
#:
#: THIS ALSO FALSIFIES A LOAD-BEARING CLAIM IN THE BTC5M WORK. That shadow
#: recorded `vol_while_fillable: None` with reason `BBO_404_WHILE_OPEN` for
#: 199 windows, on the finding that "volume-while-fillable CANNOT be
#: measured on a live window from public data". Measured today: `/book`
#: answers 200 on a LIVE `cpc-btc-updown-5m` window with a 98-level ladder
#: and `stats.sharesTraded` populated. The quantity was one endpoint away
#: the whole time. BTC5m stays closed -- it is closed on spread arithmetic,
#: not on this -- but "cannot be measured" was wrong, and the same
#: assumption would have travelled into the sports census unexamined.
#:
#: ONE TRAP, AND IT WOULD HAVE MIS-SETTLED EVERY WINDOW. `stats` also
#: carries `settlementPx`, and on an OPEN market it reads 0.0000 rather
#: than null -- measured on a live window trading at 0.98. `straddle_shadow`
#: treats a settlementPx of 0 or 1 as the settled outcome, so reading
#: settlement from `/book` would have resolved every open window to DOWN
#: with total confidence. Settlement is therefore taken ONLY from `/bbo`,
#: which correctly returns null while open, and never from here.
SETTLEMENT_NEVER_FROM_BOOK = True


def observe_book_only(slug: str) -> dict:
    """One GET: the touch, the ladder behind it, and the volume."""
    row: dict = {"slug": slug, "utc": dt.datetime.now(UTC).isoformat(),
                 "source": "book_only"}
    try:
        md = (_get("/v1/markets/%s/book" % slug).get("marketData") or {})
    except RateLimited:
        return {**row, "readable": False, "why": "RATE_LIMITED"}
    except Exception as exc:                                   # noqa: BLE001
        return {**row, "readable": False,
                "why": "%s:%s" % (type(exc).__name__,
                                  getattr(exc, "code", "") or "")}
    bids = md.get("bids") or []
    offers = md.get("offers") or []          # PMUS serves sell depth here
    st = md.get("stats") or {}
    bid = _num((bids[0] or {}).get("px")) if bids else None
    ask = _num((offers[0] or {}).get("px")) if offers else None
    row.update({
        "readable": True, "depth_available": True,
        "state": md.get("state"),
        "bid": bid, "ask": ask,
        "bid_touch_qty": _num((bids[0] or {}).get("qty")) if bids else None,
        "ask_touch_qty": _num((offers[0] or {}).get("qty")) if offers else None,
        "bid_levels": len(bids), "ask_levels": len(offers),
        "shares_traded": _num(st.get("sharesTraded")),
        "last_trade_px": _num(st.get("lastTradePx")),
        "last_trade_qty": _num(st.get("lastTradeQty")),
        "open_interest": _num(st.get("openInterest")),
        #: deliberately NOT read from stats -- see the trap note above.
        "settlement_px": None,
        "settlement_source": "not_read_here_see_SETTLEMENT_NEVER_FROM_BOOK",
    })
    row["spread"] = (None if bid is None or ask is None or ask < bid
                     else round(ask - bid, 4))
    row["maker_pair_cost"] = (None if row["spread"] is None
                              else round(bid + (1.0 - ask), 4))
    row["queue_ahead_bid"] = row["bid_touch_qty"]
    row["queue_ahead_ask"] = row["ask_touch_qty"]
    return row


def snapshot() -> int:
    """One pass over the live sports slate. Answers the depth question now."""
    slugs, cov = discover()
    emit(TAG + "_DISCOVERY", cov)
    if not slugs:
        emit(TAG + "_EMPTY", {"note": "no live sports markets on the slate; "
                                      "this is a time-of-day fact, not a "
                                      "measurement of depth"})
        return 0
    pace = max(CALLS_PER_MARKET / BUDGET_RPS, 0.0)
    rows = []
    for i, slug in enumerate(slugs):
        row = observe(slug)
        rows.append(row)
        emit(TAG + "_OBS", row)
        if i == 0 and not row.get("readable"):
            emit(TAG + "_READ_ERROR", {"slug": slug, "why": row.get("why"),
                                       "note": "first read of the pass"})
        time.sleep(pace)
    emit(TAG + "_SNAPSHOT_DONE",
         {"polled": len(rows),
          "readable": sum(1 for r in rows if r.get("readable")),
          "with_depth": sum(1 for r in rows if r.get("depth_available")),
          "two_sided": sum(1 for r in rows if r.get("spread") is not None)})
    return 0


def record(minutes: float, out_path: str,
           book_only: bool = False, slugs_file: str = None) -> int:
    """Poll the live sports slate on a cycle and write a tape.

    Re-discovers every cycle, because a sports slate turns over as games
    start and end, and a fixed list quietly becomes a list of closed
    markets. Each row carries its own read gap so the analyzer can weight
    or reject a sparse market instead of averaging it in blind.
    """
    deadline = dt.datetime.now(UTC) + dt.timedelta(minutes=minutes)
    emit(TAG + "_START", {"gateway": GATEWAY, "out": out_path,
                          "minutes": minutes, "budget_rps": BUDGET_RPS,
                          "provenance": "public gateway, GET only, no "
                                        "credentials, no order path"})
    #: AN EXPLICIT SLUG LIST, because the markets that matter are not on
    #: the live slate. The wide-spread cells that actually trade turned out
    #: to be long-dated `atc-...-series-2026-*` names, which the live-events
    #: query never returns -- it serves in-game events. Polling the live
    #: slate to judge those markets would measure the wrong population and
    #: report it as the right one.
    fixed = None
    if slugs_file:
        with open(slugs_file, encoding="utf-8") as fh:
            fixed = [ln.strip() for ln in fh
                     if ln.strip() and not ln.startswith("#")]
        emit(TAG + "_FIXED_SLATE",
             {"slugs": len(fixed), "source": slugs_file,
              "note": "explicit list; live-slate discovery is NOT used"})
    last_seen: dict = {}
    cycle = 0
    totals = {"rows": 0, "rate_limited": 0, "errors": 0, "no_depth": 0}
    with open(out_path, "a", encoding="utf-8") as fh:
        while dt.datetime.now(UTC) < deadline:
            cycle += 1
            t0 = dt.datetime.now(UTC)
            try:
                if fixed is not None:
                    slugs, cov = fixed, {"fixed_slate": len(fixed)}
                else:
                    slugs, cov = discover()
            except RateLimited:
                totals["rate_limited"] += 1
                time.sleep(10.0)
                continue
            except Exception as exc:                           # noqa: BLE001
                totals["errors"] += 1
                emit(TAG + "_DISCOVERY_ERROR", {"why": type(exc).__name__})
                time.sleep(10.0)
                continue
            calls = (CALLS_PER_MARKET_BOOK_ONLY if book_only
                     else CALLS_PER_MARKET)
            pace = max(calls / BUDGET_RPS, 0.0)
            for slug in slugs:
                if dt.datetime.now(UTC) >= deadline:
                    break
                row = (observe_book_only(slug) if book_only
                       else observe(slug))
                row["cycle"] = cycle
                prev = last_seen.get(slug)
                now = dt.datetime.now(UTC)
                row["read_gap_s"] = (None if prev is None
                                     else round((now - prev).total_seconds(), 1))
                last_seen[slug] = now
                if not row.get("readable"):
                    if row.get("why") == "RATE_LIMITED":
                        totals["rate_limited"] += 1
                    else:
                        totals["errors"] += 1
                elif not row.get("depth_available"):
                    totals["no_depth"] += 1
                fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
                totals["rows"] += 1
                time.sleep(pace)
            fh.flush()
            #: HEARTBEAT EVERY CYCLE, from the first one. This is the line
            #: whose absence is the alarm, and it exists because a silent
            #: collector once polled for 24 minutes and logged nothing.
            emit(TAG + "_HEARTBEAT",
                 {"cycle": cycle, "slate": len(slugs),
                  "cycle_s": round((dt.datetime.now(UTC) - t0).total_seconds(), 1),
                  "coverage": cov, **totals,
                  "minutes_left": round(
                      (deadline - dt.datetime.now(UTC)).total_seconds() / 60.0, 1)})
            left = MIN_CYCLE_S - (dt.datetime.now(UTC) - t0).total_seconds()
            if left > 0 and dt.datetime.now(UTC) + dt.timedelta(seconds=left) < deadline:
                time.sleep(left)
    emit(TAG + "_DONE", {"cycles": cycle, **totals, "out": out_path})
    return 0


#: THE WHOLE OPEN SPORTS UNIVERSE, not just the live slate. The live-events
#: query embeds at most ~15 markets an event while an event can carry 47-162,
#: so the live slate is a sample and a small one. `/v1/markets` pages the
#: real universe AND carries bestBidQuote/bestAskQuote inline, so a spread
#: census over thousands of markets costs one call per 500 markets instead
#: of one per market. Depth still costs a call, so it is bought only where
#: the spread is wide enough to matter -- which is the whole question.
ENUM_PAGE = 500
ENUM_MAX_PAGES = 40


def enumerate_open(max_pages: int = ENUM_MAX_PAGES) -> "tuple[list, dict]":
    """Every open+active market, paged. Degrades rather than dies: a
    mid-way failure returns what was gathered and SAYS how many pages it
    got, because a short sweep reported as a complete one would understate
    the universe and no reader could tell."""
    rows: list = []
    pages = 0
    stopped = "short_page"
    for page in range(max_pages):
        try:
            resp = _get("/v1/markets?active=true&closed=false"
                        "&limit=%d&offset=%d" % (ENUM_PAGE, page * ENUM_PAGE))
        except RateLimited:
            stopped = "rate_limited"
            break
        except Exception as exc:                               # noqa: BLE001
            stopped = "error:" + type(exc).__name__
            break
        ms = resp.get("markets") if isinstance(resp, Mapping) else None
        if not isinstance(ms, list):
            stopped = "no_markets_key"
            break
        pages += 1
        rows.extend(m for m in ms if isinstance(m, Mapping))
        if len(ms) < ENUM_PAGE:
            break
        time.sleep(1.0 / max(BUDGET_RPS, 1.0))
    else:
        stopped = "page_cap"
    return rows, {"pages": pages, "markets_seen": len(rows),
                  "stopped_because": stopped,
                  "complete": stopped == "short_page"}


def universe(wide_threshold: float = 0.05, depth_cap: int = 220) -> int:
    """Spread over the whole open sports universe; depth where it is wide.

    Reports the spread distribution from the inline quotes, then buys the
    depth ladder for every wide-spread market (up to a cap, so this stays a
    bounded read) and reports spread x depth on exactly the cells the
    strategy would want.
    """
    rows, cov = enumerate_open()
    emit(TAG + "_UNIVERSE_ENUM", cov)
    if not cov["complete"]:
        emit(TAG + "_UNIVERSE_PARTIAL",
             {"note": "sweep did not reach a short page; counts below are a "
                      "LOWER BOUND on the universe, not the universe",
              "stopped_because": cov["stopped_because"]})
    sports = []
    excluded = {"crypto": 0, "other": 0}
    for m in rows:
        slug = m.get("slug") or ""
        if is_sports(slug):
            bid, ask = _num(m.get("bestBidQuote")), _num(m.get("bestAskQuote"))
            sports.append({"slug": slug, "bid": bid, "ask": ask,
                           "market_type": m.get("marketType"),
                           "spread": (None if bid is None or ask is None
                                      or ask < bid else round(ask - bid, 4))})
        elif slug.startswith(CRYPTO_PREFIXES):
            excluded["crypto"] += 1
        else:
            excluded["other"] += 1
    two = [s for s in sports if s["spread"] is not None]
    wide = sorted((s for s in two if s["spread"] >= wide_threshold),
                  key=lambda s: -s["spread"])
    emit(TAG + "_UNIVERSE_SPORTS",
         {"sports_markets": len(sports), "two_sided": len(two),
          "one_sided_or_unquoted": len(sports) - len(two),
          "wide_ge_%.2f" % wide_threshold: len(wide),
          "excluded": excluded,
          "note": "a one-sided book has NO spread, which is not a spread "
                  "of 0 and is counted separately"})
    #: SAMPLE STRATIFIED BY SPREAD, not widest-first, and the first run of
    #: this file got it wrong in a way worth recording. Taking the widest
    #: 220 of 852 returned a sample whose median spread was 0.32 and whose
    #: lifetime volume was 0.0 shares in 100% of cases -- true, and
    #: useless, because the widest quotes are the deadest markets by
    #: construction. "No survivors" from that sample would have been an
    #: artefact of the ORDERING, not a fact about the venue, and it would
    #: have looked exactly like a clean negative result.
    #:
    #: The cells the prior work actually pointed at were 8-25c with real
    #: size and real volume, so the sample has to cover the wide RANGE
    #: evenly. Equal quota per bucket, taken evenly across each bucket.
    buckets: dict = {}
    edges = [(0.05, 0.08), (0.08, 0.12), (0.12, 0.18), (0.18, 0.25),
             (0.25, 0.40), (0.40, 1.01)]
    for lo, hi in edges:
        buckets["%.2f-%.2f" % (lo, hi)] = [s for s in wide
                                           if lo <= s["spread"] < hi]
    quota = max(1, depth_cap // max(1, len(buckets)))
    picked = []
    for name, members in buckets.items():
        if not members:
            continue
        step = max(1, len(members) // quota)
        picked.extend(members[::step][:quota])
    emit(TAG + "_UNIVERSE_STRATA",
         {name: {"available": len(m),
                 "sampled": min(len(m[::max(1, len(m) // quota)]), quota)}
          for name, m in buckets.items()})
    emit(TAG + "_UNIVERSE_DEPTH_PLAN",
         {"wide_total": len(wide), "reading_depth_for": len(picked),
          "cap": depth_cap,
          "unread": max(0, len(wide) - len(picked))})
    pace = max(CALLS_PER_MARKET / BUDGET_RPS, 0.0)
    for s in picked:
        row = observe(s["slug"])
        row["enum_spread"] = s["spread"]
        row["market_type"] = s.get("market_type")
        emit(TAG + "_WIDE", row)
        time.sleep(pace)
    emit(TAG + "_UNIVERSE_DONE", {"wide_read": len(picked)})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--universe", action="store_true")
    ap.add_argument("--wide", type=float, default=0.05)
    ap.add_argument("--book-only", action="store_true",
                    help="one GET a market instead of two; doubles resolution")
    ap.add_argument("--slugs", help="file of explicit slugs, one a line; "
                                    "bypasses live-slate discovery")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--out", default="sports_tape.jsonl")
    a = ap.parse_args()
    if a.snapshot:
        return snapshot()
    if a.universe:
        return universe(a.wide)
    if a.record:
        return record(a.minutes, a.out, a.book_only, a.slugs)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
