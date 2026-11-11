"""Why a pair planned at 0.95 completes near 0.99, and what repricing can do.

OFFLINE. Reads L5's public Polymarket BTC15m history; places nothing, changes
no configuration, and is imported by nothing under ``src/``.

THE QUESTION (owner, 2026-11-10). Both live pairs on the fixed engine opened
at a planned 0.94-0.95 and completed at 0.99. Is that execution, or is it the
structure of the trade -- and can inventory-aware repricing improve the pair
cost WITHOUT increasing stranded-leg losses?

METHOD. The same one ``residual_exit/dwell_validation.py`` established and
for the same reason: PMUS book history of this length does not exist, so the
PRICE PATH and the SETTLEMENT come from L5's Polymarket windows, and the
ticks, the order rules and the fees are PMUS's.

  * path: every L5 print in a resolved BTC15m window, as an UP price
    (``px`` for an Up fill, ``1 - px`` for a Down fill), in time order;
  * book: one tick wide around the print -- PMUS BTC15m's median spread --
    so the UP bid is ``m - 0.005`` and the DOWN bid ``1 - m - 0.005``;
  * opening: both legs post-only, ``depth`` ticks behind their touch, exactly
    as ``TS.build_orders`` prices them (depth 2 -> a 0.95 pair), only while
    both legs are at or above the 0.30 first-leg floor and before the 480 s
    entry cutoff; unfilled legs are cancelled at the 120 s order TTL;
  * a maker order fills ONLY WHEN THE PATH TRADES THROUGH IT: the price must
    pass ``through`` ticks beyond the level. ``through=1`` is the default and
    the conservative one; ``through=0`` is reported as the optimistic bound.
    A quote the path merely touches is never a fill;
  * after a first fill the complement follows the deployed controller: it
    joins its touch whenever doing so keeps the pair at or under the
    COMPLETION CEILING, until the 600 s completion cutoff;
  * a leg still unmatched then is STRANDED and settles on the venue's
    outcome: +1 - cost if its side won, -cost if not;
  * maker commission is 0.0000, as on every live fill; no rebate is counted.

THE POLICIES differ only in the completion ceiling, which is the whole of
what "inventory-aware repricing" can change while staying maker-only:

    C0.99        the deployed behaviour: complete up to 0.99 at once
    C0.96..0.98  patient: never pay more than that for the pair
    DECAY(t)     0.96 for the first t seconds after the fill, then 0.99
    UNDERWATER   0.96, relaxing to 0.99 only once the held leg is marked
                 ``k`` ticks below its cost -- pay up only to stop a loss

WHAT THIS CANNOT SAY. It is Polymarket's path, not PMUS's; the path is L5's
own prints, so it is silent between them; queue position is unmodelled beyond
``through``; and L5's presence in every print means the path is sampled where
he traded. It ranks policies against each other on identical paths. It is not
a forecast of our fills.
"""
from __future__ import annotations

import collections
import json
import math
import statistics as st
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5 import favorite_accumulation as FA

TICK = 0.01
HALF_SPREAD = 0.005
FLOOR = 0.30                 # L5_TS_MIN_FIRST_LEG_PRICE, as deployed
ENTRY_CUTOFF_S = 480.0       # L5_TS_ENTRY_CUTOFF_S
COMPLETE_CUTOFF_S = 600.0    # L5_TS_COMPLETE_CUTOFF_S
ORDER_TTL_S = 120.0
OPEN_AFTER_S = 5.0           # the engine arms a few seconds into the window
SPLIT = 0.6
EPS = 1e-9


def _down(x: float) -> float:
    """Round DOWN to the tick: a bid never rounds toward the market."""
    return math.floor(x / TICK + EPS) * TICK


def paths(trades: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """{slug: {"ws", "winner", "path": [(t_off, up_px)]}} in time order."""
    out: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        if not (0.0 <= t["t_off"] <= 900.0) or not (0.01 <= t["px"] <= 0.99):
            continue
        up = t["px"] if t["outcome"] == "Up" else 1.0 - t["px"]
        w = out.setdefault(t["slug"], {"ws": t["ws"], "winner": t["winner"],
                                       "path": []})
        w["path"].append((t["t_off"], up))
    for w in out.values():
        w["path"].sort()
    return out


def ceiling_at(policy: Tuple[str, float], *, since_fill_s: float,
               held_mark: float, held_cost: float) -> float:
    """The pair ceiling this policy allows right now."""
    kind, arg = policy
    if kind == "C":
        return arg
    if kind == "DECAY":
        return 0.96 if since_fill_s < arg else 0.99
    if kind == "UNDERWATER":
        return 0.99 if held_mark <= held_cost - arg * TICK + EPS else 0.96
    raise ValueError(kind)


def replay_window(path: Sequence[Tuple[float, float]], winner: str, *,
                  policy: Tuple[str, float], depth: int = 2,
                  through: int = 1, max_rounds: int = 1) -> Dict[str, Any]:
    """One window, per ONE contract per round. Returns its rounds and net."""
    rounds: List[Dict[str, Any]] = []
    state = "FLAT"
    q_up = q_dn = 0.0
    t_open = 0.0
    held = None                  # ("UP"|"DOWN", cost, t_fill)
    comp_px: Optional[float] = None
    thr = through * TICK

    def strand(h):
        side, cost, t_fill = h
        won = (winner == "Up") == (side == "UP")
        rounds.append({"kind": "STRAND", "side": side, "cost": cost,
                       "pnl": (1.0 if won else 0.0) - cost,
                       "won": won, "t_fill": t_fill})

    for t, m in path:
        if state == "FLAT":
            if len(rounds) >= max_rounds or t < OPEN_AFTER_S \
                    or t > ENTRY_CUTOFF_S:
                continue
            u = _down(m - HALF_SPREAD - depth * TICK)
            d = _down(1.0 - m - HALF_SPREAD - depth * TICK)
            if u < FLOOR - EPS or d < FLOOR - EPS:
                continue
            q_up, q_dn, t_open, state = u, d, t, "QUOTING"
            continue
        if state == "QUOTING":
            up_fill = m <= q_up - thr + EPS
            dn_fill = (1.0 - m) <= q_dn - thr + EPS
            if up_fill or dn_fill:
                held = ("UP", q_up, t) if up_fill else ("DOWN", q_dn, t)
                comp_px = q_dn if up_fill else q_up     # still resting
                state = "HELD"
                continue
            if t - t_open >= ORDER_TTL_S:
                state = "FLAT"                          # both cancelled
            continue
        if state == "HELD":
            side, cost, t_fill = held
            comp_m = (1.0 - m) if side == "UP" else m   # complement's price
            # the resting complement fills if the path trades through it
            if comp_px is not None and comp_m <= comp_px - thr + EPS:
                rounds.append({"kind": "PAIR", "pair": cost + comp_px,
                               "pnl": 1.0 - cost - comp_px,
                               "t_fill": t_fill, "t_done": t,
                               "first": side})
                state, held, comp_px = "FLAT", None, None
                continue
            if t > COMPLETE_CUTOFF_S:
                strand(held)
                state, held, comp_px = "DONE", None, None
                continue
            # the original complement dies at its TTL; the controller then
            # (and on any touch move) re-joins the touch under the ceiling
            ceil = ceiling_at(policy, since_fill_s=t - t_fill,
                              held_mark=1.0 - comp_m, held_cost=cost)
            touch = _down(comp_m - HALF_SPREAD)
            want = min(touch, _down(ceil - cost + EPS))
            if comp_px is None or want > comp_px + EPS or \
                    (t - t_open >= ORDER_TTL_S and want < comp_px - EPS):
                comp_px = want if want >= TICK else None
            continue
    if state == "HELD" and held is not None:
        strand(held)
    net = sum(r["pnl"] for r in rounds)
    return {"rounds": rounds, "net": net,
            "pairs": sum(1 for r in rounds if r["kind"] == "PAIR"),
            "strands": sum(1 for r in rounds if r["kind"] == "STRAND")}


def _ci(xs: Sequence[float]) -> Tuple[float, float, float]:
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else 0.0, float("nan"), float("nan"))
    m, sd = st.mean(xs), st.stdev(xs)
    h = 1.96 * sd / math.sqrt(n)
    return m, m - h, m + h


def summarise(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Window-clustered: one window is one observation."""
    pairs = [r for w in results for r in w["rounds"] if r["kind"] == "PAIR"]
    strands = [r for w in results for r in w["rounds"]
               if r["kind"] == "STRAND"]
    first = len(pairs) + len(strands)
    mean, lo, hi = _ci([w["net"] for w in results])
    costs = sorted(p["pair"] for p in pairs)

    def pct(q):
        return costs[min(len(costs) - 1, int(q * len(costs)))] if costs \
            else None

    return {
        "windows": len(results),
        "windows_with_a_first_fill": sum(1 for w in results if w["rounds"]),
        "first_fills": first, "pairs": len(pairs), "strands": len(strands),
        "strand_rate": (len(strands) / first) if first else None,
        "pair_cost_mean": (st.mean(costs) if costs else None),
        "pair_cost_p25": pct(0.25), "pair_cost_p50": pct(0.50),
        "pair_cost_p75": pct(0.75),
        "pairs_at_plan": (sum(1 for c in costs if c <= 0.955) / len(costs)
                          if costs else None),
        "pair_pnl_total": sum(p["pnl"] for p in pairs),
        "strand_pnl_total": sum(s["pnl"] for s in strands),
        "strand_pnl_mean": (st.mean(s["pnl"] for s in strands)
                            if strands else None),
        "strand_lost_frac": (sum(1 for s in strands if not s["won"])
                             / len(strands) if strands else None),
        "net_per_window": mean, "ci95": [lo, hi],
        "net_per_first_fill": (sum(w["net"] for w in results) / first
                               if first else None),
    }


POLICIES: Tuple[Tuple[str, Tuple[str, float]], ...] = (
    ("C0.99 (deployed)", ("C", 0.99)),
    ("C0.98", ("C", 0.98)),
    ("C0.97", ("C", 0.97)),
    ("C0.96 (patient)", ("C", 0.96)),
    ("DECAY 30s", ("DECAY", 30.0)),
    ("DECAY 120s", ("DECAY", 120.0)),
    ("UNDERWATER 3t", ("UNDERWATER", 3.0)),
    ("UNDERWATER 6t", ("UNDERWATER", 6.0)),
)


def run(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    ps = paths(trades)
    order = sorted(ps, key=lambda s: ps[s]["ws"])
    cut = int(len(order) * SPLIT)
    halves = {"ALL": order, "IS": order[:cut], "OOS": order[cut:]}
    out: Dict[str, Any] = {
        "evidence_class": ("L5 POLYMARKET PATHS with PMUS ticks and fees -- "
                           "not PMUS fills, not our own"),
        "windows": {k: len(v) for k, v in halves.items()},
        "policies": {}, "rounds": {}, "depth": {}, "through": {}}

    def go(slugs, **kw):
        return summarise([replay_window(ps[s]["path"], ps[s]["winner"], **kw)
                          for s in slugs])

    for name, pol in POLICIES:
        out["policies"][name] = {
            h: go(slugs, policy=pol) for h, slugs in halves.items()}
    for r in (1, 2, 4, 8):
        out["rounds"][str(r)] = {
            h: go(slugs, policy=("C", 0.99), max_rounds=r)
            for h, slugs in halves.items()}
    for d in (1, 2, 3, 4):
        out["depth"][str(d)] = go(order, policy=("C", 0.99), depth=d)
    for x in (0, 1, 2):
        out["through"][str(x)] = go(order, policy=("C", 0.99), through=x)
    return out


def _f(x, w=8, p=4):
    return (" " * (w - 3) + "n/a") if x is None else ("%+*.*f" % (w, p, x))


def main() -> None:
    r = run(FA.load_trades(FA.load_outcomes()))
    print("evidence:", r["evidence_class"])
    print("windows:", r["windows"])
    hdr = ("%-18s %5s %5s %6s %6s %6s %6s %7s %8s %9s  %s"
           % ("policy", "fills", "pairs", "strand", "p50", "mean", "@plan",
              "str.pnl", "net/win", "net/fill", "ci95 net/win"))
    for half in ("ALL", "IS", "OOS"):
        print("\n== completion ceiling, %s (depth 2, through 1, 1 round) ==" %
              half)
        print(hdr)
        for name, _ in POLICIES:
            s = r["policies"][name][half]
            print("%-18s %5d %5d %5.1f%% %6.2f %6.3f %5.0f%% %7.3f %8s %9s  "
                  "[%+.4f, %+.4f]" % (
                      name, s["first_fills"], s["pairs"],
                      100 * (s["strand_rate"] or 0), s["pair_cost_p50"] or 0,
                      s["pair_cost_mean"] or 0,
                      100 * (s["pairs_at_plan"] or 0),
                      s["strand_pnl_mean"] or 0, _f(s["net_per_window"]),
                      _f(s["net_per_first_fill"], 9), s["ci95"][0],
                      s["ci95"][1]))
    print("\n== rounds per window, deployed ceiling (per 1 contract/round) ==")
    for k, v in r["rounds"].items():
        for half in ("ALL", "OOS"):
            s = v[half]
            print("max_rounds=%s %-3s fills/win %.2f pairs/win %.2f strands "
                  "%.1f%% net/win %s ci [%+.4f, %+.4f]" % (
                      k, half, s["first_fills"] / s["windows"],
                      s["pairs"] / s["windows"],
                      100 * (s["strand_rate"] or 0), _f(s["net_per_window"]),
                      s["ci95"][0], s["ci95"][1]))
    print("\n== opening depth (ticks behind touch), deployed ceiling ==")
    for k, s in r["depth"].items():
        print("depth=%s plan %.2f fills %d strands %.1f%% p50 %.2f net/win %s "
              "net/fill %s" % (k, 0.99 - 2 * int(k) * TICK, s["first_fills"],
                               100 * (s["strand_rate"] or 0),
                               s["pair_cost_p50"] or 0,
                               _f(s["net_per_window"]),
                               _f(s["net_per_first_fill"], 9)))
    print("\n== fill model: ticks the path must pass BEYOND our level ==")
    for k, s in r["through"].items():
        print("through=%s fills %d strands %.1f%% p50 %.2f net/win %s "
              "net/fill %s" % (k, s["first_fills"],
                               100 * (s["strand_rate"] or 0),
                               s["pair_cost_p50"] or 0,
                               _f(s["net_per_window"]),
                               _f(s["net_per_first_fill"], 9)))


if __name__ == "__main__":
    main()
