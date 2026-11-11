"""What the canonical L5 model predicts AT PROBE A's states.

Probe A's BTC15m windows ran OPEN -> EXTEND -> EXTEND and never acquired an
opposite leg. The question is not "is one-sided accumulation L5-like" -- it
plainly is -- but "conditional on being k fills deep on one side, what does
L5 do next, and how soon".

FILLS ONLY. No statement here is about resting orders, cancels, reprices or
maker/taker role; none of that is in the feed.
"""
from __future__ import annotations

import collections
import json
from typing import Any, Dict, List

from p13_l5_complement import load_windows

HORIZONS = (30, 60, 90, 180)
DEPTHS = (1, 2, 3, 4, 5)


def conditional(fills: List[Dict[str, Any]]):
    """Per window: for each same-side depth k, what happened next."""
    if not fills:
        return []
    s0 = fills[0].get("outcome")
    rows = []
    qty = collections.defaultdict(float)
    spend = collections.defaultdict(float)
    other = "Up" if s0 == "Down" else "Down"
    depth = 0
    for i, f in enumerate(fills):
        o = f.get("outcome")
        q = float(f.get("size") or 0)
        px = float(f.get("price") or 0)
        qty[o] += q
        spend[o] += q * px
        if o != s0:
            break                      # the run of same-side fills is over
        depth += 1
        t = f.get("timestamp") or 0
        # find the first opposite fill after THIS same-side fill
        nxt_opp_t = None
        nxt_opp_px = None
        for g in fills[i + 1:]:
            if g.get("outcome") != s0:
                nxt_opp_t = g.get("timestamp") or 0
                nxt_opp_px = float(g.get("price") or 0)
                break
        nxt_same = (i + 1 < len(fills)
                    and fills[i + 1].get("outcome") == s0)
        # the pair cost that WOULD obtain if the opposite filled at its
        # actual next price, given inventory as it stands right now
        same_vwap = spend[s0] / qty[s0] if qty[s0] else None
        rows.append({
            "depth": depth,
            "has_next_fill": i + 1 < len(fills),
            "next_is_same_side": bool(nxt_same),
            "dt_to_opposite": (None if nxt_opp_t is None else nxt_opp_t - t),
            "same_side_vwap": same_vwap,
            "opposite_price": nxt_opp_px,
            "pair_cost_if_opposite_now": (
                None if same_vwap is None or nxt_opp_px is None
                else same_vwap + nxt_opp_px),
        })
    return rows


def main() -> int:
    wins = load_windows()
    out: Dict[str, Any] = {"label": "P13_CONDITIONAL_V1",
                           "horizons_s": list(HORIZONS), "by_family": {}}
    per_fam = collections.defaultdict(list)
    for slug, fills in wins.items():
        fam = slug.rsplit("-", 1)[0]
        for r in conditional(fills):
            per_fam[fam].append(r)

    for fam, rows in per_fam.items():
        cell = {}
        for k in DEPTHS:
            at = [r for r in rows if r["depth"] == k]
            if not at:
                continue
            withnext = [r for r in at if r["has_next_fill"]]
            opp = [r for r in at if r["dt_to_opposite"] is not None]
            pc = [r["pair_cost_if_opposite_now"] for r in at
                  if r["pair_cost_if_opposite_now"] is not None]
            pc_sorted = sorted(pc)
            cell["depth_%d" % k] = {
                "n_states": len(at),
                "p_next_action_is_same_side_EXTEND": (
                    round(sum(1 for r in withnext if r["next_is_same_side"])
                          / len(withnext), 4) if withnext else None),
                "p_opposite_ever": round(len(opp) / len(at), 4),
                "p_opposite_within_s": {
                    str(h): round(sum(1 for r in opp
                                      if r["dt_to_opposite"] <= h) / len(at), 4)
                    for h in HORIZONS},
                "pair_cost_at_that_opposite": ({
                    "n": len(pc_sorted),
                    "p25": round(pc_sorted[len(pc_sorted) // 4], 4),
                    "p50": round(pc_sorted[len(pc_sorted) // 2], 4),
                    "p75": round(pc_sorted[3 * len(pc_sorted) // 4], 4),
                    "frac_gt_1.00": round(sum(1 for x in pc_sorted if x > 1.0)
                                          / len(pc_sorted), 4),
                } if pc_sorted else {}),
            }
        out["by_family"][fam] = cell
    out["labels"] = ("p_opposite_within_s is measured from the k-th same-side "
                     "fill. pair_cost_at_that_opposite is the size-weighted "
                     "average cost a matched pair WOULD have carried had the "
                     "opposite leg filled at the price it actually next "
                     "traded at for this account -- it is not a P&L, and it "
                     "says nothing about whether an order was resting.")
    dest = "analysis/gemini/audit/p13_conditional.json"
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    for fam in ("btc-updown-15m", "btc-updown-5m"):
        print("=" * 62, "\n", fam)
        for k in DEPTHS:
            c = out["by_family"].get(fam, {}).get("depth_%d" % k)
            if not c:
                continue
            print(" depth %d  n=%-6d P(next=EXTEND)=%-7s P(opp ever)=%-7s"
                  % (k, c["n_states"],
                     c["p_next_action_is_same_side_EXTEND"],
                     c["p_opposite_ever"]))
            print("           P(opp<=30/60/90/180s) = %s" %
                  c["p_opposite_within_s"])
            print("           pair cost at that opposite p25/p50/p75 = "
                  "%s/%s/%s   frac>1.00 = %s"
                  % (c["pair_cost_at_that_opposite"].get("p25"),
                     c["pair_cost_at_that_opposite"].get("p50"),
                     c["pair_cost_at_that_opposite"].get("p75"),
                     c["pair_cost_at_that_opposite"].get("frac_gt_1.00")))
    print("\nwrote", dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
