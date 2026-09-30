"""Is a BTC15m market READY for the bounded q1 canary? Read-only, no orders.

Readiness is judged on what the canary itself will require at startup, so a
GO here means the in-process gates should not refuse:

  * the venue serves a row for EXACTLY this slug (canonical route), and
  * that row carries a usable price grid (`orderPriceMinTickSize`), and
  * /bbo answers with a TWO-SIDED book, and
  * the bid sits inside the entry band, and
  * enough runway remains before the close for the campaign plus the sweep.

Measured venue behaviour 2026-09-30: the row for a future slug is served
early, but /bbo answers only once quoting begins -- 1745z still had no quotes
~3 minutes into its own window while 1730z was already EXPIRED. So readiness
must be polled, and the row alone is not readiness.

    python3 scripts/l5_canary_target_ready.py [slug ...]
Exit 0 and print "READY <slug>" for the first ready market, else exit 1.
"""
from __future__ import annotations

import datetime as dt
import json
import ssl
import sys
import urllib.request
from decimal import Decimal as D

sys.path.insert(0, ".")

from src import pmus_q1_canary as C                            # noqa: E402
from src import pmus_q1_executor as X                          # noqa: E402
from src import pmus_market_row as MR                          # noqa: E402

GATEWAY = "https://gateway.polymarket.us"
MIN_RUNWAY_S = 180.0          # below this the campaign is not worth spending


def _get(path: str):
    req = urllib.request.Request(GATEWAY + path,
                                 headers={"User-Agent": "pmus-canary-readiness"})
    with urllib.request.urlopen(req, context=ssl.create_default_context(),
                                timeout=15) as r:
        return json.loads(r.read() or b"{}")


def candidates(now=None):
    """This window and the next two, from the clock, then CONFIRMED by slug."""
    now = now or dt.datetime.now(dt.timezone.utc)
    base = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    return ["cpc-btc-updown-15m-%s" % (base + dt.timedelta(minutes=15 * i)
                                       ).strftime("%Y-%m-%d-%H%Mz")
            for i in range(3)]


def inspect(slug: str) -> dict:
    out = {"slug": slug, "ready": False}
    try:
        row = MR.market_row(slug, get=_get)
    except Exception as exc:                                   # noqa: BLE001
        out["why"] = f"no row: {str(exc)[:80]}"
        return out
    tick = X.tick_for_market(row)
    out["tick"] = str(tick)
    if tick is None:
        out["why"] = "row carries no usable grid"
        return out
    try:
        md = _get(f"/v1/markets/{slug}/bbo").get("marketData") or {}
    except Exception as exc:                                   # noqa: BLE001
        out["why"] = f"no bbo: {str(exc)[:80]}"
        return out
    bid = (md.get("bestBid") or {}).get("value")
    ask = (md.get("bestAsk") or {}).get("value")
    out.update({"state": md.get("state"), "bid": bid, "ask": ask})
    if not (bid and ask):
        out["why"] = "not two-sided yet"
        return out
    if not X.entry_ok(D(str(bid))):
        out["why"] = f"bid {bid} outside the entry band"
        return out
    runway = C.effective_deadline_s(slug, 10_000)
    out["runway_s"] = runway
    if runway is None or runway < MIN_RUNWAY_S:
        out["why"] = f"runway {runway} below {MIN_RUNWAY_S:.0f}s"
        return out
    out["ready"] = True
    return out


def main(argv) -> int:
    slugs = argv[1:] or candidates()
    for slug in slugs:
        r = inspect(slug)
        print(json.dumps(r, default=str))
        if r["ready"]:
            print("READY %s runway_s=%.0f tick=%s bid=%s ask=%s"
                  % (slug, r["runway_s"], r["tick"], r["bid"], r["ask"]))
            return 0
    print("NO_READY_MARKET")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
