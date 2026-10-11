"""MATCHED_SHARE_LINEAGE_V1 -- which passive inventory becomes matched economics,
and which becomes negative residual?

OWNER 2026-09-17. Traces every ultimately matched BTC15m share from acquisition
through the complementary share that eventually pairs with it, and measures
which OBSERVABLE ACQUISITION STATES produce positive matched economics.

WHY FIFO, AND WHAT IT DOES NOT DO. The authoritative accounting
(`analysis/l5/btc15m/decompose.py`) splits a window at AVERAGE cost:
`matched = min(up, dn)` priced at `avg_up + avg_dn`. That is the cash account
and it does not change here. But average cost cannot say WHICH share was
matched, so it cannot answer the question asked. This module pairs shares
FIRST-IN-FIRST-OUT: the earliest unpaired share on one side pairs with the
earliest later share on the other.

THE TWO CONVENTIONS REALLOCATE BETWEEN MATCHED AND RESIDUAL AND PRESERVE THE
TOTAL. `payout - spend` is fixed by the venue, so any shift FIFO makes to the
matched leg comes straight out of the residual leg and vice versa. The
reallocation is reported explicitly in `convention_reconciliation` rather than
being allowed to look like a new profit. CASH ACCOUNTING IS UNCHANGED; only
the attribution differs, and the authoritative totals remain the ones the
decomposition reports.

A LINEAGE IS NOT A CAUSAL CLAIM. That a share paired with a later one does not
mean it was acquired in order to. There is no book and no unfilled order here,
so "why that share was bought" is UNOBSERVABLE; this measures what happened to
it.
"""
from __future__ import annotations

import collections
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.residual_exit import forensics as F

MIN_BIN = 300


def _med(xs):
    v = sorted(x for x in xs if x is not None)
    return v[len(v) // 2] if v else None


def _agg(rows: Sequence[dict], key: str) -> dict:
    q = sum(r["qty"] for r in rows)
    if not rows or q <= 0:
        return {"n": len(rows), "qty": q}
    pnl = sum(r[key] for r in rows)
    usd = sum(r["cost_usd"] for r in rows)
    return {
        "n": len(rows), "qty": round(q, 2), "cost_usd": round(usd, 2),
        "pnl": round(pnl, 2), "pnl_per_share": round(pnl / q, 6),
        "bp_per_dollar": round(1e4 * pnl / usd, 1) if usd else None,
    }


def trace(cohort: str = "BTC15m") -> Dict[str, Any]:
    """One row per PAIR and one per RESIDUAL share-lot, each carrying the
    observable state at the moment its first leg was acquired."""
    windows, capture_end = F.load_windows(cohort)
    pairs: List[dict] = []
    resid: List[dict] = []
    recon = {"fifo_matched_pnl": 0.0, "fifo_residual_pnl": 0.0,
             "avgcost_matched_pnl": 0.0, "avgcost_residual_pnl": 0.0,
             "total": 0.0, "windows": 0}

    for cid, w in windows.items():
        fills = w["fills"]
        if len(fills) < 2:
            continue
        _a, horizon, end = w["meta"]
        start = end - F.A.DURATION_S[horizon]
        won_up, src = F.settlement(w, capture_end)
        if won_up is None:
            continue
        up_q = sum(x[3] for x in fills if x[2] == "Up")
        dn_q = sum(x[3] for x in fills if x[2] != "Up")
        if up_q <= 0 or dn_q <= 0:
            continue
        up_u = sum(x[4] for x in fills if x[2] == "Up")
        dn_u = sum(x[4] for x in fills if x[2] != "Up")
        avg_up, avg_dn = up_u / up_q, dn_u / dn_q
        matched = min(up_q, dn_q)
        residual = up_q - dn_q

        # ---- observable state at each acquisition
        up = dn = 0.0
        run_side, run_len = None, 0
        hist: List[Tuple[int, float]] = []
        lots: List[dict] = []
        for i, (t, px, oc, sz, usd) in enumerate(fills):
            p_up = px if oc == "Up" else 1.0 - px
            dp = None
            for tt, pp in reversed(hist):
                if t - tt >= 60:
                    dp = p_up - pp
                    break
            recent = [pp for tt, pp in hist if t - tt <= 60]
            rng = (max(recent) - min(recent)) if len(recent) >= 2 else None
            rev = 0
            if len(recent) >= 3:
                d = [b - a for a, b in zip(recent, recent[1:])]
                rev = sum(1 for a, b in zip(d, d[1:]) if a * b < 0)
            run_len = (run_len + 1) if run_side == oc else 1
            run_side = oc
            lots.append({
                "cid": cid, "t": t, "i": i, "side": oc, "px": px, "qty": sz,
                "cost_usd": usd, "unit_cost": (usd / sz) if sz else px,
                "abs_dp_60s": None if dp is None else abs(dp),
                "range_60s": rng, "reversals_60s": rev,
                "p_up": p_up, "tte_s": end - t,
                "progress": (t - start) / (end - start) if end > start else None,
                "run_position": run_len,
                "residual_before": up - dn,
                "inventory_direction": ("flat" if abs(up - dn) < 1e-9 else
                                        "long_up" if up > dn else "long_down"),
                "acquiring_the_long_side": (
                    (oc == "Up" and up > dn) or (oc == "Down" and dn > up)),
                "fee_bearing": F._bearing(px, sz, usd),
            })
            if oc == "Up":
                up += sz
            else:
                dn += sz
            hist.append((t, p_up))

        # ---- FIFO pairing
        q_up = collections.deque(l for l in lots if l["side"] == "Up")
        q_dn = collections.deque(l for l in lots if l["side"] != "Up")
        rem_up = collections.deque((l, l["qty"]) for l in q_up)
        rem_dn = collections.deque((l, l["qty"]) for l in q_dn)
        fifo_m = 0.0
        while rem_up and rem_dn:
            lu, qu = rem_up[0]
            ld, qd = rem_dn[0]
            take = min(qu, qd)
            first, second = (lu, ld) if lu["t"] <= ld["t"] else (ld, lu)
            pair_cost = lu["unit_cost"] + ld["unit_cost"]
            pnl = take * (1.0 - pair_cost)
            fifo_m += pnl
            pairs.append({
                **{k: first[k] for k in
                   ("cid", "t", "side", "px", "abs_dp_60s", "range_60s",
                    "reversals_60s", "p_up", "tte_s", "progress",
                    "run_position", "residual_before", "inventory_direction",
                    "acquiring_the_long_side", "fee_bearing")},
                "qty": take,
                "cost_usd": take * pair_cost,
                "pair_cost": pair_cost,
                "time_to_complement_s": abs(second["t"] - first["t"]),
                "complement_px": second["px"],
                "complement_fee_bearing": second["fee_bearing"],
                "matched_pnl": pnl,
                "window_won_up": won_up,
            })
            qu -= take
            qd -= take
            if qu <= 1e-12:
                rem_up.popleft()
            else:
                rem_up[0] = (lu, qu)
            if qd <= 1e-12:
                rem_dn.popleft()
            else:
                rem_dn[0] = (ld, qd)

        # ---- whatever never paired IS the residual, and its lineage is known
        fifo_r = 0.0
        for q in (rem_up, rem_dn):
            for lot, left in q:
                payout = 1.0 if ((lot["side"] == "Up") == won_up) else 0.0
                pnl = left * (payout - lot["unit_cost"])
                fifo_r += pnl
                resid.append({
                    **{k: lot[k] for k in
                       ("cid", "t", "side", "px", "abs_dp_60s", "range_60s",
                        "reversals_60s", "p_up", "tte_s", "progress",
                        "run_position", "residual_before",
                        "inventory_direction", "acquiring_the_long_side",
                        "fee_bearing")},
                    "qty": left, "cost_usd": left * lot["unit_cost"],
                    "residual_pnl": pnl, "settled_winner": payout == 1.0,
                    "window_won_up": won_up,
                })

        am = matched * (1.0 - (avg_up + avg_dn))
        ar = ((abs(residual) if (residual > 0) == won_up else 0.0)
              - abs(residual) * (avg_up if residual > 0 else avg_dn)) \
            if abs(residual) > 1e-9 else 0.0
        recon["fifo_matched_pnl"] += fifo_m
        recon["fifo_residual_pnl"] += fifo_r
        recon["avgcost_matched_pnl"] += am
        recon["avgcost_residual_pnl"] += ar
        recon["total"] += am + ar
        recon["windows"] += 1

    pairs.sort(key=lambda r: r["t"])
    resid.sort(key=lambda r: r["t"])
    return {"pairs": pairs, "residual": resid, "recon": recon}


# ---------------------------------------------------------------------------

def _bin(name: str, r: dict) -> Optional[str]:
    v = r.get(name)
    if v is None:
        return None
    if name == "abs_dp_60s":
        return ("<=0.01" if v <= 0.01 else "0.01-0.02" if v <= 0.02 else
                "0.02-0.05" if v <= 0.05 else "0.05-0.10" if v <= 0.10 else ">0.10")
    if name == "range_60s":
        return ("<=0.02" if v <= 0.02 else "0.02-0.05" if v <= 0.05 else
                "0.05-0.15" if v <= 0.15 else ">0.15")
    if name == "reversals_60s":
        return "0" if v == 0 else "1-2" if v <= 2 else "3-5" if v <= 5 else "6+"
    if name == "p_up":
        return ("<=0.10" if v <= 0.10 else "0.10-0.30" if v <= 0.30 else
                "0.30-0.50" if v <= 0.50 else "0.50-0.70" if v <= 0.70 else
                "0.70-0.90" if v <= 0.90 else ">0.90")
    if name == "tte_s":
        return ("<=60s" if v <= 60 else "60-180s" if v <= 180 else
                "180-420s" if v <= 420 else "420-700s" if v <= 700 else ">700s")
    if name == "run_position":
        return ("1" if v == 1 else "2-3" if v <= 3 else "4-5" if v <= 5 else
                "6-8" if v <= 8 else "9-12" if v <= 12 else "13+")
    if name == "residual_before":
        a = abs(v)
        return "<=2" if a <= 2 else "2-10" if a <= 10 else "10-30" if a <= 30 else ">30"
    if name == "time_to_complement_s":
        return ("<=5s" if v <= 5 else "5-30s" if v <= 30 else
                "30-120s" if v <= 120 else "120-300s" if v <= 300 else ">300s")
    if name == "pair_cost":
        return ("<=0.90" if v <= 0.90 else "0.90-0.96" if v <= 0.96 else
                "0.96-1.00" if v <= 1.00 else "1.00-1.05" if v <= 1.05 else ">1.05")
    return str(v)


FEATURES = ("abs_dp_60s", "range_60s", "reversals_60s", "p_up", "tte_s",
            "run_position", "residual_before", "inventory_direction",
            "acquiring_the_long_side", "fee_bearing")
PAIR_ONLY = ("time_to_complement_s", "pair_cost")


def by_state(rows: Sequence[dict], key: str, features: Sequence[str]) -> dict:
    tr, oos = rows[:int(len(rows) * 0.7)], rows[int(len(rows) * 0.7):]
    out: Dict[str, Any] = {}
    for f in features:
        d: Dict[str, Any] = {}
        for label, S in (("train", tr), ("oos", oos)):
            g: Dict[str, List[dict]] = collections.defaultdict(list)
            for r in S:
                b = _bin(f, r)
                if b is not None:
                    g[b].append(r)
            d[label] = {k: _agg(v, key) for k, v in g.items()
                        if len(v) >= MIN_BIN}
        if d["train"] and d["oos"]:
            out[f] = d
    return out


def report(cohort: str = "BTC15m") -> dict:
    t = trace(cohort)
    p, r, rc = t["pairs"], t["residual"], t["recon"]
    return {
        "cohort": cohort,
        "n_pairs": len(p), "n_residual_lots": len(r),
        "convention_reconciliation": {
            **{k: round(v, 2) if isinstance(v, float) else v
               for k, v in rc.items()},
            "reallocated_matched_minus_avgcost": round(
                rc["fifo_matched_pnl"] - rc["avgcost_matched_pnl"], 2),
            "totals_agree": abs((rc["fifo_matched_pnl"] + rc["fifo_residual_pnl"])
                                - rc["total"]) < 1.0,
            "note": ("FIFO and average cost REALLOCATE between the matched and "
                     "residual legs and preserve payout - spend. The "
                     "authoritative cash account is unchanged and remains the "
                     "average-cost decomposition; FIFO exists only to say "
                     "WHICH shares paired."),
        },
        "matched_economics_by_acquisition_state":
            by_state(p, "matched_pnl", FEATURES),
        "matched_economics_by_outcome_state":
            by_state(p, "matched_pnl", PAIR_ONLY),
        "residual_economics_by_acquisition_state":
            by_state(r, "residual_pnl", FEATURES),
        "labels": ("A lineage is not a causal claim: that a share paired with "
                   "a later one does not mean it was bought in order to. Why a "
                   "share was bought is UNOBSERVABLE here."),
    }
