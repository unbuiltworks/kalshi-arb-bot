#!/usr/bin/env python3
"""Fetch Coinbase Exchange BTC-USD 1-MINUTE candles for the Up/Down replay.

WHY COINBASE AND WHY ONE MINUTE -- both are forced, and both weaken the test:

  * Chainlink BTC/USD 60s TWAP and CF Benchmarks BRTI are the actual
    settlement sources. Neither is reachable here, so NOTHING produced from
    this data is a settlement replay. It is a SPOT PROXY.
  * api.binance.com returns HTTP 451 (geo-blocked) from this environment, so
    the published table's own 1-second Binance basis cannot be reproduced.
    api.binance.us answers but quotes a near-dead book (1-minute bars with
    0.00000000 volume), so it is not a usable proxy either.
  * No reachable venue serves sub-minute history: Coinbase, Kraken, Gemini
    and Binance.US all floor at 1 minute. Coinbase Exchange is the most
    liquid of them, so it is the proxy used.

CONSEQUENCE, stated here because it bites exactly where the test is most
sensitive: the rules settle on a 60-SECOND AVERAGE, and a 1-minute OHLC bar
cannot produce one. The opening and closing 60-second averages are
approximated by OHLC4 = (O+H+L+C)/4 of the first and last minute. That proxy
is worst for near-tie windows, which is precisely the <2 bp bin.

Coinbase returns [time, low, high, open, close, volume], max 300 candles.
"""
from __future__ import annotations

import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
OUT = "data/btc15m/coinbase_1m.jsonl"
START = datetime(2026, 5, 20, tzinfo=timezone.utc)
STEP = timedelta(minutes=300)


def fetch(a, b):
    u = (f"{URL}?granularity=60&start={a.strftime('%Y-%m-%dT%H:%M:%SZ')}"
         f"&end={b.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    for attempt in range(6):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                u, headers={"Accept": "application/json",
                            "User-Agent": "l5-btc15m-replay/1.0"}), timeout=30)
            return json.load(r)
        except Exception:
            if attempt == 5:
                raise
            time.sleep(1.6 ** attempt)


def main():
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    have = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            try:
                have.add(json.loads(line)[0])
            except Exception:
                pass
    cur = START
    n_new = 0
    with open(OUT, "a") as fh:
        while cur < end:
            nxt = min(cur + STEP, end)
            try:
                rows = fetch(cur, nxt)
            except Exception as e:
                print(f"FAIL {cur}: {e}", flush=True)
                cur = nxt
                continue
            for c in rows:
                if c[0] in have:
                    continue
                have.add(c[0])
                fh.write(json.dumps(c) + "\n")
                n_new += 1
            fh.flush()
            cur = nxt
            time.sleep(0.12)          # ~8 req/s, inside Coinbase's public limit
    print(f"candles on disk: {len(have)}  new this run: {n_new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
