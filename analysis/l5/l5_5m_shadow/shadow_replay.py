"""CURRENT_R5FT vs NEW_L5_5M, action by action, on RECORDED PMUS states.

OFFLINE ONLY. This replays states the live probe already observed and logged;
it submits nothing, reads nothing back into the live session, and does not
require redeploying d108c54a. Every "action" below is a decision label, never
an order.

THE CANDIDATE, and the single thing it changes:
  CURRENT_R5FT : complete whenever the attainable pair is within the active
                 rung ceiling (pair_cost_max, currently 1.00; soft observation
                 cap SPRING_PAIR_TARGET 0.98).
  NEW_L5_5M    : complete only when the attainable pair is <= COMPLETE_TARGET
                 (0.90), otherwise WAIT and keep the residual.

WHY 0.90 AND NOT A LOOSER NUMBER. On L5's own 5m history, chronologically
split, the first-switch pair bands earn (train -> test, bp on acquisition):
    <=0.90      +1332 -> +1344     <- stable, and 95% of all net dollars
    0.90-0.96    +302 ->   +96
    0.96-0.98    -488 ->  -108
    0.98-1.00    -403 ->  +255     <- sign flips; NOT reliably harmful
    >1.05       -1331 -> -1092
Only the <=0.90 band is both large and OOS-stable. The gate itself is NOT
loosened anywhere and no risk limit is raised: this candidate REFUSES strictly
more completions than production does.
"""
from __future__ import annotations

import json
import statistics
import sys
from typing import Any, Dict, List, Optional

COMPLETE_TARGET = 0.90          # candidate completion ceiling
LIVE_OBSERVATION_CAP = 0.98     # production's soft cap, for reference only
LIVE_HARD_CEILING = 1.00        # production's active-rung hard ceiling


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def current_r5ft(row: Dict[str, Any]) -> Dict[str, Any]:
    """What production actually did, read from the record -- not re-simulated.
    `filled_qty > 0` is ground truth; `would_submit` is the shadow arm's own
    label and is NOT treated as a production action."""
    filled = (_f(row.get("filled_qty")) or 0.0) > 0
    pair = _f(row.get("econ_pair_cost"))
    act = "COMPLETE_FILLED" if filled else "COMPLETE_RESTED_NO_FILL"
    reason = "within active rung ceiling" if pair is None or pair <= LIVE_HARD_CEILING \
        else "above ceiling"
    return {"action": act, "reason": reason, "pair": pair}


def new_l5_5m(row: Dict[str, Any]) -> Dict[str, Any]:
    pair = _f(row.get("econ_pair_cost"))
    if pair is None:
        # UNKNOWN is not permission. No pair, no completion.
        return {"action": "WAIT", "reason": "pair UNKNOWN", "pair": None}
    if pair <= COMPLETE_TARGET:
        return {"action": "COMPLETE", "reason": f"pair {pair:.4f} <= {COMPLETE_TARGET}",
                "pair": pair}
    return {"action": "WAIT", "reason": f"pair {pair:.4f} > {COMPLETE_TARGET}",
            "pair": pair}


def replay(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        a, b = current_r5ft(r), new_l5_5m(r)
        out.append({
            "slug": r.get("slug"), "stage": r.get("stage"),
            "side": r.get("side"), "px": r.get("maker_px"),
            "size": r.get("qty"), "filled_qty": r.get("filled_qty"),
            "inv_up": r.get("session_inv_up"), "inv_dn": r.get("session_inv_dn"),
            "residual": r.get("session_inv_residual"),
            "pair_cost": b["pair"], "tte": r.get("quote_age_s"),
            "CURRENT_R5FT": a["action"], "NEW_L5_5M": b["action"],
            "r5ft_reason": a["reason"], "l5_reason": b["reason"],
            "agree": (a["action"].startswith("COMPLETE") and b["action"] == "COMPLETE")
                     or (a["action"] == "COMPLETE_RESTED_NO_FILL" and b["action"] == "WAIT"),
            "disagreement": None,
        })
        o = out[-1]
        if a["action"] == "COMPLETE_FILLED" and b["action"] == "WAIT":
            o["disagreement"] = "PRODUCTION_FILLED_CANDIDATE_WOULD_REFUSE"
        elif a["action"] == "COMPLETE_RESTED_NO_FILL" and b["action"] == "COMPLETE":
            o["disagreement"] = "BOTH_WANTED_IT_NEITHER_FILLED"
    return out


def report(rows: List[Dict[str, Any]]) -> str:
    rep = replay(rows)
    L = []
    L.append(f"states replayed: {len(rep)}")
    filled = [r for r in rep if (_f(r["filled_qty"]) or 0) > 0]
    L.append(f"production FILLS in the record: {len(filled)}")
    ref = [r for r in filled if r["NEW_L5_5M"] == "WAIT"]
    kept = [r for r in filled if r["NEW_L5_5M"] == "COMPLETE"]
    L.append(f"  candidate would KEEP  {len(kept)} of them")
    L.append(f"  candidate would REFUSE {len(ref)} of them")
    # a row whose pair is UNKNOWN has no median to contribute; counted, not imputed
    kp = [r["pair_cost"] for r in kept if r["pair_cost"] is not None]
    rp = [r["pair_cost"] for r in ref if r["pair_cost"] is not None]
    if kp:
        L.append(f"  kept    pair p50 {statistics.median(kp):.4f}  (n with pair {len(kp)})")
    if rp:
        L.append(f"  refused pair p50 {statistics.median(rp):.4f}  (n with pair {len(rp)})")
    unk = sum(1 for r in filled if r["pair_cost"] is None)
    if unk:
        L.append(f"  fills whose pair is UNKNOWN: {unk} -- refused by the candidate,"
                 f" since UNKNOWN is not permission")
    L.append("")
    L.append(f"{'slug':<40}{'stage':<20}{'px':>7}{'pair':>9}"
             f"{'CURRENT_R5FT':>26}{'NEW_L5_5M':>11}")
    L.append("-" * 113)
    for r in filled:
        pc = ("UNKNOWN" if r["pair_cost"] is None else "%.4f" % r["pair_cost"])
        L.append("%-40s%-20s%7s%9s%26s%11s" % (
            str(r["slug"])[:39], str(r["stage"])[:19],
            str(r["px"]), pc, r["CURRENT_R5FT"], r["NEW_L5_5M"]))
    return "\n".join(L)


def main(path: str) -> int:
    rows = [json.loads(l) for l in open(path) if l.strip()]
    print(report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1
                          else "analysis/l5/cross_shadow_live/cohort.jsonl"))
