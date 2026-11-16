"""Forward recorder of the SYNCHRONIZED BTC5m decision dataset. READ-ONLY.

Replaces the dead `btc-ref-collector` by folding its job into the recorder
that already runs: one process, one volume, one service. It polls the
venue's PUBLIC market endpoint for the 5-minute window and the venue's
PUBLIC 1-second BRTI average, and writes one `BTC5M_ALIGNED` row per poll
carrying everything a decision needed at the moment it was made --
market/window id, UP and DOWN bid/ask, depth, strike, time remaining, the
index, an INDEPENDENT probability with its model version, and the source
timestamps and freshness of each. Settlement is written once the venue
states it.

NO CREDENTIALS, NO ORDERS, NO ACCOUNT DATA. Two unauthenticated GETs. It
imports nothing from the execution path and cannot reach one: it holds no
transport and never constructs one. BTC5m and BTC15m execution behaviour is
untouched by its presence.

HEALTH IS NEVER ASSUMED. Every poll also writes a `BTC5M_HEALTH` row whose
`ok` is True only when every input it needs was actually read and fresh:
the market record, a two-sided quote, the strike, an index bucket no older
than `l5_btc_reference.MAX_AGE_S`, and a probability the model was willing
to state. Anything missing or stale sets `ok` False and names the component
and the reason, and `consecutive_bad` counts how long it has been so. A
silent feed therefore shows as a FAILING health row rather than as no row
at all, and a monitor watching for `"ok": false` or for the absence of
rows will see both cases.

DEPTH. The public market endpoint publishes no size at any level, so depth
is written as null with `depth_unavailable_reason`. It is never guessed.
Should the venue begin publishing sizes, `_depth` is the one place to read
them.

FILLS. This process sees no fills -- it holds no account. The field is
written empty with its reason so the schema matches the replay's and a
fill-aware writer can fill it in later.

usage: python3 analysis/l5/btc5m_aligned_recorder.py OUT.jsonl [HOURS]
       OUT of "-" writes to stdout, which is how the service keeps them.
       HOURS of 0 runs until stopped.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal as D
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from src import l5_btc_reference as REF
from src import l5_late_favorite as LF        # noqa: E402
from src import l5_series_registry as REG     # noqa: E402

GATEWAY = "https://gateway.polymarket.us"
MARKET_URL = GATEWAY + "/v1/market/slug/"
#: `%s` is the asset, `%d` the epoch. The asset and its index symbol
#: come from `l5_series_registry`, together: an index that did not belong
#: to the asset would record the wrong series while looking healthy.
INDEX_URL = (GATEWAY + "/v1/asset-prices/history?assets=crypto:%s"
             "&bucket=ASSET_PRICE_BUCKET_1S&from=%d")
EVERY_S = 2.0
WINDOW_S = 300.0
#: how much index history to hold: the volatility estimate needs 900 s
INDEX_KEEP_S = 1200
#: the first poll fetches this much history so a probability is available
#: from the first row rather than after fifteen minutes of warm-up
INDEX_WARMUP_S = 1500
SETTLE_AFTER_S = 330.0
SETTLE_GIVE_UP_S = 1800.0
UTC = dt.timezone.utc
SCHEMA = "btc5m_aligned/1"

DEPTH_REASON = (
    "the venue's PUBLIC market endpoint publishes no size at any level "
    "(bestBidQuote/bestAskQuote and marketSides carry prices only); depth "
    "needs the authenticated book, which this read-only path does not touch")
FILLS_REASON = (
    "this recorder holds no account and sees no fills; the field exists so a "
    "fill-aware writer can populate it")


def _get(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": "research-client"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as fh:  # noqa: S310
        return json.loads(fh.read().decode()), time.time() - t0


#: EVERY FIVE-MINUTE SERIES, from the registry. One process records all
#: four: the recorder already runs as one service on one volume, and
#: four processes would need three more services for no benefit -- the
#: polls are two cheap unauthenticated GETs an asset.
LANES = tuple(r for r in REG.REGISTRY if r.horizon_s == WINDOW_S)


def slug_for(start: dt.datetime, prefix: str = "cpc-btc-updown-5m-") -> str:
    return "%s%s-%sz" % (prefix, start.strftime("%Y-%m-%d"),
                         start.strftime("%H%M"))


def window_start(now: dt.datetime) -> dt.datetime:
    return now.replace(minute=(now.minute // 5) * 5, second=0, microsecond=0)


def _q(v):
    """The venue's {"value": ...} money shape, as a float or None."""
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


#: the margin a recorded opportunity must clear. Recording only; it
#: gates nothing and places nothing.
RECORD_MARGIN = D("0.005")
#: what the recorder cannot know, and why. Never a number.
FILLABLE_REASON = ("no public size at any level, so queue position and "
                   "fill probability are unobservable from this feed")
OUR_FILL_REASON = "this process holds no account and sees no fills"


def _candidates(p, up_bid, up_ask):
    """Each maker level priced at THIS poll's probability.

    Uses `l5_late_favorite.ev_per_share` and `break_even_p`, so the
    recorded economics and the live gate can never drift apart -- a
    second formula here would be a second answer.
    """
    out = {"levels": [], "fillable": None, "fillable_reason": FILLABLE_REASON,
           "our_fill": None, "our_fill_reason": OUR_FILL_REASON,
           "margin_used": str(RECORD_MARGIN)}
    pv = p.get("p_up") if p.get("ok") else None
    if pv is None or up_bid is None or up_ask is None:
        out["reason"] = "NO_PROBABILITY" if pv is None else "NO_TWO_SIDED_QUOTE"
        return out
    try:
        pu = D(str(pv))
    except Exception:                                          # noqa: BLE001
        out["reason"] = "NO_PROBABILITY"
        return out
    side = "UP" if pu >= D("0.5") else "DOWN"
    p_win = pu if side == "UP" else (D("1") - pu)
    fav_ask = (D(str(up_ask)) if side == "UP"
               else (D("1") - D(str(up_bid))))
    out.update(side=side, p_win=str(p_win), favourite_ask=str(fav_ask))
    for px in LF.DEFAULT_PRICES:
        ev = LF.ev_per_share(p_win, px, D("0"))
        out["levels"].append({
            "price": str(px),
            "ev_per_share": str(ev),
            "break_even_p": str(LF.break_even_p(px, D("0"))),
            "would_cross": bool(px >= fav_ask),
            #: a statement about the BOOK, not about us
            "inferred_opportunity": bool(px < fav_ask and ev >= RECORD_MARGIN),
        })
    return out


def _depth(market, side):
    """Available size on `side`, or None. The public record carries none;
    this is the one place to read it if the venue ever publishes it."""
    for key in ("bestBidSize", "bestAskSize"):
        if key in (market or {}):
            break
    else:
        return None
    return _q((market or {}).get(
        "bestBidSize" if side == "bid" else "bestAskSize"))


class IndexFeed:
    """One asset's own 1-second RTI average, newest last. Read-only."""

    def __init__(self, get=_get, asset: str = "btc",
                 index_symbol: str = REF.INDEX_SYMBOL):
        self.get = get
        #: btc reads BRTI; every other asset reads <TICKER>USD_RTI. Not
        #: derivable, so it is carried, and VERIFIED per asset: 8
        #: consecutive windows each, 32 of 32 exact, the 1-second bucket
        #: ending at windowStart equals priceToBeat to the cent.
        self.asset = str(asset).lower()
        self.index_symbol = str(index_symbol)
        self.points = {}
        self.last_rtt = None
        self.last_error = None
        self.last_read_t = None

    def poll(self, now_epoch: float) -> None:
        have = max(self.points) if self.points else None
        frm = int(now_epoch - (INDEX_WARMUP_S if have is None else 90))
        try:
            doc, rtt = self.get(INDEX_URL % (self.asset, frm))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self.last_error = type(exc).__name__
            return
        self.last_error, self.last_rtt = None, rtt
        self.last_read_t = time.time()
        #: THE SAME TWO FILTERS `l5_btc_reference._poll_index` APPLIES.
        #: They were missing here, so this recorder would have accepted a
        #: candle from any series in the answer and a bucket built from
        #: FEWER than the full minute -- recording an index the live
        #: engine would have rejected, which is the one way a dataset
        #: meant to validate that engine can quietly stop describing it.
        #: A partial bucket is not the 60-second average the market
        #: settles on.
        self.rejected = getattr(self, "rejected", 0)
        for s in (doc or {}).get("series") or ():
            a = s.get("asset") if isinstance(s, dict) else None
            if not isinstance(a, dict) \
                    or str(a.get("symbol")).lower() != self.asset \
                    or str(s.get("currency")) != "USD":
                self.rejected += 1
                continue
            for c in s.get("candles") or ():
                if c.get("tickCount") != REF.AVERAGING_S:
                    self.rejected += 1
                    continue
                try:
                    self.points[int(c["timestamp"])] = float(c["close"])
                except (KeyError, TypeError, ValueError):
                    self.rejected += 1
                    continue
        cut = now_epoch - INDEX_KEEP_S
        for t in [t for t in self.points if t < cut]:
            del self.points[t]

    def series(self, upto_epoch: float):
        """Buckets that had ALREADY ENDED at `upto_epoch`, oldest first.
        A bucket stamped t covers [t, t+1), so it has ended at t+1."""
        return sorted((t, v) for t, v in self.points.items()
                      if t + 1.0 <= upto_epoch)


def model_version() -> str:
    return ("l5_btc_reference;source=%s;averaging_s=%d;vol_inflation=%s;"
            "sigma_rel_floor=%s;max_index_age_s=%s"
            % (REF.VENUE_INDEX, REF.AVERAGING_S, REF.VOL_INFLATION,
               REF.SIGMA_REL_FLOOR, REF.MAX_AGE_S))


def build_row(*, slug, start, now, market, market_rtt, market_error, index):
    """One aligned row plus its health verdict. Pure given its inputs."""
    now_epoch = now.timestamp()
    ws, we = start.timestamp(), start.timestamp() + WINDOW_S
    terms = (market or {}).get("assetPriceTerms") or {}
    bid, ask = _q((market or {}).get("bestBidQuote")), \
        _q((market or {}).get("bestAskQuote"))
    strike = _q(terms.get("priceToBeat"))
    series = index.series(now_epoch)
    p = REF.probability_up(
        series=series, strike=(strike if strike else float("nan")),
        window_end=we, now=now_epoch, received_t=index.last_read_t,
        rtt_s=index.last_rtt)

    bad = []
    if market_error:
        bad.append("market:%s" % market_error)
    elif not market:
        bad.append("market:EMPTY")
    else:
        if str((market or {}).get("slug")) != slug:
            bad.append("market:SLUG_MISMATCH")
        if bid is None or ask is None:
            bad.append("quote:ONE_SIDED_OR_ABSENT")
        if strike is None:
            bad.append("strike:ABSENT")
    if index.last_error:
        bad.append("index:%s" % index.last_error)
    elif not series:
        bad.append("index:NO_BUCKETS")
    if not p.get("ok"):
        bad.append("probability:%s" % p.get("reason"))

    row = {
        "schema": SCHEMA,
        "market": slug,
        "window_start_utc": start.isoformat(),
        "window_end_utc": dt.datetime.fromtimestamp(we, UTC).isoformat(),
        "t_in_window_s": round(now_epoch - ws, 2),
        "seconds_remaining": round(we - now_epoch, 2),
        "quote_utc": now.isoformat(),
        "quote_rtt_s": market_rtt,
        "up": {"bid": bid, "ask": ask, "depth": _depth(market, "bid")},
        "down": {"bid": (None if ask is None else round(1.0 - ask, 4)),
                 "ask": (None if bid is None else round(1.0 - bid, 4)),
                 "depth": _depth(market, "ask")},
        "depth_unavailable_reason": DEPTH_REASON,
        "strike": strike,
        "index": {"a": p.get("index"), "bucket_t": p.get("bucket_t"),
                  "age_s": (None if p.get("index_age_s") is None
                            else round(p["index_age_s"], 3)),
                  "symbol": index.index_symbol,
                  "asset": index.asset,
                  "buckets_rejected": getattr(index, "rejected", 0),
                  "averaging_s": REF.AVERAGING_S,
                  "buckets_held": len(index.points),
                  "read_rtt_s": index.last_rtt,
                  "read_error": index.last_error},
        "p_up": {"ok": bool(p.get("ok")), "value": p.get("p_up"),
                 "reason": p.get("reason"), "model_version": model_version(),
                 "tau_s": p.get("seconds_left"),
                 "expected_settlement": p.get("expected_settlement"),
                 "sd": p.get("sd"), "z": p.get("z"),
                 "sigma_per_second": p.get("sigma_per_second"),
                 "distance_to_strike": p.get("distance")},
        "closed": (market or {}).get("closed"),
        #: THE LATE-FAVOURITE CANDIDATES, PRICED EVERY POLL.
        #:
        #: This is the UNBIASED DENOMINATOR. A competitor's +$310 over 40
        #: SELECTED WINNING positions establishes nothing, because the
        #: windows that lost and the windows that never filled are
        #: missing from it. Every poll of every window is written here
        #: whatever happens, so the losing and no-fill windows are in the
        #: sample by construction.
        #:
        #: THREE SEPARATE THINGS, never collapsed into one:
        #:   inferred_opportunity  the price was below the favourite's ask
        #:                         and the EV cleared the margin. A
        #:                         statement about the BOOK.
        #:   fillable              whether our bid would actually be hit.
        #:   our_fill              whether OUR order filled.
        #: Only the first is knowable here. The public endpoint publishes
        #: no size at any level (verified again 2026-11-16: every
        #: orderbook/book/depth path 404s and the market record carries no
        #: size key), so fill probability CANNOT be recorded -- it can
        #: only be learned by resting real orders. Writing the second or
        #: third as anything but null-with-a-reason would manufacture the
        #: very evidence this row exists to supply honestly.
        "late_favorite": _candidates(p, bid, ask),
        "settled_outcome": None,
        "fills": [],
        "fills_reason": FILLS_REASON,
    }
    health = {"ok": not bad, "bad": bad, "market": slug,
              "utc": now.isoformat()}
    return row, health


def main(argv):
    out = sys.stdout if argv[1] == "-" else open(argv[1], "a", buffering=1)
    hours = float(argv[2]) if len(argv) > 2 else 0.0
    end = time.time() + hours * 3600 if hours > 0 else float("inf")
    tag = argv[1] == "-"
    #: ONE FEED AN ASSET, all in this one process. Each polls only its
    #: own index, and the asset and symbol travel together from the
    #: registry so a feed can never price the wrong series.
    feeds = {r.strategy: IndexFeed(asset=r.asset,
                                   index_symbol=r.index_symbol)
             for r in LANES}
    print("L5_ALIGNED_RECORDER_LANES %s" % json.dumps(
        {r.strategy: {"asset": r.asset, "index": r.index_symbol,
                      "prefix": r.prefix} for r in LANES},
        sort_keys=True), file=sys.stderr, flush=True)
    unsettled = {}
    #: per lane, so one sick asset does not reset another's counter
    consecutive_bad = {}

    def emit(kind, doc):
        out.write(("%s " % kind if tag else "") + json.dumps(doc) + "\n")
        out.flush()

    while time.time() < end:
        now = dt.datetime.now(UTC)
        start = window_start(now)
        #: EVERY LANE, EVERY POLL. A lane whose fetch fails still writes
        #: its row and a FAILING health row, so a silent asset shows as
        #: unhealthy rather than as an absence nobody notices.
        for lane in LANES:
            index = feeds[lane.strategy]
            slug = slug_for(start, lane.prefix)
            index.poll(now.timestamp())
            market, rtt, err = None, None, None
            try:
                market, rtt = _get(MARKET_URL + slug)
                market = market.get("market") or market
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                err = type(exc).__name__
            row, health = build_row(slug=slug, start=start, now=now,
                                    market=market, market_rtt=rtt,
                                    market_error=err, index=index)
            row["strategy"] = lane.strategy
            row["asset"] = lane.asset
            emit("BTC5M_ALIGNED", row)
            bad_n = consecutive_bad.get(lane.strategy, 0)
            bad_n = 0 if health["ok"] else bad_n + 1
            consecutive_bad[lane.strategy] = bad_n
            health["consecutive_bad"] = bad_n
            health["strategy"] = lane.strategy
            emit("BTC5M_HEALTH", health)
            unsettled.setdefault(slug, start)

        for s, st0 in list(unsettled.items()):
            if (now - st0).total_seconds() < SETTLE_AFTER_S:
                continue
            try:
                m, _ = _get(MARKET_URL + s)
                m = m.get("market") or m
                prices = m.get("outcomePrices")
                if prices and prices not in ('["0","0"]', '["0.5","0.5"]'):
                    emit("BTC5M_SETTLED", {"schema": SCHEMA, "market": s,
                                           "settled": prices,
                                           "utc": now.isoformat()})
                    del unsettled[s]
                elif (now - st0).total_seconds() > SETTLE_GIVE_UP_S:
                    del unsettled[s]
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
        time.sleep(EVERY_S)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
