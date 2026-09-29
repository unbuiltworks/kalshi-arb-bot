"""Are L5's 10.27 fills/min decisions, or one quote filling repeatedly?

THE OVERCLAIM THIS CORRECTS. The cadence comparison put L5's 10.27 fills/min
against our ~1.0 SUBMITTED ORDERS/min and concluded the $81/$250 envelope
mechanically prevents L5's cadence. That comparison is not sound: a maker
quote resting on the book can be filled many times by many takers without its
owner deciding anything, so L5's fill count is an upper bound on L5's decision
count, and our order count is close to our decision count. Until the fills are
collapsed into quote episodes the two numbers are not the same unit.

WHAT A QUOTE EPISODE IS. Consecutive fills on the SAME side at the SAME price,
each within `GAP_S` of the previous one, are treated as one resting parent
quote being consumed. Same side, same price, close in time is the signature of
one order being hit repeatedly; a price change or a side change ends the
episode, because either requires a new order.

WHY A GAP AT ALL. Same side and same price alone would merge two genuinely
separate quotes placed minutes apart at a round number. Several gaps are
reported rather than one being chosen, so the sensitivity is visible instead
of assumed.

THIS CANNOT PROVE THE COUNT. The public tape has no order id, so an episode
is an INFERENCE about which fills shared a parent. The collapse is therefore
reported as a RANGE across gap thresholds, and both raw and collapsed figures
are given everywhere. Read-only; nothing is fitted.
"""
from __future__ import annotations

import json
import statistics
from typing import Any, Dict, List, Optional, Sequence

import scripts.l5_completion_behavior as B

#: gaps at which the collapse is reported. 1s/2s/5s are the owner's; 0s means
#: "adjacent in the tape, any gap" and is the most aggressive collapse.
GAPS = (1.0, 2.0, 5.0)


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def fills_of(rows: Sequence[dict]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        px, sh = _f(r["price"]), _f(r["shares"])
        t = _f(r["timestamp"])
        if None in (px, sh, t):
            continue
        out.append({"t": t, "side": r["outcome"], "px": px, "shares": sh,
                    "notional": px * sh,
                    "s_to_expiry": _f(r["seconds_to_expiry"])})
    out.sort(key=lambda x: x["t"])
    return out


def adjacency(fills: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """How often is a fill same-side AND same-price as the one before it?"""
    n = len(fills)
    if n < 2:
        return {}
    same_sp = 0
    within = {g: 0 for g in GAPS}
    dts = []
    for a, b in zip(fills, fills[1:]):
        dt = b["t"] - a["t"]
        dts.append(dt)
        if b["side"] == a["side"] and abs(b["px"] - a["px"]) < 1e-9:
            same_sp += 1
            for g in GAPS:
                if dt <= g:
                    within[g] += 1
    pairs = n - 1
    return {
        "consecutive_pairs": pairs,
        "same_side_same_price_frac": same_sp / pairs,
        **{"same_sp_within_%gs_frac" % g: within[g] / pairs for g in GAPS},
        "median_dt_between_fills": statistics.median(dts) if dts else None,
    }


def episodes(fills: Sequence[Dict[str, Any]], gap_s: float) -> List[Dict]:
    """Collapse same-side same-price runs within `gap_s` into one episode."""
    eps: List[Dict[str, Any]] = []
    for f in fills:
        if eps:
            e = eps[-1]
            if (f["side"] == e["side"] and abs(f["px"] - e["px"]) < 1e-9
                    and (f["t"] - e["t_last"]) <= gap_s):
                e["n_fills"] += 1
                e["shares"] += f["shares"]
                e["notional"] += f["notional"]
                e["t_last"] = f["t"]
                continue
        eps.append({"side": f["side"], "px": f["px"], "n_fills": 1,
                    "shares": f["shares"], "notional": f["notional"],
                    "t_first": f["t"], "t_last": f["t"],
                    "s_to_expiry": f["s_to_expiry"]})
    return eps


WINDOW_S = 900.0


def per_window(rows: Sequence[dict]) -> Optional[Dict[str, Any]]:
    fills = fills_of(rows)
    if len(fills) < 5:
        return None
    out: Dict[str, Any] = {
        "n_fills": len(fills),
        "fills_per_min": len(fills) * 60.0 / WINDOW_S,
        "shares_total": sum(f["shares"] for f in fills),
        "shares_per_min": sum(f["shares"] for f in fills) * 60.0 / WINDOW_S,
        "notional_total": sum(f["notional"] for f in fills),
        "notional_per_min": sum(f["notional"] for f in fills) * 60.0 / WINDOW_S,
        "fill_qty_median": statistics.median(f["shares"] for f in fills),
        "fill_qty_mean": statistics.fmean(f["shares"] for f in fills),
        "fill_qty_p90": sorted(f["shares"] for f in fills)[
            int(0.9 * len(fills))],
        **adjacency(fills),
    }
    for g in GAPS:
        eps = episodes(fills, g)
        out["episodes_%gs" % g] = len(eps)
        out["episodes_per_min_%gs" % g] = len(eps) * 60.0 / WINDOW_S
        out["fills_per_episode_%gs" % g] = len(fills) / len(eps)
        out["shares_per_episode_%gs" % g] = statistics.median(
            e["shares"] for e in eps)
        out["notional_per_episode_%gs" % g] = statistics.median(
            e["notional"] for e in eps)
    return out


def run() -> Dict[str, Any]:
    by = B.windows()
    recs = []
    for wid, rows in by.items():
        o = B.outcome_of(rows)
        if o is None:
            continue
        w = per_window(rows)
        if w is None:
            continue
        recs.append({"window_id": wid, "first_t": rows[0]["datetime_utc"],
                     "completion": o["completion"],
                     "residual_frac": o["residual_frac"], **w})
    comp = sorted(r["completion"] for r in recs)
    hi, lo = comp[2 * len(comp) // 3], comp[len(comp) // 3]
    good = [r for r in recs if r["completion"] >= hi]
    bad = [r for r in recs if r["completion"] <= lo]

    keys = [k for k in recs[0] if k not in
            ("window_id", "first_t")] if recs else []

    def sm(rows, name):
        d = {"cohort": name, "n": len(rows)}
        for k in keys:
            v = [r[k] for r in rows if r.get(k) is not None]
            d[k] = round(statistics.median(v), 4) if v else None
        return d

    return {"windows": len(recs),
            "gaps_tested_s": list(GAPS),
            "tercile_cuts": {"high": round(hi, 4), "low": round(lo, 4)},
            "all": sm(recs, "ALL"), "high": sm(good, "HIGH_COMPLETION"),
            "low": sm(bad, "LOW_COMPLETION"),
            "labels": ("an episode is an INFERENCE -- the public tape carries "
                       "no order id -- so results are a range across gaps and "
                       "raw fills are reported alongside")}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
