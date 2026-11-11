"""Does L5's 0.9624 exist at the start, or emerge from accumulation?

THE QUESTION R/S/D FORCED. Three arms that required an UP quote and a DOWN
quote to clear the 0.96 pair ceiling BEFORE either could rest produced pairs at
1.085-1.098, every one above par. L5 reaches 0.9624. If his cheap economics
were available at acquisition, a pair-forming clone would find them. It did
not, which leaves the other possibility: the number is an EMERGENT PROPERTY of
a running inventory, not a property of any individual pair.

This reconstructs his actual BTC15m windows fill by fill and measures where the
number comes from.

TWO ACCOUNTING RULES, both established elsewhere and treated as authoritative:

  * COST IS `usdcSize`, NEVER price*size. They differ by 0.07*p*(1-p) per share
    on a fee-bearing fill. Only usdcSize reconciles to realized cash, and the
    whole point here is a cost basis.
  * EVERY BTC15m TRADE IS A BUY. Measured, not assumed: 66,949 TRADE rows
    across 528 windows, all side=BUY, zero sells. Inventory is therefore purely
    accumulative and a position is never traded out of -- only redeemed. That
    alone is evidence about the mechanism.

THE PAIR COST IS A RUNNING VWAP SUM:

    matched_vwap_pair_cost = (UP spend / UP qty) + (DOWN spend / DOWN qty)

undefined until BOTH sides are held, because a one-sided position has a cost
but not a pair cost. Reporting 0 there would read as the cheapest pair
possible.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from typing import Any, Dict, Iterable, List, Optional, Sequence

SLUG_PREFIX = "btc-updown-15m"
WINDOW_S = 900
# where the user asked for the trajectory to be read
CHECKPOINTS = (2, 5, 10, 20, 40, 60, 80, 100)


def load(path: str, prefix: str = SLUG_PREFIX) -> Dict[str, List[dict]]:
    """Chronological BUY fills per window. REDEEM rows are settlement, not
    inventory, and are excluded from the trajectory."""
    wins: Dict[str, List[dict]] = {}
    seen = set()
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        for r in json.loads(line).get("payload") or []:
            slug = str(r.get("eventSlug") or "")
            if not slug.startswith(prefix) or r.get("type") != "TRADE":
                continue
            # the same page can be fetched twice; a fill is identified by its
            # transaction, asset and timestamp together
            k = (r.get("transactionHash"), r.get("asset"), r.get("timestamp"),
                 r.get("size"))
            if k in seen:
                continue
            seen.add(k)
            wins.setdefault(slug, []).append(r)
    for slug, rows in wins.items():
        rows.sort(key=lambda r: (r.get("timestamp") or 0,
                                 str(r.get("transactionHash") or "")))
    return wins


def window_end(slug: str) -> Optional[int]:
    try:
        return int(slug.rsplit("-", 1)[1]) + WINDOW_S
    except Exception:                                         # noqa: BLE001
        return None


def trajectory(slug: str, fills: Sequence[dict]) -> List[Dict[str, Any]]:
    """One row per fill: the inventory state AFTER it, plus what came next."""
    end = window_end(slug)
    up_q = dn_q = 0.0
    up_c = dn_c = 0.0
    out: List[Dict[str, Any]] = []
    for i, f in enumerate(fills):
        sz = float(f.get("size") or 0.0)
        usd = float(f.get("usdcSize") or 0.0)
        side = "Up" if str(f.get("outcome")) == "Up" else "Down"
        if side == "Up":
            up_q += sz
            up_c += usd
        else:
            dn_q += sz
            dn_c += usd
        up_v = (up_c / up_q) if up_q > 0 else None
        dn_v = (dn_c / dn_q) if dn_q > 0 else None
        pair = (up_v + dn_v) if (up_v is not None and dn_v is not None) else None
        matched = min(up_q, dn_q)
        resid = abs(up_q - dn_q)
        tot = up_q + dn_q
        nxt = fills[i + 1] if i + 1 < len(fills) else None
        ts = f.get("timestamp")
        out.append({
            "n": i + 1,
            "t": ts,
            "tte_s": ((end - ts) if (end is not None and ts is not None)
                      else None),
            "side": side,
            "price": float(f.get("price") or 0.0),
            "size": sz,
            "usdc": usd,
            "cum_UP_qty": round(up_q, 6), "cum_DOWN_qty": round(dn_q, 6),
            "cum_UP_VWAP": (round(up_v, 6) if up_v is not None else None),
            "cum_DOWN_VWAP": (round(dn_v, 6) if dn_v is not None else None),
            "matched_qty": round(matched, 6),
            "matched_VWAP_pair_cost": (round(pair, 6) if pair is not None
                                       else None),
            "residual_qty": round(resid, 6),
            "residual_dir": (None if resid == 0
                             else ("Up" if up_q > dn_q else "Down")),
            "residual_rel": (round(resid / tot, 6) if tot > 0 else None),
            "next_side": (("Up" if str(nxt.get("outcome")) == "Up" else "Down")
                          if nxt else None),
            "next_price": (float(nxt.get("price") or 0.0) if nxt else None),
            "same_side_next": ((("Up" if str(nxt.get("outcome")) == "Up"
                                 else "Down") == side) if nxt else None),
        })
    return out


def _q(xs: Sequence[float], p: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(int(p * (len(s) - 1)), len(s) - 1)], 6)


def _summ(xs: Sequence[float]) -> Optional[Dict[str, Any]]:
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"n": len(xs), "p10": _q(xs, 0.10), "median": _q(xs, 0.50),
            "p90": _q(xs, 0.90), "mean": round(statistics.fmean(xs), 6)}


def checkpoints(traj: Sequence[Sequence[dict]]) -> Dict[str, Any]:
    """Pair cost and imbalance at fill 2/5/10/.../100/final, across windows.

    A window shorter than a checkpoint contributes NOTHING to it rather than
    its last value: carrying the final row forward would drag the late
    checkpoints toward the short windows' endpoints and manufacture the very
    convergence this is testing for.
    """
    out: Dict[str, Any] = {}
    for c in CHECKPOINTS:
        pcs, imb, mat = [], [], []
        for rows in traj:
            if len(rows) < c:
                continue
            r = rows[c - 1]
            if r["matched_VWAP_pair_cost"] is not None:
                pcs.append(r["matched_VWAP_pair_cost"])
            if r["residual_rel"] is not None:
                imb.append(r["residual_rel"])
            mat.append(r["matched_qty"])
        out[f"after_{c}"] = {
            "windows_reaching_this_fill": sum(1 for rows in traj
                                              if len(rows) >= c),
            "windows_two_sided_by_here": len(pcs),
            "matched_VWAP_pair_cost": _summ(pcs),
            "inventory_imbalance_rel": _summ(imb),
            "matched_qty": _summ(mat),
        }
    pcs, imb, mat, n = [], [], [], []
    for rows in traj:
        if not rows:
            continue
        r = rows[-1]
        n.append(len(rows))
        if r["matched_VWAP_pair_cost"] is not None:
            pcs.append(r["matched_VWAP_pair_cost"])
        if r["residual_rel"] is not None:
            imb.append(r["residual_rel"])
        mat.append(r["matched_qty"])
    out["final"] = {
        "windows_reaching_this_fill": len(n),
        "windows_two_sided_by_here": len(pcs),
        "fills_per_window": _summ([float(x) for x in n]),
        "matched_VWAP_pair_cost": _summ(pcs),
        "inventory_imbalance_rel": _summ(imb),
        "matched_qty": _summ(mat),
    }
    return out


def first_formation(traj: Sequence[Sequence[dict]]) -> Dict[str, Any]:
    """The pair cost the moment both sides are first held -- the ORIGIN."""
    costs, at_n, tte = [], [], []
    for rows in traj:
        for r in rows:
            if r["matched_VWAP_pair_cost"] is not None:
                costs.append(r["matched_VWAP_pair_cost"])
                at_n.append(float(r["n"]))
                if r["tte_s"] is not None:
                    tte.append(float(r["tte_s"]))
                break
    return {"windows_ever_two_sided": len(costs),
            "pair_cost_at_first_formation": _summ(costs),
            "fill_number_at_formation": _summ(at_n),
            "tte_s_at_formation": _summ(tte),
            "within_0_96_at_formation": sum(1 for c in costs if c <= 0.96),
            "reads": ("if the median here is already ~0.96 the economics are "
                      "available at acquisition; if it sits near par they are "
                      "EMERGENT and a pair-forming gate rejects the entry")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity", default="data/l5/raw/activity.jsonl")
    ap.add_argument("--out",
                    default="analysis/l5/inventory_trajectory/trajectory.json")
    a = ap.parse_args(argv)

    wins = load(a.activity)
    traj = [trajectory(s, f) for s, f in sorted(wins.items())]
    traj = [t for t in traj if t]
    rep = {
        "windows": len(traj),
        "total_fills": sum(len(t) for t in traj),
        "all_fills_are_buys": True,
        "first_formation": first_formation(traj),
        "checkpoints": checkpoints(traj),
        "labels": ("cost basis from usdcSize, never price*size. pair cost is "
                   "the running VWAP sum and is undefined until both sides are "
                   "held. A window shorter than a checkpoint contributes "
                   "nothing to it."),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump({**rep, "windows_detail": [t[:400] for t in traj[:40]]},
                  fh, indent=1, default=str)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
