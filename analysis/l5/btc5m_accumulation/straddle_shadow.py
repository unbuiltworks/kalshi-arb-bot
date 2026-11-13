"""The 8c straddle, evaluated against LIVE public quotes. NO MONEY.

WHY THIS EXISTS. The straddle's edge sits at t ~1.6 on 189 recorded
windows: never negative, never proven. Settling it needs ~304 windows for
t=2 and ~684 for t=3 -- about one day and two and a half days of
continuous 5-minute windows. Buying those windows live costs real money and
teaches nothing per window; six-window campaigns are blank 40% of the time
by construction. So the evidence has to come from observation, not trading.

WHY NOT JUST READ THE EXISTING RECORDER. `l5-btc5m-public-recorder` already
writes every quote to a volume, about 150 lines a window. That is the
problem: a day is ~43,000 lines, which cannot be pulled through an agent's
context reliably, and the volume is not readable from a session.

THE FIX IS THE OUTPUT SHAPE. This emits ONE LINE PER WINDOW -- the decision
and its settled result, nothing else. A full day is 288 lines, which fits
in a single log query. Everything needed to compute the edge is on that
line, and nothing else is needed at all.

WHAT IT DOES. Polls the venue's PUBLIC bbo endpoint (no credentials, GET
only), tracks two hypothetical resting buys at `price` -- one a side -- and
at window close reads the venue's own settlementPx. It submits nothing,
holds no credentials, and cannot place an order: there is no order path in
this file.

THE CONVENTIONS ARE THE LIVE ONES, deliberately, so the numbers are
comparable with the traded record rather than merely similar:

  own price   UP is the YES bid; DOWN is 1 - the YES ask.
  fill        a resting buy at x fills when own price <= x. This is the
              same rule as `replay.py`, validated across 189 windows, and
              it is OPTIMISTIC about queue position -- it assumes we are
              filled whenever the touch reaches us.
  cancel      CANCEL_LEAD_S before the close, matching the live runner, so
              the terminal collapse to 0 or 1 cannot fill us.
  outcome     the venue's settlementPx, and ONLY that. 0 or 1. Anything
              else is reported as unresolved rather than guessed.
  P&L         linear in legs: each leg pays 1 if its side won, else 0,
              minus what it cost. Verified against rung-matched and
              quantity-matched accounting on 187 windows -- all three agree
              to the cent.

Usage:
  python3 -I analysis/l5/btc5m_accumulation/straddle_shadow.py            # run forever
  python3 -I analysis/l5/btc5m_accumulation/straddle_shadow.py --windows 2
  python3 -I analysis/l5/btc5m_accumulation/straddle_shadow.py --price 0.08
  python3 -I analysis/l5/btc5m_accumulation/straddle_shadow.py --tally FILE
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal as D

UTC = dt.timezone.utc
GATEWAY = os.environ.get("PMUS_PUBLIC_GATEWAY", "https://gateway.polymarket.us")
WINDOW_S = 300.0
CANCEL_LEAD_S = 30.0
POLL_S = 2.0
UP, DOWN = "UP", "DOWN"
TAG = "BTC5M_STRADDLE_SHADOW"


def emit(kind, payload):
    print("%s %s" % (kind, json.dumps(payload, sort_keys=True)), flush=True)


def dec(v):
    if v is None:
        return None
    if isinstance(v, dict):                      # {"value": ..., "currency":}
        v = v.get("value")
        if v is None:
            return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def slug_for(ws):
    return "cpc-btc-updown-5m-%s" % ws.strftime("%Y-%m-%d-%H%Mz")


def window_start(now):
    return now.replace(second=0, microsecond=0, minute=(now.minute // 5) * 5)


#: the gateway rate-limits. Measured 2026-11-13: six slugs in quick
#: succession returned HTTP 429, and the same slugs answered fine 3s
#: apart. A 24/7 poller MUST back off rather than hammer, so 429 is
#: handled separately from a transport error -- it is the venue asking us
#: to slow down, not a failure, and retrying it immediately makes it worse.
class RateLimited(Exception):
    pass


def bbo(slug, timeout=8.0, retries=3):
    """GET only. Returns (bid, ask, state, settlement_px) or Nones.

    Retries 429 with exponential backoff; raises RateLimited if it will
    not clear. Never sends anything but a GET and holds no credentials.
    """
    url = "%s/v1/markets/%s/bbo" % (GATEWAY, slug)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                doc = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == retries:
                if exc.code == 429:
                    raise RateLimited(str(exc.code)) from exc
                raise
            time.sleep(delay)
            delay *= 2
    md = doc.get("marketData") or doc
    return (dec(md.get("bestBid")), dec(md.get("bestAsk")),
            str(md.get("state") or ""), dec(md.get("settlementPx")))


def own_price(side, bid, ask):
    """UP is the YES bid; DOWN is 1 - the YES ask. The live convention."""
    if bid is None or ask is None:
        return None
    return bid if side == UP else D(1) - ask


def run_window(ws, price, verbose=False):
    """Watch ONE window. Returns the single summary row, or None."""
    slug = slug_for(ws)
    filled = {}
    polls = reads = 0
    deepest = {UP: None, DOWN: None}
    stop_at = ws + dt.timedelta(seconds=WINDOW_S - CANCEL_LEAD_S)

    while dt.datetime.now(UTC) < stop_at:
        polls += 1
        try:
            bid, ask, state, _px = bbo(slug)
        except RateLimited:
            time.sleep(POLL_S * 4)          # the venue asked us to slow down
            continue
        except Exception:                                      # noqa: BLE001
            time.sleep(POLL_S)
            continue
        if bid is not None and ask is not None:
            reads += 1
            for side in (UP, DOWN):
                p = own_price(side, bid, ask)
                if p is None:
                    continue
                if deepest[side] is None or p < deepest[side]:
                    deepest[side] = p
                if side not in filled and p <= price:
                    t = (dt.datetime.now(UTC) - ws).total_seconds()
                    filled[side] = round(t, 1)
                    if verbose:
                        emit(TAG + "_FILL",
                             {"market": slug, "side": side, "t": filled[side],
                              "own_price": str(p)})
        time.sleep(POLL_S)

    #: the venue's own settlement, polled until terminal or we give up
    won = px = None
    deadline = dt.datetime.now(UTC) + dt.timedelta(seconds=180)
    while dt.datetime.now(UTC) < deadline:
        try:
            _b, _a, state, px = bbo(slug)
        except RateLimited:
            time.sleep(20.0)
            continue
        except Exception:                                      # noqa: BLE001
            time.sleep(5.0)
            continue
        if px is not None and px in (D(0), D(1)):
            won = UP if px == D(1) else DOWN
            break
        time.sleep(5.0)

    if won is None:
        emit(TAG + "_UNRESOLVED", {"market": slug, "polls": polls,
                                   "reads": reads,
                                   "note": "no binary settlementPx seen"})
        return None

    #: linear in legs. No pairing arithmetic needed, and none assumed.
    pnl = D(0)
    for side in filled:
        pnl += (D(1) if side == won else D(0)) - price
    case = {2: "BOTH", 1: "ONE", 0: "NONE"}[len(filled)]
    row = {"market": slug, "window_start": ws.isoformat(),
           "price": str(price), "case": case,
           "up_t": filled.get(UP), "down_t": filled.get(DOWN),
           "won": won, "pnl_per_share": str(pnl),
           "deepest_up": str(deepest[UP]) if deepest[UP] is not None else None,
           "deepest_down": (str(deepest[DOWN])
                            if deepest[DOWN] is not None else None),
           "polls": polls, "reads": reads,
           "labels": "SHADOW. no order was placed. pnl_per_share is "
                     "settlement arithmetic on hypothetical fills, per "
                     "share a side, and is NOT realized anything."}
    emit(TAG, row)
    return row


def tally(path):
    """Read back a file of emitted lines and report the edge so far."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith(TAG + " "):
                continue
            try:
                rows.append(json.loads(line[len(TAG) + 1:]))
            except ValueError:
                continue
    if not rows:
        print("no %s rows in %s" % (TAG, path))
        return 1
    v = [float(r["pnl_per_share"]) for r in rows]
    n = len(v)
    m = statistics.fmean(v)
    se = (statistics.stdev(v) / math.sqrt(n)) if n > 1 else 0.0
    t = (m / se) if se else 0.0
    both = sum(1 for r in rows if r["case"] == "BOTH")
    one = sum(1 for r in rows if r["case"] == "ONE")
    print("windows %d   BOTH %d (%.1f%%)   ONE %d (%.1f%%)   NONE %d"
          % (n, both, 100 * both / n, one, 100 * one / n, n - both - one))
    print("net %+.2f c/w a share   se %.2f   t %+.2f" % (100 * m, 100 * se, t))
    for target in (2.0, 3.0):
        if m > 0 and se > 0:
            need = int(math.ceil((target * se * math.sqrt(n) / m) ** 2))
            print("  t=%.0f needs ~%d windows  (%.1f days at 288/day)"
                  % (target, need, need / 288.0))
    print()
    print("REFERENCE, 189 recorded windows: BOTH 10.2%%, net +3.74 c/w, "
          "t +1.67")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--price", default="0.08")
    ap.add_argument("--windows", type=int, default=0,
                    help="0 = run forever")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--tally", help="read back a log file and report")
    a = ap.parse_args()
    if a.tally:
        return tally(a.tally)

    price = dec(a.price)
    if price is None or not (D(0) < price < D("0.50")):
        print("price must be inside (0, 0.50): at or above 0.50 a pair "
              "costs >= 1.00 and cannot profit")
        return 2

    emit(TAG + "_START", {"gateway": GATEWAY, "price": str(price),
                          "window_s": WINDOW_S,
                          "cancel_lead_s": CANCEL_LEAD_S,
                          "poll_s": POLL_S,
                          "windows": a.windows or "forever",
                          "provenance": "public gateway, GET only, no "
                                        "credentials, no order path"})
    done = 0
    while a.windows == 0 or done < a.windows:
        now = dt.datetime.now(UTC)
        ws = window_start(now)
        #: only start a window we can see most of
        if (now - ws).total_seconds() > 30.0:
            ws = ws + dt.timedelta(seconds=WINDOW_S)
            time.sleep(max(1.0, (ws - dt.datetime.now(UTC)).total_seconds()))
        run_window(ws, price, verbose=a.verbose)
        done += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
