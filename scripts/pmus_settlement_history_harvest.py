"""Walk PMUS's public BTC15m markets BACKWARD in exact 15-minute steps.

WHAT IT IS FOR. Recovering the venue's settlement-outcome series as far back as
the venue exposes it. Settlement outcomes support streak and base-rate
statistics. They do NOT support executable-price P&L, and this harvest
deliberately collects no book: there is nothing here from which a fill price
could be constructed, so nothing downstream can accidentally treat one as real.

THE DISTINCTION THIS WHOLE STUDY RESTS ON. Three outcomes per window, never
collapsed into each other:

  EXISTS  -- /v1/market/slug/{slug} answered 200 with a row whose own `slug`
             field equals the slug asked for. The gateway answers 200 with an
             unrelated default page for filters it does not understand, so a
             status code alone is not a match and never counts as existence.
  ABSENT  -- the venue answered 404. The market is not served.
  ERROR   -- we failed to ask, or got a 429/5xx. Retried with backoff, and if
             it still fails it is recorded as ERROR and NEVER counted as
             ABSENT. An unresolved request is not evidence about the venue.

Only a run of consecutive ABSENT (never ERROR) can end the walk, so a transient
outage cannot be mistaken for the beginning of history. The gateway has already
been observed 404ing a window that existed, for sixteen minutes, so the stop
threshold is deliberately long.

THE OUTCOME, AND THE CROSS-CHECK. Measured on 2026-10-05, the market row carries
everything this study needs in ONE request, so /bbo is not asked by default:

    status            MARKET_STATUS_RESOLVED
    outcomePrices     ["1","0"] -> Yes/Up won; ["0","1"] -> No/Down won
    assetPriceTerms.priceToBeat.value        the opening BRTI average
    assetPriceTerms.settlementPrice.value    the closing BRTI average
    assetPriceTerms.windowStart / windowEnd  the venue's own window bounds

The outcome is taken from `outcomePrices`, which is the venue's own resolution.
It is then CROSS-CHECKED against the independent arithmetic the market's own
description states -- Up iff the closing average is greater than or equal to the
opening average. The two are recorded separately and every disagreement is
flagged rather than silently preferred, because a disagreement would mean one of
the two readings is wrong and the series should not be trusted until it is
explained. The arithmetic is never used to FILL IN an outcome the venue did not
state: a window the venue has not resolved is left unresolved, and the streak
analysis treats it as a hole.

PROVENANCE. Every response body is preserved verbatim, with its route, status
and fetch time, so every settlement in the series can be audited back to the
bytes the venue sent.

UNAUTHENTICATED AND GET-ONLY. Plain urllib GETs, no credential, no session, no
account. There is no code path here that could place an order.
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

GATEWAY = "https://gateway.polymarket.us"
UA = "pmus-settlement-history (read-only public settlement outcomes)"
CTX = ssl.create_default_context()
STEP = dt.timedelta(minutes=15)
MARKET_PREFIX = "cpc-btc-updown-15m-"

#: routes, in the order asked. The market row is the existence oracle; /bbo is
#: asked only when the row exists, because it 404s for windows that do exist.
ROW_PATH = "/v1/market/slug/%s"
BBO_PATH = "/v1/markets/%s/bbo"

#: the venue's own word for "this market has been resolved". Checked exactly,
#: not by substring: MARKET_STATUS_CLOSED is NOT resolved and a loose match
#: would accept it.
RESOLVED_STATUS = "MARKET_STATUS_RESOLVED"


def stamp(when: dt.datetime) -> str:
    return "%04d-%02d-%02d-%02d%02dz" % (when.year, when.month, when.day,
                                         when.hour, when.minute)


def http_get(path: str, *, timeout: float, attempts: int,
             pause: float) -> dict:
    """One route, retried only for ERROR-shaped failures.

    A 404 is an ANSWER and is returned immediately -- retrying it would turn a
    clear negative into wasted requests. A 429, a 5xx or a transport failure is
    retried with growing backoff, because those mean we failed to ask.
    """
    last = None
    for i in range(1, attempts + 1):
        req = urllib.request.Request(GATEWAY + path,
                                     headers={"User-Agent": UA})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, context=CTX,
                                        timeout=timeout) as r:
                return {"path": path, "status": int(r.status),
                        "fetched_at": dt.datetime.now(dt.timezone.utc)
                                        .isoformat(),
                        "ms": int((time.time() - t0) * 1000),
                        "body": (r.read() or b"").decode("utf-8", "replace")}
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read() or b""
            except Exception:                                  # noqa: BLE001
                pass
            rec = {"path": path, "status": int(exc.code),
                   "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "ms": int((time.time() - t0) * 1000),
                   "body": body.decode("utf-8", "replace")[:4000]}
            if int(exc.code) == 404:
                return rec
            last = rec
        except Exception as exc:                               # noqa: BLE001
            last = {"path": path, "status": None,
                    "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "ms": int((time.time() - t0) * 1000),
                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:240])}
        if i < attempts:
            time.sleep(pause * (2 ** (i - 1)))
    return last or {"path": path, "status": None, "error": "no attempt made"}


def decode(rec: dict):
    try:
        return json.loads(rec.get("body") or "null")
    except Exception:                                          # noqa: BLE001
        return None


def pick(doc, *names):
    """First of these field names found anywhere in the document.

    The venue has moved settlement fields between the market row and /bbo and
    nests them under `market` or `marketData` depending on the route, so the
    search is structural rather than a guessed path. The name matched is
    reported alongside the value so the provenance is not lost.
    """
    stack = [doc]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for n in names:
                if cur.get(n) not in (None, ""):
                    return n, cur[n]
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None, None


def unwrap(v):
    """A money/price field as a plain string. The venue wraps these as
    {"value": "85497.31", "currency": "USD"}, and the bare scalar form exists
    on other routes, so both are accepted and neither is assumed."""
    if isinstance(v, dict):
        v = v.get("value")
    return None if v in (None, "") else str(v)


def outcome_from_prices(raw):
    """The venue's own resolution, read off `outcomePrices`.

    `outcomePrices` is a JSON STRING holding a two-element array ordered as
    `outcomes`, which is ["Yes","No"]. Yes is Up. Exactly one of the two must be
    1 and the other 0; anything else is not a resolution and returns None rather
    than a guess.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:                                      # noqa: BLE001
            return None
    if not isinstance(raw, list) or len(raw) != 2:
        return None
    try:
        yes, no = float(raw[0]), float(raw[1])
    except Exception:                                          # noqa: BLE001
        return None
    if (yes, no) == (1.0, 0.0):
        return "UP"
    if (yes, no) == (0.0, 1.0):
        return "DOWN"
    return None


def outcome_from_arithmetic(price_to_beat, settlement_price):
    """Up iff the closing BRTI average >= the opening one.

    This is the market description's own rule, stated on every row. It is an
    INDEPENDENT check on `outcomePrices`, never a substitute for it.
    """
    if price_to_beat in (None, "") or settlement_price in (None, ""):
        return None
    try:
        return "UP" if float(settlement_price) >= float(price_to_beat) \
            else "DOWN"
    except Exception:                                          # noqa: BLE001
        return None


def harvest_one(when: dt.datetime, *, timeout: float, attempts: int,
                pause: float, want_bbo: bool) -> dict:
    slug = MARKET_PREFIX + stamp(when)
    row = http_get(ROW_PATH % slug, timeout=timeout, attempts=attempts,
                   pause=pause)
    out = {"window_start": when.isoformat(),
           "window_end": (when + STEP).isoformat(),
           "slug": slug,
           "raw": {"market_row": row}}

    if row.get("status") == 404:
        out["existence"] = "ABSENT"
        return out
    if row.get("status") != 200:
        out["existence"] = "ERROR"
        out["error"] = row.get("error") or ("HTTP %s" % row.get("status"))
        return out

    doc = decode(row)
    r = doc.get("market") if isinstance(doc, dict) else None
    if not isinstance(r, dict):
        r = doc if isinstance(doc, dict) else {}
    # Identity is matched on the row the venue returned, not on the status:
    # the gateway answers 200 with an unrelated default page for filters it
    # does not understand.
    if r.get("slug") != slug:
        out["existence"] = "ERROR"
        out["error"] = ("row did not identify itself: got slug %r"
                        % r.get("slug"))
        return out

    terms = r.get("assetPriceTerms") or {}
    if not isinstance(terms, dict):
        terms = {}

    out["existence"] = "EXISTS"
    out["market_id"] = r.get("id")
    out["status"] = r.get("status")
    out["ep3_status"] = r.get("ep3Status")
    out["resolved"] = (r.get("status") == RESOLVED_STATUS)
    # The venue's own window bounds, kept ALONGSIDE the slug-derived ones so a
    # slug whose stamp disagreed with the market's real window would be visible
    # instead of assumed away.
    out["venue_window_start"] = terms.get("windowStart")
    out["venue_window_end"] = terms.get("windowEnd")
    out["start_date"] = r.get("startDate")
    out["end_date"] = r.get("endDate")
    out["horizon"] = terms.get("horizon")
    out["index_symbol"] = terms.get("indexSymbol")
    out["price_to_beat"] = unwrap(terms.get("priceToBeat"))
    out["final_price"] = unwrap(terms.get("settlementPrice"))
    out["outcome_prices"] = r.get("outcomePrices")

    venue = outcome_from_prices(r.get("outcomePrices"))
    arith = outcome_from_arithmetic(out["price_to_beat"], out["final_price"])
    out["outcome_from_venue_prices"] = venue
    out["outcome_from_arithmetic"] = arith
    # The resolution is only taken when the venue SAYS it is resolved. The
    # arithmetic never fills in a missing resolution.
    out["outcome"] = venue if out["resolved"] else None
    out["cross_check"] = ("agree" if (venue and arith and venue == arith)
                          else "disagree" if (venue and arith)
                          else "incomplete")

    # /bbo is not needed -- the row carries the settlement -- so it is asked
    # only when explicitly requested, and only when the row left the
    # resolution unstated.
    if want_bbo and out["outcome"] is None:
        bbo = http_get(BBO_PATH % slug, timeout=timeout, attempts=attempts,
                       pause=pause)
        out["raw"]["bbo"] = bbo
        if bbo.get("status") == 200:
            bdoc = decode(bbo)
            md = (bdoc or {}).get("marketData") or bdoc or {}
            px = unwrap(md.get("settlementPx")) if isinstance(md, dict) \
                else None
            out["bbo_settlement_px"] = px
            if px is not None:
                try:
                    v = float(px)
                except Exception:                              # noqa: BLE001
                    v = None
                if v == 1.0:
                    out["outcome"] = "UP"
                elif v == 0.0:
                    out["outcome"] = "DOWN"
                if out["outcome"]:
                    out["outcome_source"] = "bbo_settlement_px"
    out.setdefault("outcome_source",
                   "market_row_outcome_prices" if out["outcome"] else None)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-window", default=None,
                    help="ISO window start to begin the BACKWARD walk at; "
                         "default is the last fully elapsed window")
    ap.add_argument("--max-windows", type=int, default=20000)
    ap.add_argument("--stop-after-absent", type=int, default=288,
                    help="consecutive ABSENT windows that end the walk. 288 is "
                         "three full days: the gateway has 404ed a LIVE window "
                         "for sixteen minutes, and a genuine multi-day pause in "
                         "the series must not be mistaken for its beginning")
    ap.add_argument("--confirm-days", type=int, default=120,
                    help="after the walk stops, probe one window per day this "
                         "far further back, to test the boundary rather than "
                         "assume it. These probes are recorded separately and "
                         "are NOT part of the series")
    ap.add_argument("--sleep", type=float, default=0.12,
                    help="pause between windows; politeness, not a rate limit")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--retry-pause", type=float, default=2.0)
    # /bbo is NOT asked by default: the market row measured on 2026-10-05
    # carries status, outcomePrices, priceToBeat and settlementPrice, so one
    # request per window is enough and a second would double the load on the
    # venue for nothing.
    ap.add_argument("--bbo", action="store_true",
                    help="additionally ask /bbo for windows whose row left the "
                         "resolution unstated")
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw-out", default=None,
                    help="where the verbatim response bodies go; default is "
                         "<out>.raw.jsonl")
    a = ap.parse_args()

    if a.from_window:
        cur = dt.datetime.fromisoformat(a.from_window)
        if cur.tzinfo is None:
            cur = cur.replace(tzinfo=dt.timezone.utc)
    else:
        now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        cur = now - dt.timedelta(minutes=now.minute % 15) - STEP

    raw_out = a.raw_out or (a.out + ".raw.jsonl")
    for p in (a.out, raw_out):
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)

    newest = cur.isoformat()
    tally = {"EXISTS": 0, "ABSENT": 0, "ERROR": 0}
    cross = {"agree": 0, "disagree": 0, "incomplete": 0}
    disagreements = []
    resolved = 0
    absent_run = 0
    walked = 0
    stop = None
    oldest_exists = None
    t_start = time.time()

    with open(a.out, "w", encoding="utf-8") as f, \
            open(raw_out, "w", encoding="utf-8") as rf:
        while walked < a.max_windows:
            rec = harvest_one(cur, timeout=a.timeout, attempts=a.attempts,
                              pause=a.retry_pause, want_bbo=a.bbo)
            walked += 1
            raw = rec.pop("raw")
            rf.write(json.dumps({"slug": rec["slug"],
                                 "window_start": rec["window_start"],
                                 "raw": raw}) + "\n")
            f.write(json.dumps(rec) + "\n")

            tally[rec["existence"]] = tally.get(rec["existence"], 0) + 1
            if rec["existence"] == "EXISTS":
                oldest_exists = rec["window_start"]
                cc = rec.get("cross_check") or "incomplete"
                cross[cc] = cross.get(cc, 0) + 1
                if cc == "disagree":
                    # Never silently preferred. A disagreement is a defect in
                    # the series and is named so it can be explained.
                    disagreements.append(
                        {"slug": rec["slug"],
                         "venue": rec.get("outcome_from_venue_prices"),
                         "arithmetic": rec.get("outcome_from_arithmetic"),
                         "price_to_beat": rec.get("price_to_beat"),
                         "final_price": rec.get("final_price")})
                if rec.get("outcome"):
                    resolved += 1
            # ONLY a consecutive run of ABSENT ends the walk. An ERROR breaks
            # the run without extending it, so a transient failure can neither
            # stop the walk nor hide a real boundary.
            if rec["existence"] == "ABSENT":
                absent_run += 1
                if absent_run >= a.stop_after_absent:
                    stop = ("%d consecutive ABSENT windows back to %s"
                            % (absent_run, rec["window_start"]))
                    break
            else:
                absent_run = 0

            if walked % 96 == 0:
                print("... %5d windows walked, back to %s | exists %d "
                      "absent %d error %d resolved %d | %.0fs"
                      % (walked, rec["window_start"], tally["EXISTS"],
                         tally["ABSENT"], tally["ERROR"], resolved,
                         time.time() - t_start), flush=True)
                f.flush()
                rf.flush()

            cur = cur - STEP
            time.sleep(a.sleep)

    # Test the boundary instead of assuming it: one window per day, further
    # back than the walk reached. If any of these EXISTS, the walk stopped too
    # early and the summary says so rather than reporting a false beginning.
    confirm = []
    probe_from = cur
    for d in range(1, a.confirm_days + 1):
        w = probe_from - dt.timedelta(days=d)
        rec = harvest_one(w, timeout=a.timeout, attempts=a.attempts,
                          pause=a.retry_pause, want_bbo=False)
        rec.pop("raw", None)
        confirm.append({"window_start": rec["window_start"],
                        "existence": rec["existence"]})
        time.sleep(a.sleep)
    beyond = [c for c in confirm if c["existence"] == "EXISTS"]

    summary = {
        "LABEL": "PMUS_BTC15M_SETTLEMENT_HISTORY_HARVEST",
        "authenticated": False, "method": "GET", "books_collected": False,
        "walked_windows": walked,
        "existence_tally": tally,
        "resolved_outcomes": resolved,
        "oldest_existing_window": oldest_exists,
        "newest_requested_window": newest,
        "cross_check_tally": cross,
        "cross_check_disagreements": disagreements,
        "boundary_confirmation_probes": len(confirm),
        "boundary_confirmation_existing_beyond_walk": beyond,
        "boundary_confirmed": not beyond,
        "stop_reason": stop or ("max-windows %d reached" % a.max_windows),
        "stop_after_absent": a.stop_after_absent,
        "elapsed_s": int(time.time() - t_start),
        "settlements": a.out, "raw": raw_out,
        "labels": ("ABSENT means the venue answered 404. ERROR means we failed "
                   "to ask and is NEVER counted as absence. An outcome is UP "
                   "only at settlementPx 1 and DOWN only at 0; anything else "
                   "is left unresolved and never inferred -- the independent "
                   "priceToBeat/settlementPrice arithmetic is a CROSS-CHECK and "
                   "never fills in an outcome the venue did not state. "
                   "boundary_confirmation probes test whether anything exists "
                   "further back than the walk reached; they are not part of "
                   "the series. Settlement outcomes cannot establish the "
                   "profitability of any strategy."),
    }
    print("HARVEST_SUMMARY " + json.dumps(summary, indent=1))
    with open(a.out + ".summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
