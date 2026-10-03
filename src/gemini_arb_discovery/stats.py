"""Gemini-wide liquidity census, by category.

THREE SOURCES, THREE DIFFERENT STRENGTHS OF CLAIM, kept apart on purpose:

  QUOTES    `prices.bestBid`/`bestAsk` from the catalogue. FULL coverage of every
            active contract, and says nothing about size. Good for two-sided
            availability and spread; worthless for depth.
  BOOKS     `/v1/book/{symbol}`, full ladder. Real executable size, but one
            request per contract, so it is SAMPLED and reported as a sample.
  TAPE      `/v1/trades/{symbol}`, public prints with millisecond timestamps.
            Gives an observed trade rate over the window the tape actually
            covers -- which is NOT the same as a rate over the window we chose,
            and is reported with its own span attached.

A sampled figure is never presented as a platform total.
"""
from __future__ import annotations

import json
import random
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src.gemini_arb_discovery import books as BK
from src.gemini_arb_discovery import universe as UN
from src.gemini_arb_discovery.http import get_json

TRADES_PATH = "/v1/trades/%s?limit_trades=500"


def _pct(vals: List[D], q: float) -> Optional[str]:
    if not vals:
        return None
    s = sorted(vals)
    return str(s[min(len(s) - 1, int(q * (len(s) - 1)))])


def quote_census(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Spread and two-sided availability over EVERY active contract."""
    out: Dict[str, Any] = {}
    for e in events:
        cat = e.get("category") or "Unknown"
        d = out.setdefault(cat, {"events": 0, "contracts": 0, "two_sided": 0,
                                 "bid_only": 0, "ask_only": 0, "no_quote": 0,
                                 "_spreads": []})
        d["events"] += 1
        for c in UN.active_contracts(e):
            d["contracts"] += 1
            p = c.get("prices") or {}
            bb, ba = p.get("bestBid"), p.get("bestAsk")
            has_b = bb not in (None, "")
            has_a = ba not in (None, "")
            if has_b and has_a:
                d["two_sided"] += 1
                d["_spreads"].append(D(str(ba)) - D(str(bb)))
            elif has_b:
                d["bid_only"] += 1
            elif has_a:
                d["ask_only"] += 1
            else:
                d["no_quote"] += 1
    for cat, d in out.items():
        sp = d.pop("_spreads")
        n = d["contracts"] or 1
        d["two_sided_pct"] = round(100.0 * d["two_sided"] / n, 1)
        d["spread_median"] = _pct(sp, 0.50)
        d["spread_p25"] = _pct(sp, 0.25)
        d["spread_p75"] = _pct(sp, 0.75)
        d["spread_n"] = len(sp)
    return out


def book_census(events: List[Dict[str, Any]], *, per_category: int = 40,
                seed: int = 7) -> Dict[str, Any]:
    """Executable depth, from full ladders, on a random sample per category."""
    rng = random.Random(seed)
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        cat = e.get("category") or "Unknown"
        for c in UN.active_contracts(e):
            buckets.setdefault(cat, []).append(c)
    out: Dict[str, Any] = {}
    for cat, cs in buckets.items():
        pick = cs if len(cs) <= per_category else rng.sample(cs, per_category)
        touch_bid, touch_ask, tot_bid, tot_ask = [], [], [], []
        two, unread, n = 0, 0, 0
        for c in pick:
            bk = BK.fetch_book(c["instrumentSymbol"])
            if not bk.get("readable"):
                unread += 1
                continue
            n += 1
            if BK.two_sided(bk):
                two += 1
            if bk["bids"]:
                touch_bid.append(bk["bids"][0][1])
            if bk["asks"]:
                touch_ask.append(bk["asks"][0][1])
            tot_bid.append(BK.depth_total(bk, "bids"))
            tot_ask.append(BK.depth_total(bk, "asks"))
            time.sleep(0.05)
        out[cat] = {
            "population_contracts": len(cs), "sampled": len(pick),
            "readable": n, "unreadable": unread,
            "two_sided_pct_of_sample": round(100.0 * two / n, 1) if n else None,
            "touch_qty_bid_median": _pct(touch_bid, 0.50),
            "touch_qty_ask_median": _pct(touch_ask, 0.50),
            "ladder_qty_bid_median": _pct(tot_bid, 0.50),
            "ladder_qty_ask_median": _pct(tot_ask, 0.50),
            "ladder_qty_ask_p90": _pct(tot_ask, 0.90),
        }
    return out


def tape_census(events: List[Dict[str, Any]], *, per_category: int = 25,
                seed: int = 11) -> Dict[str, Any]:
    """Observed trade rate, over the span each contract's own tape covers.

    A contract with five prints spread over three hours has a real rate of
    0.03/min, and reporting it as "5 trades" without the span would overstate how
    tradeable it is. The span is therefore carried alongside every rate.
    """
    rng = random.Random(seed)
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        buckets.setdefault(e.get("category") or "Unknown", []).extend(
            UN.active_contracts(e))
    out: Dict[str, Any] = {}
    for cat, cs in buckets.items():
        pick = cs if len(cs) <= per_category else rng.sample(cs, per_category)
        rates, prints_, spans, silent = [], 0, [], 0
        for c in pick:
            r = get_json(TRADES_PATH % c["instrumentSymbol"])
            if not r.get("readable") or not isinstance(r["json"], list):
                continue
            rows = r["json"]
            prints_ += len(rows)
            if len(rows) < 2:
                silent += 1
                continue
            ts = sorted(int(x.get("timestampms") or 0) for x in rows)
            span_min = (ts[-1] - ts[0]) / 60000.0
            if span_min <= 0:
                continue
            spans.append(D(str(round(span_min, 2))))
            rates.append(D(str(round(len(rows) / span_min, 4))))
            time.sleep(0.05)
        out[cat] = {
            "sampled": len(pick), "total_prints_seen": prints_,
            "contracts_with_under_2_prints": silent,
            "trades_per_min_median": _pct(rates, 0.50),
            "trades_per_min_p90": _pct(rates, 0.90),
            "tape_span_min_median": _pct(spans, 0.50),
            "caveat": ("rate is over each contract's OWN tape span, capped at "
                       "500 prints; it is not a rate over a chosen wall-clock "
                       "window"),
        }
    return out
