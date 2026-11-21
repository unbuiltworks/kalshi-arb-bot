"""P0/P1: where does the .99 -> .96 movement actually come from?

THE DECOMPOSITION IS EXACT, NOT NARRATED. Pair cost is a sum of two running
averages:

    pair_cost = (UP spend / UP qty) + (DOWN spend / DOWN qty)

and every acquisition event touches exactly ONE of those terms. So

    pair_final - pair_at_formation  ==  SUM over events of (that side's VWAP move)

identically. Every bucket below is a partition of those per-event deltas, and
the parts are checked to re-sum to the whole. Nothing is apportioned by
assumption.

THE COUNTER-INTUITIVE PART, and the reason a pair-forming clone cannot work:
an event that buys the side he is ALREADY LONG does not raise matched quantity
at all -- matched is min(UP, DOWN) -- yet it still moves that side's VWAP, and
therefore still moves the pair cost. A RESIDUAL_EXTENSION is an economically
active event even though it creates no new matched inventory.

EVENT LEVEL, NOT LEG LEVEL. A transaction is one decision; its legs are how the
venue filled it, and every multi-leg BTC15m transaction is single-sided.

FOUR CLASSES, and none is assumed good or intentional:

    MATCH_CREATION              matched goes 0 -> positive
    MATCH_EXPANSION_IMPROVING   matched rises and pair cost falls
    MATCH_EXPANSION_WORSENING   matched rises and pair cost rises
    RESIDUAL_EXTENSION          matched unchanged; he bought the long side
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from typing import Any, Dict, List, Optional, Sequence

from analysis.l5.inventory_trajectory.trajectory import load, window_end
from analysis.l5.next_side.features import aggregate_transactions

MATCH_CREATION = "MATCH_CREATION"
MATCH_IMPROVING = "MATCH_EXPANSION_IMPROVING"
MATCH_WORSENING = "MATCH_EXPANSION_WORSENING"
RESIDUAL_EXTENSION = "RESIDUAL_EXTENSION"
CLASSES = (MATCH_CREATION, MATCH_IMPROVING, MATCH_WORSENING, RESIDUAL_EXTENSION)


def events(slug: str, fills: Sequence[dict]) -> List[Dict[str, Any]]:
    """One row per transaction event: state before, state after, and the deltas."""
    end = window_end(slug)
    ev = aggregate_transactions(fills)
    up_q = dn_q = up_c = dn_c = 0.0
    run = 0
    prev_side: Optional[str] = None
    out: List[Dict[str, Any]] = []
    for i, f in enumerate(ev):
        side = "Up" if str(f.get("outcome")) == "Up" else "Down"
        sz = float(f.get("size") or 0.0)
        usd = float(f.get("usdcSize") or 0.0)
        t = f.get("timestamp")

        b_up_v = (up_c / up_q) if up_q > 0 else None
        b_dn_v = (dn_c / dn_q) if dn_q > 0 else None
        b_pair = (b_up_v + b_dn_v) if (b_up_v is not None
                                       and b_dn_v is not None) else None
        b_matched = min(up_q, dn_q)
        b_resid = up_q - dn_q

        if side == "Up":
            up_q += sz
            up_c += usd
        else:
            dn_q += sz
            dn_c += usd
        run = run + 1 if side == prev_side else 1
        switched = (prev_side is not None and side != prev_side)
        prev_side = side

        a_up_v = (up_c / up_q) if up_q > 0 else None
        a_dn_v = (dn_c / dn_q) if dn_q > 0 else None
        a_pair = (a_up_v + a_dn_v) if (a_up_v is not None
                                       and a_dn_v is not None) else None
        a_matched = min(up_q, dn_q)
        a_resid = up_q - dn_q

        d_matched = a_matched - b_matched
        d_pair = ((a_pair - b_pair) if (a_pair is not None
                                        and b_pair is not None) else None)
        if b_matched <= 0 and a_matched > 0:
            klass = MATCH_CREATION
        elif d_matched > 0:
            klass = (MATCH_IMPROVING if (d_pair is not None and d_pair < 0)
                     else MATCH_WORSENING)
        else:
            klass = RESIDUAL_EXTENSION
        out.append({
            "slug": slug, "n": i + 1, "t": t, "side": side,
            "qty": sz, "usdc": usd,
            "exec_vwap": (usd / sz) if sz > 0 else None,
            "tte_s": ((end - t) if (end is not None and t is not None) else None),
            "run_len": run, "after_switch": switched,
            "up_qty": up_q, "dn_qty": dn_q,
            "up_vwap": a_up_v, "dn_vwap": a_dn_v,
            "matched_qty_before": b_matched, "matched_qty_after": a_matched,
            "pair_before": b_pair, "pair_after": a_pair,
            "d_matched": d_matched, "d_pair": d_pair,
            "resid_before": b_resid, "resid_after": a_resid,
            "d_abs_resid": abs(a_resid) - abs(b_resid),
            "class": klass,
        })
    return out


# ------------------------------------------------------------- decomposition

def _bucket(e: dict, total_events: int) -> Dict[str, str]:
    tte = e["tte_s"]
    p = e["exec_vwap"] or 0.0
    return {
        "tte": ("unknown" if tte is None else
                "<120s" if tte < 120 else "120-300s" if tte < 300 else
                "300-600s" if tte < 600 else "600s+"),
        "decile": f"d{min(10, max(1, int(e['n'] * 10 / max(total_events, 1)) + 1))}",
        "side": e["side"],
        "probability": ("<0.20" if p < 0.20 else "0.20-0.40" if p < 0.40 else
                        "0.40-0.60" if p < 0.60 else "0.60-0.80" if p < 0.80
                        else ">=0.80"),
        "run_len": ("1" if e["run_len"] == 1 else "2-3" if e["run_len"] <= 3
                    else "4-7" if e["run_len"] <= 7 else "8+"),
        "switch": ("first_after_SWITCH" if e["after_switch"] else "continuation"),
        "class": e["class"],
    }


DIMS = ("class", "tte", "decile", "side", "probability", "run_len", "switch")


def decompose(all_events: Sequence[Sequence[dict]]) -> Dict[str, Any]:
    """Partition the total pair-cost movement. Parts must re-sum to the whole."""
    totals: Dict[str, Dict[str, float]] = {d: {} for d in DIMS}
    counts: Dict[str, Dict[str, int]] = {d: {} for d in DIMS}
    grand = 0.0
    n_priced = 0
    per_window = []
    for evs in all_events:
        priced = [e for e in evs if e["d_pair"] is not None]
        if not priced:
            continue
        form = next((e for e in evs if e["class"] == MATCH_CREATION), None)
        if form is None:
            continue
        start = form["pair_after"]
        finish = evs[-1]["pair_after"]
        if start is None or finish is None:
            continue
        moved = sum(e["d_pair"] for e in priced)
        per_window.append({"slug": evs[0]["slug"], "pair_at_formation": start,
                           "pair_final": finish, "movement": finish - start,
                           "sum_of_deltas": moved,
                           "reconciles": abs((finish - start) - moved) < 1e-6})
        grand += moved
        n_priced += len(priced)
        for e in priced:
            b = _bucket(e, len(evs))
            for d in DIMS:
                totals[d][b[d]] = totals[d].get(b[d], 0.0) + e["d_pair"]
                counts[d][b[d]] = counts[d].get(b[d], 0) + 1
    out: Dict[str, Any] = {
        "windows": len(per_window),
        "events_contributing": n_priced,
        "total_pair_movement_summed": round(grand, 6),
        "mean_movement_per_window": (round(grand / len(per_window), 6)
                                     if per_window else None),
        "all_windows_reconcile": all(w["reconciles"] for w in per_window),
        "by": {},
    }
    for d in DIMS:
        rows = []
        for k in sorted(totals[d], key=lambda k: totals[d][k]):
            rows.append({"bucket": k, "n_events": counts[d][k],
                         "total_contribution": round(totals[d][k], 6),
                         "share_of_total": (round(totals[d][k] / grand, 4)
                                            if grand else None),
                         "mean_per_event": round(totals[d][k] / counts[d][k], 8)})
        out["by"][d] = rows
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity", default="data/l5/raw/activity.jsonl")
    ap.add_argument("--out", default="analysis/l5/acceptance/decomposition.json")
    a = ap.parse_args(argv)
    wins = load(a.activity)
    allev = [events(s, f) for s, f in sorted(wins.items())]
    allev = [e for e in allev if e]
    rep = decompose(allev)
    flat = [e for evs in allev for e in evs]
    rep["class_census"] = {
        c: {"events": sum(1 for e in flat if e["class"] == c),
            "share": round(sum(1 for e in flat if e["class"] == c) / len(flat), 4)}
        for c in CLASSES}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in rep.items() if k != "by"},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
