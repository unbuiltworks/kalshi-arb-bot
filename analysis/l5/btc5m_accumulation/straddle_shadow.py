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
#: ADAPTIVE POLLING. For most of a window the price is nowhere near the
#: resting level and a 2s sample is a wasted request -- and those wasted
#: requests were buying 429s that blocked the settlement reads. So poll
#: FAST only when a fill is plausible and slow down when it is not. This
#: cuts request volume substantially while IMPROVING resolution exactly
#: where the fill rule needs it.
POLL_NEAR_S = 2.0          # within NEAR_BAND of the resting price
POLL_FAR_S = 6.0           # both sides far away; nothing can fill
NEAR_BAND = D("0.06")
#: A WINDOW WE COULD NOT SEE IS NOT AN OBSERVATION. Measured 2026-11-13:
#: 0130z was rate-limited out completely, reads=0, and still emitted
#: "case NONE, pnl 0" as though that were data. It is not -- a window with
#: no reads cannot know whether a fill happened, and reporting it as NONE
#: silently drags BOTH the pair rate and the bleed toward zero. Three
#: hundred windows polluted that way would be worthless, and worse, they
#: would look like a clean null result. Below this many reads the window is
#: emitted under a different tag that `--tally` does not count.
MIN_READS = 30
POLL_S = POLL_NEAR_S       # the settle-loop backoff still keys off this
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
            str(md.get("state") or ""), dec(md.get("settlementPx")),
            dec(md.get("sharesTraded")), dec(md.get("bidShares")),
            dec(md.get("askShares")))


def own_price(side, bid, ask):
    """UP is the YES bid; DOWN is 1 - the YES ask. The live convention."""
    if bid is None or ask is None:
        return None
    return bid if side == UP else D(1) - ask


def watch_window(ws, price, verbose=False):
    """Watch ONE window. Returns the single summary row, or None."""
    slug = slug_for(ws)
    filled = {}
    polls = reads = rate_limited = 0
    deepest = {UP: None, DOWN: None}
    #: READ DENSITY IS A DATA-QUALITY SIGNAL, not a curiosity. The tape the
    #: fill rule was validated on samples every ~2s; a gap of 10s can hide
    #: a brief dip to the resting price entirely, which UNDER-counts fills.
    #: That bias is conservative -- it cannot manufacture an edge -- but a
    #: sparse window is weaker evidence than a dense one, so the gap is
    #: reported and a window can be filtered on it later rather than
    #: silently averaged in.
    last_read = None
    max_gap = 0.0
    #: WOULD SIZE HAVE FILLED? Every figure in this analysis comes from a
    #: rule that fills us whenever the price touches our level, with no
    #: queue position and no size. That is fine for one share and unproven
    #: for forty-five. The decisive quantity is how much volume trades
    #: THROUGH our level while we would be resting there: hundreds of
    #: shares and size fills, three shares and it does not.
    #:
    #: And the asymmetry matters more than the total. The one-sided toxic
    #: fill is informed sellers dumping onto a bid worth 2.5c -- they take
    #: that at any size. The pair-completing fill is incidental. So this
    #: records volume-while-fillable PER SIDE, because size could raise the
    #: bleed faster than the pairs, which is the direction that kills it.
    traded = {UP: D(0), DOWN: D(0)}
    touch_shares = {UP: None, DOWN: None}
    last_vol = None
    stop_at = ws + dt.timedelta(seconds=WINDOW_S - CANCEL_LEAD_S)

    while dt.datetime.now(UTC) < stop_at:
        polls += 1
        try:
            bid, ask, state, _px, vol, bshares, ashares = bbo(slug)
        except RateLimited:
            rate_limited += 1
            time.sleep(POLL_S * 4)          # the venue asked us to slow down
            continue
        except Exception:                                      # noqa: BLE001
            time.sleep(POLL_S)
            continue
        if bid is not None and ask is not None:
            reads += 1
            nowt = dt.datetime.now(UTC)
            if last_read is not None:
                max_gap = max(max_gap, (nowt - last_read).total_seconds())
            last_read = nowt
            dvol = D(0)
            if vol is not None:
                if last_vol is not None and vol >= last_vol:
                    dvol = vol - last_vol
                last_vol = vol
            for side in (UP, DOWN):
                p = own_price(side, bid, ask)
                if p is None:
                    continue
                if deepest[side] is None or p < deepest[side]:
                    deepest[side] = p
                if p <= price:
                    #: we would be AT or INSIDE the touch here, so this
                    #: window's volume is volume we could have been filled by
                    traded[side] += dvol
                    sh = bshares if side == UP else ashares
                    if sh is not None and (touch_shares[side] is None
                                           or sh < touch_shares[side]):
                        touch_shares[side] = sh
                if side not in filled and p <= price:
                    t = (dt.datetime.now(UTC) - ws).total_seconds()
                    filled[side] = round(t, 1)
                    if verbose:
                        emit(TAG + "_FILL",
                             {"market": slug, "side": side, "t": filled[side],
                              "own_price": str(p)})
        #: how close is a fill? The nearest own price decides the cadence.
        near = None
        for side in (UP, DOWN):
            pp = own_price(side, bid, ask)
            if pp is not None and (near is None or pp < near):
                near = pp
        time.sleep(POLL_NEAR_S if (near is not None and near <= price + NEAR_BAND)
                   else POLL_FAR_S)

    #: SETTLEMENT IS NOT WAITED FOR HERE. Blocking on it cost us every
    #: other window: the venue can take minutes to publish settlementPx,
    #: and by the time it did the next window had already opened. Observed
    #: 2026-11-13 -- a two-window run captured 0110z and 0120z and skipped
    #: 0115z entirely, which over a day is 144 windows instead of 288 and
    #: doubles the time to a verdict. So this returns the OBSERVATION and
    #: the caller settles it later, out of band.
    return {"slug": slug, "ws": ws, "price": price, "filled": filled,
            "deepest": deepest, "polls": polls, "reads": reads,
            "rate_limited": rate_limited, "max_gap": max_gap,
            "traded": traded, "touch_shares": touch_shares}


def try_settle(slug):
    """The venue's own settlement, or None if not published yet.

    RETRIES 429. With retries=0 a single rate-limit answer made this return
    None, drain treated that as "not settled yet", and the next sweep was a
    window away -- so a window could stay unsettled indefinitely while the
    venue had published it all along. Observed 2026-11-13: 0135z read
    EXPIRED with settlementPx 0.0000 from a plain curl while the running
    poller had emitted nothing for three windows, because its own 2s
    polling was generating the 429s that blocked its settlement reads. The
    poller was starving itself.
    """
    try:
        _b, _a, _state, px = bbo(slug, retries=3)[:4]
    except Exception:                                          # noqa: BLE001
        return None
    if px is not None and px in (D(0), D(1)):
        return UP if px == D(1) else DOWN
    return None


def finish(obs, won):
    """Turn a settled observation into the single summary row."""
    ws, price, filled = obs["ws"], obs["price"], obs["filled"]
    deepest, slug = obs["deepest"], obs["slug"]
    reads, max_gap = obs["reads"], obs["max_gap"]
    #: linear in legs. No pairing arithmetic needed, and none assumed.
    pnl = D(0)
    for side in filled:
        pnl += (D(1) if side == won else D(0)) - price
    case = {2: "BOTH", 1: "ONE", 0: "NONE"}[len(filled)]
    polls = obs["polls"]
    usable = reads >= MIN_READS
    rate_limited = obs["rate_limited"]
    row = {"market": slug, "window_start": ws.isoformat(),
           "price": str(price), "case": case,
           "up_t": filled.get(UP), "down_t": filled.get(DOWN),
           "won": won, "pnl_per_share": str(pnl),
           "deepest_up": str(deepest[UP]) if deepest[UP] is not None else None,
           "deepest_down": (str(deepest[DOWN])
                            if deepest[DOWN] is not None else None),
           "vol_while_fillable_up": str(obs["traded"][UP]),
           "vol_while_fillable_down": str(obs["traded"][DOWN]),
           "min_touch_shares_up": (str(obs["touch_shares"][UP])
                                   if obs["touch_shares"][UP] is not None
                                   else None),
           "min_touch_shares_down": (str(obs["touch_shares"][DOWN])
                                     if obs["touch_shares"][DOWN] is not None
                                     else None),
           "polls": polls, "reads": reads,
           "rate_limited": rate_limited,
           "max_read_gap_s": round(max_gap, 1),
           "mean_read_gap_s": (round((WINDOW_S - CANCEL_LEAD_S) / reads, 1)
                               if reads else None),
           "usable": usable,
           "labels": "SHADOW. no order was placed. pnl_per_share is "
                     "settlement arithmetic on hypothetical fills, per "
                     "share a side, and is NOT realized anything."}
    #: an unseen window goes out under its own tag, so no tally can count
    #: it as a zero and no reader can mistake it for a measurement.
    emit(TAG if usable else TAG + "_UNUSABLE",
         row if usable else dict(row, why="FEWER_THAN_%d_READS" % MIN_READS))
    return row


#: how long to keep asking the venue about a closed window before giving up
SETTLE_PATIENCE_S = 1800.0


def drain(pending, until=None):
    """Try to settle each closed window once. Returns what is still open.

    Costs one GET per pending window per sweep, so the backlog clears
    without ever delaying the NEXT window -- which is the whole point of
    separating this from `watch_window`. `until` stops the sweep early so
    the pre-window wait is never overrun.
    """
    still = []
    for obs in pending:
        if until is not None and dt.datetime.now(UTC) >= until:
            still.append(obs)
            continue
        won = try_settle(obs["slug"])
        if won is not None:
            finish(obs, won)
            continue
        age = (dt.datetime.now(UTC) - obs["ws"]).total_seconds()
        if age > SETTLE_PATIENCE_S:
            #: a window the venue never settled is reported as such, never
            #: guessed from a quote -- an unknown outcome contributes
            #: nothing to the edge and must not be inferred.
            emit(TAG + "_UNRESOLVED",
                 {"market": obs["slug"], "reads": obs["reads"],
                  "age_s": round(age, 0),
                  "note": "no binary settlementPx within patience"})
            continue
        still.append(obs)
    return still


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
    #: data quality first. A window sampled every 10s can miss a brief dip
    #: to the resting price, which UNDER-counts fills -- conservative, but
    #: it makes the window weaker evidence, so say so before the figures.
    gaps = [r.get("max_read_gap_s") for r in rows
            if isinstance(r.get("max_read_gap_s"), (int, float))]
    if gaps:
        sparse = sum(1 for g in gaps if g > 10.0)
        print("read gaps: median %.1fs   worst %.1fs   windows over 10s: "
              "%d of %d" % (statistics.median(gaps), max(gaps), sparse,
                            len(gaps)))
        if sparse:
            print("  (a sparse window can miss a brief dip to the price, so "
                  "fills are under-counted there, never over-counted)")
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
    pending = []
    done = 0
    while a.windows == 0 or done < a.windows:
        now = dt.datetime.now(UTC)
        ws = window_start(now)
        #: only start a window we can see most of
        if (now - ws).total_seconds() > 30.0:
            ws = ws + dt.timedelta(seconds=WINDOW_S)
            #: the wait before the next window is the natural moment to
            #: clear the settlement backlog, so it costs no coverage.
            pending = drain(pending, until=ws)
            left = (ws - dt.datetime.now(UTC)).total_seconds()
            if left > 0:
                time.sleep(left)
        obs = watch_window(ws, price, verbose=a.verbose)
        if obs is not None:
            pending.append(obs)
            done += 1
        pending = drain(pending)
    #: one last sweep so a bounded run does not exit holding observations
    deadline = dt.datetime.now(UTC) + dt.timedelta(seconds=240)
    while pending and dt.datetime.now(UTC) < deadline:
        pending = drain(pending)
        if pending:
            time.sleep(10.0)
    for obs in pending:
        emit(TAG + "_UNRESOLVED",
             {"market": obs["slug"], "reads": obs["reads"],
              "note": "no binary settlementPx seen before giving up"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
