"""PART C -- seven PMUS execution counterfactuals, one variable at a time.

WHY THIS EXISTS. The question is not "which configuration earns most" -- no
threshold is searched here and no arm is selected on its P&L. The question is
which EXECUTION variable moves fills, and by how much, so that the bottleneck
can be named from evidence rather than assumed.

EACH ARM CHANGES EXACTLY ONE KNOB from REAL_QUEUE. The policy, the frozen
model, the books, the decision gap, the residual cap and the completion rule
are identical in every arm.

    REAL_QUEUE            the measured charge: observed touch depth resting
                          ahead of us when we joined                (baseline)
    ZERO_QUEUE            depth_mult 0 -- first in line everywhere
    IMPROVED_QUEUE_50     depth_mult 0.5   position improved, price unchanged
    IMPROVED_QUEUE_25     depth_mult 0.25
    ONE_TICK_BEHIND       rest a tick worse than the touch
    ONE_TICK_AHEAD        rest a tick better; crossing rungs are dropped by
                          `rung_prices`, so this never becomes a taker
    CANCEL_REPRICE        preserve=False -- every decision re-posts and loses
                          its place, against the persistent baseline
    SINGLE_PRICE          one resting price instead of four

TWO FLOW SERIES, NEVER MERGED. PMUS flow is inferred from `shares_traded_cum`
deltas priced at `last_trade_px`, and that price lies outside the venue's OWN
simultaneously returned [bid, ask] in 22.8% of increments -- a lagging point
estimate of every trade in an ~8 s poll interval. So every arm is run twice:

    ALL            every increment                              (upper bound)
    ATTRIBUTABLE   only increments whose last_trade_px lies inside that same
                   row's quote, i.e. where price and volume can be attributed
                   to each other at all                          (lower bound)

A conclusion that survives only on ALL flow is a conclusion about our flow
reconstruction, not about execution.

ACCOUNTING. Settlement is read ONLY from state == MARKET_STATE_EXPIRED. The two
P&L lines are kept apart, because they are not the same kind of quantity:

    matched-pair   (1.00 - pair_basis) * matched_units     outcome-independent
    residual       (settle_on_surplus_side - avg_cost) * residual_units

The label is SETTLED-MODELED. No order was ever submitted, so no fill is
venue-confirmed and none of this is realized profit. Fees are zero on PMUS
maker; rebates are ZERO here and are never estimated.
"""
from __future__ import annotations

import collections
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

ARMS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("REAL_QUEUE",        {}),
    ("ZERO_QUEUE",        {"depth_mult": 0.0}),
    ("IMPROVED_QUEUE_50", {"depth_mult": 0.5}),
    ("IMPROVED_QUEUE_25", {"depth_mult": 0.25}),
    ("ONE_TICK_BEHIND",   {"px_offset_ticks": -1}),
    ("ONE_TICK_AHEAD",    {"px_offset_ticks": 1}),
    ("CANCEL_REPRICE",    {"preserve": False}),
    ("SINGLE_PRICE",      {"rungs": 1}),
)


def attributable(row: dict, px: float) -> bool:
    """Can this increment's price and volume be attributed to each other?

    True only when `last_trade_px` lies inside the quote returned in the SAME
    response. Outside it, the price is a stale point estimate of trades we did
    not see, and the volume belongs to prices we cannot name."""
    b, a = row.get("bid"), row.get("ask")
    if b is None or a is None:
        return False
    return float(b) - 1e-9 <= px <= float(a) + 1e-9


def build_flow(raw_by_slug: Dict[str, List[Tuple[float, dict]]]
               ) -> Tuple[Dict[str, list], Dict[str, list]]:
    """(ALL, ATTRIBUTABLE) inferred-flow series from polled cumulative volume."""
    al: Dict[str, list] = collections.defaultdict(list)
    at: Dict[str, list] = collections.defaultdict(list)
    for slug, v in raw_by_slug.items():
        v = sorted(v, key=lambda r: r[0])
        prev = None
        for t, d in v:
            if prev is not None:
                try:
                    dv = float(d["shares_traded_cum"]) - float(prev["shares_traded_cum"])
                except (KeyError, TypeError, ValueError):
                    dv = 0.0
                if dv > 0 and d.get("last_trade_px") is not None:
                    px = float(d["last_trade_px"])
                    p = {"t": t, "px": px, "size": dv,
                         "prov": "CAUSAL_INFERRED_NOT_A_PRINT"}
                    al[slug].append(p)
                    if attributable(d, px):
                        at[slug].append(p)
            prev = d
    return al, at


def settled_yes(settlements: dict, slug: str) -> Optional[float]:
    """The YES settlement value, or None. An OPEN market returns 0.0000 as a
    DEFAULT rather than a result, so state is checked before the number is."""
    s = settlements.get(slug) or {}
    if not s.get("settled") or s.get("state") != "MARKET_STATE_EXPIRED":
        return None
    v = s.get("yes_settle")
    return None if v is None else float(v)


def economics(res: Iterable[dict], settlements: dict) -> Dict[str, Any]:
    """Aggregate one arm. The two P&L lines are accumulated separately and the
    combined figure is reported as its own clearly labelled line -- it is not a
    claim that matched-pair and residual P&L are the same kind of thing."""
    t = collections.Counter()
    yc = nc = 0.0
    mp = rp = 0.0
    covered = uncovered = 0
    for rr in res:
        for k in ("fills", "yes_n", "no_n", "matched", "resid"):
            t[k] += rr[k]
        yc += rr["yes_cost"]
        nc += rr["no_cost"]
        ys = settled_yes(settlements, rr["slug"])
        if ys is None:
            uncovered += 1
            continue
        covered += 1
        m = rr["matched"]
        if m and rr["yes_n"] and rr["no_n"]:
            basis = rr["yes_cost"] / rr["yes_n"] + rr["no_cost"] / rr["no_n"]
            mp += (1.0 - basis) * m
        r = rr["yes_n"] - rr["no_n"]
        if r:
            sy = r > 0                       # surplus is on the YES side
            n = abs(r)
            avg = (rr["yes_cost"] / rr["yes_n"]) if sy else (rr["no_cost"] / rr["no_n"])
            val = ys if sy else (1.0 - ys)
            rp += (val - avg) * n
    basis = ((yc / t["yes_n"] + nc / t["no_n"])
             if t["yes_n"] and t["no_n"] else None)
    return {"fills": t["fills"], "yes_n": t["yes_n"], "no_n": t["no_n"],
            "matched": t["matched"], "resid": t["resid"],
            "pair_basis": basis, "turnover": yc + nc,
            "settled_markets": covered, "unsettled_markets": uncovered,
            "matched_pair_pnl_SETTLED_MODELED": mp,
            "residual_pnl_SETTLED_MODELED": rp,
            "combined_line_NOT_A_SINGLE_QUANTITY": mp + rp,
            "fees": 0.0, "rebates_NEVER_ESTIMATED": 0.0,
            "label": "SETTLED-MODELED, never realized: no order was submitted"}


def run_arms(books: dict, flow: Dict[str, list], settlements: dict,
             replay, venue: str = "PMUS", arms=ARMS,
             base: Optional[dict] = None) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for name, kw in arms:
        k = dict(base or {})
        k.update(kw)
        res = []
        for (v, slug), se in books.items():
            if v != venue or len(se) < 3:
                continue
            rr = replay(slug, se, flow.get(slug, []), **k)
            if rr["fills"]:
                res.append(rr)
        out[name] = economics(res, settlements)
        out[name]["knob"] = kw or {"(baseline)": True}
    return out


def table(by_flow: Dict[str, Dict[str, Dict[str, Any]]]) -> str:
    cols = ("fills", "matched", "resid", "pair_basis", "turnover",
            "matched_pair_pnl_SETTLED_MODELED", "residual_pnl_SETTLED_MODELED")
    head = ("%-19s %-13s %7s %8s %7s %9s %10s %11s %10s"
            % ("arm", "flow", "fills", "matched", "resid", "basis",
               "turnover$", "matched$", "residual$"))
    lines = [head, "-" * len(head)]
    for arm, _ in ARMS:
        for fname in by_flow:
            e = by_flow[fname].get(arm)
            if not e:
                continue
            lines.append("%-19s %-13s %7d %8d %7d %9s %10.2f %11.2f %10.2f"
                         % (arm, fname, e["fills"], e["matched"], e["resid"],
                            ("%.4f" % e["pair_basis"]) if e["pair_basis"] else "n/a",
                            e["turnover"], e[cols[5]], e[cols[6]]))
    lines.append("")
    lines.append("SETTLED-MODELED. No order was submitted; no fill is venue-confirmed.")
    lines.append("matched$ and residual$ are different quantities and are never summed "
                 "into one 'profit'.")
    return "\n".join(lines)
