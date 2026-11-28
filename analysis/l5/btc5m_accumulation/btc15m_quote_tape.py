"""Record a BTC15m quote tape in the SAME shape as the 5m one.

WHY THIS EXISTS. The 5m instrument was closed by one measurement: a maker
bid's realized win rate SATURATES near 44% whatever price it pays, while a
taker's tracks the price (MAKER_PRICE_CURVE.md). That test needs a dense
bid/ask tape, and `pmus_btc5m_quotes_20261112.jsonl` is the only one that
exists. There is no 15m equivalent, so the single question that decides
whether the 15m is a different instrument or the same trap at a slower
cadence HAS NEVER BEEN ASKED OF IT.

The 15m shadow records one summary row per window, which is enough for a
pair rate and a lone-leg win rate but NOT enough for the price curve: that
needs every observation, because it asks what happens at each price level.

OUTPUT SHAPE is deliberately identical to the 5m tape -- slug, utc, t,
bid, ask, to_beat, closed -- so favorite_accumulation_test.py,
taker_entry_test.py and taker_window_level_test.py all run against it with
only the filename changed. No new analysis code is needed.

READ-ONLY. One public GET per poll, no credentials, places nothing.

usage: python3 -I analysis/l5/btc5m_accumulation/btc15m_quote_tape.py [seconds]
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "pmus_btc15m_quotes.jsonl")
GATEWAY = "https://gateway.polymarket.us"
SERIES = "cpc-btc-updown-15m-"
WINDOW_S = 900.0
POLL_S = 2.0
UTC = dt.timezone.utc


def slug_for(ws):
    return "%s%s-%02d%02dz" % (SERIES, ws.strftime("%Y-%m-%d"),
                               ws.hour, ws.minute)


def window_now(now):
    m = (now.minute // 15) * 15
    return now.replace(minute=m, second=0, microsecond=0)


def quote(slug, timeout=8.0):
    """(bid, ask, to_beat) from the OPEN-window endpoint.

    `/v1/market/slug/{slug}` is the one that answers while a market is
    OPEN; `/bbo` 404s until expiry. Same choice the 5m recorder made, and
    matching it is what keeps the two tapes comparable.
    """
    out = subprocess.run(["curl", "-sS", "%s/v1/market/slug/%s" % (GATEWAY, slug),
                          "--max-time", str(timeout)],
                         capture_output=True, text=True, timeout=timeout + 6)
    d = json.loads(out.stdout)
    m = d.get("market", d)

    def val(x):
        if isinstance(x, dict):
            x = x.get("value")
        return None if x is None else float(x)

    terms = m.get("assetPriceTerms") or {}
    return (val(m.get("bestBidQuote")), val(m.get("bestAskQuote")),
            val((terms.get("priceToBeat") or {}).get("value")),
            str(m.get("status") or ""))


def main(argv):
    secs = float(argv[0]) if argv else 3600.0
    end = time.time() + secs
    n = 0
    with open(STORE, "a", encoding="utf-8") as fh:
        while time.time() < end:
            now = dt.datetime.now(UTC)
            ws = window_now(now)
            slug = slug_for(ws)
            t_in = (now - ws).total_seconds()
            try:
                bid, ask, tb, status = quote(slug)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                time.sleep(POLL_S)
                continue
            if bid is not None and ask is not None:
                fh.write(json.dumps({"slug": slug,
                                     "utc": now.isoformat(),
                                     "t": round(t_in, 2),
                                     "bid": "%.4f" % bid,
                                     "ask": "%.4f" % ask,
                                     "to_beat": tb,
                                     "closed": status != "MARKET_STATE_OPEN"},
                                    sort_keys=True) + "\n")
                fh.flush()
                n += 1
                if n % 150 == 1:
                    print("%s t=%6.1f bid=%.2f ask=%.2f  rows=%d"
                          % (slug[-6:], t_in, bid, ask, n), flush=True)
            time.sleep(POLL_S)
    print("wrote %d rows -> %s" % (n, STORE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
