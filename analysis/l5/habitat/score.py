"""L5_HABITAT_SCORE -- is a venue somewhere a passive quote surface can live?

OWNER 2026-09-17. Venue-neutral, real observations only, and deliberately NOT a
single number.

THE PRIMARY DIAGNOSTIC IS POST-FILL CONTINUATION OPPORTUNITY. After acquiring
one side, how many credible OPPOSITE-side interactions remain before the market
settles? MATCHED_SHARE_LINEAGE_V1 found, on history, that the complement has to
arrive within tens of seconds to be worth anything -- so a venue with plenty of
volume but no timely opposite interaction is a venue where L5's mechanism
cannot run, however busy it looks. Raw volume is reported, and it is not the
headline.

NO COMPOSITE SCORE. Every component is reported separately and a failed
component is never averaged away behind a passing one. `composite` is
deliberately null and says why.

IDENTICAL DEFINITIONS ACROSS VENUES, DIFFERENT ECONOMIC MAPPINGS. The
measurements below are computed the same way everywhere. What a "pair" MEANS is
not the same everywhere and is never flattened:

    PMUS / Polymarket   two outcome tokens, both BOUGHT: cost = b_yes + b_no
    GEMINI              ONE contract (the -DOWN book does not exist), so the
                        analogue is BUY b + SELL s and the sale is a SYNTHETIC
                        complement worth (1 - s). Not the same object, and a
                        filled buy with no sale is an open position, not half a
                        pair.

An INTERACTION is a trade that actually printed. On a venue that publishes
prints, prints are used; on one that publishes only a cumulative traded
quantity, an interaction is that quantity RISING, priced at the venue's own
`last`. Both are real trades; neither is a book movement, and book movement is
never counted as interaction -- that distinction cost us a 97%-vs-46% error
once already.
"""
from __future__ import annotations

import collections
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

LABEL = "L5_HABITAT_SCORE_V1"
HORIZONS_S = (5, 30, 60, 120)
TICK = 0.01

COMPONENTS = (
    "post_fill_continuation_opportunity",     # PRIMARY
    "expected_credible_interactions_remaining",
    "two_sided_uptime",
    "reversal_frequency_per_min",
    "spread",
    "touch_depth",
    "interaction_frequency_per_min",
    "time_to_settlement",
    "maker_post_only_validity",
    "known_fees",
    "capacity",
)


def _f(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _ts(v) -> Optional[float]:
    f = _f(v)
    if f is not None:
        return f
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:                                        # noqa: BLE001
        return None


def _med(xs):
    v = sorted(x for x in xs if x is not None)
    return v[len(v) // 2] if v else None


def _pct(xs, p):
    v = sorted(x for x in xs if x is not None)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


# ---------------------------------------------------------------------------
# normalizers: every venue becomes the same two streams

def from_gemini(paths: Sequence[str], *, market_prefix: str = "GEMI-BTC05M"
                ) -> Dict[str, dict]:
    """Gemini publishes explicit prints, and quotes as ladders."""
    out: Dict[str, dict] = collections.defaultdict(
        lambda: {"quotes": [], "interactions": [], "expiry": None})
    for p in paths:
        for line in open(p, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k, m = r.get("kind"), str(r.get("market") or "")
            if not m.startswith(market_prefix):
                continue
            t = _f(r.get("t"))
            if k == "window":
                out[m]["expiry"] = _f(r.get("expiry_t"))
            elif k == "bbo":
                bids, asks = r.get("bids") or [], r.get("asks") or []
                bb = max((_f(x[0]) for x in bids if _f(x[0]) is not None),
                         default=None)
                aa = min((_f(x[0]) for x in asks if _f(x[0]) is not None),
                         default=None)
                bs = sum(_f(x[1]) or 0 for x in bids if _f(x[0]) == bb) if bb else None
                asz = sum(_f(x[1]) or 0 for x in asks if _f(x[0]) == aa) if aa else None
                out[m]["quotes"].append({"t": t, "bid": bb, "ask": aa,
                                         "bid_size": bs, "ask_size": asz})
            elif k == "print":
                out[m]["interactions"].append(
                    {"t": t, "price": _f(r.get("price")),
                     "qty": _f(r.get("qty"))})
    return dict(out)


def from_pmus(paths: Sequence[str]) -> Dict[str, dict]:
    """PMUS publishes a cumulative traded quantity, not prints. An interaction
    is that quantity RISING, priced at the venue's own `last`. This is a real
    trade; a book movement with no volume change is NOT one and is excluded."""
    out: Dict[str, dict] = collections.defaultdict(
        lambda: {"quotes": [], "interactions": [], "expiry": None})
    last_vol: Dict[str, float] = {}
    for p in paths:
        for line in open(p, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") != "bbo":
                continue
            s = str(r.get("slug") or "")
            t = _ts(r.get("t"))
            out[s]["quotes"].append({
                "t": t, "bid": _f(r.get("bid")), "ask": _f(r.get("ask")),
                "bid_size": _f(r.get("bid_shares")),
                "ask_size": _f(r.get("ask_shares"))})
            v, lastpx = _f(r.get("shares_traded")), _f(r.get("last"))
            if v is not None:
                prev = last_vol.get(s)
                if prev is not None and v > prev and lastpx is not None:
                    out[s]["interactions"].append(
                        {"t": t, "price": lastpx, "qty": v - prev})
                last_vol[s] = v
    return dict(out)


def from_book_context(paths: Sequence[str]) -> Dict[str, dict]:
    """Polymarket BTC windows, from the L5_BOOK_CONTEXT recorder."""
    out: Dict[str, dict] = collections.defaultdict(
        lambda: {"quotes": [], "interactions": [], "expiry": None})
    for p in paths:
        for line in open(p, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            k = r.get("kind")
            if k == "book_observation":
                # keyed by market AND outcome token: the two outcomes of one
                # window are two separate books and averaging them would
                # invent a spread neither one has
                key = "%s|%s" % (r.get("slug"), r.get("outcome"))
                out[key]["quotes"].append({
                    "t": _f(r.get("L5_OBSERVATION_CLOCK"))
                         or _f(r.get("MARKET_STATE_CLOCK")),
                    "bid": _f(r.get("bid")), "ask": _f(r.get("ask")),
                    "bid_size": _f(r.get("bid_size")),
                    "ask_size": _f(r.get("ask_size"))})
            elif k == "l5_event_seen":
                # L5's own fills are REAL trades and are the only interaction
                # stream this recorder sees. They are a LOWER BOUND on the
                # market's interactions -- everyone else's trades are invisible
                # here -- and the price is not published on this row, so these
                # cannot feed the continuation test. Counted, and named.
                for oc in ("Up", "Down"):
                    out["%s|%s" % (r.get("slug"), oc)]["interactions"].append(
                        {"t": _f(r.get("L5_EVENT_CLOCK")), "price": None,
                         "qty": None, "source": "L5_fill_lower_bound"})
    return dict(out)


def public_trades_for(markets: Dict[str, dict]) -> Dict[str, dict]:
    """Fill in the interaction stream for the Polymarket recorder.

    The recorder sees L5's fills but the venue publishes no price on that row,
    so continuation was UNMEASURABLE from it. The market's own public prints DO
    carry a price and are fetched here, read-only and AFTER the fact, so the
    running recorder is not touched. These are everyone's trades, not L5's --
    which is exactly what a habitat measure needs."""
    from src.l5_shadow import venues as V
    out: Dict[str, dict] = {}
    seen: Dict[str, list] = {}
    for key, v in markets.items():
        slug = key.split("|")[0]
        if slug not in seen:
            w = V.pm_window(slug)
            cid = (w or {}).get("condition_id")
            tr = V.pm_trades(cid) if cid else None
            seen[slug] = [{"t": float(x["t"]), "price": float(x["price"]),
                           "qty": float(x["size"])} for x in (tr or [])]
        out[key] = {"quotes": v["quotes"], "expiry": v.get("expiry"),
                    "interactions": seen[slug]}
    return out


# ---------------------------------------------------------------------------
# the components, computed identically everywhere

def continuation(q: Sequence[dict], inter: Sequence[dict]) -> dict:
    """PRIMARY. Treat each two-sided observation as a hypothetical first-side
    fill and ask whether a credible OPPOSITE interaction follows.

    Bought at the bid -> the complement must trade at or above the ask.
    Sold at the ask   -> the complement must trade at or below the bid.
    Both directions are reported, and so is the harder of the two, because a
    surface needs whichever side it did not just fill."""
    its = sorted((x for x in inter if x["t"] is not None), key=lambda x: x["t"])
    ts = [x["t"] for x in its]
    res: Dict[str, Any] = {}
    # A PRICELESS INTERACTION STREAM CANNOT ANSWER THIS. The Polymarket
    # recorder sees L5's fills but the venue does not publish a price on that
    # row, so "no opposite interaction" would be indistinguishable from "we
    # cannot tell". Refuse rather than return a zero that reads as a
    # measurement.
    if its and not any(x.get("price") is not None for x in its):
        return {"UNMEASURABLE": ("interactions carry no price on this stream, "
                                 "so a trade cannot be placed relative to the "
                                 "touch; this is not a zero")}
    for h in HORIZONS_S:
        up = dn = n = 0
        for o in q:
            if o["t"] is None or o["bid"] is None or o["ask"] is None:
                continue
            n += 1
            lo = _bisect(ts, o["t"])
            hi = _bisect(ts, o["t"] + h)
            win = its[lo:hi]
            if any(x["price"] is not None and x["price"] >= o["ask"] for x in win):
                up += 1
            if any(x["price"] is not None and x["price"] <= o["bid"] for x in win):
                dn += 1
        res["p_opposite_within_%ds" % h] = {
            "n_two_sided_observations": n,
            "after_buying_the_bid": round(up / n, 4) if n else None,
            "after_selling_the_ask": round(dn / n, 4) if n else None,
            "harder_side": round(min(up, dn) / n, 4) if n else None,
        }
    return res


def _bisect(a: Sequence[float], x: float) -> int:
    lo, hi = 0, len(a)
    while lo < hi:
        m = (lo + hi) // 2
        if a[m] < x:
            lo = m + 1
        else:
            hi = m
    return lo


def score_market(m: dict, *, window_s: Optional[float], venue: str) -> dict:
    q = [x for x in m["quotes"] if x["t"] is not None]
    q.sort(key=lambda x: x["t"])
    inter = m["interactions"]
    if not q:
        return {"n_quotes": 0, "verdict": "NO_OBSERVATIONS"}
    span = (q[-1]["t"] - q[0]["t"]) or 1.0
    two = [x for x in q if x["bid"] is not None and x["ask"] is not None]
    spreads = [x["ask"] - x["bid"] for x in two]
    mids = [(x["bid"] + x["ask"]) / 2.0 for x in two]
    dmid = [b - a for a, b in zip(mids, mids[1:])]
    rev = sum(1 for a, b in zip(dmid, dmid[1:]) if a * b < 0)
    legal = sum(1 for s in spreads if s >= TICK)
    # a credible opposite interaction rate, per second, from the harder side
    cont = continuation(q, inter)
    hard60 = (cont.get("p_opposite_within_60s") or {}).get("harder_side")
    rate = (len(inter) / span) if span else 0.0
    return {
        "n_quotes": len(q), "n_interactions": len(inter),
        "observed_span_s": round(span, 1),
        "post_fill_continuation_opportunity": cont,
        "expected_credible_interactions_remaining": (
            None if window_s is None else
            round(rate * 0.5 * (window_s / 2.0), 2)),
        "two_sided_uptime": round(len(two) / len(q), 4),
        "reversal_frequency_per_min": (round(60.0 * rev / span, 3)
                                       if span else None),
        "spread": {"median": _med(spreads), "p25": _pct(spreads, 0.25),
                   "p75": _pct(spreads, 0.75),
                   "median_ticks": (round(_med(spreads) / TICK, 1)
                                    if _med(spreads) else None)},
        "touch_depth": {"median_bid": _med([x["bid_size"] for x in two]),
                        "median_ask": _med([x["ask_size"] for x in two]),
                        "median_harder_side": _med(
                            [min(x["bid_size"] or 0, x["ask_size"] or 0)
                             for x in two])},
        "interaction_frequency_per_min": round(60.0 * rate, 3),
        "maker_post_only_validity": (round(legal / len(spreads), 4)
                                     if spreads else None),
        "capacity": {
            "median_touch_notional_usd": _med(
                [(x["bid_size"] or 0) * (x["bid"] or 0) for x in two]),
            "basis": "touch size x touch price; ladder depth beyond the touch "
                     "is not counted here",
        },
        "_hard60": hard60,
    }


def aggregate(markets: Dict[str, dict], *, venue: str,
              window_s: Optional[float], fees: dict,
              economic_mapping: str) -> dict:
    per = {k: score_market(v, window_s=window_s, venue=venue)
           for k, v in markets.items()}
    ok = [v for v in per.values() if v.get("n_quotes")]
    if not ok:
        return {"venue": venue, "verdict": "NO_OBSERVATIONS", "markets": 0}

    def med_of(path):
        vals = []
        for v in ok:
            cur = v
            for p in path:
                cur = (cur or {}).get(p) if isinstance(cur, dict) else None
            if isinstance(cur, (int, float)):
                vals.append(cur)
        return _med(vals)

    # POOLED = counts summed ACROSS markets, never event streams merged.
    #
    # The first version of this merged every market's quotes and every
    # market's interactions into one call, so an observation in market X could
    # be "completed" by a trade in market Y. On a 325-market venue that
    # inflated PMUS from a few percent to 0.83 -- a cross-market artifact, not
    # flow. Counts are therefore accumulated per market and added.
    agg: Dict[int, List[int]] = {h: [0, 0, 0, 0] for h in HORIZONS_S}
    unmeasurable = 0
    for v in ok:
        c = v.get("post_fill_continuation_opportunity") or {}
        if "UNMEASURABLE" in c:
            unmeasurable += 1
            continue
        for h in HORIZONS_S:
            cell = c.get("p_opposite_within_%ds" % h) or {}
            n = cell.get("n_two_sided_observations") or 0
            agg[h][0] += n
            agg[h][1] += int(round((cell.get("after_buying_the_bid") or 0) * n))
            agg[h][2] += int(round((cell.get("after_selling_the_ask") or 0) * n))
            agg[h][3] += int(round((cell.get("harder_side") or 0) * n))
    pooled = {"p_opposite_within_%ds" % h: {
        "n_two_sided_observations": agg[h][0],
        "after_buying_the_bid": (round(agg[h][1] / agg[h][0], 4) if agg[h][0] else None),
        "after_selling_the_ask": (round(agg[h][2] / agg[h][0], 4) if agg[h][0] else None),
        "harder_side": (round(agg[h][3] / agg[h][0], 4) if agg[h][0] else None),
    } for h in HORIZONS_S}
    if unmeasurable == len(ok):
        pooled = {"UNMEASURABLE": "no market on this venue could be measured"}
    pooled["markets_unmeasurable"] = unmeasurable

    return {
        "label": LABEL, "venue": venue, "markets": len(ok),
        "post_fill_continuation_opportunity_POOLED": pooled,
        "aggregation_note": (
            "POOLED sums each market's own counts -- an observation is only "
            "ever completed by a trade IN THE SAME MARKET. MEDIAN below is the median "
            "MARKET, which on a venue of mostly-idle markets reports an idle "
            "market. Read POOLED for 'is there flow here at all' and MEDIAN "
            "for 'what is a typical market like'."),
        "economic_mapping": economic_mapping,
        "total_quotes": sum(v["n_quotes"] for v in ok),
        "total_interactions": sum(v["n_interactions"] for v in ok),
        # ---- PRIMARY, reported first and never folded into anything
        "post_fill_continuation_opportunity": {
            "p_opposite_within_%ds" % h: {
                "after_buying_the_bid": med_of(
                    ["post_fill_continuation_opportunity",
                     "p_opposite_within_%ds" % h, "after_buying_the_bid"]),
                "after_selling_the_ask": med_of(
                    ["post_fill_continuation_opportunity",
                     "p_opposite_within_%ds" % h, "after_selling_the_ask"]),
                "harder_side": med_of(
                    ["post_fill_continuation_opportunity",
                     "p_opposite_within_%ds" % h, "harder_side"]),
            } for h in HORIZONS_S},
        "expected_credible_interactions_remaining": med_of(
            ["expected_credible_interactions_remaining"]),
        "two_sided_uptime": med_of(["two_sided_uptime"]),
        "reversal_frequency_per_min": med_of(["reversal_frequency_per_min"]),
        "spread": {"median": med_of(["spread", "median"]),
                   "median_ticks": med_of(["spread", "median_ticks"])},
        "touch_depth": {
            "median_bid": med_of(["touch_depth", "median_bid"]),
            "median_ask": med_of(["touch_depth", "median_ask"]),
            "median_harder_side": med_of(["touch_depth", "median_harder_side"])},
        "interaction_frequency_per_min": med_of(
            ["interaction_frequency_per_min"]),
        "time_to_settlement": {"window_s": window_s},
        "maker_post_only_validity": med_of(["maker_post_only_validity"]),
        "known_fees": fees,
        "capacity": {"median_touch_notional_usd": med_of(
            ["capacity", "median_touch_notional_usd"])},
        "composite": None,
        "composite_reason": (
            "DELIBERATELY NOT COMPUTED. A composite lets a failed component "
            "hide behind a passing one -- a venue with huge volume and no "
            "timely opposite interaction is uninhabitable for this mechanism "
            "however good its average looks. Read the components."),
        "per_market": per,
    }
