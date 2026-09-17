"""PARENT_ORDER_SURFACE_GEOMETRY_V1 -- what orders does L5 actually place?

OWNER 2026-09-17. The behavioral unit is now the CHAIN-PROVEN PARENT ORDER
(`OrderFilled.orderHash`), never the public fill fragment. CLUSTER_SIZE_
FORENSICS_V1 established why: 92.7% of resolved same-instant clusters are ONE
order, parent quantities are round and dominated by 100 while fill sizes carry
fragments like 6 and 15, and collapsing removes 17-30% of apparent actions
without touching the run-persistence structure.

THE QUESTION. Can chain-proven parents reveal the SIZE and PRICE geometry of the
latent quote surface even though we never see an unfilled order?

TWO LIMITS THAT ARE NEVER BLURRED HERE.

  EXECUTED QUANTITY IS NOT ORDER SIZE. `OrderFilled` reports what MATCHED. An
  order for 200 that filled 100 and was cancelled looks exactly like an order
  for 100. Every quantity below is `executed_qty`, a LOWER BOUND on the order's
  size, and the field is named that way.

  EXECUTION LIFETIME IS NOT ORDER LIFETIME. first -> last observed execution
  says nothing about when the order was submitted or finally cancelled. It is
  reported as `execution_lifetime_lower_bound_s` and never as persistence.

A round-number mode in executed quantity is still informative: 100 appearing
152 times is not what partial fills of arbitrary sizes produce. But it is
evidence about the QUANTITY L5 WORKS IN, not proof of his order size.
"""
from __future__ import annotations

import collections
import json
import math
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.causal import cluster_size_forensics as CS
from analysis.l5.causal import order_id_forensics as OF

LABEL = "PARENT_ORDER_SURFACE_GEOMETRY_V1"
USDC = 10 ** 6


def _leg(f: dict) -> Optional[Tuple[float, float, str]]:
    """(shares, usdc, direction) from one OrderFilled row.

    makerAssetId == 0 means the ORDER OWNER paid USDC and received tokens -- a
    BUY. Otherwise the owner gave tokens, a SELL. Reading this backwards would
    invert every price."""
    mk, tk = f["maker_amount_filled"], f["taker_amount_filled"]
    if f["maker_asset_id"] == 0 and f["taker_asset_id"] != 0:
        return (tk / USDC, mk / USDC, "BUY")
    if f["taker_asset_id"] == 0 and f["maker_asset_id"] != 0:
        return (mk / USDC, tk / USDC, "SELL")
    return None


def build(pages: int = 6, max_tx: int = 1500) -> Dict[str, Any]:
    raw = CS.recent_fills(pages=pages)
    u = CS.units(raw)
    fills = u["fills"]
    r = CS.resolve(fills, max_tx=max_tx)
    per_tx = r["per_tx"]

    # public-fill context, keyed by transaction, so a parent can be placed in
    # time / probability / inventory even though the chain row carries none
    ctx: Dict[str, List[dict]] = collections.defaultdict(list)
    for f in fills:
        ctx[f["tx"]].append(f)

    # running inventory per market, in public-fill order (oldest first)
    ordered = sorted(fills, key=lambda x: x["t"])
    inv: Dict[str, Dict[str, float]] = collections.defaultdict(
        lambda: {"Up": 0.0, "Down": 0.0})
    inv_at: Dict[Tuple[str, int], Tuple[float, float]] = {}
    for f in ordered:
        k = (f["cid"], f["t"])
        inv_at.setdefault(k, (inv[f["cid"]]["Up"], inv[f["cid"]]["Down"]))
        inv[f["cid"]][f["outcome"]] += f["qty"]

    parents: Dict[str, Dict[str, Any]] = {}
    for tx, rows in per_tx.items():
        cf = ctx.get(tx) or []
        if not cf:
            continue
        base = cf[0]
        for row in rows:
            leg = _leg(row)
            if leg is None:
                continue
            shares, usd, direction = leg
            p = parents.setdefault(row["order_hash"], {
                "order_hash": row["order_hash"], "executions": [],
                "direction": direction, "slug": base["slug"],
                "cid": base["cid"], "outcome": base["outcome"],
            })
            up0, dn0 = inv_at.get((base["cid"], base["t"]), (0.0, 0.0))
            p["executions"].append({
                "t": base["t"], "tx": tx, "shares": shares, "usd": usd,
                "px": (usd / shares) if shares else None,
                "fee": row["fee"] / USDC,
                "tte_s": None, "inv_up": up0, "inv_dn": dn0,
                "public_px": base["px"], "public_outcome": base["outcome"],
            })

    out = []
    for p in parents.values():
        ex = sorted(p["executions"], key=lambda e: e["t"])
        qty = sum(e["shares"] for e in ex)
        usd = sum(e["usd"] for e in ex)
        ts = sorted({e["t"] for e in ex})
        out.append({
            "order_hash": p["order_hash"], "slug": p["slug"], "cid": p["cid"],
            "outcome": p["outcome"], "direction": p["direction"],
            "executed_qty": round(qty, 4),
            "executed_usd": round(usd, 6),
            "avg_px": round(usd / qty, 6) if qty else None,
            "public_px": ex[0]["public_px"],
            "p_up": (ex[0]["public_px"] if ex[0]["public_outcome"] == "Up"
                     else 1.0 - ex[0]["public_px"]),
            "n_executions": len(ex),
            "first_t": ts[0], "last_t": ts[-1],
            "execution_lifetime_lower_bound_s": ts[-1] - ts[0],
            "n_distinct_timestamps": len(ts),
            "fee": round(sum(e["fee"] for e in ex), 6),
            "inv_up_before": ex[0]["inv_up"], "inv_dn_before": ex[0]["inv_dn"],
            "residual_before": ex[0]["inv_up"] - ex[0]["inv_dn"],
            "inventory_total_before": ex[0]["inv_up"] + ex[0]["inv_dn"],
        })
    out.sort(key=lambda x: (x["slug"], x["first_t"]))
    return {"parents": out, "tx_attempted": r["tx_attempted"],
            "tx_missing": r["tx_missing"], "n_public_fills": len(fills)}


# ---------------------------------------------------------------------------

def _modes(xs, n=12):
    return collections.Counter(round(x, 2) for x in xs).most_common(n)


def _bin_p(p):
    return ("<=0.10" if p <= .10 else "0.10-0.30" if p <= .30 else
            "0.30-0.50" if p <= .50 else "0.50-0.70" if p <= .70 else
            "0.70-0.90" if p <= .90 else ">0.90")


def _bin_tte(t):
    if t is None:
        return None
    return ("<=60s" if t <= 60 else "60-180s" if t <= 180 else
            "180-420s" if t <= 420 else ">420s")


def _bin_inv(v):
    return ("0" if v <= 0 else "<=25" if v <= 25 else "25-100" if v <= 100
            else "100-400" if v <= 400 else ">400")


def geometry(parents: Sequence[dict]) -> Dict[str, Any]:
    q = [p["executed_qty"] for p in parents]
    by_p = collections.defaultdict(list)
    by_inv = collections.defaultdict(list)
    for p in parents:
        by_p[_bin_p(p["p_up"])].append(p["executed_qty"])
        by_inv[_bin_inv(p["inventory_total_before"])].append(p["executed_qty"])

    # is 100 a BASE LOT or a regime artifact?
    hundred = [p for p in parents if abs(p["executed_qty"] - 100.0) < 1e-6]
    share100_by_p = {k: round(sum(1 for x in v if abs(x - 100) < 1e-6) / len(v), 4)
                     for k, v in by_p.items() if len(v) >= 20}

    # are quantities multiples of a smaller base unit?
    base_hits = {}
    for base in (1, 5, 10, 20, 25, 50, 100):
        ok = sum(1 for x in q if abs((x / base) - round(x / base)) < 1e-6)
        base_hits[base] = round(ok / len(q), 4) if q else None

    # ladders: distinct parents, same market and outcome, within 5s, at
    # DIFFERENT prices
    ladders, opposite_near = [], []
    bym = collections.defaultdict(list)
    for p in parents:
        bym[p["cid"]].append(p)
    gaps = []
    for cid, ps in bym.items():
        ps.sort(key=lambda x: x["first_t"])
        for a, b in zip(ps, ps[1:]):
            gaps.append(b["first_t"] - a["first_t"])
        for i, a in enumerate(ps):
            for b in ps[i + 1:]:
                dt = b["first_t"] - a["first_t"]
                if dt > 5:
                    break
                if a["order_hash"] == b["order_hash"]:
                    continue
                if a["outcome"] == b["outcome"] and a["public_px"] != b["public_px"]:
                    ladders.append({"dt": dt, "outcome": a["outcome"],
                                    "px": [a["public_px"], b["public_px"]],
                                    "tick_gap": round(abs(a["public_px"] - b["public_px"]) / 0.01, 1),
                                    "qty": [a["executed_qty"], b["executed_qty"]]})
                if a["outcome"] != b["outcome"]:
                    opposite_near.append({"dt": dt,
                                          "pair_cost": round(a["public_px"] + b["public_px"], 4),
                                          "qty": [a["executed_qty"], b["executed_qty"]]})

    # repeated identical quantity at the same price in the same market
    rep = collections.Counter()
    for cid, ps in bym.items():
        for p in ps:
            rep[(cid, p["public_px"], round(p["executed_qty"], 2))] += 1
    repeated = sum(1 for v in rep.values() if v > 1)

    def stat(v):
        v = sorted(v)
        return {"n": len(v), "median": v[len(v) // 2] if v else None,
                "mean": round(sum(v) / len(v), 2) if v else None,
                "modes": _modes(v, 5)}
    return {
        "n_parents": len(parents),
        "executed_qty_modes": _modes(q),
        "share_exactly_100": round(len(hundred) / len(q), 4) if q else None,
        "is_100_a_base_lot_or_a_regime": {
            "share_100_by_probability": share100_by_p,
            "reads": ("a base lot appears at a similar rate in EVERY "
                      "probability regime; a regime artifact concentrates"),
        },
        "multiples_of_base_unit": base_hits,
        "executed_qty_by_probability": {k: stat(v) for k, v in by_p.items()
                                        if len(v) >= 20},
        "executed_qty_by_inventory_before": {k: stat(v) for k, v in by_inv.items()
                                             if len(v) >= 20},
        "parent_arrival_gap_s": {
            "n": len(gaps),
            "median": sorted(gaps)[len(gaps) // 2] if gaps else None,
            "p25": sorted(gaps)[len(gaps) // 4] if gaps else None,
            "p75": sorted(gaps)[3 * len(gaps) // 4] if gaps else None,
            "share_within_1s": (round(sum(1 for g in gaps if g <= 1) / len(gaps), 4)
                                if gaps else None),
            "share_within_5s": (round(sum(1 for g in gaps if g <= 5) / len(gaps), 4)
                                if gaps else None)},
        "price_ladders_within_5s": {
            "n": len(ladders),
            "tick_gap_modes": collections.Counter(
                l["tick_gap"] for l in ladders).most_common(6),
            "median_dt_s": (sorted(l["dt"] for l in ladders)[len(ladders) // 2]
                            if ladders else None)},
        "opposite_outcome_parents_within_5s": {
            "n": len(opposite_near),
            "median_implied_pair_cost": (
                sorted(x["pair_cost"] for x in opposite_near)[len(opposite_near) // 2]
                if opposite_near else None),
            "share_pair_below_1": (
                round(sum(1 for x in opposite_near if x["pair_cost"] < 1.0)
                      / len(opposite_near), 4) if opposite_near else None)},
        "repeated_same_qty_same_price": repeated,
        "execution_lifetime_lower_bound_s": {
            "note": "NOT order lifetime -- says nothing about submission or "
                    "cancellation",
            "share_multi_timestamp": round(
                sum(1 for p in parents if p["n_distinct_timestamps"] > 1)
                / len(parents), 4) if parents else None,
            "p50": sorted([p["execution_lifetime_lower_bound_s"] for p in parents
                           if p["execution_lifetime_lower_bound_s"] > 0])
                   [max(0, sum(1 for p in parents
                               if p["execution_lifetime_lower_bound_s"] > 0) // 2)]
                   if any(p["execution_lifetime_lower_bound_s"] > 0 for p in parents)
                   else None},
        "labels": ("executed_qty is a LOWER BOUND on order size: OrderFilled "
                   "reports what MATCHED, so an order for 200 that filled 100 "
                   "and was cancelled is indistinguishable from an order for "
                   "100."),
    }


def report(pages: int = 6, max_tx: int = 1500) -> Dict[str, Any]:
    b = build(pages=pages, max_tx=max_tx)
    g = geometry(b["parents"])
    g["label"] = LABEL
    g["n_public_fills"] = b["n_public_fills"]
    g["tx_attempted"] = b["tx_attempted"]
    g["tx_missing"] = b["tx_missing"]
    return g


# ---------------------------------------------------------------------------
# WHAT IS THE 100-LOT? Five competing readings, discriminated rather than
# asserted. OWNER 2026-09-17.

def base_lot_hypotheses(parents: Sequence[dict]) -> Dict[str, Any]:
    """fixed base lot | probability-scaled notional | inventory-scaled lot |
    ladder rung | mixture.

    Each makes a different prediction and they are tested against each other,
    not confirmed one at a time."""
    ok = [p for p in parents if p["executed_qty"] > 0 and p["avg_px"]]
    qty = [p["executed_qty"] for p in ok]
    notional = [p["executed_qty"] * p["avg_px"] for p in ok]

    def cv(xs):
        m = sum(xs) / len(xs)
        return round((statistics.pstdev(xs) / m), 4) if m else None

    is100 = [p for p in ok if abs(p["executed_qty"] - 100.0) < 1e-6]
    not100 = [p for p in ok if abs(p["executed_qty"] - 100.0) >= 1e-6]

    # A fixed base lot has a FLAT share across every conditioning variable.
    by_p = collections.defaultdict(lambda: [0, 0])
    by_inv = collections.defaultdict(lambda: [0, 0])
    for p in ok:
        for d, k in ((by_p, _bin_p(p["p_up"])),
                     (by_inv, _bin_inv(p["inventory_total_before"]))):
            d[k][1] += 1
            d[k][0] += int(abs(p["executed_qty"] - 100.0) < 1e-6)
    sh_p = {k: round(v[0] / v[1], 4) for k, v in by_p.items() if v[1] >= 20}
    sh_i = {k: round(v[0] / v[1], 4) for k, v in by_inv.items() if v[1] >= 20}

    # A LADDER RUNG shows up disproportionately among parents that have a
    # same-outcome neighbour within 5s at a different price.
    bym = collections.defaultdict(list)
    for p in ok:
        bym[p["cid"]].append(p)
    in_ladder = set()
    for ps in bym.values():
        ps.sort(key=lambda x: x["first_t"])
        for i, a in enumerate(ps):
            for b in ps[i + 1:]:
                if b["first_t"] - a["first_t"] > 5:
                    break
                if (a["outcome"] == b["outcome"]
                        and a["public_px"] != b["public_px"]):
                    in_ladder.add(a["order_hash"])
                    in_ladder.add(b["order_hash"])
    lad100 = sum(1 for p in is100 if p["order_hash"] in in_ladder)
    ladoth = sum(1 for p in not100 if p["order_hash"] in in_ladder)

    return {
        "n": len(ok),
        "dispersion": {
            "cv_executed_qty": cv(qty),
            "cv_notional_usd": cv(notional),
            "reads": ("a PROBABILITY-SCALED NOTIONAL rule makes notional the "
                      "tighter quantity; a FIXED LOT makes share count the "
                      "tighter one"),
            "tighter": ("notional" if (cv(notional) or 9) < (cv(qty) or 9)
                        else "share_count"),
        },
        "fixed_base_lot": {
            "share_100_by_probability": sh_p,
            "spread_across_probability": (round(max(sh_p.values()) - min(sh_p.values()), 4)
                                          if sh_p else None),
            "share_100_by_inventory": sh_i,
            "spread_across_inventory": (round(max(sh_i.values()) - min(sh_i.values()), 4)
                                        if sh_i else None),
            "reads": "a fixed lot is FLAT in both; a large spread falsifies it",
        },
        "inventory_scaled": {
            "median_qty_by_inventory": {
                k: round(statistics.median(
                    [p["executed_qty"] for p in ok if _bin_inv(p["inventory_total_before"]) == k]), 2)
                for k in sh_i},
            "reads": "an inventory-scaled lot rises or falls monotonically with inventory",
        },
        "ladder_rung": {
            "share_of_100s_in_a_ladder": (round(lad100 / len(is100), 4)
                                          if is100 else None),
            "share_of_others_in_a_ladder": (round(ladoth / len(not100), 4)
                                            if not100 else None),
            "reads": ("a ladder rung appears in ladders MORE than other sizes; "
                      "similar shares mean 100 is not specifically a rung"),
        },
        "notional_usd": {
            "median": round(statistics.median(notional), 2),
            "modes": _modes(notional, 8),
            "median_for_100_lots": (round(statistics.median(
                [p["executed_qty"] * p["avg_px"] for p in is100]), 2)
                if is100 else None),
        },
    }


def opposite_outcome_event_time(parents: Sequence[dict]) -> Dict[str, Any]:
    """How close in EVENT time are opposite-outcome parents? A persistent
    two-sided surface needs them near-simultaneous, not merely eventual."""
    bym = collections.defaultdict(list)
    for p in parents:
        bym[p["cid"]].append(p)
    gaps = []
    for ps in bym.values():
        ps.sort(key=lambda x: x["first_t"])
        for i, a in enumerate(ps):
            nxt = next((b for b in ps[i + 1:] if b["outcome"] != a["outcome"]), None)
            if nxt is not None:
                gaps.append({"dt": nxt["first_t"] - a["first_t"],
                             "pair": round(a["public_px"] + nxt["public_px"], 4)})
    if not gaps:
        return {"n": 0}
    d = sorted(g["dt"] for g in gaps)
    out = {"n": len(gaps),
           "median_dt_s": d[len(d) // 2], "p25": d[len(d) // 4],
           "p75": d[3 * len(d) // 4]}
    for h in (0, 1, 5, 15, 30, 60):
        sel = [g for g in gaps if g["dt"] <= h]
        out["within_%ds" % h] = {
            "share": round(len(sel) / len(gaps), 4),
            "median_implied_pair": (sorted(g["pair"] for g in sel)[len(sel) // 2]
                                    if sel else None),
            "share_pair_below_1": (round(sum(1 for g in sel if g["pair"] < 1.0)
                                         / len(sel), 4) if sel else None)}
    return out
