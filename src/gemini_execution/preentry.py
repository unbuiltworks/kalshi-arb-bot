"""PRE-ENTRY HABITAT: what is observable about a window BEFORE we commit.

MODEL B ONLY. This measures the venue. It does not touch the L5 policy, its
prices, its sizes, its bounds or its actions after admission, and nothing
here is wired into `eligible_symbol`. `rank_key` returns an ORDINAL for
sorting a report; it is not an admission rule and there is no threshold in
this module.

THE STRUCTURAL FACT THAT SHAPES ALL OF IT: the BTC5m grid carries exactly ONE
contract per five-minute slot, so at any instant exactly one contract sits
inside a 240-280 s entry band. "Rank across windows" therefore cannot mean
"pick the best of several simultaneous candidates" -- there are none. It can
only mean ADMIT THIS ONE OR WAIT FOR THE NEXT. So the question a selector
lives or dies on is:

    does what we can see BEFORE entry predict the flow we get DURING the
    window?

If window flow is independent from one five-minute slot to the next, no
pre-entry read can help and a selector is a way to lose windows for nothing.
If it is regime-like -- quiet stretches and busy stretches -- a selector can
work. That is an empirical question and this module exists to answer it,
not to assume it.

WHY THE CANDIDATE'S OWN TAPE IS NOT ENOUGH. At TTE 280 the contract is ~20
seconds old, so its own print tape is nearly empty BY CONSTRUCTION, and a low
count there means "new", not "quiet". Three sources are therefore measured
side by side and kept apart:

  own      the candidate's own tape so far, with its age stated, so a rate
           is never computed over an unknown denominator
  prior    the PREDECESSOR five-minute contract, which has a full life of
           history and is the closest thing to a regime reading
  spot     BTC/USD public trades -- the underlying's activity and movement,
           which exists regardless of which contract is listed

NOTHING HERE IS REALIZED, and no number here is permitted to change a price,
a size, a bound or an action.
"""
from __future__ import annotations

import time as _time
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: the underlying's public trade tape, which has history when a 20-second-old
#: contract does not
SPOT_SYMBOL = "btcusd"

#: how far back a "recent" print window looks, in seconds
RECENT_S = 120.0

#: the BTC5m grid: one contract per five minutes
SLOT_S = 300.0

#: below this many (prior, actual) pairs, NO persistence figure is stated.
#: A rank correlation over a handful of windows is a shape, not a finding.
MIN_PAIRS = 20


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _rate_per_min(n: Any, seconds: Any) -> Optional[str]:
    """Prints per minute, or None. A rate over an unknown or non-positive
    denominator is not a small rate; it is not a rate."""
    s = _dec(seconds)
    if not isinstance(n, int) or s is None or s <= 0:
        return None
    return str(round(D(n) * D("60") / s, 3))


def tape_window(flow: Any, *, now_ms: Optional[float] = None,
                recent_s: float = RECENT_S) -> Dict[str, Any]:
    """Summarise a `habitat.public_flow` read over the last `recent_s`.

    Never raises, and an unreadable tape stays unreadable rather than
    collapsing to zero prints.
    """
    out: Dict[str, Any] = {"recent_s": recent_s}
    if not isinstance(flow, Mapping) or not flow.get("readable"):
        out.update({"readable": False,
                    "why": ((flow or {}).get("why")
                            if isinstance(flow, Mapping) else "no tape read"),
                    "n_recent": None, "prints_per_min": None,
                    "since_last_print_s": None})
        return out
    prints = [p for p in (flow.get("prints") or []) if isinstance(p, Mapping)]
    t_ms = now_ms if now_ms is not None else _time.time() * 1000.0
    cutoff = t_ms - recent_s * 1000.0
    recent = [p for p in prints
              if isinstance(p.get("utc_ms"), (int, float))
              and p["utc_ms"] >= cutoff]
    newest = flow.get("newest_utc_ms")
    since = (round((t_ms - newest) / 1000.0, 1)
             if isinstance(newest, (int, float)) else None)
    buys = sum(1 for p in recent if p.get("side") == "buy")
    sells = sum(1 for p in recent if p.get("side") == "sell")
    vol = sum((_dec(p.get("qty")) or D("0") for p in recent), D("0"))
    out.update({
        "readable": True,
        "n_total_on_tape": flow.get("n_prints"),
        "n_recent": len(recent),
        "prints_per_min": _rate_per_min(len(recent), recent_s),
        "since_last_print_s": since,
        "recent_volume": str(vol),
        "buy_prints": buys, "sell_prints": sells,
        # TWO-SIDED where observable: one side printing is not a two-sided
        # market, and a passive quote on the untraded side has no counterparty
        "two_sided": bool(buys and sells),
        "side_balance": (round(min(buys, sells) / max(buys, sells), 3)
                         if buys and sells else (0.0 if (buys or sells)
                                                 else None)),
    })
    return out


def spot_movement(flow: Any) -> Dict[str, Any]:
    """Underlying activity and movement from the PUBLIC spot tape.

    Movement is reported as the observed price RANGE over the prints on the
    tape, relative to the last price. It is a measurement of the underlying,
    never a forecast and never an input to a quote.
    """
    out: Dict[str, Any] = {"symbol": SPOT_SYMBOL}
    if not isinstance(flow, Mapping) or not flow.get("readable"):
        out.update({"readable": False,
                    "why": ((flow or {}).get("why")
                            if isinstance(flow, Mapping) else "no tape read")})
        return out
    px = [_dec(p.get("price")) for p in (flow.get("prints") or [])
          if isinstance(p, Mapping)]
    px = [x for x in px if x is not None]
    if not px:
        out.update({"readable": False, "why": "tape carried no usable prices"})
        return out
    lo, hi, last = min(px), max(px), px[0]
    span_ms = None
    a, b = flow.get("newest_utc_ms"), flow.get("oldest_utc_ms")
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a > b:
        span_ms = a - b
    out.update({
        "readable": True, "n_prints": len(px),
        "last": str(last), "low": str(lo), "high": str(hi),
        "range_abs": str(hi - lo),
        "range_bps": (str(round((hi - lo) / last * D("10000"), 1))
                      if last else None),
        "span_s": (round(span_ms / 1000.0, 1) if span_ms else None),
        "prints_per_min": _rate_per_min(
            len(px), (span_ms / 1000.0) if span_ms else None),
        "labels": ("underlying movement, measured. NOT a forecast and not an "
                   "input to any price, size or bound."),
    })
    return out


def touch_turnover(before: Any, after: Any) -> Dict[str, Any]:
    """Did the touch MOVE between two reads, and by how much.

    A book that never changes between reads is a book nobody is working. Two
    ladder reads a few seconds apart answer that without any model.
    """
    def _touch(d):
        if not isinstance(d, Mapping) or not d.get("readable"):
            return None, None
        b = (d.get("bids") or {})
        return b.get("best"), b.get("best_qty")

    p0, q0 = _touch(before)
    p1, q1 = _touch(after)
    if p0 is None or p1 is None:
        return {"observable": False,
                "why": "one of the two ladder reads was unreadable"}
    dp = None
    a, b = _dec(p0), _dec(p1)
    if a is not None and b is not None:
        dp = str(b - a)
    dq = None
    a, b = _dec(q0), _dec(q1)
    if a is not None and b is not None:
        dq = str(b - a)
    return {"observable": True,
            "bid_before": p0, "bid_after": p1, "bid_moved": p0 != p1,
            "bid_delta": dp,
            "qty_before": q0, "qty_after": q1, "qty_changed": q0 != q1,
            "qty_delta": dq,
            "changed": bool(p0 != p1 or q0 != q1)}


def snapshot(*, symbol: str, tte_s: Any, book: Any, depth: Any,
             own_flow: Any, prior_flow: Any, spot_flow: Any,
             depth_2: Any = None, crossed: Optional[bool] = None,
             now_ms: Optional[float] = None) -> Dict[str, Any]:
    """One candidate window's PRE-ENTRY habitat. Pure: reads what it is given.

    Every field the owner named is present or explicitly absent with a reason.
    """
    from src.gemini_execution import habitat as HB

    b = book if isinstance(book, Mapping) else {}
    yes = b.get("yes") or {}
    lad = HB.ladder_shape(depth)
    bids = (lad.get("bids") or {}) if lad.get("readable") else {}
    age_s = (SLOT_S - float(tte_s)) if isinstance(tte_s, (int, float)) else None

    own = tape_window(own_flow, now_ms=now_ms)
    prior = tape_window(prior_flow, now_ms=now_ms)
    # the candidate's own tape covers only its life so far, so its rate is
    # computed over its OWN AGE and never over RECENT_S
    own["age_s"] = (round(age_s, 1) if age_s is not None else None)
    own["prints_per_min_over_age"] = _rate_per_min(
        own.get("n_total_on_tape"), age_s)
    own["why_age"] = ("a 20-second-old contract with two prints is NEW, not "
                      "quiet; its rate is stated over its own age or not at "
                      "all")

    return {
        "symbol": symbol, "tte_s": tte_s,
        "readable": bool(b.get("readable")) and not bool(crossed),
        "crossed": crossed,
        "book_why": b.get("why"),
        "spread": yes.get("spread"),
        "yes_bid": yes.get("bid"), "yes_ask": yes.get("ask"),
        "touch_depth": bids.get("best_qty"),
        "touch_is_ours": bids.get("touch_is_ours"),
        "depth_within_1": bids.get("cum_within_1_ticks"),
        "depth_within_2": bids.get("cum_within_2_ticks"),
        "depth_within_3": bids.get("cum_within_3_ticks"),
        "n_levels": bids.get("n_levels"),
        "flow_own": own,
        "flow_prior_window": prior,
        "spot": spot_movement(spot_flow),
        "touch_turnover": touch_turnover(depth, depth_2),
        "labels": ("PRE-ENTRY EVIDENCE, model B. Measured before any order "
                   "exists. Nothing here is wired into admission, and no "
                   "threshold is defined in this module."),
    }


#: the ordinal's components, in the order they are compared. NAMED so a
#: reader can see there is no weighting, no fitted coefficient and no cutoff.
RANK_FIELDS = ("prior_prints_per_min", "prior_two_sided", "own_prints",
               "spot_prints_per_min")


def rank_key(snap: Mapping[str, Any]) -> tuple:
    """An ORDINAL for sorting a report. NOT an admission rule.

    It is a plain lexicographic tuple over observed activity, with no weights
    and no threshold, so that a report can be sorted busiest-first and the
    DISTRIBUTION inspected. Turning any part of this into an admission rule
    needs evidence this module is being built to gather, and an owner
    authorization.
    """
    prior = snap.get("flow_prior_window") or {}
    own = snap.get("flow_own") or {}
    spot = snap.get("spot") or {}

    def f(v):
        x = _dec(v)
        return float(x) if x is not None else -1.0

    return (f(prior.get("prints_per_min")),
            1.0 if prior.get("two_sided") else 0.0,
            float(own.get("n_total_on_tape") or 0),
            f(spot.get("prints_per_min")))


def dispersion(snaps: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Is there CROSS-WINDOW dispersion, or is every window the same window?

    Reports the distribution rather than a mean, because the whole question is
    whether a pooled 88.8% zero-print figure hides windows that are busy.
    """
    def series(get):
        out = []
        for s in snaps:
            v = _dec(get(s))
            if v is not None:
                out.append(float(v))
        return sorted(out)

    def stats(xs, name):
        if not xs:
            return {"field": name, "n": 0, "why": "no readable observation"}
        n = len(xs)
        q = lambda p: xs[min(n - 1, max(0, int(p * (n - 1))))]  # noqa: E731
        return {"field": name, "n": n, "min": xs[0], "p25": q(0.25),
                "median": q(0.5), "p75": q(0.75), "p90": q(0.90),
                "max": xs[-1],
                "zero_share": round(sum(1 for x in xs if x == 0) / n, 3),
                "spread_p90_over_median": (
                    round(q(0.90) / q(0.5), 2) if q(0.5) else None)}

    return {
        "windows": len(snaps),
        "prior_prints_per_min": stats(
            series(lambda s: (s.get("flow_prior_window") or {})
                   .get("prints_per_min")), "prior_prints_per_min"),
        "own_prints_on_tape": stats(
            series(lambda s: (s.get("flow_own") or {})
                   .get("n_total_on_tape")), "own_prints_on_tape"),
        "spot_prints_per_min": stats(
            series(lambda s: (s.get("spot") or {}).get("prints_per_min")),
            "spot_prints_per_min"),
        "spot_range_bps": stats(
            series(lambda s: (s.get("spot") or {}).get("range_bps")),
            "spot_range_bps"),
        "spread": stats(series(lambda s: s.get("spread")), "spread"),
        "touch_depth": stats(series(lambda s: s.get("touch_depth")),
                             "touch_depth"),
        "two_sided_prior_share": (
            round(sum(1 for s in snaps
                      if (s.get("flow_prior_window") or {}).get("two_sided"))
                  / len(snaps), 3) if snaps else None),
        "labels": ("DISPERSION, not a verdict. A wide spread between median "
                   "and p90 means windows differ and a selector has "
                   "something to select on; a narrow one means it does not."),
    }


def persistence(pairs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Does the PREVIOUS window's flow predict THIS window's flow?

    THE QUESTION A SELECTOR LIVES OR DIES ON. Each pair carries `prior` (what
    was observable before entry) and `actual` (what the window then delivered).
    If they are unrelated, a pre-entry read cannot choose a better window and
    a selector only costs windows.

    Reported as a rank correlation, which needs no linear assumption, together
    with the sample size -- and it refuses to state one below `MIN_PAIRS`.
    """
    xs, ys = [], []
    for p in pairs:
        a, b = _dec(p.get("prior")), _dec(p.get("actual"))
        if a is not None and b is not None:
            xs.append(float(a))
            ys.append(float(b))
    n = len(xs)
    out: Dict[str, Any] = {"n_pairs": n, "min_pairs": MIN_PAIRS}
    if n < MIN_PAIRS:
        out.update({"computable": False,
                    "why": ("%d pairs; %d are needed before a persistence "
                            "statement means anything" % (n, MIN_PAIRS))})
        return out

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    rho = (num / (dx * dy)) if dx and dy else None
    out.update({
        "computable": rho is not None,
        "spearman_rho": (round(rho, 4) if rho is not None else None),
        "why": (None if rho is not None else
                "one series had no variation, so no rank correlation exists"),
        "labels": ("ASSOCIATION over %d windows. It is not causal, it is not "
                   "an admission rule, and it does not become one without "
                   "owner authorization." % n),
    })
    return out


# ===================================================================== I/O
# Everything above is pure. This one function does the reads, so a test can
# exercise the measurement without a network and a reader can see exactly
# which endpoints a pre-entry look costs.

def read_snapshot(symbol: str, tte_s: Any, *,
                  turnover_gap_s: float = 3.0) -> Dict[str, Any]:
    """Take one candidate's pre-entry habitat from PUBLIC endpoints.

    Four public reads and one short pause: the contract's book, its ladder
    twice (for touch turnover), its own tape, the PREDECESSOR contract's tape,
    and the spot tape. No credential is used and no order path is reachable.

    Never raises: an endpoint that fails becomes an unreadable field with its
    reason, because a pre-entry look must never be able to stop a window.
    """
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import habitat as HB
    from src.gemini_execution import metadata as MD

    def _safe(fn, why):
        try:
            return fn()
        except Exception as exc:                                # noqa: BLE001
            return {"readable": False,
                    "why": "%s: %s: %s" % (why, type(exc).__name__, exc)}

    book = _safe(lambda: MD.outcome_book(symbol), "book")
    depth = _safe(lambda: MD.contract_depth(symbol), "depth")
    own = _safe(lambda: HB.public_flow(symbol), "own tape")

    prior_sym = None
    tk = MD.event_ticker(symbol)
    if tk:
        sib = MD.sibling_ticker(tk, 5)          # the window that just closed
        if sib:
            prior_sym = "GEMI-%s-UP" % sib
    prior = ({"readable": False, "why": "no predecessor ticker could be built"}
             if not prior_sym else _safe(lambda: HB.public_flow(prior_sym),
                                         "prior tape"))
    spot = _safe(lambda: HB.public_flow(SPOT_SYMBOL), "spot tape")

    _time.sleep(max(0.0, turnover_gap_s))
    depth_2 = _safe(lambda: MD.contract_depth(symbol), "depth (second read)")

    crossed = None
    try:
        crossed = CP.crossed(book)
    except Exception:                                           # noqa: BLE001
        crossed = None

    snap = snapshot(symbol=symbol, tte_s=tte_s, book=book, depth=depth,
                    own_flow=own, prior_flow=prior, spot_flow=spot,
                    depth_2=depth_2, crossed=crossed)
    snap["prior_symbol"] = prior_sym
    snap["sources"] = ("PUBLIC: contract prices, /v1/book/{symbol} x2, "
                       "/v1/trades/{symbol}, /v1/trades/{predecessor}, "
                       "/v1/trades/btcusd. No credential, no order path.")
    snap["turnover_gap_s"] = turnover_gap_s
    return snap


def realised_window_flow(samples: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """What the window ACTUALLY delivered, from its own recorded cycles.

    The counterpart to a pre-entry reading: `prior` is what we could see
    before committing, this is what happened after. Pairing the two is the
    only way to learn whether the pre-entry read was worth taking.
    """
    from src.gemini_execution import segments as SG
    rates = SG.flow_rates(list(samples))
    meas = [float(x) for x in rates if x is not None]
    n_first = next((s for s in samples
                    if isinstance((s.get("habitat") or {}).get("flow"), Mapping)
                    ), None)
    first = (((n_first or {}).get("habitat") or {}).get("flow") or {}
             ).get("n_prints")
    last = None
    for s in reversed(list(samples)):
        f = (s.get("habitat") or {}).get("flow") or {}
        if isinstance(f.get("n_prints"), int):
            last = f["n_prints"]
            break
    if not meas:
        return {"measurable": False,
                "why": ("no adjacent-cycle pair produced a rate; a window "
                        "with one usable cycle has no flow measurement")}
    return {
        "measurable": True,
        "intervals": len(meas),
        "zero_intervals": sum(1 for x in meas if x == 0),
        "zero_share": round(sum(1 for x in meas if x == 0) / len(meas), 3),
        "mean_prints_per_min": round(sum(meas) / len(meas), 3),
        "max_prints_per_min": max(meas),
        "prints_first_cycle": first, "prints_last_cycle": last,
        "prints_added": ((last - first)
                         if isinstance(first, int) and isinstance(last, int)
                         else None),
        "labels": ("what the window delivered, measured between its own "
                   "cycles. Not a forecast and not a target."),
    }
