"""BTC15m, isolated: how does L5 get from a first fill to a two-sided book?

THE TARGET. BTC15m is 96.0% two-sided at a median achieved pair cost of 0.9624,
over 527 windows. Our clone forms no economically valid pair at all. This walks
every one of those windows leg by leg, so the mechanism is read off the record
rather than inferred from summary statistics.

Per leg: fill number, side, price, quantity, event time, TTE, cumulative Up and
Down inventory, matched quantity, residual, and the effective pair cost AT THAT
POINT -- the running number, not the final one, because a pair cost is a path
and treating it as an endpoint hides how it was reached.

AND THE QUESTION THAT DECIDES THE MODEL: is pair cost a CONTROL VARIABLE or an
EMERGENT RESULT? A median of 0.9624 is not evidence that he targets 0.96. Two
independently attractive maker fills would produce a distribution with a median
somewhere, and it would look exactly like a target to anyone who assumed one.

The discriminator used here is the SECOND leg. If pair cost is controlled, the
price he pays for the complement should depend on what the first leg cost --
cheap first leg, he can afford a dearer complement, and the sum lands in a
narrow band. If it is emergent, the two legs are independent and the sum is
just their convolution, with a wide spread and no first-leg dependence.

Read-only over public history. Nothing here is our fill, our shadow result, or
realized P&L.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import statistics
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

SEQ = "analysis/l5/l5_canonical_sequence.csv.gz"
COHORT = ("BTC", "15")
# our execution policy, shown for comparison only. It is NOT a definition of
# L5's behaviour and never filters what is measured here.
ADAPTED_CEILING = D("0.96")


def load(path: str = SEQ, *, asset: str = COHORT[0], dur: str = COHORT[1],
         limit: Optional[int] = None) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = defaultdict(list)
    with gzip.open(path, "rt") as fh:
        for i, r in enumerate(csv.DictReader(fh)):
            if limit and i >= limit:
                break
            if r.get("asset") != asset or r.get("duration_minutes") != dur:
                continue
            if (r.get("side") or "").upper() != "BUY":
                continue
            out[r["window_id"]].append(r)
    for v in out.values():
        v.sort(key=lambda r: (float(r["timestamp"]), int(r["trade_seq"])))
    return out


def walk(fills: Sequence[dict]) -> List[Dict[str, Any]]:
    """Every leg, with the running inventory and pair cost at that point."""
    up_q = up_c = dn_q = dn_c = D("0")
    legs = []
    for n, f in enumerate(fills, 1):
        q, p = D(f["quantity"]), D(f["price"])
        if f["outcome"] == "Up":
            up_q += q
            up_c += q * p
        else:
            dn_q += q
            dn_c += q * p
        pair = (((up_c / up_q) + (dn_c / dn_q))
                if up_q > 0 and dn_q > 0 else None)
        legs.append({
            "fill_n": n, "side": f["outcome"], "price": str(p), "qty": str(q),
            "event_t": float(f["timestamp"]),
            "tte_s": float(f["seconds_to_settlement"] or 0),
            "cum_up": str(up_q), "cum_dn": str(dn_q),
            "matched_qty": str(min(up_q, dn_q)),
            "residual": str(up_q - dn_q),
            "effective_pair_cost": (str(pair.quantize(D("0.000001")))
                                    if pair is not None else None),
        })
    return legs


def formation(fills: Sequence[dict]) -> Optional[Dict[str, Any]]:
    """The first two-sided formation in this window, and how it was reached."""
    legs = walk(fills)
    idx = next((i for i, l in enumerate(legs)
                if l["effective_pair_cost"] is not None), None)
    if idx is None:
        return None
    first, form = legs[0], legs[idx]
    # the legs on the FIRST side before the complement arrived: was he
    # extending into a one-sided position, or waiting on one fill?
    same = [l for l in legs[:idx] if l["side"] == first["side"]]
    return {
        "market_slug": fills[0]["market_slug"],
        "first_side": first["side"], "first_price": first["price"],
        "first_qty": first["qty"], "first_tte_s": first["tte_s"],
        "complement_at_fill_n": form["fill_n"],
        "complement_price": form["price"],
        "extends_before_complement": len(same) - 1,
        "seconds_to_complement": round(form["event_t"] - first["event_t"], 1),
        "tte_at_complement_s": form["tte_s"],
        "pair_cost_at_formation": form["effective_pair_cost"],
        "final_pair_cost": legs[-1]["effective_pair_cost"],
        "matched_at_formation": form["matched_qty"],
        "residual_at_formation": form["residual"],
        "n_legs_total": len(legs),
        "legs": legs,
    }


def control_or_emergent(forms: Sequence[dict]) -> Dict[str, Any]:
    """Is the pair cost steered, or is it what two independent fills add up to?

    Three signals, none of them conclusive alone:

      1. SPREAD. A controlled sum is tighter than the convolution of its parts.
         Compared against the spread the two legs would produce if independent.
      2. FIRST-LEG DEPENDENCE. If controlled, a cheaper first leg should be
         followed by a DEARER complement -- correlation near -1. If emergent,
         the two are unrelated and the correlation is near 0.
      3. BAND OCCUPANCY. A target produces a mode at the target. A convolution
         produces a mode wherever the parts happen to put it.
    """
    rows = [f for f in forms if f.get("pair_cost_at_formation")]
    if len(rows) < 30:
        return {"decided": False,
                "why": f"only {len(rows)} formations; too few to discriminate"}
    first = [float(f["first_price"]) for f in rows]
    comp = [float(f["complement_price"]) for f in rows]
    pair = [float(f["pair_cost_at_formation"]) for f in rows]

    sd_pair = statistics.pstdev(pair)
    # if the legs were independent, Var(sum) = Var(a) + Var(b)
    sd_indep = math.sqrt(statistics.pvariance(first)
                         + statistics.pvariance(comp))
    r = _corr(first, comp)
    return {
        "decided": True, "n": len(rows),
        "pair_cost_sd": round(sd_pair, 5),
        "sd_if_legs_independent": round(sd_indep, 5),
        "tightening_ratio": round(sd_pair / sd_indep, 4) if sd_indep else None,
        "first_leg_vs_complement_corr": round(r, 4),
        "pair_cost_median": round(statistics.median(pair), 5),
        "within_our_ceiling": sum(1 for x in pair
                                  if D(str(x)) <= ADAPTED_CEILING),
        "reading": _reading(sd_pair, sd_indep, r),
        "labels": ("a median near 0.96 is NOT evidence of a 0.96 target. Two "
                   "independently attractive maker fills produce a "
                   "distribution with a median somewhere, and it looks like a "
                   "target to anyone who assumed one."),
    }


def _reading(sd_pair: float, sd_indep: float, r: float) -> str:
    tight = sd_indep and (sd_pair / sd_indep) < 0.7
    anti = r < -0.3
    if tight and anti:
        return ("CONTROLLED: the sum is materially tighter than independent "
                "legs would give, and a cheaper first leg is followed by a "
                "dearer complement. He is steering the pair, not collecting it")
    if not tight and abs(r) < 0.15:
        return ("EMERGENT: the sum is about as wide as independent legs would "
                "give and the legs are uncorrelated. The pair cost is what two "
                "separately attractive fills happened to add up to, and "
                "treating its median as a target would be reading our own "
                "assumption back out of the data")
    return ("MIXED: partial tightening or partial dependence. Not enough to "
            "call it either, and it must not be called either")


def _corr(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return (num / (da * db)) if da and db else 0.0


def by_conditioner(forms: Sequence[dict]) -> Dict[str, Any]:
    """The pair-cost distribution split by state, not one threshold."""
    rows = [f for f in forms if f.get("pair_cost_at_formation")]
    out: Dict[str, Any] = {}
    for name, fn in (
        ("tte_at_first_leg", lambda f: _bucket(f["first_tte_s"],
                                               (60, 180, 360, 600))),
        ("first_leg_price", lambda f: _bucket(float(f["first_price"]),
                                              (0.2, 0.4, 0.6, 0.8))),
        ("favourite_side", lambda f: ("favourite"
                                      if float(f["first_price"]) > 0.5
                                      else "underdog")),
        ("extends_before_complement", lambda f: _bucket(
            f["extends_before_complement"], (0, 1, 3))),
        ("seconds_to_complement", lambda f: _bucket(
            f["seconds_to_complement"], (10, 30, 90, 300))),
        ("residual_at_formation", lambda f: _bucket(
            abs(float(f["residual_at_formation"])), (1, 10, 50))),
    ):
        g: Dict[str, List[float]] = defaultdict(list)
        for f in rows:
            g[fn(f)].append(float(f["pair_cost_at_formation"]))
        out[name] = {k: {"n": len(v),
                         "median": round(statistics.median(v), 5),
                         "p10": round(sorted(v)[int(len(v) * 0.1)], 5),
                         "p90": round(sorted(v)[int(len(v) * 0.9)], 5),
                         "within_our_ceiling": sum(
                             1 for x in v if D(str(x)) <= ADAPTED_CEILING)}
                     for k, v in sorted(g.items()) if len(v) >= 10}
    return out


def _bucket(v: float, edges: Sequence[float]) -> str:
    for e in edges:
        if v <= e:
            return f"<={e}"
    return f">{edges[-1]}"


def report(path: str = SEQ, *, limit: Optional[int] = None) -> Dict[str, Any]:
    ws = load(path, limit=limit)
    forms = [f for f in (formation(v) for v in ws.values() if v) if f]
    one_sided = len(ws) - len(forms)
    slim = [{k: v for k, v in f.items() if k != "legs"} for f in forms]
    return {
        "cohort": "BTC15m", "windows": len(ws),
        "two_sided_windows": len(forms), "one_sided_windows": one_sided,
        "two_sided_rate": round(len(forms) / len(ws), 4) if ws else None,
        "complement_at_fill_n": _dist([f["complement_at_fill_n"]
                                       for f in forms]),
        "extends_before_complement": _dist([f["extends_before_complement"]
                                            for f in forms]),
        "seconds_to_complement": _dist([f["seconds_to_complement"]
                                        for f in forms]),
        "tte_at_first_leg_s": _dist([f["first_tte_s"] for f in forms]),
        "pair_cost_at_formation": _dist([float(f["pair_cost_at_formation"])
                                         for f in forms
                                         if f["pair_cost_at_formation"]]),
        "control_or_emergent": control_or_emergent(forms),
        "by_conditioner": by_conditioner(forms),
        "formations": slim[:400],
        "labels": ("L5's own record. Not our fill, not a shadow result, not "
                   "realized P&L. Our 0.96 ceiling is shown for comparison and "
                   "never filters what is measured."),
    }


def _dist(v: Sequence) -> Optional[Dict[str, Any]]:
    if not v:
        return None
    s = sorted(v)
    return {"n": len(s), "p10": s[int(len(s) * 0.1)], "median": s[len(s) // 2],
            "p90": s[int(len(s) * 0.9)], "min": s[0], "max": s[-1]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq", default=SEQ)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args(argv)
    rep = report(a.seq, limit=a.limit or None)
    if not a.full:
        rep.pop("formations", None)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
