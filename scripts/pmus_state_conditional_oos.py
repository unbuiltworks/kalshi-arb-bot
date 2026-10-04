"""Evaluate the FROZEN STATE_CONDITIONAL_PAIR_V1 on OOS, exactly once.

Nothing is fitted here. The coefficients and the standardization constants are
READ from the committed development artifact
(`state_conditional_v1_development.json`, commit 2939ee67) so there is a single
source for them and no opportunity to retype one. Features, EV gate, controller
and fee treatment are imported from the same modules the development run used,
unchanged -- a second implementation could drift silently, and the whole value
of this test is that it cannot.

The primary hypothesis is NOT "is the model calibrated". It is:

    does  q - executable_price - fee  RANK realized economic returns?

A model can be a good classifier and still have no tradable alpha, because the
quantity that must be beaten is PMUS's price, not 0.5. So the report leads with
the rank correlation between predicted edge and realized per-leg P&L, and with
the predicted-edge-bucket table, rather than with Brier.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.pmus_state_table import FEATURES, LABEL  # noqa
from scripts.pmus_state_conditional_v1 import (  # noqa
    brier, logloss, reliability, predict, run_window, evs, ONE, ZERO, UNIT)

DEV_ARTIFACT = ("analysis/l5/state_conditional/"
                "state_conditional_v1_development.json")


def load_frozen(path: str):
    c = json.load(open(path))["calibration"]["frozen_full_sample_coefficients"]
    w = [c["intercept"]] + [c[f] for f in FEATURES]
    return w, c["standardization_mu"], c["standardization_sd"]


def q_of_row(w, mu, sd, r) -> float:
    x = [1.0] + [(float(r[f]) - mu[i]) / (sd[i] if sd[i] > 0 else 1.0)
                 for i, f in enumerate(FEATURES)]
    return predict(w, x)


def spearman(a, b) -> float:
    """Rank correlation. The ranking question, answered without assuming
    linearity between a predicted edge and a binary-settlement payoff."""
    n = len(a)
    if n < 3:
        return float("nan")

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((x - mb) ** 2 for x in rb))
    return num / (da * db) if da > 0 and db > 0 else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True, help="OOS state table jsonl")
    ap.add_argument("--dev-artifact", default=DEV_ARTIFACT)
    ap.add_argument("--out", default="analysis/l5/state_conditional")
    ap.add_argument("--label", default="OOS")
    a = ap.parse_args()

    w, mu, sd = load_frozen(a.dev_artifact)
    rows = [json.loads(l) for l in open(a.table)]
    by = {}
    for r in rows:
        by.setdefault(r["slug"], []).append(r)
    for sl in by:
        by[sl].sort(key=lambda r: r["t"])
    slugs = sorted(by)

    qf = {(r["slug"], r["t"]): q_of_row(w, mu, sd, r) for r in rows}
    y = [r[LABEL] for r in rows]
    p = [qf[(r["slug"], r["t"])] for r in rows]
    base = sum(y) / len(y)

    res = [run_window(by[sl], lambda r: qf[(r["slug"], r["t"])],
                      by[sl][0][LABEL]) for sl in slugs]
    traded = [r for r in res if r["purchases"]]

    # ---- the primary question: does predicted edge RANK realized returns?
    legs = []
    for r in traded:
        for x in r["purchases"]:
            won = (r["outcome_up"] == (x["side"] == "UP"))
            sh = D(x["sh"])
            legs.append({
                "slug": r["slug"], "side": x["side"], "ev": float(x["ev"]),
                "pnl": float((sh if won else ZERO) - D(x["px"]) * sh
                             - D(x["fee_cent"])),
                "q": x["q"], "px": x["px"],
                "seconds_remaining": x["seconds_remaining"],
                "is_first": False})
    for r in traded:
        if r["purchases"]:
            t0 = r["purchases"][0]["t"]
            for lg in legs:
                if lg["slug"] == r["slug"] and lg["seconds_remaining"] == \
                        r["purchases"][0]["seconds_remaining"]:
                    lg["is_first"] = True
                    break
    firsts = [lg for lg in legs if lg["is_first"]]
    rank_all = spearman([lg["ev"] for lg in legs], [lg["pnl"] for lg in legs])
    rank_first = spearman([lg["ev"] for lg in firsts],
                          [lg["pnl"] for lg in firsts])
    buckets = []
    for lo, hi in ((0, 0.01), (0.01, 0.02), (0.02, 0.05), (0.05, 1)):
        g = [lg for lg in legs if lo <= lg["ev"] < hi]
        if g:
            buckets.append({
                "edge": "[%.2f,%.2f)" % (lo, hi), "n": len(g),
                "mean_predicted_edge": round(sum(x["ev"] for x in g)
                                             / len(g), 5),
                "mean_realized_pnl": round(sum(x["pnl"] for x in g)
                                           / len(g), 5),
                "win_rate": round(sum(1 for x in g if x["pnl"] > 0)
                                  / len(g), 4)})

    s = lambda v: sum(v, ZERO)  # noqa
    resid_pnl = matched_book = ZERO
    for r in traded:
        cu = (r["up_cash"] / r["up_sh"]) if r["up_sh"] > 0 else ZERO
        cd = (r["dn_cash"] / r["dn_sh"]) if r["dn_sh"] > 0 else ZERO
        m = min(r["up_sh"], r["dn_sh"])
        matched_book += m * (ONE - (cu + cd))
        rr = r["residual"]
        if rr != 0:
            basis = abs(rr) * (cu if rr > 0 else cd)
            val = (abs(rr) if ((rr > 0 and r["outcome_up"])
                               or (rr < 0 and not r["outcome_up"]))
                   else ZERO)
            resid_pnl += val - basis
    nets = [float(r["net_cent"]) for r in traded]
    lb = lambda k: (round(sum(sorted(nets)[k:]) / (len(nets) - k), 4)  # noqa
                    if len(nets) > k else None)
    entry_t = [r["purchases"][0]["seconds_remaining"] for r in traded]
    out = {
        "sample": a.label, "frozen_from": a.dev_artifact,
        "frozen_coefficients": {"w": w, "mu": mu, "sd": sd},
        "refitted": False,
        "1_probability_quality": {
            "n_rows": len(y), "base_rate": round(base, 4),
            "brier": round(brier(y, p), 6),
            "brier_vs_baserate": round(brier(y, [base] * len(y)), 6),
            "logloss": round(logloss(y, p), 6),
            "reliability": reliability(y, p)},
        "2_directional_gross": str(round(resid_pnl, 4)),
        "3_matched_book_gross": str(round(matched_book, 4)),
        "4_total_gross": str(round(s(r["gross"] for r in traded), 4)),
        "5_fees": {"modeled_4dp": str(round(s(r["fees_4dp"]
                                              for r in traded), 4)),
                   "venue_cent": str(round(s(r["fees_cent"]
                                             for r in traded), 4))},
        "6_total_net": {"venue_cent": str(round(s(r["net_cent"]
                                                 for r in traded), 4)),
                        "modeled_4dp": str(round(s(r["net_4dp"]
                                                   for r in traded), 4))},
        "7_first_leg_predicted_vs_realized": {
            "n": len(firsts),
            "total_predicted_EV": round(sum(x["ev"] for x in firsts), 4),
            "total_realized_pnl": round(sum(x["pnl"] for x in firsts), 4)},
        "8_predicted_edge_buckets": buckets,
        "PRIMARY_does_edge_rank_returns": {
            "spearman_all_legs": round(rank_all, 4),
            "n_all_legs": len(legs),
            "spearman_first_legs": round(rank_first, 4),
            "n_first_legs": len(firsts)},
        "9_first_entry_seconds_remaining": {
            "median": statistics.median(entry_t) if entry_t else None,
            "min": min(entry_t) if entry_t else None,
            "max": max(entry_t) if entry_t else None},
        "10_windows": {"eligible": len(slugs), "traded": len(traded)},
        "13_distribution": {
            "net_per_traded_window": (round(sum(nets) / len(nets), 4)
                                      if nets else None),
            "median": round(statistics.median(nets), 4) if nets else None,
            "positive_pct": (round(100.0 * sum(1 for v in nets if v > 0)
                                   / len(nets), 1) if nets else None),
            "LB1": lb(1), "LB3": lb(3),
            "worst_window": (min(((round(v, 4), r["slug"])
                                  for v, r in zip(nets, traded)),
                                 key=lambda x: x[0]) if nets else None)},
    }
    os.makedirs(a.out, exist_ok=True)
    pth = os.path.join(a.out, "state_conditional_v1_%s.json" % a.label.lower())
    with open(pth, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps(out, indent=1, default=str)[:4000])
    print("\nwrote %s" % pth)
    return 0


if __name__ == "__main__":
    sys.exit(main())
