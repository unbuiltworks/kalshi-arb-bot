"""CELL_C_HOLD_NO_REENTRY_V1 vs frozen Cell C, on one tape.

Frozen by PREREGISTRATION_CELL_C_HOLD_NO_REENTRY_V1.md. The controller is
Cell C with the SELL sleeve deleted and nothing else changed, so it is
expressed as the SAME frozen replay with exits_enabled=False rather than as a
new replay loop -- a second implementation could drift from Cell C in ways a
diff would not show.

The same file runs DEVELOPMENT and OOS. The tape is an argument; no parameter
of the controller is.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.pmus_favorite_dca import load, eligible, replay_c, TAPE  # noqa
from src import pmus_fees as FEES  # noqa

ZERO = D(0)
PURCHASE_KINDS = ("seed", "extend", "extend_maker", "match", "match_only")


def _legs(r: dict):
    """(purchase legs, sell legs) of one replay, each as (t, side, px, sh, fee)."""
    buys = [(a["t"], a["side"], D(a["px"]), D(a["sh"]), D(a["fee"]))
            for a in r["allocation_log"] if a["kind"] in PURCHASE_KINDS]
    sells = []
    for e in r["exit_log"]:
        for lg in e.get("legs", []):
            sells.append((lg["t"], e.get("side"), D(lg["px"]), D(lg["sh"]),
                          D(lg["fee"])))
    return buys, sells


def _fees(legs):
    """(four-decimal modeled, venue-cent banker-rounded) over legs."""
    modeled = sum((lg[4] for lg in legs), ZERO)
    cents = sum((FEES.taker_fee(lg[2], lg[3], cents=True) for lg in legs),
                ZERO)
    return modeled, cents


def window_row(slug: str, r: dict) -> dict:
    buys, sells = _legs(r)
    bf_m, bf_c = _fees(buys)
    sf_m, sf_c = _fees(sells)
    fees_m, fees_c = bf_m + sf_m, bf_c + sf_c
    purchase_cash = sum((lg[2] * lg[3] for lg in buys), ZERO)
    sale_cash = sum((lg[2] * lg[3] for lg in sells), ZERO)
    settle = D(r["settlement_value"])
    # gross is pre-fee by construction: what came back minus what went out
    gross = sale_cash + settle - purchase_cash
    net_m = gross - fees_m
    net_c = gross - fees_c
    return {
        "slug": slug,
        "actions": len(buys) + len(r["exit_log"]),
        "entry_legs": 1 if buys else 0,
        "extend_legs": max(0, len(buys) - 1),
        "purchase_legs": len(buys),
        "exit_events": len(r["exit_log"]),
        "exit_legs": len(sells),
        "purchase_cash": purchase_cash,
        "sale_cash": sale_cash,
        "settlement_cash": settle,
        "gross": gross,
        "fees_modeled": fees_m,
        "fees_venue_cent": fees_c,
        "net_modeled": net_m,
        "net_venue_cent": net_c,
        "peak_capital": D(r["max_capital"]),
        "peak_residual": D(r["max_residual"]),
        "identity_residual": D(r["decomposition_identity_residual"]),
        "traded": bool(buys),
    }


def _lb(vals, k):
    """leave-worst-k mean: the mean after discarding the k worst windows."""
    if len(vals) <= k:
        return None
    return sum(sorted(vals)[k:]) / (len(vals) - k)


def aggregate(rows, envelopes=(25, 50, 100, 200)) -> dict:
    traded = [r for r in rows if r["traded"]]
    if not traded:
        return {"eligible_windows": len(rows), "traded_windows": 0}
    n = len(traded)
    f = lambda k: [float(r[k]) for r in traded]  # noqa: E731
    net_m, net_c, gross = f("net_modeled"), f("net_venue_cent"), f("gross")
    purchase = sum(r["purchase_cash"] for r in traded)
    peak_cap = sum(r["peak_capital"] for r in traded)
    out = {
        "eligible_windows": len(rows),
        "traded_windows": n,
        "actions_per_window": round(sum(r["actions"] for r in traded) / n, 2),
        "ENTRY_events": sum(r["entry_legs"] for r in traded),
        "EXTEND_legs": sum(r["extend_legs"] for r in traded),
        "EXIT_events": sum(r["exit_events"] for r in traded),
        "EXIT_legs": sum(r["exit_legs"] for r in traded),
        "purchase_cash": str(round(purchase, 4)),
        "sale_cash": str(round(sum(r["sale_cash"] for r in traded), 4)),
        "settlement_cash": str(round(sum(r["settlement_cash"]
                                         for r in traded), 4)),
        "gross_before_fees": str(round(sum(r["gross"] for r in traded), 4)),
        "fees_modeled_4dp": str(round(sum(r["fees_modeled"]
                                          for r in traded), 4)),
        "fees_venue_cent": str(round(sum(r["fees_venue_cent"]
                                         for r in traded), 4)),
        "net_modeled": str(round(sum(r["net_modeled"] for r in traded), 4)),
        "net_venue_cent": str(round(sum(r["net_venue_cent"]
                                        for r in traded), 4)),
        "gross_per_traded_window": round(sum(gross) / n, 4),
        "net_per_traded_window_modeled": round(sum(net_m) / n, 4),
        "net_per_traded_window_venue_cent": round(sum(net_c) / n, 4),
        "median_net_modeled": round(statistics.median(net_m), 4),
        "median_net_venue_cent": round(statistics.median(net_c), 4),
        "median_gross": round(statistics.median(gross), 4),
        "positive_pct_net_modeled": round(
            100.0 * sum(1 for v in net_m if v > 0) / n, 1),
        "positive_pct_net_venue_cent": round(
            100.0 * sum(1 for v in net_c if v > 0) / n, 1),
        "positive_pct_gross": round(
            100.0 * sum(1 for v in gross if v > 0) / n, 1),
        "LB1_net_modeled": (round(_lb(net_m, 1), 4)
                            if _lb(net_m, 1) is not None else None),
        "LB3_net_modeled": (round(_lb(net_m, 3), 4)
                            if _lb(net_m, 3) is not None else None),
        "LB1_net_venue_cent": (round(_lb(net_c, 1), 4)
                               if _lb(net_c, 1) is not None else None),
        "LB3_net_venue_cent": (round(_lb(net_c, 3), 4)
                               if _lb(net_c, 3) is not None else None),
        "worst_window_net_modeled": min(
            ((round(float(r["net_modeled"]), 4), r["slug"]) for r in traded),
            key=lambda x: x[0]),
        "best_window_net_modeled": max(
            ((round(float(r["net_modeled"]), 4), r["slug"]) for r in traded),
            key=lambda x: x[0]),
        "peak_capital_max": str(round(max(r["peak_capital"]
                                          for r in traded), 4)),
        "peak_capital_mean": str(round(peak_cap / n, 4)),
        "peak_residual_max": str(round(max(r["peak_residual"]
                                           for r in traded), 4)),
        "peak_residual_mean": str(round(sum(r["peak_residual"]
                                            for r in traded) / n, 4)),
        # capital turnover: cash actually deployed over peak capital tied up.
        # NOT the mislabeled "30.24", which was acquisition cost basis.
        "capital_turnover_deployed_over_peak": (
            round(float(purchase / peak_cap), 3) if peak_cap > 0 else None),
        "identity_max_abs_residual": str(max(
            abs(r["identity_residual"]) for r in traded)),
    }
    # distribution at each defensible envelope: the $1-scale replay scaled
    # linearly by capital, which is an UPPER bound -- the capacity study
    # measured impact as flat with size only to ~$100.
    dist = {}
    for env in envelopes:
        k = {}
        for r in traded:
            pk = float(r["peak_capital"])
            s = (env / pk) if pk > 0 else 0.0
            k.setdefault("gross_ge_5", 0)
            k.setdefault("net_modeled_ge_5", 0)
            k.setdefault("net_venue_cent_ge_5", 0)
            if float(r["gross"]) * s >= 5.0:
                k["gross_ge_5"] += 1
            if float(r["net_modeled"]) * s >= 5.0:
                k["net_modeled_ge_5"] += 1
            if float(r["net_venue_cent"]) * s >= 5.0:
                k["net_venue_cent_ge_5"] += 1
        k["of_traded_windows"] = n
        dist["$%d" % env] = k
    out["windows_reaching_5_by_envelope_LINEAR_UPPER_BOUND"] = dist
    return out


def run(tape: str, label: str, out_dir: str) -> dict:
    by = load(tape)
    elig, rejected = {}, {}
    for slug, w in by.items():
        ok, oc, why = eligible(slug, w)
        (elig if ok else rejected)[slug] = (w, oc, why)
    cc_rows, hd_rows, per_window = [], [], {}
    for slug in sorted(elig):
        w, oc, _ = elig[slug]
        cc = replay_c(slug, w, oc, D(1), seed=False, name="CELL_C",
                      require_strengthening=False, exits_enabled=True)
        hd = replay_c(slug, w, oc, D(1), seed=False,
                      name="CELL_C_HOLD_NO_REENTRY_V1",
                      require_strengthening=False, exits_enabled=False)
        # PREREGISTERED VERIFICATION 1: the purchase stream must be identical.
        cb, _ = _legs(cc)
        hb, hs = _legs(hd)
        if cb != hb:
            raise SystemExit(
                "PURCHASE STREAM DIVERGED in %s: Cell C %d legs vs HOLD %d "
                "legs. The counterfactual changed entry/extension, which the "
                "preregistration forbids." % (slug, len(cb), len(hb)))
        # VERIFICATION 2: HOLD has no sell sleeve at all.
        if hs or hd["exit_log"]:
            raise SystemExit("HOLD produced %d sell legs in %s" % (len(hs),
                                                                   slug))
        for r in (cc, hd):
            r.pop("trajectory", None)
        crow, hrow = window_row(slug, cc), window_row(slug, hd)
        # VERIFICATION 3: the five-term identity, both controllers.
        for nm, rr in (("CELL_C", crow), ("HOLD", hrow)):
            if abs(rr["identity_residual"]) > D("0.000001"):
                raise SystemExit("identity broke for %s in %s: %s"
                                 % (nm, slug, rr["identity_residual"]))
        cc_rows.append(crow)
        hd_rows.append(hrow)
        per_window[slug] = {
            "settlement_outcome": cc["settlement_outcome"],
            "CELL_C": {k: str(v) for k, v in crow.items()},
            "CELL_C_HOLD_NO_REENTRY_V1": {k: str(v) for k, v in hrow.items()},
        }
    res = {
        "sample": label,
        "tape": tape,
        "rejected_windows": {s: v[2] for s, v in rejected.items()},
        "CELL_C": aggregate(cc_rows),
        "CELL_C_HOLD_NO_REENTRY_V1": aggregate(hd_rows),
        "per_window": per_window,
        "fee_effective_date_caveat": (
            "taker theta 0.0695 from 2026-09-17T04:00Z per CFTC notice; PMUS "
            "docs apparently say Oct 1. Windows on Sep 30 - Oct 1 are "
            "affected and this is UNRESOLVED."),
    }
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "hold_no_reentry_%s.json" % label.lower())
    with open(p, "w") as fh:
        json.dump(res, fh, indent=1, default=str)
    print(json.dumps({k: res[k] for k in
                      ("sample", "CELL_C", "CELL_C_HOLD_NO_REENTRY_V1")},
                     indent=1, default=str))
    print("\nwrote %s" % p)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", default=TAPE)
    ap.add_argument("--label", default="DEVELOPMENT")
    ap.add_argument("--out", default="analysis/l5/hold_no_reentry")
    a = ap.parse_args()
    run(a.tape, a.label, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
