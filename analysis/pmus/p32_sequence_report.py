"""P32 -- the L5 sequence on PMUS, per window, per bound, as a trajectory.

Replaces the per-window pair-cost test. A window is judged on whether the
SEQUENCE occurs and on the economics of the COMPLETE sequence -- matched
inventory plus directional residual plus rebates minus fees -- never on
whether an intermediate pair cost under a dollar.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from typing import Any, Dict, List

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from analysis.pmus import p30_auth_trade_harvest as H     # noqa: E402
from analysis.pmus import p31_mirror_bounds as P          # noqa: E402
from src import pmus_mirror_lifecycle as M                # noqa: E402
from src import pmus_sequence_trajectory as T             # noqa: E402


def acquisitions_for(bound: str, classified: List[dict]) -> List[dict]:
    """Which classified orders count as an acquisition under this bound."""
    out = []
    for c in classified:
        take = (c.get("copyable_at_receipt") if bound == M.COPYABILITY
                else c.get("klass") in M.BOUND_CLASSES[bound])
        if take:
            out.append({"t": c["actionable_from"], "side": c["side"],
                        "price": c["price"],
                        "evidence": (c.get("klass") if bound != M.COPYABILITY
                                     else "COPYABLE_AT_RECEIPT")})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None)
    ap.add_argument("--bound", default=None, help="limit printing to one")
    a = ap.parse_args(argv)

    rows = H.load_rows()
    onrow = {s: [r for r in v if r.get("bid_at_trade") is not None]
             for s, v in rows.items()}
    proof = H.prove_dir_semantics({s: v for s, v in onrow.items() if v}, {})
    norm = H.proven_normalizer(proof)

    orders = [o for o in P.orders_from_observer() if o["market"] in rows]
    by_w = collections.defaultdict(list)
    for o in orders:
        by_w[o["window"]].append(o)

    settles = {}
    report: Dict[str, Any] = {
        "label": "PMUS_L5_SEQUENCE_TRAJECTORY_V1",
        "criterion": ("the SEQUENCE, not per-pair profitability. A window is "
                      "never rejected for an expensive intermediate pair; the "
                      "pair ceiling was falsified on L5's own BTC15m history "
                      "(<=0.96 gave 0 completed windows and -206.18, against "
                      "1016 and +1.50 with no ceiling)."),
        "aggressor_proof_verdict": proof["verdict"],
        "windows": {},
    }
    for w, os_ in sorted(by_w.items()):
        classified = M.scan(os_, rows, normalize=norm)
        slug = os_[0]["market"]
        if slug not in settles:
            settles[slug] = T.settlement(slug)
        entry = {"market": slug, "orders": len(os_),
                 "settlement": settles[slug], "bounds": {}}
        for b in M.BOUNDS:
            acq = acquisitions_for(b, classified)
            entry["bounds"][b] = T.trajectory(acq, slug, settles[slug])
        report["windows"][w] = entry

    if a.out:
        json.dump(report, open(a.out, "w"), indent=1, default=str)

    FIELDS = ("acquisitions", "first_side",
              "repeated_same_side_before_opposite", "opposite_acquired",
              "acquisitions_after_first_opposite",
              "continued_recycling_after_opposite",
              "matched_final", "residual_final", "residual_direction",
              "inventory_crossed_zero", "max_temporary_pair_cost",
              "min_pair_cost", "final_pair_cost", "pair_cost_exceeded_par",
              "pair_cost_repaired", "pair_cost_repaired_below_par",
              "matched_pair_pnl_usd", "residual_cost_basis_usd",
              "residual_settlement_value_usd", "residual_pnl_usd",
              "maker_rebate_accrual_usd", "taker_fees_usd",
              "liquidity_rewards_usd", "total_settlement_implied_shadow_usd")
    print("aggressor proof:", proof["verdict"],
          "(%s/%s consistent)" % (proof["consistent"],
                                  proof["consistent"] + proof["inconsistent"]))
    for w, e in report["windows"].items():
        print("\n================ WINDOW %s  (%s)  orders=%d"
              % (w, e["market"][-5:], e["orders"]))
        print("  settlement: state=%s yes_value=%s"
              % (e["settlement"].get("state"), e["settlement"].get("yes_value")))
        for b in M.BOUNDS:
            if a.bound and b != a.bound:
                continue
            t = e["bounds"][b]
            print("  --", b)
            for f in FIELDS:
                print("     %-38s %s" % (f, t[f]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
