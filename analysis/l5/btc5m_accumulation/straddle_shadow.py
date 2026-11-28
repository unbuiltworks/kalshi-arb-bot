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
#: the series and its window length, both configurable so the SAME
#: validated collector can watch btc-updown-15m. Window length is the
#: one dimension never varied, and window_length.py models the 15m pair
#: rate at 29.2% against 5m's 10.2% -- a round trip is a hitting-time
#: problem and 15 minutes gives it three times as long. L5 itself trades
#: btc-updown-15m alongside the 5m. That is a model, though: this is how
#: it gets measured on real quotes.
SERIES = os.environ.get("SHADOW_SERIES", "cpc-btc-updown-5m")
WINDOW_S = float(os.environ.get("SHADOW_WINDOW_S", "300"))
CANCEL_LEAD_S = 30.0
#: ADAPTIVE POLLING. For most of a window the price is nowhere near the
#: resting level and a 2s sample is a wasted request -- and those wasted
#: requests were buying 429s that blocked the settlement reads. So poll
#: FAST only when a fill is plausible and slow down when it is not. This
#: cuts request volume substantially while IMPROVING resolution exactly
#: where the fill rule needs it.
POLL_NEAR_S = 2.0          # within NEAR_BAND of the resting price
POLL_FAR_S = 2.0           # was 6.0; see MEASURED note below
NEAR_BAND = D("0.50")      # effectively always near; kept as a knob
#: a window sampled worse than this gets its own warning line
GAP_WARN_S = 10.0
#: MEASURED IN PRODUCTION 2026-11-13, and it reversed the decision above.
#: The first settled window reported rate_limited 0 across 61 polls: the
#: venue was not pushing back at all. The 429s that motivated throttling
#: came from a session's BURST queries during testing, not from steady
#: polling, and the public recorder has sustained 2s for 27 hours.
#:
#: The throttle cost real accuracy: that window sampled at a 6.8s mean
#: and a 16.1s WORST gap, against the ~2s tape the fill rule was
#: validated on. A 16s hole hides a dip to the resting price entirely,
#: so fills are UNDER-counted -- which biases the PAIR RATE DOWNWARD.
#: That is not a safe direction: the pair case carries the whole edge, so
#: a real 10.2%% could measure 7%% and be called dead on an artefact of
#: our own sampling. Under-counting the bleed would be conservative;
#: under-counting the pairs is simply wrong.
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
    return "%s-%s" % (SERIES, ws.strftime("%Y-%m-%d-%H%Mz"))


def window_start(now):
    """Floor to the series boundary -- 5 or 15 minutes, from WINDOW_S."""
    m = max(1, int(WINDOW_S // 60))
    return now.replace(second=0, microsecond=0,
                       minute=(now.minute // m) * m)


#: the gateway rate-limits. Measured 2026-11-13: six slugs in quick
#: succession returned HTTP 429, and the same slugs answered fine 3s
#: apart. A 24/7 poller MUST back off rather than hammer, so 429 is
#: handled separately from a transport error -- it is the venue asking us
#: to slow down, not a failure, and retrying it immediately makes it worse.
class RateLimited(Exception):
    pass


def _get(url, timeout=8.0, retries=3):
    """GET a JSON document. Retries 429 with backoff; raises RateLimited."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == retries:
                if exc.code == 429:
                    raise RateLimited(str(exc.code)) from exc
                raise
            time.sleep(delay)
            delay *= 2
    raise RateLimited("429")


def quote(slug, timeout=8.0):
    """The live touch: (bid, ask). GET only.

    USES THE RECORDER'S ENDPOINT, and the distinction cost most of a night.
    `/v1/markets/{slug}/bbo` -- which carries settlementPx, depth and volume
    -- returns HTTP 404 for a market while that market is OPEN. Measured
    2026-11-13: twenty rapid probes of the live window all failed, and a
    spaced probe at age 195s still 404'd, while the four EXPIRED windows
    behind it all answered. My shadow therefore polled a dead URL half the
    time and sampled at a 5.7s mean gap.

    `/v1/market/slug/{slug}` (singular, no /bbo) answers for an OPEN market
    and is what `btc5m_public_recorder.py` has always used. Its tape proves
    the cadence is achievable: 189 windows, median 147 observations each,
    gap median 2.0s, p95 2.1s, SIX gaps over 10s out of 27,236. That is the
    density the fill rule was validated at, so matching the endpoint is what
    makes the shadow's pair rate comparable to the 10.2% baseline rather
    than merely similar.

    What this endpoint does NOT carry: settlementPx, sharesTraded or any
    depth. Settlement comes from `settlement()` below, and volume-while-
    fillable is simply not obtainable from public data on a live window --
    see the note at `vol_while_fillable_*`.
    """
    doc = _get("%s/v1/market/slug/%s" % (GATEWAY, slug), timeout=timeout)
    m = doc.get("market") or doc
    return dec(m.get("bestBidQuote")), dec(m.get("bestAskQuote"))


def traded_now(slug, timeout=8.0):
    """Cumulative `sharesTraded` for an OPEN window, or None.

    SETTLEMENT IS NEVER TAKEN FROM HERE and never from /book. /book's
    stats carry `settlementPx` 0.0000 on an OPEN market rather than null,
    so reading an outcome from it resolves every open window to DOWN with
    total confidence and no error. /bbo returns settlementPx None while
    open, which is why `settlement()` below uses it and this does not
    touch the field at all.

    None on any failure, which keeps the old behaviour exactly: a missing
    volume reads as unknown, never as zero.
    """
    try:
        doc = _get("%s/v1/markets/%s/bbo" % (GATEWAY, slug), timeout=timeout)
    except BaseException:                                      # noqa: BLE001
        return None
    st = (doc or {}).get("marketData") or (doc or {}).get("stats") or {}
    v = st.get("sharesTraded")
    try:
        return D(str(v)) if v is not None else None
    except Exception:                                          # noqa: BLE001
        return None


def settlement(slug, timeout=8.0):
    """The venue's own settlementPx, or None. Works once EXPIRED.

    This is the /bbo endpoint, which is useless while the window is open
    and correct once it is not -- the opposite of `quote()`. Verified
    against the live service's own settle() on five of five windows.
    """
    doc = _get("%s/v1/markets/%s/bbo" % (GATEWAY, slug), timeout=timeout)
    md = doc.get("marketData") or doc
    return dec(md.get("settlementPx"))


def own_price(side, bid, ask):
    """UP is the YES bid; DOWN is 1 - the YES ask. The live convention."""
    if bid is None or ask is None:
        return None
    return bid if side == UP else D(1) - ask


def watch_window(ws, price, verbose=False):
    """Watch ONE window. Returns the single summary row, or None."""
    slug = slug_for(ws)
    emit(TAG + "_WINDOW_OPEN", {"market": slug,
                                "window_start": ws.isoformat(),
                                "price": str(price)})
    filled = {}
    polls = reads = rate_limited = 0
    errors = {}
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
    #: THE COMPLETION CURVE, captured in the same pass. A pair does not need
    #: to cost 2x the entry -- it needs to cost under 1.00 -- so once one
    #: leg fills, the other can be bought far higher and still lock profit.
    #: On the recorded tape, completing at 0.20 instead of 0.08 raised the
    #: pair rate from 10.2% to 12.8% at the same net, with lower variance
    #: and much better out-of-sample stability. That finding came from 14
    #: arms on ONE tape and badly needs fresh data.
    #:
    #: Recording the opposite side's DEEPEST price AFTER the first fill is
    #: all that is needed: offline, a pair forms at completion price c iff
    #: that value is <= c. One number tests every completion price, so the
    #: whole curve comes free with the same windows.
    first_fill = None               # (side, t) of whichever filled first
    #: SAME-SIDE PEAK AFTER THE FILL. exit_on_recovery.py is the only
    #: arm in this program to clear its own search bar (t +2.69 against
    #: a 2.04 bar for 8 arms): 31% of legs that collapse to 8c RECOVER
    #: to 12c inside the window, and selling into that recovery as a
    #: maker turns -8c into +4c. Offers fill when the price RISES to
    #: them, so it is mechanically available where buying the winner is
    #: not. The collector recorded only the OPPOSITE side's low, which
    #: is the wrong quantity for it -- so the best finding we have could
    #: not be checked against live data at all.
    own_peak_after = None           # that side's HIGH after it filled
    opp_deepest_after = None        # other side's low after that moment
    stop_at = ws + dt.timedelta(seconds=WINDOW_S - CANCEL_LEAD_S)

    while dt.datetime.now(UTC) < stop_at:
        polls += 1
        try:
            bid, ask = quote(slug)
            bshares = ashares = None
            #: VOLUME, WHICH WAS NEVER ACTUALLY UNAVAILABLE. This line was
            #: `vol = None` for 199 windows on the belief, recorded in
            #: `quote()` below and repeated in every report built on it,
            #: that /bbo 404s while a window is OPEN. MEASURED
            #: 2026-11-14T13:50Z on a live cpc-btc-updown-5m window: /bbo
            #: answers 200 with sharesTraded 108701.31, lastTradePx 0.0800,
            #: openInterest 24198.57 and settlementPx None. The 404 was
            #: real when it was first seen and is not real now.
            #:
            #: It matters because `traded[side]` below already accumulates
            #: volume-through-our-level and was written for exactly this
            #: input -- so the one number that answers "would our size have
            #: filled?" was an endpoint away the whole time, and the
            #: question was instead worked around with post-expiry data.
            vol = traded_now(slug)
        except RateLimited:
            rate_limited += 1
            time.sleep(POLL_S * 4)          # the venue asked us to slow down
            continue
        except Exception as exc:                               # noqa: BLE001
            #: NEVER SWALLOW THIS SILENTLY. A bare continue here is how the
            #: first deployment managed to poll for 24 minutes and log
            #: nothing: every call was failing and the loop just spun. The
            #: reason is recorded and the FIRST one is emitted, so a broken
            #: collector says so within seconds instead of looking idle.
            why = "%s:%s" % (type(exc).__name__,
                             getattr(exc, "code", "") or "")
            errors[why] = errors.get(why, 0) + 1
            if sum(errors.values()) == 1:
                emit(TAG + "_READ_ERROR", {"market": slug, "why": why,
                                           "note": "first failure this "
                                                   "window"})
            time.sleep(POLL_S)
            continue
        if bid is not None and ask is not None:
            reads += 1
            nowt = dt.datetime.now(UTC)
            if last_read is not None:
                max_gap = max(max_gap, (nowt - last_read).total_seconds())
            last_read = nowt
            #: track the opposite side's low once one leg has filled
            if first_fill is not None:
                opp = DOWN if first_fill[0] == UP else UP
                op = own_price(opp, bid, ask)
                if op is not None and (opp_deepest_after is None
                                       or op < opp_deepest_after):
                    opp_deepest_after = op
                #: and the filled side's own recovery high
                sp = own_price(first_fill[0], bid, ask)
                if sp is not None and (own_peak_after is None
                                       or sp > own_peak_after):
                    own_peak_after = sp
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
                    #: DEAD ON PURPOSE, AND LEFT SAYING SO. `bshares` and
                    #: `ashares` are bound to None a few lines up and are
                    #: never assigned, because `/v1/market/slug/{slug}` --
                    #: the only endpoint that answers for an OPEN window --
                    #: carries no depth at all. The /bbo endpoint that does
                    #: carry it 404s until the window EXPIRES. So this
                    #: tracked a quantity it could never observe and
                    #: `min_touch_shares_*` has been null on every row ever
                    #: emitted, pair windows included.
                    #:
                    #: IT IS NOT WORTH FIXING, which is the part worth
                    #: recording. The question it existed to answer -- "was
                    #: there depth to fill our size on both sides?" -- was
                    #: answered another way on 2026-11-14 from post-expiry
                    #: /bbo `sharesTraded`: 163,023 shares on the 0255z pair
                    #: window and 219,628 on 0345z. At 8 shares we are
                    #: 0.005% of a window's volume, and live confirms it --
                    #: one leg filled promptly in 19 of 19 placing windows.
                    #: Depth is not the binding constraint at our size, so
                    #: the pair rate is a genuine price-path property and
                    #: the shadow's rate is NOT inflated by unfillable
                    #: touches. Chasing per-level depth would buy nothing.
                    pass
                if side not in filled and p <= price:
                    t = (dt.datetime.now(UTC) - ws).total_seconds()
                    filled[side] = round(t, 1)
                    if first_fill is None:
                        first_fill = (side, round(t, 1))
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
    emit(TAG + "_WINDOW_CLOSE",
         {"market": slug, "reads": reads, "polls": polls,
          "rate_limited": rate_limited, "errors": errors,
          "filled": sorted(filled), "awaiting_settlement": True})
    return {"slug": slug, "ws": ws, "price": price, "filled": filled,
            "deepest": deepest, "polls": polls, "reads": reads,
            "rate_limited": rate_limited, "max_gap": max_gap,
            "errors": errors,
            "traded": traded, "touch_shares": touch_shares,
            "first_fill": first_fill,
            "opp_deepest_after": opp_deepest_after,
            "own_peak_after": own_peak_after}


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
        px = settlement(slug)
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
           "first_fill_side": (obs["first_fill"][0]
                               if obs["first_fill"] else None),
           "first_fill_t": (obs["first_fill"][1]
                            if obs["first_fill"] else None),
           "opp_deepest_after_first": (str(obs["opp_deepest_after"])
                                       if obs["opp_deepest_after"] is not None
                                       else None),
           #: tests every exit level offline: a leg exits at X iff this >= X
           "own_peak_after_first": (str(obs.get("own_peak_after"))
                                    if obs.get("own_peak_after") is not None
                                    else None),
           #: NOT MEASURED, and reported as null rather than 0. The only
           #: public endpoint carrying sharesTraded is /bbo, which 404s
           #: while the window is OPEN -- so volume-through-our-level,
           #: the quantity that would answer whether 45 shares fills,
           #: cannot be had from public data at all. A 0 here would read
           #: as "no volume passed through", which is the same fake-zero
           #: mistake as a rate-limited window reporting case NONE.
           #: Answering the scaling question needs the authenticated
           #: book, which this process deliberately has no key for.
           #: NOW MEASURED. /bbo answers 200 on an OPEN window (verified
           #: 2026-11-14), so this is volume that passed through while our
           #: resting price was at or inside the touch -- the quantity
           #: that answers whether our size would have filled.
           "vol_while_fillable_up": (str(obs["traded"][UP])
                                     if obs["traded"][UP] is not None
                                     else None),
           "vol_while_fillable_down": (str(obs["traded"][DOWN])
                                       if obs["traded"][DOWN] is not None
                                       else None),
           "vol_unavailable_reason": None,
           #: ALWAYS NULL, and now says why in the row itself rather
           #: than leaving a reader to guess. See the note at the
           #: touch-depth block above: no OPEN-window endpoint carries
           #: depth, and the question is answered without it.
           "min_touch_shares_up": None,
           "min_touch_shares_down": None,
           "touch_depth_unavailable_reason": "NO_DEPTH_ON_OPEN_WINDOW",
           "polls": polls, "reads": reads,
           "rate_limited": rate_limited,
           "read_errors": obs.get("errors") or {},
           "max_read_gap_s": round(max_gap, 1),
           "mean_read_gap_s": (round((WINDOW_S - CANCEL_LEAD_S) / reads, 1)
                               if reads else None),
           "usable": usable,
           "labels": "SHADOW. no order was placed. pnl_per_share is "
                     "settlement arithmetic on hypothetical fills, per "
                     "share a side, and is NOT realized anything."}
    #: an unseen window goes out under its own tag, so no tally can count
    #: it as a zero and no reader can mistake it for a measurement.
    if usable and max_gap > GAP_WARN_S:
        #: usable but coarse. Its own line, because a hole this big
        #: under-counts fills and so under-states the pair rate.
        emit(TAG + "_COARSE",
             {"market": slug, "max_read_gap_s": round(max_gap, 1),
              "reads": reads,
              "note": "a gap this large can hide a dip to the resting "
                      "price; fills under-counted, never over"})
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
                          "series": SERIES,
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
