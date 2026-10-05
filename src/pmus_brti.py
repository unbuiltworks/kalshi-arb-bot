"""The venue's own BRTI reference index, and its settlement statistic.

WHY THIS EXISTS. PMUS BTC Up/Down settles on CF Benchmarks' Bitcoin Real-Time
Index, published once per second; the venue's own market description says in
terms that spot venues and other indices have no effect. The repo's only
forward-recorded BTC series is Coinbase (`src/btc_ref_recorder.py`), which its
own header already labels a proxy. Substituting it would have put an
unquantified basis between our features and the contract's actual reference.

It turns out no substitution is needed: the gateway serves the index itself,
keyless, at one-second buckets, and the settlement statistic reconstructs from
it EXACTLY -- 28/28 windows on both the open and the close, to the cent. See
VENUE_REFERENCE_SEMANTICS.md.

THE OFFSET CONVENTION IS MEASURED, NOT READ. The docs describe the averaging
interval as the inclusive [T-59s, T]. In bucket-start timestamps that same set
of 60 prices is [T-60, T-1], which is what reproduces every published value.
That is why `settlement_stat` is written against bucket starts and why
`verify_window` exists: the convention is re-checkable against the venue rather
than trusted.

GAP FILL IS NOT SILENT. The endpoint gap-fills empty buckets by carrying the
close forward and setting tickCount to 0. Averaging those without saying so
would quietly invent index prices, so every return value carries the number of
zero-tick buckets it used.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from decimal import Decimal as D, ROUND_HALF_EVEN
from typing import Dict, Optional, Tuple

GATEWAY = "https://gateway.polymarket.us"
INDEX_SYMBOL = "BRTI"
#: the settlement statistic's own parameters, from the venue's market rules
STAT_N = 60
CENT = D("0.01")
#: bucket-start offsets, measured against published values (see module docstring)
STAT_LO, STAT_HI = -60, -1


def _get(path: str, params) -> dict:
    url = "%s%s?%s" % (GATEWAY, path,
                       urllib.parse.urlencode(params, doseq=True))
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode())


def unix(iso: str) -> int:
    """'2026-09-30T12:45:00Z' -> unix seconds."""
    return int(dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=dt.timezone.utc).timestamp())


def market_terms(slug: str) -> dict:
    """The venue's own terms for one window: strike, settlement, bounds.

    `price_to_beat` is the published strike (the window's open statistic) and
    `settlement_price` the published close statistic. Both are the venue's
    numbers, so the outcome label is READ rather than modelled -- and is a
    label only, never a decision feature.
    """
    m = _get("/v1/markets", {"slug": slug})["markets"][0]
    a = m["assetPriceTerms"]
    ptb = (a.get("priceToBeat") or {}).get("value")
    sp = (a.get("settlementPrice") or {}).get("value")
    return {
        "slug": slug,
        "index_symbol": a.get("indexSymbol"),
        "window_start": a["windowStart"],
        "window_end": a["windowEnd"],
        "window_start_unix": unix(a["windowStart"]),
        "window_end_unix": unix(a["windowEnd"]),
        "price_to_beat": D(ptb) if ptb else None,
        "settlement_price": D(sp) if sp else None,
        # ties settle UP: the long side pays iff close >= open
        "outcome": (None if not (ptb and sp)
                    else ("UP" if D(sp) >= D(ptb) else "DOWN")),
        "status": m.get("status"),
        "horizon": a.get("horizon"),
    }


def index_1s(from_unix: int, to_unix: int,
             asset: str = "crypto:btc") -> Dict[int, dict]:
    """One-second index candles, keyed by bucket-start unix second.

    At most 3600 buckets per call, which the caller must respect; a 15-minute
    window is 900.
    """
    s = _get("/v1/asset-prices/history",
             {"assets": asset, "from": str(from_unix), "to": str(to_unix),
              "bucket": "ASSET_PRICE_BUCKET_1S"})["series"]
    out: Dict[int, dict] = {}
    for ser in s:
        for c in ser.get("candles", []):
            out[int(c["timestamp"])] = {
                "close": D(str(c["close"])), "open": D(str(c["open"])),
                "high": D(str(c["high"])), "low": D(str(c["low"])),
                "ticks": int(c.get("tickCount", 0)),
            }
    return out


def settlement_stat(candles: Dict[int, dict], T: int) -> Tuple[Optional[D],
                                                               int, int]:
    """The venue statistic at instant T: (value, n_used, n_zero_tick).

    Simple average of the 60 one-second closes on [T-60, T-1], rounded to the
    cent half-even. Returns None unless all 60 buckets are present, because the
    venue itself does not settle on partial index data.
    """
    sel = [candles[t] for t in range(T + STAT_LO, T + STAT_HI + 1)
           if t in candles]
    if len(sel) != STAT_N:
        return None, len(sel), sum(1 for c in sel if c["ticks"] == 0)
    v = sum((c["close"] for c in sel), D(0)) / STAT_N
    return (v.quantize(CENT, rounding=ROUND_HALF_EVEN), STAT_N,
            sum(1 for c in sel if c["ticks"] == 0))


def running_level(candles: Dict[int, dict], t: int) -> Optional[D]:
    """The last index close published at or before t. No lookahead."""
    for k in range(t, t - 120, -1):
        if k in candles:
            return candles[k]["close"]
    return None


def settlement_minute_published(window_end_unix: int, t: int) -> int:
    """How many of the close statistic's 60 prices are already published at t.

    The close averages [T-60, T-1], so this rises from 0 to 60 across the final
    minute and the outcome locks progressively. Any feature touching the
    settlement minute must be auditable against this count rather than quietly
    reading across it.
    """
    return max(0, min(STAT_N, t - (window_end_unix + STAT_LO) + 1))


def verify_window(slug: str) -> dict:
    """Re-derive both published statistics for one window from the index.

    The convention check, kept runnable: if the venue ever changes the
    averaging interval or the rounding, this stops matching instead of silently
    shifting every feature built on it.
    """
    tm = market_terms(slug)
    a, b = tm["window_start_unix"], tm["window_end_unix"]
    c = index_1s(a - 120, b + 30)
    o, no, zo = settlement_stat(c, a)
    cl, nc, zc = settlement_stat(c, b)
    return {
        "slug": slug,
        "open_reconstructed": o, "open_published": tm["price_to_beat"],
        "open_exact": o is not None and o == tm["price_to_beat"],
        "close_reconstructed": cl,
        "close_published": tm["settlement_price"],
        "close_exact": cl is not None and cl == tm["settlement_price"],
        "outcome_derived": (None if (o is None or cl is None)
                            else ("UP" if cl >= o else "DOWN")),
        "outcome_published": tm["outcome"],
        "zero_tick_buckets_open": zo, "zero_tick_buckets_close": zc,
        "n_open": no, "n_close": nc,
    }
