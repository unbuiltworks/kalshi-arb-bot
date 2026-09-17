"""CLUSTER_SIZE_FORENSICS_V1 -- how many "L5 decisions" are actually one order?

OWNER 2026-09-17. Three observational units, never silently equated:

    A  individual public fill            what the 693k-fill history counts
    B  same (market, timestamp, outcome, price) execution cluster
    C  chain-proven parent order  (OrderFilled.orderHash)

B IS A HEURISTIC AND C IS EVIDENCE. Two fills at the same instant, side and
price MIGHT be one order being swept, or two orders that happened to coincide.
Only the chain says which. This module reports the A->B->C reduction and
refuses to report B as if it were C.

WHY RECENT ACTIVITY. L5_ORDER_ID_FORENSICS_V1 resolved only half its historical
sample because public Polygon RPCs PRUNE OLD RECEIPTS -- every unresolved
cluster had all receipts missing, which was measured rather than assumed. The
pruning cuts the other way for recent blocks, so a far larger resolved sample is
available on L5's CURRENT activity. That is a different population from the
2026-03..08 BTC15m tape and is labelled as such throughout; it is not pooled
with it and it is not used to restate a frozen historical figure.

WHAT A PARENT'S LIFETIME MEANS HERE. If one orderHash receives partial fills at
several distinct timestamps, the span between its first and last observed
execution is a LOWER BOUND on how long that order was live. It is not the
order's age: the order existed before its first fill by an unknown amount, and
may have persisted after its last. Reported as a lower bound, never as
persistence.
"""
from __future__ import annotations

import collections
import json
import ssl
import statistics
import time
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.causal import order_id_forensics as OF

LABEL = "CLUSTER_SIZE_FORENSICS_V1"
L5 = OF.L5
CA = "/root/.ccr/ca-bundle.crt"
DATA = "https://data-api.polymarket.com"


def _ctx():
    return ssl.create_default_context(cafile=CA)


def recent_fills(pages: int = 6, limit: int = 500) -> List[dict]:
    """L5's public fills, newest first, paged. One row per FILL: a transaction
    can carry several and collapsing on the hash would delete real fills."""
    out: List[dict] = []
    for p in range(pages):
        u = "%s/activity?user=%s&limit=%d&offset=%d&type=TRADE" % (
            DATA, L5, limit, p * limit)
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "l5-research/1.0"})
            with urllib.request.urlopen(req, timeout=30, context=_ctx()) as fh:
                rows = json.loads(fh.read().decode())
        except Exception:                                    # noqa: BLE001
            break
        if not isinstance(rows, list) or not rows:
            break
        out.extend(rows)
        if len(rows) < limit:
            break
        time.sleep(0.2)
    return out


def units(fills: Sequence[dict]) -> Dict[str, Any]:
    """A and B. C needs the chain and is added by `resolve`."""
    a = []
    for r in fills:
        try:
            a.append({
                "tx": str(r.get("transactionHash") or ""),
                "t": int(float(r.get("timestamp") or 0)),
                "cid": str(r.get("conditionId") or ""),
                "slug": str(r.get("slug") or ""),
                "outcome": "Up" if str(r.get("outcome")) == "Up" else "Down",
                "px": float(r.get("price") or 0),
                "qty": float(r.get("size") or 0),
                "usd": float(r.get("usdcSize") or 0),
            })
        except (TypeError, ValueError):
            continue
    b: Dict[tuple, List[dict]] = collections.defaultdict(list)
    for f in a:
        b[(f["cid"], f["t"], f["outcome"], f["px"])].append(f)
    return {"fills": a, "clusters": dict(b)}


def resolve(fills: Sequence[dict], cache: Optional[dict] = None,
            max_tx: int = 400) -> Dict[str, Any]:
    """C: pull each transaction's receipt once and read L5's OrderFilled rows.

    Keyed by transaction, not by fill, because one receipt answers for every
    fill in it."""
    cache = cache if cache is not None else {}
    txs = []
    for f in fills:
        if f["tx"] and f["tx"] not in txs:
            txs.append(f["tx"])
    txs = txs[:max_tx]
    per_tx: Dict[str, List[dict]] = {}
    missing = 0
    for tx in txs:
        rc = cache.get(tx)
        if rc is None:
            rc = OF.rpc("eth_getTransactionReceipt", [tx])
            cache[tx] = rc if rc is not None else False
        if not rc:
            missing += 1
            continue
        per_tx[tx] = OF.l5_orders_in(rc)
    return {"per_tx": per_tx, "tx_attempted": len(txs), "tx_missing": missing}


def analyse(fills: Sequence[dict], clusters: Dict[tuple, List[dict]],
            per_tx: Dict[str, List[dict]]) -> Dict[str, Any]:
    # ---- parent orders, with every execution attached
    parents: Dict[str, Dict[str, Any]] = {}
    fills_with_parent = 0
    for f in fills:
        rows = per_tx.get(f["tx"])
        if not rows:
            continue
        fills_with_parent += 1
        for r in rows:
            p = parents.setdefault(r["order_hash"], {
                "order_hash": r["order_hash"], "executions": [],
                "token_ids": set(), "fees": 0, "txs": set()})
            p["executions"].append({"t": f["t"], "tx": f["tx"],
                                    "maker_amount": r["maker_amount_filled"],
                                    "taker_amount": r["taker_amount_filled"],
                                    "fee": r["fee"], "px": f["px"],
                                    "slug": f["slug"], "outcome": f["outcome"]})
            p["token_ids"].add(r["taker_asset_id"] or r["maker_asset_id"])
            p["fees"] += r["fee"]
            p["txs"].add(f["tx"])

    lifetimes, exec_counts, distinct_ts = [], [], []
    for p in parents.values():
        ts = sorted({e["t"] for e in p["executions"]})
        p["first_t"], p["last_t"] = ts[0], ts[-1]
        p["lifetime_lower_bound_s"] = ts[-1] - ts[0]
        p["n_distinct_timestamps"] = len(ts)
        p["n_executions"] = len(p["executions"])
        p["n_transactions"] = len(p["txs"])
        lifetimes.append(p["lifetime_lower_bound_s"])
        exec_counts.append(p["n_executions"])
        distinct_ts.append(len(ts))

    cl_sizes = [len(v) for v in clusters.values()]
    multi = [v for v in clusters.values() if len(v) > 1]

    # ---- how many clusters are ONE parent, on chain evidence
    same = mult = unres = 0
    fills_same = fills_res = 0
    for v in multi:
        hs = set()
        ok = False
        for f in v:
            for r in per_tx.get(f["tx"], []) or []:
                hs.add(r["order_hash"])
                ok = True
        if not ok:
            unres += 1
        elif len(hs) == 1:
            same += 1
            fills_same += len(v)
            fills_res += len(v)
        else:
            mult += 1
            fills_res += len(v)

    def q(xs, p):
        v = sorted(xs)
        return v[min(len(v) - 1, int(p * len(v)))] if v else None

    sizes = [f["qty"] for f in fills]
    par_qty = [sum(e["taker_amount"] for e in p["executions"]) / 1e6
               for p in parents.values()]
    return {
        "unit_A_public_fills": len(fills),
        "unit_B_clusters": len(clusters),
        "unit_C_proven_parent_orders": len(parents),
        "A_to_B_reduction": (round(1 - len(clusters) / len(fills), 4)
                             if fills else None),
        "B_to_C_reduction_on_resolved": (
            round(1 - len(parents) / max(1, fills_with_parent), 4)),
        "A_to_C_reduction_on_resolved": (
            round(1 - len(parents) / max(1, fills_with_parent), 4)),
        "fills_with_a_proven_parent": fills_with_parent,
        "cluster_size": {"mean": round(sum(cl_sizes) / len(cl_sizes), 3) if cl_sizes else None,
                         "max": max(cl_sizes) if cl_sizes else None,
                         "share_multi": round(len(multi) / len(clusters), 4) if clusters else None},
        "multi_fill_clusters_on_chain": {
            "n": len(multi), "SAME_PARENT": same, "MULTIPLE_PARENTS": mult,
            "UNRESOLVABLE": unres,
            "share_same_of_resolved": (round(same / (same + mult), 4)
                                       if (same + mult) else None),
            "share_FILLS_collapsing": (round(fills_same / fills_res, 4)
                                       if fills_res else None)},
        "parent_executions": {
            "mean": round(sum(exec_counts) / len(exec_counts), 3) if exec_counts else None,
            "p50": q(exec_counts, 0.5), "p90": q(exec_counts, 0.9),
            "max": max(exec_counts) if exec_counts else None,
            "share_with_multiple_executions": (
                round(sum(1 for c in exec_counts if c > 1) / len(exec_counts), 4)
                if exec_counts else None)},
        "parent_lifetime_lower_bound_s": {
            "n_with_multiple_timestamps": sum(1 for d in distinct_ts if d > 1),
            "share_spanning_timestamps": (round(sum(1 for d in distinct_ts if d > 1)
                                                / len(distinct_ts), 4)
                                          if distinct_ts else None),
            "p50": q([l for l in lifetimes if l > 0], 0.5),
            "p90": q([l for l in lifetimes if l > 0], 0.9),
            "max": max(lifetimes) if lifetimes else None,
            "basis": "LOWER BOUND ONLY: the span between first and last "
                     "observed execution. The order existed before its first "
                     "fill by an unknown amount and may have persisted after "
                     "its last."},
        "fill_size_modes": collections.Counter(
            round(s, 2) for s in sizes).most_common(10),
        "parent_quantity_modes": collections.Counter(
            round(x, 1) for x in par_qty).most_common(10),
        "labels": ("B is a HEURISTIC (same instant, side and price). C is "
                   "EVIDENCE (one chain orderHash). They are never equated. "
                   "This is L5's RECENT activity, a different population from "
                   "the frozen 2026-03..08 BTC15m tape, and is not pooled "
                   "with it."),
    }


def run(pages: int = 6, max_tx: int = 400) -> Dict[str, Any]:
    raw = recent_fills(pages=pages)
    u = units(raw)
    r = resolve(u["fills"], max_tx=max_tx)
    out = analyse(u["fills"], u["clusters"], r["per_tx"])
    out["label"] = LABEL
    out["population"] = "L5 RECENT public activity (data-api), not the frozen tape"
    out["tx_attempted"] = r["tx_attempted"]
    out["tx_missing_receipts"] = r["tx_missing"]
    out["receipt_availability"] = (
        round(1 - r["tx_missing"] / max(1, r["tx_attempted"]), 4))
    return out


# ---------------------------------------------------------------------------
# The historical re-run: what does the behavioral model look like on the
# COLLAPSED unit?
#
# The frozen BTC15m tape cannot be collapsed to unit C -- its receipts are
# pruned. It CAN be collapsed to unit B, and the chain gives us the accuracy of
# that proxy: on RESOLVED multi-fill clusters 96% were a single parent order.
# So B is a good but IMPERFECT stand-in for C, and every number below carries
# that caveat rather than being presented as parent-order truth.

def historical_units(cohort: str = "BTC15m"):
    """Per window: the FILL sequence and the CLUSTER-collapsed sequence."""
    from analysis.l5.residual_exit import forensics as F
    windows, _ = F.load_windows(cohort)
    out = {}
    for cid, w in windows.items():
        fills = [{"t": t, "px": px, "oc": oc, "qty": sz, "usd": usd}
                 for t, px, oc, sz, usd in w["fills"]]
        if len(fills) < 5:
            continue
        g = collections.OrderedDict()
        for f in fills:
            k = (f["t"], f["oc"], f["px"])
            e = g.setdefault(k, {"t": f["t"], "px": f["px"], "oc": f["oc"],
                                 "qty": 0.0, "usd": 0.0, "n": 0})
            e["qty"] += f["qty"]
            e["usd"] += f["usd"]
            e["n"] += 1
        out[cid] = {"A": fills, "B": list(g.values()),
                    "end": w["meta"][2]}
    return out


def _seq_stats(seqs: Dict[str, List[dict]], key: str) -> Dict[str, Any]:
    """Run lengths, continuation hazard, switches and the state-model gain,
    computed identically on whichever unit is passed in."""
    runs, haz = [], collections.defaultdict(lambda: [0, 0])
    switches, n_actions, n_windows = 0, 0, 0
    rows = []
    for cid, d in seqs.items():
        s = d[key]
        if len(s) < 3:
            continue
        n_windows += 1
        n_actions += len(s)
        run_side, run_len = None, 0
        up = dn = 0.0
        for i, e in enumerate(s):
            run_len = (run_len + 1) if run_side == e["oc"] else 1
            if run_side is not None and run_side != e["oc"]:
                switches += 1
            run_side = e["oc"]
            if i + 1 < len(s):
                k = min(run_len, 10)
                haz[k][1] += 1
                haz[k][0] += (s[i + 1]["oc"] == e["oc"])
                rows.append({"side": e["oc"], "next": s[i + 1]["oc"],
                             "run": min(run_len, 4),
                             "res": up - dn, "tte": d["end"] - e["t"]})
            if e["oc"] == "Up":
                up += e["qty"]
            else:
                dn += e["qty"]
            if i + 1 == len(s) or s[i + 1]["oc"] != e["oc"]:
                runs.append(run_len)
    # OOS log-loss, previous-side baseline vs a small state table
    rows.sort(key=lambda r: 0)
    cut = int(len(rows) * 0.7)
    tr, oos = rows[:cut], rows[cut:]

    def fit(keyfn):
        c = collections.defaultdict(lambda: [0, 0])
        for r in tr:
            k = keyfn(r)
            c[k][1] += 1
            c[k][0] += (r["next"] == "Up")
        return c

    prior = (sum(1 for r in tr if r["next"] == "Up") / len(tr)) if tr else 0.5

    def score(c, keyfn, a=20.0):
        tot = n = 0.0
        for r in oos:
            h, m = c.get(keyfn(r), [0, 0])
            p = min(max((h + a * prior) / (m + a), 1e-6), 1 - 1e-6)
            tot += -(math.log(p) if r["next"] == "Up" else math.log(1 - p))
            n += 1
        return tot / n if n else None
    import math as _m
    globals().setdefault("math", _m)
    bk = lambda r: r["side"]
    sk = lambda r: (r["side"], r["run"],
                    "neg" if r["res"] < -2 else "flat" if r["res"] <= 2 else "pos",
                    "late" if r["tte"] < 180 else "mid" if r["tte"] < 450 else "early")
    ll_b = score(fit(bk), bk)
    ll_s = score(fit(sk), sk)
    hz = {k: round(v[0] / v[1], 4) for k, v in sorted(haz.items()) if v[1] >= 500}
    return {
        "n_windows": n_windows, "n_actions": n_actions,
        "actions_per_window": round(n_actions / n_windows, 2) if n_windows else None,
        "run_length": {"mean": round(sum(runs) / len(runs), 3) if runs else None,
                       "max": max(runs) if runs else None,
                       "share_len1": round(sum(1 for r in runs if r == 1) / len(runs), 4) if runs else None},
        "switches_per_window": round(switches / n_windows, 2) if n_windows else None,
        "continuation_hazard": hz,
        "hazard_rise_run1_to_run10": (round(hz[max(hz)] - hz[min(hz)], 4)
                                      if len(hz) > 1 else None),
        "oos_logloss_prev_side": round(ll_b, 6) if ll_b else None,
        "oos_logloss_state": round(ll_s, 6) if ll_s else None,
        "state_gain_nats": (round(ll_b - ll_s, 6)
                            if (ll_b and ll_s) else None),
    }


def historical_comparison(cohort: str = "BTC15m") -> Dict[str, Any]:
    import math
    globals()["math"] = math
    seqs = historical_units(cohort)
    a = _seq_stats(seqs, "A")
    b = _seq_stats(seqs, "B")
    return {
        "label": "CLUSTER_SIZE_FORENSICS_V1/historical",
        "cohort": cohort,
        "unit_A_public_fills": a,
        "unit_B_collapsed_clusters": b,
        "action_count_reduction": (round(1 - b["n_actions"] / a["n_actions"], 4)
                                   if a["n_actions"] else None),
        "caveat": (
            "The frozen tape CANNOT be collapsed to unit C: its receipts are "
            "pruned. B is a PROXY for C, and the chain measured its accuracy "
            "on recent activity -- 96% of RESOLVED multi-fill clusters were a "
            "single parent order. So these are 'as if same-instant clusters "
            "were parent orders', which is right about 96% of the time on a "
            "different population, and is not parent-order truth."),
    }
