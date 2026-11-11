"""LARGE-FLOW PRECURSORS: is a burst visible before it lands?

Nine prints of 100+ contracts carry 78.7% of all notional across 26 BTC5m
windows. If those bursts are preceded by anything observable, maker liquidity
can legitimately be resting before they arrive -- which is the only honest way
to be in front of them. If they are not, then "be there when the flow comes"
is not a strategy, it is a wish, and this module should say so.

THE COMPARISON IS AGAINST MATCHED CONTROLS, not against the window average. A
burst's precursor window is compared with non-burst moments drawn from the
SAME contracts, so "the market was busier just before the big print" cannot be
manufactured by comparing an active window against a dead one.

Everything here is observational. No threshold is set, no admission rule is
proposed, and nothing is fitted.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: the lookbacks asked for
LOOKBACKS_S = (5, 15, 30, 60, 120)

#: a burst
BURST_CONTRACTS = D("100")

#: a control must be this far from any burst, so it is not a burst's own
#: precursor window wearing a different label
CONTROL_CLEARANCE_S = 180.0


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def bursts_in(prints: Sequence[Mapping[str, Any]],
              *, threshold: Any = BURST_CONTRACTS) -> List[Mapping[str, Any]]:
    th = _dec(threshold) or BURST_CONTRACTS
    return [t for t in prints or []
            if (_dec(t.get("amount")) or D("0")) >= th]


def control_points(prints: Sequence[Mapping[str, Any]],
                   burst_ms: Sequence[Any], *,
                   clearance_s: float = CONTROL_CLEARANCE_S
                   ) -> List[Mapping[str, Any]]:
    """Non-burst moments from the SAME tape, clear of every burst.

    Drawn at the non-burst prints themselves rather than at arbitrary clock
    times, so a control is a moment when SOMETHING traded -- otherwise the
    comparison would mostly be measuring that bursts happen when the market is
    awake at all, which is true and useless.
    """
    bs = [_dec(m) for m in burst_ms]
    bs = [m for m in bs if m is not None]
    gap = D(str(clearance_s)) * D("1000")
    out = []
    for t in prints or []:
        ms = _dec(t.get("utc_ms"))
        if ms is None or (_dec(t.get("amount")) or D("0")) >= BURST_CONTRACTS:
            continue
        if any(abs(ms - b) <= gap for b in bs):
            continue
        out.append(t)
    return out


def features_before(prints: Sequence[Mapping[str, Any]],
                    cycles: Sequence[Mapping[str, Any]], *, at_ms: Any,
                    tte_s: Any = None) -> Dict[str, Any]:
    """Everything observable in the seconds before a moment.

    Book-derived features are present only where a recorded cycle falls inside
    the lookback; they are reported as None rather than carried forward from
    an older book, because a stale book is not an observation.
    """
    now = _dec(at_ms)
    out: Dict[str, Any] = {"utc_ms": (str(now) if now is not None else None),
                           "tte_s": tte_s}
    for w in LOOKBACKS_S:
        lo = (now - D(str(w)) * D("1000")) if now is not None else None
        sl = [t for t in prints or []
              if _dec(t.get("utc_ms")) is not None
              and (lo is None or _dec(t["utc_ms"]) > lo)
              and (now is None or _dec(t["utc_ms"]) < now)]
        buys = [t for t in sl if str(t.get("type")) == "buy"]
        sells = [t for t in sl if str(t.get("type")) == "sell"]
        vol = sum((_dec(t.get("amount")) or D("0")) for t in sl)
        n = len(sl)
        out["prints_%ds" % w] = n
        out["contracts_%ds" % w] = str(vol)
        out["buy_share_%ds" % w] = (str(round(D(len(buys)) / D(n), 3))
                                    if n else None)
        out["aggressor_imbalance_%ds" % w] = (
            str(round(D(len(buys) - len(sells)) / D(n), 3)) if n else None)
        out["max_print_%ds" % w] = (
            str(max((_dec(t.get("amount")) or D("0")) for t in sl))
            if sl else None)
        if len(sl) >= 2:
            a, b = _dec(sl[0].get("price")), _dec(sl[-1].get("price"))
            ta, tb = _dec(sl[0].get("utc_ms")), _dec(sl[-1].get("utc_ms"))
            out["price_velocity_%ds" % w] = (
                str(round((b - a) / ((tb - ta) / D("1000")) * D("60"), 5))
                if None not in (a, b, ta, tb) and tb > ta else None)
            out["price_range_%ds" % w] = str(
                max(_dec(t["price"]) for t in sl if _dec(t.get("price")))
                - min(_dec(t["price"]) for t in sl if _dec(t.get("price"))))
        else:
            out["price_velocity_%ds" % w] = None
            out["price_range_%ds" % w] = None

    out.update(_book_features(cycles, now))
    return out


def _book_features(cycles: Sequence[Mapping[str, Any]],
                   now: Optional[D]) -> Dict[str, Any]:
    """Spread, depth depletion, level disappearance and book imbalance.

    Depletion and disappearance need TWO observations, so they exist only
    where two cycles fall in the 120s before the moment.
    """
    inside = sorted(
        [c for c in cycles or []
         if _dec(c.get("utc_ms")) is not None and now is not None
         and now - D("120000") <= _dec(c["utc_ms"]) < now],
        key=lambda c: _dec(c["utc_ms"]))
    if not inside:
        return {"book_observed": False,
                "why_no_book": "no recorded cycle in the 120s before this"}
    last = inside[-1]
    y = (last.get("book") or {}).get("yes") or {}
    bid, ask = _dec(y.get("bid")), _dec(y.get("ask"))

    def ladder(c):
        d = c.get("depth") or {}
        if not d.get("readable"):
            return None
        lv = (d.get("bids") or {}).get("levels") or []
        return [(_dec(x.get("price")), _dec(x.get("qty"))) for x in lv]

    l_last, l_first = ladder(last), ladder(inside[0])
    out: Dict[str, Any] = {
        "book_observed": True,
        "book_age_s": float((now - _dec(last["utc_ms"])) / D("1000")),
        "cycles_in_120s": len(inside),
        "spread": (str(ask - bid) if bid is not None and ask is not None
                   else None),
        "yes_bid": y.get("bid"), "yes_ask": y.get("ask"),
    }
    if l_last:
        tot = sum(q for _, q in l_last if q is not None)
        top = l_last[0][1] if l_last and l_last[0][1] is not None else None
        out.update({"touch_depth": (str(top) if top is not None else None),
                    "ladder_total": str(tot), "ladder_levels": len(l_last),
                    "book_imbalance_top_over_total": (
                        str(round(top / tot, 3))
                        if top is not None and tot > 0 else None)})
    if l_last and l_first:
        t0 = sum(q for _, q in l_first if q is not None)
        t1 = sum(q for _, q in l_last if q is not None)
        p0 = {p for p, _ in l_first if p is not None}
        p1 = {p for p, _ in l_last if p is not None}
        out.update({
            "depth_change": str(t1 - t0),
            "depth_depletion_share": (str(round((t0 - t1) / t0, 3))
                                      if t0 > 0 else None),
            "levels_disappeared": len(p0 - p1),
            "levels_appeared": len(p1 - p0),
        })
    return out


def _num(rows: Sequence[Mapping[str, Any]], key: str) -> List[D]:
    out = []
    for r in rows:
        v = _dec(r.get(key))
        if v is not None:
            out.append(v)
    return out


def compare(burst_rows: Sequence[Mapping[str, Any]],
            control_rows: Sequence[Mapping[str, Any]],
            keys: Sequence[str]) -> Dict[str, Any]:
    """Burst precursors against matched controls, feature by feature.

    Medians and coverage, with the sample size on every line. No test is run
    and no threshold is chosen: with nine bursts nothing here could survive
    one, and pretending otherwise would be the error this whole programme
    keeps finding.
    """
    out = {}
    for k in keys:
        b, c = _num(burst_rows, k), _num(control_rows, k)
        out[k] = {
            "burst_n": len(b), "control_n": len(c),
            "burst_median": (str(sorted(b)[len(b) // 2]) if b else None),
            "control_median": (str(sorted(c)[len(c) // 2]) if c else None),
            "burst_coverage": (round(len(b) / len(burst_rows), 3)
                               if burst_rows else None),
            "separated": None,
        }
        if b and c:
            bm, cm = sorted(b)[len(b) // 2], sorted(c)[len(c) // 2]
            out[k]["median_difference"] = str(bm - cm)
            # "separated" only if the two medians sit outside each other's
            # interquartile range -- a weak, honest statement, not a test
            bq = sorted(b)
            lo, hi = bq[len(bq) // 4], bq[(3 * len(bq)) // 4]
            out[k]["separated"] = bool(cm < lo or cm > hi)
    return out


def verdict(cmp_: Mapping[str, Any], n_bursts: int) -> Dict[str, Any]:
    sep = [k for k, v in cmp_.items() if v.get("separated")]
    return {
        "n_bursts": n_bursts,
        "features_compared": len(cmp_),
        "features_separated_from_control": sep,
        "n_separated": len(sep),
        "predictable_enough_to_rest_liquidity_beforehand": False if
        n_bursts < 30 else None,
        "why": ("with %d bursts no feature can establish predictability at "
                "any useful confidence, whatever the medians do. A separated "
                "median is a lead worth collecting more windows for, never a "
                "reason to rest size in front of a burst." % n_bursts),
        "labels": ("OBSERVATIONAL. Nothing is fitted, no threshold is set and "
                   "no admission rule follows from this."),
    }
