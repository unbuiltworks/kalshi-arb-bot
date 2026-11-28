"""Record PMUS and Polymarket prices for the SAME 5-minute BTC window.

WHY. Every analysis on this branch measured PMUS against itself, which is
why "price is the probability" kept coming back -- there was no external
reference. Polymarket runs the SAME product on the SAME 5-minute grid
(`btc-updown-5m-<epoch>` against `cpc-btc-updown-5m-<date>-<hhmm>z`), so
it supplies an independent opinion on the identical event. That is the
first predictor class available to us that is not circular.

WHAT IS VERIFIED. Same asset, same window boundaries, same up-or-down-
from-open structure. PMUS settles on CF Benchmarks BRTI, "Up if the price
at window end >= the price at window start". One settled window checked by
hand agreed across venues (PMUS 0230z settled Up at 67306.58 vs a 67098.49
open; Polymarket's late trades on that window printed Up at 0.98-0.99).

WHAT IS NOT VERIFIED, and it decides whether this is arbitrage or merely a
signal: Polymarket's settlement SOURCE. Its /v2/markets endpoint is
retired and the trades feed carries no market metadata, so the index
behind its contract is unknown here. If it is not BRTI the two contracts
are correlated, not identical, and any apparent arbitrage carries index
basis risk. TREAT THIS AS A SIGNAL UNTIL THE SOURCE IS CONFIRMED.

ALSO: we hold READ-ONLY access to Polymarket. There is no order path, so
even an identical contract would be tradeable on the PMUS leg only.

WHAT IT RECORDS. One row per poll: the window, PMUS best bid/ask, and
Polymarket's most recent traded price per outcome. Polymarket exposes
trades rather than quotes here, so its price is last-trade and noisier
than a quote -- recorded as such, never as a quote.

READ-ONLY. Two public GETs. No credentials, no order path, places
nothing.

usage: python3 -I analysis/l5/btc5m_accumulation/paired_venue_collect.py [seconds]
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "paired_venue_quotes.jsonl")
PMUS = "https://gateway.polymarket.us/v1/market/slug/"
POLY = "https://data-api.polymarket.com/v2/trades?limit=200"


def get(url, timeout=20):
    out = subprocess.run(["curl", "-sS", url, "--max-time", str(timeout)],
                         capture_output=True, text=True, timeout=timeout + 10)
    try:
        return json.loads(out.stdout)
    except ValueError:
        return None


def window_now(now):
    m = (now.minute // 5) * 5
    start = now.replace(minute=m, second=0, microsecond=0)
    slug = "cpc-btc-updown-5m-%s-%02d%02dz" % (start.strftime("%Y-%m-%d"),
                                               start.hour, start.minute)
    return slug, int(start.timestamp()), start


def poll():
    now = dt.datetime.now(dt.timezone.utc)
    slug, epoch, start = window_now(now)
    row = {"utc": now.isoformat(), "window_start": start.isoformat(),
           "t_in_window_s": round((now - start).total_seconds(), 1),
           "pmus_slug": slug, "poly_slug": "btc-updown-5m-%d" % epoch}

    d = get(PMUS + slug)
    if isinstance(d, dict):
        mk = d.get("market", d)
        row["pmus_bid"] = mk.get("bestBidQuote")
        row["pmus_ask"] = mk.get("bestAskQuote")
        row["pmus_status"] = mk.get("status")
        terms = mk.get("assetPriceTerms") or {}
        ptb = (terms.get("priceToBeat") or {}).get("value")
        row["pmus_price_to_beat"] = ptb
        row["pmus_index"] = terms.get("indexSymbol")

    t = get(POLY, 25)
    if isinstance(t, dict):
        rows = t.get("data") or []
        mine = [x for x in rows if x.get("slug") == row["poly_slug"]]
        last = {}
        for x in sorted(mine, key=lambda z: z.get("timestamp") or 0):
            oc = str(x.get("outcome") or "").upper()
            last[oc] = {"price": x.get("price"), "side": x.get("side"),
                        "size": x.get("size"), "ts": x.get("timestamp")}
        row["poly_last"] = last
        row["poly_trades_seen"] = len(mine)
    return row


def main(argv):
    secs = float(argv[0]) if argv else 600.0
    end = time.time() + secs
    n = 0
    with open(STORE, "a", encoding="utf-8") as fh:
        while time.time() < end:
            try:
                r = poll()
                fh.write(json.dumps(r, sort_keys=True) + "\n")
                fh.flush()
                n += 1
                if n % 5 == 1:
                    pl = r.get("poly_last") or {}
                    up = (pl.get("UP") or {}).get("price")
                    print("%s  t=%5.1fs  PMUS %s/%s   POLY up=%s (%d trades)"
                          % (r["pmus_slug"][-6:], r["t_in_window_s"],
                             r.get("pmus_bid"), r.get("pmus_ask"), up,
                             r.get("poly_trades_seen") or 0), flush=True)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                print("poll error: %s" % type(exc).__name__, flush=True)
            time.sleep(10)
    print("wrote %d rows -> %s" % (n, STORE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
