"""FROZEN Gemini venue-capacity metrics. OWNER 2026-09-17, Lane D.

Written BEFORE the hot-window tape closed, deliberately, so the verdict cannot
be reverse-engineered from the numbers it produces. The decision question is
fixed here and does not move:

    Is there enough ACTUAL flow for an L5-style incremental inventory
    controller to receive repeated REAL fills at economically meaningful
    frequency?

EVERY METRIC BELOW COMES FROM VENUE EVIDENCE: `bbo` rows are the book the venue
served, `print` rows are trades that actually happened. There is no queue model
anywhere in this file and no metric named "fill". A maker opportunity is
counted as an OPPORTUNITY -- a moment when a resting maker COULD have been
filled because a trade printed through its side -- and never as a fill.

WHY THE SEQUENCE ESTIMATE IS AN UPPER BOUND. An L5 sequence is
OPEN -> EXTEND -> ... -> COMPLETE, which needs at least 2 fills on one side and
1 on the other, and the completing fill must be on the OPPOSITE side. So a
window supporting one sequence needs >= 3 prints with both sides represented.
Counting it that way assumes we win every one of those prints, which we would
not: other makers are in the queue ahead of us. The number this produces is
therefore the most generous figure the evidence can support, and if it is small
the real number is smaller.
"""
from __future__ import annotations

import gzip
import json
import math
import re
import statistics as st
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

#: GEMI-<ASSET><WINDOW>M<YYMMDDHHMM>-UP
MARKET_RE = re.compile(r"^GEMI-(BTC|ETH)(05|15)M(\d{10})-UP$", re.I)
#: the series we report on, in a fixed order so two runs are comparable
SERIES = ("BTC05M", "BTC15M", "ETH05M", "ETH15M")
#: print-count thresholds the owner named
THRESHOLDS = (1, 2, 5, 10)
#: a sequence needs two fills one side and one the other
PRINTS_PER_SEQUENCE = 3


def series_of(market: Any) -> Optional[str]:
    m = MARKET_RE.match(str(market or ""))
    return (m.group(1) + m.group(2) + "M").upper() if m else None


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_tape(paths: Iterable[Path]) -> list:
    """Gemini rows from one or more tapes, plain or gzipped."""
    rows = []
    for p in paths:
        p = Path(p)
        op = gzip.open if p.suffix == ".gz" else open
        try:
            with op(p, "rt", encoding="utf-8") as fh:            # type: ignore
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(r, dict) and r.get("venue") == "gemini":
                        rows.append(r)
        except OSError:
            continue
    return rows


def flow(rows: list) -> dict:
    """The frozen metric set, per series plus a total."""
    span_t = [r.get("t") for r in rows if isinstance(r.get("t"), (int, float))]
    minutes = ((max(span_t) - min(span_t)) / 60.0) if len(span_t) > 1 else 0.0

    bbo = defaultdict(list)
    prints = defaultdict(list)
    per_market_prints: dict = defaultdict(lambda: defaultdict(int))
    markets = defaultdict(set)
    for r in rows:
        s = series_of(r.get("market"))
        if not s:
            continue
        k = r.get("kind")
        if k == "bbo":
            bbo[s].append(r)
            markets[s].add(r.get("market"))
        elif k == "print":
            prints[s].append(r)
            markets[s].add(r.get("market"))
            per_market_prints[s][r.get("market")] += 1

    out: dict = {"span_minutes": round(minutes, 1), "series": {}}
    for s in SERIES:
        bs, ps = bbo.get(s, []), prints.get(s, [])
        mk = markets.get(s, set())
        spreads, depth_b, depth_a, mids, two_sided = [], [], [], [], 0
        for r in bs:
            bids, asks = r.get("bids") or [], r.get("asks") or []
            bb = _f(bids[0][0]) if bids else None
            aa = _f(asks[0][0]) if asks else None
            if bb is not None and aa is not None:
                two_sided += 1
                spreads.append(aa - bb)
                mids.append((aa + bb) / 2.0)
                qb, qa = _f(bids[0][1]), _f(asks[0][1])
                if qb is not None:
                    depth_b.append(qb)
                if qa is not None:
                    depth_a.append(qa)
        sizes = [x for x in (_f(p.get("qty")) for p in ps) if x is not None]
        # BBO movement: mean absolute mid change between consecutive quotes
        moves = [abs(mids[i] - mids[i - 1]) for i in range(1, len(mids))]
        counts = per_market_prints.get(s, {})
        n_mk = len(mk) or 1
        pct = {}
        for th in THRESHOLDS:
            pct["pct_windows_ge_%d" % th] = round(
                100.0 * sum(1 for v in counts.values() if v >= th) / n_mk, 1)
        active = [v for v in counts.values() if v >= 1]

        # MAKER OPPORTUNITY: a print is a moment a resting maker on that side
        # could have been hit. buyer_is_maker True means the resting BID traded.
        # This counts OPPORTUNITIES, never fills: we would be one of several
        # makers in that queue.
        opp_bid = sum(1 for p in ps if p.get("buyer_is_maker") is True)
        opp_ask = sum(1 for p in ps if p.get("buyer_is_maker") is False)

        out["series"][s] = {
            "windows": len(mk),
            "bbo_rows": len(bs),
            "prints": len(ps),
            "trades_per_min": round(len(ps) / minutes, 4) if minutes else None,
            "prints_per_active_window": (
                round(st.mean(active), 2) if active else 0.0),
            "windows_with_any_trade": len(active),
            "pct_windows_zero_trades": round(
                100.0 * (n_mk - len(active)) / n_mk, 1),
            **pct,
            "spread_median": round(st.median(spreads), 4) if spreads else None,
            "spread_p25": (round(st.quantiles(spreads, n=4)[0], 4)
                           if len(spreads) > 3 else None),
            "depth_bid_median": round(st.median(depth_b), 1) if depth_b else None,
            "depth_ask_median": round(st.median(depth_a), 1) if depth_a else None,
            "two_sided_uptime_pct": (
                round(100.0 * two_sided / len(bs), 1) if bs else None),
            "bbo_mean_abs_move": round(st.mean(moves), 5) if moves else None,
            "trade_size_median": round(st.median(sizes), 2) if sizes else None,
            "trade_size_max": round(max(sizes), 2) if sizes else None,
            "maker_opportunities_bid_side": opp_bid,
            "maker_opportunities_ask_side": opp_ask,
            # UPPER BOUND, see module docstring
            "plausible_sequences_upper_bound": sum(
                v // PRINTS_PER_SEQUENCE for v in counts.values()),
            "labels": ("venue evidence only: bbo = book served, print = trade "
                       "that happened. No queue model, no fills. Maker "
                       "opportunities are moments a resting maker COULD have "
                       "been hit, never fills we would have won."),
        }
    return out


def compare(hot: dict, baseline: dict) -> dict:
    """Hot window against the Aug31/Sep1 overnight tape, side by side and
    never merged: they are different liquidity regimes and averaging them
    would hide the only thing worth knowing."""
    rows = {}
    for s in SERIES:
        h = (hot.get("series") or {}).get(s) or {}
        b = (baseline.get("series") or {}).get(s) or {}
        rows[s] = {
            "hot_trades_per_min": h.get("trades_per_min"),
            "overnight_trades_per_min": b.get("trades_per_min"),
            "hot_pct_zero_trade_windows": h.get("pct_windows_zero_trades"),
            "overnight_pct_zero_trade_windows": b.get("pct_windows_zero_trades"),
            "hot_sequences_upper_bound": h.get("plausible_sequences_upper_bound"),
        }
    return {"per_series": rows,
            "labels": "hot and overnight reported side by side, never averaged"}


#: An L5 sequence takes OPEN + EXTEND + COMPLETE. For a controller working ONE
#: market at a time, a series must supply enough independent sequences per hour
#: for the strategy to compound at all. Below this the venue cannot support the
#: behaviour regardless of how good the policy is.
VIABLE_MIN_SEQUENCES_PER_HOUR = 2.0
VIABLE_MIN_TRADES_PER_MIN = 0.5


def verdict(hot: dict) -> dict:
    """VIABLE_FOR_NEXT_L5_STAGE or INSUFFICIENT_OBSERVED_FLOW, per series.

    The thresholds are declared above, before the data, and are deliberately
    modest: two sequences an hour is not a business, it is the floor below
    which there is nothing to measure."""
    minutes = hot.get("span_minutes") or 0.0
    hours = minutes / 60.0 if minutes else 0.0
    per_series, any_viable = {}, False
    for s in SERIES:
        v = (hot.get("series") or {}).get(s) or {}
        seq = v.get("plausible_sequences_upper_bound") or 0
        tpm = v.get("trades_per_min") or 0.0
        seq_hr = (seq / hours) if hours else 0.0
        ok = (seq_hr >= VIABLE_MIN_SEQUENCES_PER_HOUR
              and tpm >= VIABLE_MIN_TRADES_PER_MIN)
        any_viable = any_viable or ok
        per_series[s] = {
            "verdict": "VIABLE_FOR_NEXT_L5_STAGE" if ok
                       else "INSUFFICIENT_OBSERVED_FLOW",
            "sequences_per_hour_upper_bound": round(seq_hr, 2),
            "trades_per_min": tpm,
            "against": {"min_sequences_per_hour": VIABLE_MIN_SEQUENCES_PER_HOUR,
                        "min_trades_per_min": VIABLE_MIN_TRADES_PER_MIN},
        }
    return {
        "overall": "VIABLE_FOR_NEXT_L5_STAGE" if any_viable
                   else "INSUFFICIENT_OBSERVED_FLOW",
        "per_series": per_series,
        "labels": ("sequence counts are UPPER BOUNDS that assume we win every "
                   "print; the real figure is lower. A verdict of VIABLE means "
                   "the venue clears the floor, never that the strategy is "
                   "profitable there."),
    }
