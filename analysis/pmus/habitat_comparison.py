"""NFL Sunday habitat experiment: cross-cohort comparison.

EVERYTHING HERE IS SHADOW / HYPOTHETICAL. No order was placed, no fill is
venue-confirmed, and no number below is realized profit. A "first fill" is a
MODELLED event: it assumes a maker resting at the observed touch would have
been filled, which the prospective work has since shown is not safe to assume.
It is a habitat measurement, not an execution claim.

RIGHT-CENSORING IS EXCLUDED, NEVER COUNTED AS A MISS. If a market's
observations end before t+N, that state cannot answer "did the complement
become acquirable within N seconds", so it is dropped from the reach@N
DENOMINATOR. Counting it as a failure would penalise exactly the states nearest
the end of collection and make every cohort look worse the earlier its
collector stopped.

THE PAIR CONVENTION. On a binary book the complement's ask is 1 - this side's
bid, so "acquiring the other side" means paying (1 - bid_other) and the pair
cost is p + q. Fee-safe means p + q <= 1.00. PMUS maker fee is ZERO and no
rebate is assumed anywhere.
"""
from __future__ import annotations

import bisect
import collections
import statistics as stt
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Preregistered habitat thresholds, fixed before this analysis ran.
THRESHOLDS = {
    "median_spread_c": ("<=", 2.0),
    "share_spread_le_2c": (">=", 0.95),
    "reach120": (">=", 0.70),
    "median_reach_s": ("<=", 30.0),
}
REACH_HORIZONS = (30, 60, 120)
# The pair TARGET, not par. Resting on both touches costs 1 - spread, which is
# below 1.00 whenever the spread is positive -- i.e. always. So "can I make a
# pair at <= 1.00" is an ALGEBRAIC IDENTITY and measures nothing; scored that
# way every cohort answers yes in the same state, median reach 0.0 s. The
# program's own fee-safe target is 0.98, which a state can genuinely fail.
PAIR_TARGET = 0.98


def cents(x: float) -> float:
    return round(x * 100.0, 4)


def touch_moves(states: Sequence[dict]) -> float:
    """Touch changes per minute. A flat book is not a fast one."""
    if len(states) < 2:
        return 0.0
    n = sum(1 for a, b in zip(states, states[1:])
            if a["bid"] != b["bid"] or a["ask"] != b["ask"])
    span = states[-1]["t"] - states[0]["t"]
    return (n / span * 60.0) if span > 0 else 0.0


def reach_from(states: Sequence[dict], ts: Sequence[float], i: int,
               horizon: Optional[float]) -> Tuple[Optional[bool], Optional[float]]:
    """Would a maker filled at state i find a fee-safe complement?

    Returns (outcome, seconds). outcome is None when the question is
    UNANSWERABLE because observation ends before the horizon -- that is
    censoring, and the caller must drop it rather than score it False.
    """
    p = states[i]["bid"]                      # first leg locked at the entry bid
    end = ts[-1]
    if horizon is not None and ts[i] + horizon > end:
        return None, None                     # censored: cannot be answered
    lim = ts[i] + horizon if horizon is not None else end
    j = i
    while j < len(states) and ts[j] <= lim:
        # Complement bought at ITS touch costs 1 - ask. Pair = p + (1 - ask_j),
        # so the ask must rise at least (PAIR_TARGET complement) above entry.
        if p + (1.0 - states[j]["ask"]) <= PAIR_TARGET + 1e-9:
            return True, ts[j] - ts[i]
        j += 1
    return False, None


def cohort_metrics(by_market: Dict[str, List[dict]]) -> Dict[str, Any]:
    """Habitat metrics for one cohort. Markets are the clustering unit."""
    spreads: List[float] = []
    moves: List[float] = []
    reach: Dict[int, List[bool]] = {h: [] for h in REACH_HORIZONS}
    censored: Dict[int, int] = {h: 0 for h in REACH_HORIZONS}
    times: List[float] = []
    matched = 0
    firsts = 0
    basis: List[float] = []
    for slug, st in by_market.items():
        st = sorted(st, key=lambda r: r["t"])
        ts = [r["t"] for r in st]
        spreads += [r["ask"] - r["bid"] for r in st]
        moves.append(touch_moves(st))
        for i in range(len(st)):
            firsts += 1
            for h in REACH_HORIZONS:
                ok, _ = reach_from(st, ts, i, float(h))
                if ok is None:
                    censored[h] += 1
                else:
                    reach[h].append(ok)
            ok, secs = reach_from(st, ts, i, None)   # remainder of market
            if ok:
                matched += 1
                if secs is not None:
                    times.append(secs)
                j = bisect.bisect_left(ts, ts[i] + secs) if secs else i
                j = min(j, len(st) - 1)
                basis.append(st[i]["bid"] + (1.0 - st[j]["ask"]))
    def med(v):
        return stt.median(v) if v else None
    out = {
        "markets": len(by_market),
        "states": firsts,
        "median_spread_c": cents(med(spreads)) if spreads else None,
        "share_spread_le_1c": (sum(1 for s in spreads if s <= 0.010001) / len(spreads)
                               if spreads else None),
        "share_spread_le_2c": (sum(1 for s in spreads if s <= 0.020001) / len(spreads)
                               if spreads else None),
        "movement_per_min": med(moves),
        "median_reach_s": med(times),
        "matched_conversion": (matched / firsts) if firsts else None,
        "pair_basis": med(basis),
        "residual_share": (1.0 - matched / firsts) if firsts else None,
    }
    for h in REACH_HORIZONS:
        v = reach[h]
        out["reach%d" % h] = (sum(v) / len(v)) if v else None
        out["reach%d_n" % h] = len(v)
        out["reach%d_censored_excluded" % h] = censored[h]
    out["after_fee_edge_per_pair"] = (
        None if out["pair_basis"] is None else 1.0 - out["pair_basis"])
    out["pair_target"] = PAIR_TARGET
    out["labels"] = ("SHADOW/HYPOTHETICAL. No order placed; a 'first fill' is "
                     "modelled at the observed touch and is not venue-confirmed. "
                     "PMUS maker fee is zero; no rebate is assumed. Censored "
                     "states are EXCLUDED from reach denominators.")
    return out


def qualifies(m: Dict[str, Any]) -> Dict[str, Any]:
    """Against the preregistered thresholds, naming every failing dimension."""
    fails = []
    for k, (op, v) in THRESHOLDS.items():
        got = m.get(k)
        if got is None:
            fails.append("%s=UNMEASURED" % k)
            continue
        ok = (got <= v) if op == "<=" else (got >= v)
        if not ok:
            fails.append("%s=%.4g (needs %s%.4g)" % (k, got, op, v))
    return {"qualifies": not fails, "fails": fails, "n_fails": len(fails)}
