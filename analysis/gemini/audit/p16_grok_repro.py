"""P16 -- independent reproduction of the Grok 2026-09-22/23 L5 findings.

HYPOTHESIS SOURCE, NOT GROUND TRUTH. Every number here is recomputed from a
harvest this module fetched itself from the public Polymarket activity API for
wallet 0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68. Grok's figures are quoted
only to be compared against.

FILLS AND REDEMPTIONS ONLY. Resting orders, cancels, reprices and maker/taker
role are not in this feed and nothing here infers them.
"""
from __future__ import annotations

import collections
import json
from typing import Any, Dict, List

FAM = "btc-updown-15m"


def load(path: str):
    d = json.load(open(path, encoding="utf-8"))
    rows = d["rows"] if isinstance(d, dict) else d
    wins: Dict[str, List[dict]] = collections.defaultdict(list)
    winner: Dict[str, str] = {}
    for r in rows:
        slug = r.get("eventSlug") or ""
        if not slug.rsplit("-", 1)[0] == FAM:
            continue
        if r.get("type") == "REDEEM":
            # a redemption pays the WINNING outcome
            winner[slug] = r.get("outcome")
        elif r.get("type") == "TRADE" and r.get("side") == "BUY":
            wins[slug].append(r)
    for s in wins:
        wins[s].sort(key=lambda x: (x.get("timestamp") or 0))
    return dict(wins), winner


def _vw(q, s):
    return None if q <= 0 else s / q


def window_rows(fills, win):
    """Per-window facts. `win` is the settled outcome or None."""
    f0 = fills[0]
    s0, t0 = f0.get("outcome"), f0.get("timestamp") or 0
    qty = collections.defaultdict(float)
    spend = collections.defaultdict(float)
    other = "Up" if s0 == "Down" else "Down"

    first_opp_dt = None
    pair_series = []
    excursion_max = None
    prices_30 = collections.defaultdict(set)
    prices_60 = collections.defaultdict(set)
    for f in fills:
        o = f.get("outcome")
        q = float(f.get("size") or 0)
        px = float(f.get("price") or 0)
        dtv = (f.get("timestamp") or 0) - t0
        if dtv <= 30:
            prices_30[o].add(px)
        if dtv <= 60:
            prices_60[o].add(px)
        if o != s0 and first_opp_dt is None:
            first_opp_dt = dtv
        qty[o] += q
        spend[o] += q * px
        u, d = _vw(qty["Up"], spend["Up"]), _vw(qty["Down"], spend["Down"])
        if u is not None and d is not None:
            pc = u + d
            pair_series.append((dtv, pc))
            excursion_max = pc if excursion_max is None else max(excursion_max, pc)

    matched = min(qty["Up"], qty["Down"])
    resid_side = ("Up" if qty["Up"] > qty["Down"]
                  else ("Down" if qty["Down"] > qty["Up"] else None))
    last = fills[-1].get("outcome")
    # EXTEND = the last fill added to the side already in surplus;
    # REPAIR  = it added to the deficit side.
    if resid_side is None:
        traj = None
    else:
        traj = "EXTEND" if last == resid_side else "REPAIR"

    return {
        "slug": fills[0].get("eventSlug"),
        "n_fills": len(fills),
        "first_side": s0,
        "first_price": float(f0.get("price") or 0),
        "winner": win,
        "first_side_won": (None if win is None else (s0 == win)),
        "ever_two_sided": first_opp_dt is not None,
        "latency_first_opposite_s": first_opp_dt,
        "qty_up": qty["Up"], "qty_down": qty["Down"],
        "matched_qty": matched,
        "residual_side": resid_side,
        "residual_qty": abs(qty["Up"] - qty["Down"]),
        "residual_won": (None if (win is None or resid_side is None)
                         else (resid_side == win)),
        "last_action": traj,
        "pair_first": pair_series[0][1] if pair_series else None,
        "pair_max": excursion_max,
        "pair_final": pair_series[-1][1] if pair_series else None,
        "pair_min": min((p for _, p in pair_series), default=None),
        "distinct_px_30s": max((len(v) for v in prices_30.values()), default=0),
        "distinct_px_60s": max((len(v) for v in prices_60.values()), default=0),
        "started_on_loser": (None if win is None else (s0 != win)),
        "_pair_series": pair_series,
        "_qty": dict(qty), "_spend": dict(spend),
    }


def settled_pnl(qty, spend, winner):
    """Settlement-implied trading P&L: payout on the winning leg - spend."""
    if winner is None:
        return None
    payout = qty.get(winner, 0.0)
    return payout - (spend.get("Up", 0.0) + spend.get("Down", 0.0))


def q(vals, ps=(0.25, 0.5, 0.75)):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return {}
    out = {"n": len(v), "mean": round(sum(v) / len(v), 4)}
    for p in ps:
        out["p%d" % int(p * 100)] = round(v[min(len(v) - 1,
                                                int(round(p * (len(v) - 1))))], 4)
    return out
