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


def record(minutes: float, out_path: str) -> int:
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
    last_seen: dict = {}
    cycle = 0
    totals = {"rows": 0, "rate_limited": 0, "errors": 0, "no_depth": 0}
    with open(out_path, "a", encoding="utf-8") as fh:
        while dt.datetime.now(UTC) < deadline:
            cycle += 1
            t0 = dt.datetime.now(UTC)
            try:
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
            pace = max(CALLS_PER_MARKET / BUDGET_RPS, 0.0)
            for slug in slugs:
                if dt.datetime.now(UTC) >= deadline:
                    break
                row = observe(slug)
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--out", default="sports_tape.jsonl")
    a = ap.parse_args()
    if a.snapshot:
        return snapshot()
    if a.record:
        return record(a.minutes, a.out)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
