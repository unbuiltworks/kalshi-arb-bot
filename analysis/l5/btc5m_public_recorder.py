"""READ-ONLY recorder of PMUS BTC 5-minute up/down quotes from the venue's
PUBLIC market endpoint. No credentials, no orders, no account data.

One JSON line per poll: window slug, seconds from window start, best bid and
ask of the YES (Up) outcome, the venue's price to beat, and once the window
is over the settled outcome prices. Polls the active window every
`EVERY_S` seconds and re-reads each finished window until it settles.

usage: python3 analysis/l5/btc5m_public_recorder.py OUT.jsonl [HOURS]
       OUT of "-" writes the lines to standard output, which is how the
       stand-alone recorder service keeps them (in its own log); HOURS of 0
       runs until stopped.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.request

URL = "https://gateway.polymarket.us/v1/market/slug/"
EVERY_S = 2.0
UTC = dt.timezone.utc


def slug_for(start: dt.datetime) -> str:
    return "cpc-btc-updown-5m-%s-%sz" % (start.strftime("%Y-%m-%d"),
                                         start.strftime("%H%M"))


def read(slug: str):
    req = urllib.request.Request(URL + slug, headers={
        "Accept": "application/json", "User-Agent": "research-client"})
    with urllib.request.urlopen(req, timeout=8) as fh:
        m = json.loads(fh.read().decode())
    return m.get("market") or m


def quote(q):
    return None if not isinstance(q, dict) else q.get("value")


def main(argv):
    out = (sys.stdout if argv[1] == "-"
           else open(argv[1], "a", buffering=1))
    hours = float(argv[2]) if len(argv) > 2 else 2.0
    end = time.time() + hours * 3600 if hours > 0 else float("inf")
    unsettled = {}
    while time.time() < end:
        now = dt.datetime.now(UTC)
        start = now.replace(minute=(now.minute // 5) * 5, second=0,
                            microsecond=0)
        slug = slug_for(start)
        try:
            m = read(slug)
            terms = m.get("assetPriceTerms") or {}
            out.write("BTC5M_QUOTE " * (argv[1] == "-") + json.dumps({
                "slug": slug, "t": round((now - start).total_seconds(), 2),
                "utc": now.isoformat(), "bid": quote(m.get("bestBidQuote")),
                "ask": quote(m.get("bestAskQuote")),
                "to_beat": quote(terms.get("priceToBeat")),
                "closed": m.get("closed")}) + "\n")
            out.flush()
            unsettled.setdefault(slug, start)
        except Exception as exc:                               # noqa: BLE001
            out.write(json.dumps({"slug": slug, "utc": now.isoformat(),
                                  "error": type(exc).__name__}) + "\n")
        for s, st0 in list(unsettled.items()):
            if (now - st0).total_seconds() < 330:
                continue
            try:
                m = read(s)
                prices = m.get("outcomePrices")
                if prices and prices not in ('["0","0"]', '["0.5","0.5"]'):
                    out.write("BTC5M_SETTLED " * (argv[1] == "-")
                              + json.dumps({"slug": s, "settled": prices,
                                            "utc": now.isoformat()}) + "\n")
                    out.flush()
                    del unsettled[s]
                elif (now - st0).total_seconds() > 1800:
                    del unsettled[s]
            except Exception:                                  # noqa: BLE001
                pass
        time.sleep(EVERY_S)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
