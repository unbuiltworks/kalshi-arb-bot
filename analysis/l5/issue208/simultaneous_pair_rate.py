"""How often do simultaneous quotes make a PAIR versus a lone leg?

PRIORITY 4. The owner's question, exactly: increase economically viable
completed pairs WITHOUT increasing residual losses.

Those two move together under one lever and apart under another, and that
is the whole finding:

  * SYMMETRIC DEPTH trades them off. Resting both legs deeper makes the
    pair cheaper (every completed pair is worth more) and makes both legs
    fill less, so the pair RATE falls too. Whether net improves is an
    empirical question, not an obvious one.

  * ASYMMETRIC DEPTH attacks the residual directly. A lone leg is an
    ASYMMETRY of outcome: one side filled and the other did not. If one
    side is systematically likelier to fill, resting THAT side deeper and
    the shy side at the touch equalises the two fill probabilities and
    converts single-leg outcomes into either pairs or no-fills. No-fills
    cost nothing but a submission; lone legs cost -0.0236/contract.

This reuses `queue_scenarios` wholesale -- its ladder loading, its
PROVEN/POSSIBLE queue verdicts, its residual constant and its
settled-equivalent accounting. Nothing here re-derives economics; it only
asks a different question of the same replay, and only OUT OF SAMPLE.

NOTHING HERE IS A FILL OR A PROFIT. Every figure is historical replay.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from analysis.l5.issue208 import queue_scenarios as QS   # noqa: E402

TICK = QS.TICK
UP, DOWN = QS.UP, QS.DOWN
GRID = (0, 1, 2, 3)


def entries(sources: Sequence[Tuple[str, str]], *, up_depth: int,
            dn_depth: int, qty: D = D(1), ttl_s: Optional[float] = None,
            rebate_per_fill: D = D(0), root: str = "") -> List[Dict[str, Any]]:
    """One row per (frame, scenario), with the joint leg outcome.

    Identical to `QS.replay` except the two legs may rest at DIFFERENT
    depths, which is the lever this module exists to test.
    """
    rows: List[Dict[str, Any]] = []
    for path, shape in sources:
        full = os.path.join(root, path) if root else path
        if not os.path.exists(full):
            continue
        for slug, frames in sorted(QS.load(full, shape).items()):
            for i in range(len(frames) - 1):
                tt = QS.touches(frames[i])
                if tt is None:
                    continue
                bid, ask = tt
                up_own = bid - up_depth * TICK
                dn_own = (D(1) - ask) - dn_depth * TICK
                if up_own <= 0 or dn_own <= 0:
                    continue
                pair = up_own + dn_own
                for name, arm, frac in QS.SCENARIOS:
                    up = QS.leg_outcome(UP, up_own, frames, start=i,
                                        queue_fraction=frac, own_qty=qty,
                                        ttl_s=ttl_s)
                    dn = QS.leg_outcome(DOWN, dn_own, frames, start=i,
                                        queue_fraction=frac, own_qty=qty,
                                        ttl_s=ttl_s)
                    uf, df = QS.filled(up, arm=arm), QS.filled(dn, arm=arm)
                    if uf is None or df is None:
                        continue
                    pnl = QS.window_pnl(pair_cost=pair, qty=qty,
                                        up_filled=uf, dn_filled=df,
                                        rebate_per_fill=rebate_per_fill)
                    rows.append({"slug": slug, "frame": i, "scenario": name,
                                 "up_depth": up_depth, "dn_depth": dn_depth,
                                 "pair_cost": str(pair),
                                 "up_filled": bool(uf), "dn_filled": bool(df),
                                 **pnl})
    return rows


def tabulate(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Joint distribution and the P&L decomposition, per cell."""
    buckets: Dict[Tuple[str, int, int], List[Mapping[str, Any]]] = \
        collections.defaultdict(list)
    for r in rows:
        buckets[(r["scenario"], r["up_depth"], r["dn_depth"])].append(r)
    out: List[Dict[str, Any]] = []
    for (scen, ud, dd), rs in sorted(buckets.items(),
                                     key=lambda k: (k[0][0], k[0][1], k[0][2])):
        n = len(rs)
        if not n:
            continue
        pair = [r for r in rs if r["state"] == "PAIR"]
        resid = [r for r in rs if r["state"] == "RESIDUAL"]
        nofill = [r for r in rs if r["state"] == "NO_FILL"]
        # WHICH side was the lone leg, because that is what asymmetric
        # depth is meant to correct
        lone_up = sum(1 for r in resid if r["up_filled"])
        lone_dn = len(resid) - lone_up
        m = sum(D(str(r["matched_pnl"])) for r in rs)
        c = sum(D(str(r["residual_pnl"])) for r in rs)
        reb = sum(D(str(r["rebate"])) for r in rs)
        net = m + c + reb
        q = D(n)
        out.append({
            "scenario": scen, "up_depth": ud, "dn_depth": dd, "entries": n,
            "p_pair": str((D(len(pair)) / q).quantize(D("0.0001"))),
            "p_residual": str((D(len(resid)) / q).quantize(D("0.0001"))),
            "p_nofill": str((D(len(nofill)) / q).quantize(D("0.0001"))),
            "lone_up": lone_up, "lone_dn": lone_dn,
            "pairs_per_residual": (None if not resid else
                                   str((D(len(pair)) / D(len(resid)))
                                       .quantize(D("0.01")))),
            "mean_pair_cost": (None if not pair else
                               str((sum(D(str(r["pair_cost"]))
                                        for r in pair) / D(len(pair)))
                                   .quantize(D("0.0001")))),
            "pair_contrib_per_entry": str((m / q).quantize(D("0.000001"))),
            "residual_contrib_per_entry": str((c / q).quantize(D("0.000001"))),
            "rebate_contrib_per_entry": str((reb / q).quantize(D("0.000001"))),
            "net_per_entry": str((net / q).quantize(D("0.000001"))),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--rebate", default="0.00727",
                    help="CONFIRMED CREDITED maker rebate per fill")
    ap.add_argument("--ttl", default="", help="seconds, or empty for hold")
    ap.add_argument("--symmetric-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    ttl = float(a.ttl) if str(a.ttl).strip() else None
    reb = D(str(a.rebate))
    pairs = ([(d, d) for d in GRID] if a.symmetric_only
             else [(u, v) for u in GRID for v in GRID])
    rows: List[Dict[str, Any]] = []
    for ud, dd in pairs:
        rows.extend(entries(QS.OUT_OF_SAMPLE, up_depth=ud, dn_depth=dd,
                            ttl_s=ttl, rebate_per_fill=reb, root=a.root))
    tab = tabulate(rows)
    if a.json:
        print(json.dumps(tab, indent=1))
        return 0
    hdr = ("scen", "uD", "dD", "n", "P(pair)", "P(resid)", "P(none)",
           "lone_up", "lone_dn", "pair$", "pairC", "residC", "rebC", "net")
    print("%-12s %2s %2s %6s %8s %8s %8s %7s %7s %7s %10s %10s %9s %10s"
          % hdr)
    for r in tab:
        print("%-12s %2d %2d %6d %8s %8s %8s %7d %7d %7s %10s %10s %9s %10s"
              % (r["scenario"], r["up_depth"], r["dn_depth"], r["entries"],
                 r["p_pair"], r["p_residual"], r["p_nofill"],
                 r["lone_up"], r["lone_dn"], r["mean_pair_cost"] or "-",
                 r["pair_contrib_per_entry"],
                 r["residual_contrib_per_entry"],
                 r["rebate_contrib_per_entry"], r["net_per_entry"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
