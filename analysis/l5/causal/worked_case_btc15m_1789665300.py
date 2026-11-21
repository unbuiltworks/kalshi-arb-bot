"""WORKED CASE -- btc-updown-15m-1789665300, end to end.

A single BTC15m window taken from public fills through chain-proven parent
orders to the book that stood before each execution. It is an instance of
PARENT_ORDER_SURFACE_GEOMETRY_V1 and L5_PASSIVE_SURFACE_SPEC_V1, not a new
line of research.

IDENTITY VERIFIED INDEPENDENTLY, not accepted from the lead that suggested it:
the conditionId was read from L5's OWN activity rows
(0xa1a869306d77fc4643ee7816eb05f97f53223c8cdd6627f850155b63fefff300) and the
58 TRADE rows, 37 Down / 21 Up, all BUY, were counted here.

TWO LABELS THAT ARE NEVER MERGED:

    L5_OWNED       the chain names L5 as the order's owner. Proof.
    L5_COMPATIBLE  anonymous book liquidity sitting at a price L5 later
                   executed at. It MAY be his order or anyone's. Suggestive,
                   never ownership.

Quantities stay `executed_qty`, a LOWER BOUND on order size, and first->last
stays an execution-lifetime lower bound.
"""
from __future__ import annotations

import collections
import glob
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.causal import order_id_forensics as OF

SLUG = "btc-updown-15m-1789665300"
CONDITION_ID = "0xa1a869306d77fc4643ee7816eb05f97f53223c8cdd6627f850155b63fefff300"
WINDOW_START, WINDOW_END = 1789665300, 1789666200
USDC = 10 ** 6


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def leg(row: dict) -> Optional[Tuple[float, float, str]]:
    """(shares, usdc, direction). makerAssetId == 0 => the owner paid
    collateral and BOUGHT."""
    mk, tk = row["maker_amount_filled"], row["taker_amount_filled"]
    if row["maker_asset_id"] == 0 and row["taker_asset_id"] != 0:
        return tk / USDC, mk / USDC, "BUY"
    if row["taker_asset_id"] == 0 and row["maker_asset_id"] != 0:
        return mk / USDC, tk / USDC, "SELL"
    return None


def build(rows: Sequence[dict]) -> Dict[str, Any]:
    fills = sorted(({"t": int(float(r["timestamp"])),
                     "outcome": "Up" if r["outcome"] == "Up" else "Down",
                     "px": float(r["price"]), "qty": float(r["size"]),
                     "usd": float(r.get("usdcSize") or 0),
                     "tx": str(r["transactionHash"])} for r in rows),
                   key=lambda x: (x["t"], x["tx"]))

    # ---- same-second clusters (the heuristic unit), BEFORE the chain
    clusters: Dict[tuple, List[dict]] = collections.defaultdict(list)
    for f in fills:
        clusters[(f["t"], f["outcome"], f["px"])].append(f)

    # ---- the chain: one receipt per transaction, cached
    cache: Dict[str, Any] = {}
    parents: Dict[str, Dict[str, Any]] = {}
    unresolved = []
    for f in fills:
        rc = cache.get(f["tx"])
        if rc is None:
            rc = OF.rpc("eth_getTransactionReceipt", [f["tx"]])
            cache[f["tx"]] = rc if rc is not None else False
        if not rc:
            unresolved.append(f)
            continue
        mine = OF.l5_orders_in(rc)
        if not mine:
            unresolved.append(f)
            continue
        for row in mine:
            lg = leg(row)
            if lg is None:
                continue
            sh, usd, direction = lg
            p = parents.setdefault(row["order_hash"], {
                "order_hash": row["order_hash"], "outcome": f["outcome"],
                "direction": direction, "executions": []})
            p["executions"].append({
                "t": f["t"], "tx": f["tx"], "shares": round(sh, 6),
                "usd": round(usd, 6),
                "px": round(usd / sh, 6) if sh else None,
                "fee": row["fee"] / USDC,
                "public_px": f["px"], "public_qty": f["qty"]})

    # ---- chronology and inventory from PARENT EXECUTIONS, not UI rows
    ex = []
    for p in parents.values():
        for e in p["executions"]:
            ex.append({**e, "order_hash": p["order_hash"],
                       "outcome": p["outcome"], "direction": p["direction"]})
    ex.sort(key=lambda x: (x["t"], x["order_hash"]))
    inv = {"Up": 0.0, "Down": 0.0}
    for e in ex:
        e["inv_before"] = dict(inv)
        inv[e["outcome"]] += e["shares"]
        e["inv_after"] = dict(inv)
        e["residual_after"] = round(inv["Up"] - inv["Down"], 6)

    out = []
    for p in parents.values():
        es = sorted(p["executions"], key=lambda x: x["t"])
        q = sum(x["shares"] for x in es)
        u = sum(x["usd"] for x in es)
        mine_ex = [x for x in ex if x["order_hash"] == p["order_hash"]]
        out.append({
            "order_hash": p["order_hash"], "outcome": p["outcome"],
            "direction": p["direction"],
            "first_execution": es[0]["t"], "last_execution": es[-1]["t"],
            "execution_lifetime_lower_bound_s": es[-1]["t"] - es[0]["t"],
            "executed_qty_LOWER_BOUND": round(q, 4),
            "avg_px": round(u / q, 6) if q else None,
            "public_px": es[0]["public_px"],
            "fragments": len(es),
            "fragment_qtys": [round(x["shares"], 4) for x in es],
            "fee_usd": round(sum(x["fee"] for x in es), 6),
            "inventory_before": mine_ex[0]["inv_before"] if mine_ex else None,
            "inventory_after": mine_ex[-1]["inv_after"] if mine_ex else None,
            "ownership": "L5_OWNED",
        })
    out.sort(key=lambda x: (x["first_execution"], x["order_hash"]))
    return {"fills": fills, "clusters": clusters, "parents": out,
            "executions": ex, "unresolved": unresolved,
            "final_inventory": inv}
