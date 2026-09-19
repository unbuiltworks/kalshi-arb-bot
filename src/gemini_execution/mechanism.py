"""THREE MECHANISM STUDIES: why the complement never arrives, not which price.

Parameter search is over. These ask what the venue was actually doing while
our COMPLETE sat there unfilled.

1. CLOSEST APPROACH. 82% of missed complements "required a cross" -- but that
   is a verdict at one instant, and a quote lives for minutes. This walks the
   opposite book forward in strict chronology and asks how CLOSE executable
   maker territory ever came, for how long, and which way the midpoint was
   moving. "Required a cross" splits into never approached, approached within
   5 ticks, within 2, reached the touch without a qualifying execution, and
   a maker fill that was historically possible.

2. UNDERLYING STATE. A prediction contract is a function of BTC against a
   strike. Whether the complement is reachable may be a fact about the
   underlying, not about the book. CONTEMPORANEOUS INFORMATION ONLY: the
   strike is published at the window's open, and BTC comes from 1-minute
   candles. `settlementValue` is the OUTCOME and is never a feature.

3. LIQUIDITY PRESENCE. Our probe is 0.01 contracts against prints of 263. A
   size that small may prove the plumbing works and nothing else. This sizes
   what a bounded-but-meaningful presence would be from the venue's own
   prints and book.

   WHETHER A LARGER QUOTE WOULD ATTRACT FLOW IS NOT ANSWERABLE HERE and is
   labelled unknowable rather than estimated. Historical replay can say what
   existing flow would have done against a bigger quote; it cannot say what
   flow a bigger quote would have created.

EVERYTHING IS SHADOW. No orders, no sizing decision, no authorisation.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

TICK = D("0.01")

NEVER = "never_approached"
WITHIN_5 = "approached_within_5_ticks"
WITHIN_2 = "approached_within_2_ticks"
TOUCH_NO_EXEC = "touch_reached_but_no_qualifying_external_execution"
MAKER_POSSIBLE = "maker_fill_historically_possible"
NO_BOOK = "no_opposite_book_observed"
APPROACH_BUCKETS = (NEVER, WITHIN_5, WITHIN_2, TOUCH_NO_EXEC,
                    MAKER_POSSIBLE, NO_BOOK)

#: the velocity horizons asked for. Only 60s is historically available from
#: 1-minute candles; the shorter ones are recorded as unavailable rather than
#: interpolated, because an interpolated velocity is a fabrication.
VELOCITY_HORIZONS_S = (5, 15, 30, 60)
AVAILABLE_HORIZONS_S = (60,)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


# ===================================================== 1. CLOSEST APPROACH

def approach(episode: Mapping[str, Any],
             cycles: Sequence[Mapping[str, Any]],
             prints: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """How close did executable maker territory ever come to our COMPLETE?

    STRICT CHRONOLOGY: only books and prints inside the quote's own life are
    considered. Executable maker territory on a leg is everything strictly
    below that leg's ask -- a price at or above the ask is a cross and is not
    territory we may enter.
    """
    leg = str(episode.get("leg")).upper()
    p = _dec(episode.get("price"))
    lo, hi = _dec(episode.get("placed_ms")), _dec(episode.get("ended_ms"))
    if p is None:
        return {"computable": False, "why": "no Model-A price"}
    side = "yes" if leg == "YES" else "no"

    obs = []
    for c in cycles or []:
        ms = _dec(c.get("utc_ms"))
        if ms is None:
            continue
        if lo is not None and ms < lo:
            continue
        if hi is not None and ms > hi:
            continue
        q = ((c.get("book") or {}).get(side) or {})
        bid, ask = _dec(q.get("bid")), _dec(q.get("ask"))
        if ask is None:
            continue
        obs.append({"ms": ms, "bid": bid, "ask": ask,
                    "best_maker": ask - TICK,
                    "gap_ticks": (ask - TICK - p) / TICK,
                    "mid": ((bid + ask) / 2 if bid is not None else None),
                    "depth": c.get("depth")})
    if not obs:
        return {"computable": True, "bucket": NO_BOOK, "observations": 0,
                "why": ("no cycle of ours observed the opposite book while "
                        "this quote rested, so its approach is unmeasured -- "
                        "not zero")}

    gaps = [o["gap_ticks"] for o in obs]
    closest = min(gaps, key=lambda g: abs(g))
    near = [o for o in obs if abs(o["gap_ticks"]) <= abs(closest) + 1]
    dur = (float((near[-1]["ms"] - near[0]["ms"]) / D("1000"))
           if len(near) > 1 else 0.0)

    mids = [o["mid"] for o in obs if o["mid"] is not None]
    drift = (mids[-1] - mids[0]) if len(mids) > 1 else None
    toward = None
    if drift is not None:
        toward = (drift > 0) if leg == "YES" else (drift < 0)

    hit = [t for t in prints or []
           if _dec(t.get("utc_ms")) is not None
           and (lo is None or _dec(t["utc_ms"]) >= lo)
           and (hi is None or _dec(t["utc_ms"]) <= hi)
           and _reaches(leg, p, t)]

    depth_formed = _depth_formation(obs, leg, p)

    if hit:
        bucket = MAKER_POSSIBLE
    elif closest <= 0:
        bucket = TOUCH_NO_EXEC
    elif closest <= 2:
        bucket = WITHIN_2
    elif closest <= 5:
        bucket = WITHIN_5
    else:
        bucket = NEVER

    return {
        "computable": True, "bucket": bucket,
        "observations": len(obs),
        "model_a_price": str(p), "leg": leg,
        "closest_gap_ticks": float(round(closest, 3)),
        "seconds_near_the_minimum": round(dur, 1),
        "midpoint_drift": (str(drift) if drift is not None else None),
        "midpoint_moved_toward_our_complete": toward,
        "opposite_depth_formation": depth_formed,
        "reaching_prints_in_the_quotes_life": len(hit),
        "rested_s": episode.get("rested_s"),
        "why": ("closest gap is measured against the best MAKER price -- one "
                "tick under the ask -- because at or above the ask is a cross "
                "and not territory we may enter"),
    }


def _reaches(leg: str, price: Any, t: Mapping[str, Any]) -> bool:
    p, tp = _dec(price), _dec(t.get("price"))
    if p is None or tp is None:
        return False
    want = "sell" if str(leg).upper() == "YES" else "buy"
    if str(t.get("type")) != want:
        return False
    return (tp <= p) if str(leg).upper() == "YES" else (tp >= (D("1") - p))


def _depth_formation(obs: Sequence[Mapping[str, Any]], leg: str,
                     price: D) -> Dict[str, Any]:
    """Did size ever build on the side that would have to trade with us?

    Only the YES ladder is published, so for a NO rung this is UNKNOWN and
    says so instead of reporting an absence as a zero.
    """
    if str(leg).upper() != "YES":
        return {"observable": False,
                "why": "Gemini publishes no NO ladder, so depth formation on "
                       "this leg is unknown, not absent"}
    firsts, lasts = None, None
    for o in obs:
        d = o.get("depth") or {}
        if not d.get("readable"):
            continue
        tot = sum((_dec(l.get("qty")) or D("0"))
                  for l in ((d.get("bids") or {}).get("levels") or [])
                  if (_dec(l.get("price")) or D("0")) >= price)
        if firsts is None:
            firsts = tot
        lasts = tot
    if firsts is None:
        return {"observable": False, "why": "no readable ladder in this life"}
    return {"observable": True, "at_or_better_first": str(firsts),
            "at_or_better_last": str(lasts),
            "change": str(lasts - firsts),
            "built": bool(lasts > firsts)}


def approach_census(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    ok = [r for r in rows if r.get("computable")]
    by = {b: 0 for b in APPROACH_BUCKETS}
    for r in ok:
        by[r["bucket"]] = by.get(r["bucket"], 0) + 1
    gaps = sorted(r["closest_gap_ticks"] for r in ok
                  if r.get("closest_gap_ticks") is not None)
    tow = [r["midpoint_moved_toward_our_complete"] for r in ok
           if r.get("midpoint_moved_toward_our_complete") is not None]
    built = [r for r in ok
             if (r.get("opposite_depth_formation") or {}).get("built")]
    return {
        "episodes": len(ok),
        "by_bucket": by,
        "median_closest_gap_ticks": (gaps[len(gaps) // 2] if gaps else None),
        "min_closest_gap_ticks": (gaps[0] if gaps else None),
        "share_where_midpoint_moved_toward_us": (
            round(sum(1 for t in tow if t) / len(tow), 3) if tow else None),
        "midpoint_direction_observed_on": len(tow),
        "episodes_where_opposite_depth_built": len(built),
        "depth_observable_on": sum(
            1 for r in ok
            if (r.get("opposite_depth_formation") or {}).get("observable")),
        "labels": ("SHADOW. Strict chronology: only books and prints inside "
                   "each quote's own life are considered."),
    }


# ================================================== 2. UNDERLYING STATE

CANDLES_URL = "https://api.gemini.com/v2/candles/btcusd/1m"


def fetch_candles(*, timeout: float = 25.0) -> Dict[str, Any]:
    """1-minute BTC bars. ~1,440 of them, a rolling 24 hours.

    Rows are [ms, open, high, low, close, volume], newest first as served.
    """
    from src.l5_shadow import venues as V
    r = V.get(CANDLES_URL, timeout=timeout)
    if not r.get("ok"):
        return {"readable": False, "why": r.get("err")}
    import json as _json
    try:
        rows = _json.loads(r["body"])
    except ValueError:
        return {"readable": False, "why": "unparseable"}
    bars = []
    for x in rows or []:
        try:
            bars.append({"ms": D(str(x[0])), "open": D(str(x[1])),
                         "high": D(str(x[2])), "low": D(str(x[3])),
                         "close": D(str(x[4]))})
        except Exception:                                     # noqa: BLE001
            continue
    bars.sort(key=lambda b: b["ms"])
    return {"readable": True, "bars": bars, "n": len(bars),
            "first_ms": str(bars[0]["ms"]) if bars else None,
            "last_ms": str(bars[-1]["ms"]) if bars else None}


def _bar_at(bars: Sequence[Mapping[str, Any]], ms: D) -> Optional[Mapping]:
    """The bar in force at this instant. NEVER a later one."""
    prev = None
    for b in bars:
        if b["ms"] > ms:
            break
        prev = b
    return prev


def underlying_state(*, at_ms: Any, strike: Any, held_leg: str,
                     tte_s: Any, bars: Sequence[Mapping[str, Any]],
                     vol_lookback_bars: int = 15) -> Dict[str, Any]:
    """BTC against the strike, from contemporaneous information only.

    The strike is published at the window's open (`strike.availableAt`), so it
    is known before any of this. `settlementValue` is the OUTCOME and is not
    read here at all.

    ONLY THE 60s VELOCITY IS AVAILABLE. The 5/15/30s horizons the study asks
    for cannot be built from 1-minute bars, and interpolating them would be a
    fabrication, so they are reported as unavailable with the reason.
    """
    ms, k = _dec(at_ms), _dec(strike)
    if ms is None or k is None or not bars:
        return {"computable": False,
                "why": ("no strike published for this contract" if k is None
                        else "no bar coverage at this instant")}
    b = _bar_at(bars, ms)
    if b is None:
        return {"computable": False,
                "why": "the candle history does not reach back this far"}
    px = b["close"]
    idx = [i for i, x in enumerate(bars) if x["ms"] == b["ms"]]
    i = idx[0] if idx else 0
    prior = bars[max(0, i - vol_lookback_bars):i + 1]
    rng = [x["high"] - x["low"] for x in prior]
    vol = (sum(rng, D("0")) / D(len(rng))) if rng else None

    dist = px - k
    held = str(held_leg).upper()
    # holding YES is a bet BTC finishes at or above the strike
    favourable = (dist > 0) if held == "YES" else (dist < 0)

    vels: Dict[str, Any] = {}
    for h in VELOCITY_HORIZONS_S:
        if h not in AVAILABLE_HORIZONS_S:
            vels["velocity_%ds" % h] = None
            vels["velocity_%ds_why" % h] = (
                "1-minute bars cannot resolve %ds; interpolating it would be "
                "a fabrication" % h)
            continue
        j = i - 1
        vels["velocity_%ds" % h] = (str(px - bars[j]["close"])
                                    if j >= 0 else None)
    return {
        "computable": True,
        "bar_ms": str(b["ms"]), "btc_close": str(px), "strike": str(k),
        "distance_from_strike": str(dist),
        "recent_volatility_1m_range": (str(vol) if vol is not None else None),
        "distance_over_volatility": (str(round(dist / vol, 4))
                                     if vol is not None and vol > 0
                                     else None),
        "held_leg": held,
        "underlying_favours_held_side": bool(favourable),
        "tte_s": tte_s,
        **vels,
        "labels": ("CONTEMPORANEOUS ONLY. The strike is published at the "
                   "window's open; settlementValue is the OUTCOME and is "
                   "never read as a feature."),
    }


def underlying_contrast(rows: Sequence[Mapping[str, Any]], *,
                        outcome: str = "par_constrained") -> Dict[str, Any]:
    """Do recyclable states differ from leakage states in the underlying?

    Medians with both cell sizes on every line. NO SELECTOR: with the cells
    this sample produces, a difference is a description of these windows.
    """
    ok = [r for r in rows if r.get("computable")]
    good = [r for r in ok if r.get(outcome)]
    bad = [r for r in ok if not r.get(outcome)]
    keys = ("distance_from_strike", "distance_over_volatility",
            "recent_volatility_1m_range", "velocity_60s", "tte_s")
    feats = {}
    for k in keys:
        a = sorted(x for x in (_dec(r.get(k)) for r in good) if x is not None)
        b = sorted(x for x in (_dec(r.get(k)) for r in bad) if x is not None)
        feats[k] = {
            "recyclable_n": len(a), "leakage_n": len(b),
            "recyclable_median": (str(a[len(a) // 2]) if a else None),
            "leakage_median": (str(b[len(b) // 2]) if b else None),
            "difference": (str(a[len(a) // 2] - b[len(b) // 2])
                           if a and b else None),
        }
    fav_g = [r for r in good if r.get("underlying_favours_held_side")]
    fav_b = [r for r in bad if r.get("underlying_favours_held_side")]
    return {
        "states": len(ok), "recyclable": len(good), "leakage": len(bad),
        "smaller_cell": min(len(good), len(bad)),
        "supports_a_selector": bool(min(len(good), len(bad)) >= 30),
        "underlying_favoured_held_side": {
            "recyclable_share": (round(len(fav_g) / len(good), 3)
                                 if good else None),
            "leakage_share": (round(len(fav_b) / len(bad), 3)
                              if bad else None)},
        "features": feats,
        "why": ("the smaller cell holds %d states. No selector follows from "
                "this and none is proposed; 30 per cell would be the floor."
                % min(len(good), len(bad))),
    }


# ============================================== 3. LIQUIDITY PRESENCE

#: fractions of observed liquidity a bounded presence might represent. These
#: are ARITHMETIC REFERENCE POINTS for reading the table, not proposals.
PRESENCE_FRACTIONS = ("0.01", "0.02", "0.05", "0.10")

CAUSAL_UNKNOWABLE = (
    "WHETHER A LARGER QUOTE WOULD ATTRACT FLOW IS NOT ANSWERABLE FROM "
    "HISTORICAL REPLAY. The tape records what happened against the book that "
    "existed. It cannot record what a different book would have caused. Every "
    "figure here describes existing flow meeting a hypothetical size; none of "
    "it estimates flow that a larger presence might create, and no such "
    "estimate should be read into it.")


def presence(prints: Sequence[Mapping[str, Any]],
             books: Sequence[Mapping[str, Any]], *,
             our_size: Any = "0.01") -> Dict[str, Any]:
    """What a bounded-but-meaningful resting size would be, from the venue.

    Two independent anchors, because they answer different questions: the
    PRINT distribution says what a counterparty typically brings, and the
    DISPLAYED BOOK says what we would be queuing behind.
    """
    ours = _dec(our_size) or D("0.01")
    sizes = sorted(x for x in (_dec(t.get("amount")) for t in prints or [])
                   if x is not None)
    tops, ladders = [], []
    for d in books or []:
        if not (d or {}).get("readable"):
            continue
        lv = ((d.get("bids") or {}).get("levels") or [])
        qs = [_dec(l.get("qty")) for l in lv]
        qs = [q for q in qs if q is not None]
        if qs:
            tops.append(qs[0])
            ladders.append(sum(qs, D("0")))
    med_print = sizes[len(sizes) // 2] if sizes else None
    med_top = (sorted(tops)[len(tops) // 2] if tops else None)
    med_lad = (sorted(ladders)[len(ladders) // 2] if ladders else None)

    frac_of_print = (ours / med_print if med_print else None)
    frac_of_top = (ours / med_top if med_top else None)

    bounded = {}
    for f in PRESENCE_FRACTIONS:
        ff = D(f)
        bounded[f] = {
            "of_median_print": (str(round(med_print * ff, 4))
                                if med_print else None),
            "of_median_touch_depth": (str(round(med_top * ff, 4))
                                      if med_top else None),
            "of_median_displayed_ladder": (str(round(med_lad * ff, 4))
                                           if med_lad else None),
        }
    return {
        "our_size": str(ours),
        "prints_seen": len(sizes),
        "median_print": (str(med_print) if med_print else None),
        "p25_print": (str(sizes[len(sizes) // 4]) if sizes else None),
        "p75_print": (str(sizes[(3 * len(sizes)) // 4]) if sizes else None),
        "max_print": (str(sizes[-1]) if sizes else None),
        "books_seen": len(tops),
        "median_touch_depth": (str(med_top) if med_top else None),
        "median_displayed_ladder": (str(med_lad) if med_lad else None),
        "our_size_over_median_print": (str(round(frac_of_print, 8))
                                       if frac_of_print else None),
        "our_size_over_median_touch_depth": (str(round(frac_of_top, 10))
                                             if frac_of_top else None),
        "bounded_presence_at_fraction": bounded,
        "fractions_are": ("arithmetic reference points for reading this "
                          "table. They are not proposals and nothing is "
                          "wired to them."),
        "causal_question": CAUSAL_UNKNOWABLE,
    }


def presence_verdict(p: Mapping[str, Any], *,
                     plumbing_threshold: str = "0.001") -> Dict[str, Any]:
    """Is our probe large enough to say anything about being a maker?

    The test is blunt on purpose: if our size is a rounding error against
    both the median print and the median touch, then a fill tells us the
    wire works and essentially nothing about liquidity provision.
    """
    a = _dec(p.get("our_size_over_median_print"))
    b = _dec(p.get("our_size_over_median_touch_depth"))
    th = _dec(plumbing_threshold) or D("0.001")
    known = [x for x in (a, b) if x is not None]
    tiny = [x for x in known if x < th]
    # THREE-WAY, because the binary form read as encouraging on real data:
    # 0.00125 of a median print sits above a 0.001 threshold and still means a
    # single typical counterparty could fill us 800 times over. "Meaningful on
    # at least one anchor" was true and misleading.
    if not known:
        verdict, reading = "UNMEASURED", "no anchor was observable"
    elif len(tiny) == len(known):
        verdict = "PLUMBING_ONLY"
        reading = ("at this size a fill evidences that the order path works "
                   "and that a counterparty crossed our price. It does not "
                   "evidence liquidity provision: the quantity is a rounding "
                   "error against both what counterparties bring and what is "
                   "already displayed.")
    elif tiny:
        verdict = "MARGINAL"
        reading = ("one anchor is above the threshold and the other is a "
                   "rounding error. A fill still says far more about the wire "
                   "than about presence, and the ratios below should be read "
                   "directly rather than through this label.")
    else:
        verdict = "MEANINGFUL"
        reading = ("our size is a non-trivial fraction of both anchors, so "
                   "fills carry information about presence.")
    return {
        "our_size_over_median_print": p.get("our_size_over_median_print"),
        "our_size_over_median_touch_depth": p.get(
            "our_size_over_median_touch_depth"),
        "median_print": p.get("median_print"),
        "median_touch_depth": p.get("median_touch_depth"),
        "threshold": str(th),
        "anchors_below_threshold": len(tiny),
        "anchors_observable": len(known),
        "both_anchors_below_threshold": bool(known and len(tiny) == len(known)),
        "verdict": verdict,
        "reading": reading,
        "threshold_caveat": ("the threshold is arbitrary; the RATIOS are the "
                             "evidence and the label is a convenience."),
        "causal_question": CAUSAL_UNKNOWABLE,
    }
