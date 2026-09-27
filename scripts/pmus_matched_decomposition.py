"""Matched-unit decomposition — DIAGNOSTIC ONLY, gates nothing.

For every matched unit under the frozen policy, reconstruct the chronological
acquisition path and separate matched inventory by the mechanism that created it:

  EC        the unit was retired by an ECONOMIC_COMPLETE taker completion
  PASSIVE   the unit was matched by a later passive ACQUIRE/EXTEND on the
            opposite side (the oscillator), never by a completion

Pairing convention, stated because it is a convention: passive matches are paired
FIFO in chronological order, earliest unmatched UP against earliest unmatched
DOWN. The scorer's own P&L uses AVERAGE cost, so the per-unit pair costs here
reconstruct the same totals while attributing them to individual legs. Totals are
asserted against the scorer.

Read-only. No policy input, no threshold, no selection.
"""
from __future__ import annotations

import json, os, sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import pmus_completion_shadow as S                      # noqa: E402
from scripts.audit_economic_complete import load, USABLE         # noqa: E402
from scripts.audit_completion_orderstate import walk             # noqa: E402

THETA, VARIANT, FILL = D("0.96"), "B", "queue_aware"
BUCKETS = (("<0.96", None, D("0.96")), ("0.96-1.00", D("0.96"), D("1.00")),
           ("1.00-1.10", D("1.00"), D("1.10")), ("1.10-1.25", D("1.10"), D("1.25")),
           (">1.25", D("1.25"), None))


def _bucket(pc: D) -> str:
    for name, lo, hi in BUCKETS:
        if (lo is None or pc >= lo) and (hi is None or pc < hi):
            return name
    return ">1.25"


def decompose(rows, winner, clip):
    """Per-unit matched records for one window."""
    a = walk(rows, clip, THETA, VARIANT, winner)
    ev = a["events"]
    # chronological acquisition lots per side, consumed as matches are attributed
    lots = {"up": [], "down": []}
    recs = []
    for e in ev:
        if e["kind"] == "ACQUIRE":
            lots[e["side"]].append({"t": e["t"], "qty": e["qty"], "px": e["px"]})
        elif e["kind"] == "COMPLETE":
            # a COMPLETE retires `qty` of the HELD side against a fresh taker buy
            held = "up" if e["side"] == "down" else "down"
            need = e["qty"]
            comp_px = e.get("compl_px")
            basis = e["held_basis"]
            while need > 0 and lots[held]:
                lot = lots[held][0]
                take = lot["qty"] if lot["qty"] <= need else need
                # the scorer retires the held leg at its RUNNING AVERAGE basis;
                # use that so the EC total ties, and keep the lot price for the path
                pc = basis + comp_px
                recs.append({
                    "mechanism": "EC", "qty": take,
                    "leg1_t": lot["t"], "leg1_side": held, "leg1_px": lot["px"],
                    "leg1_avg_basis_used": basis,
                    "leg2_t": e["t"], "leg2_side": e["side"],
                    "counterpart_basis": comp_px,
                    "effective_pair_cost": pc,
                    "matched_pnl": take * (D(1) - pc),
                    "gap_s": S._epoch(e["t"]) - S._epoch(lot["t"]),
                })
                lot["qty"] -= take; need -= take
                if lot["qty"] <= 0:
                    lots[held].pop(0)
    # whatever remains pairs FIFO across sides: the oscillator's own matches
    while lots["up"] and lots["down"]:
        u, dn = lots["up"][0], lots["down"][0]
        take = u["qty"] if u["qty"] <= dn["qty"] else dn["qty"]
        pc = u["px"] + dn["px"]
        first, second = (u, dn) if u["t"] <= dn["t"] else (dn, u)
        recs.append({
            "mechanism": "PASSIVE", "qty": take,
            "leg1_t": first["t"], "leg1_side": "up" if first is u else "down",
            "leg1_px": first["px"], "leg2_t": second["t"],
            "leg2_side": "down" if first is u else "up",
            "counterpart_basis": second["px"],
            "effective_pair_cost": pc,
            "matched_pnl": take * (D(1) - pc),
            "gap_s": S._epoch(second["t"]) - S._epoch(first["t"]),
        })
        u["qty"] -= take; dn["qty"] -= take
        if u["qty"] <= 0: lots["up"].pop(0)
        if dn["qty"] <= 0: lots["down"].pop(0)
    return recs, a


def summarise(clip):
    by = load()
    out = {"clip": str(clip), "convention": "passive matches paired FIFO; diagnostic only, gates nothing",
           "per_window": [], "rolling": {}}
    for slug, w in USABLE.items():
        recs, a = decompose(by[slug], w, clip)
        ec = [r for r in recs if r["mechanism"] == "EC"]
        pa = [r for r in recs if r["mechanism"] == "PASSIVE"]
        g = lambda rs: sum(r["matched_pnl"] for r in rs)
        buckets = {n: D(0) for n, _, _ in BUCKETS}
        for r in recs:
            buckets[_bucket(r["effective_pair_cost"])] += r["qty"]
        out["per_window"].append({
            "window": slug[-5:], "settlement": w,
            "ec_qty": str(sum(r["qty"] for r in ec)), "ec_matched_gross": str(g(ec)),
            "passive_qty": str(sum(r["qty"] for r in pa)),
            "passive_matched_gross": str(g(pa)),
            "pair_cost_buckets_qty": {k: str(v) for k, v in buckets.items()},
            "median_gap_s": (sorted(r["gap_s"] for r in recs)[len(recs)//2]
                             if recs else None),
            "units": [{k: (str(v) if isinstance(v, D) else v) for k, v in r.items()}
                      for r in recs],
        })
    n = len(out["per_window"])
    for k in (5, 10, 15, 20, 25):
        if n < k:
            out["rolling"]["n=%d" % k] = "NOT YET AVAILABLE (%d of %d)" % (n, k)
            continue
        w = out["per_window"][:k]
        tot = {b: sum(D(x["pair_cost_buckets_qty"][b]) for x in w)
               for b, _, _ in BUCKETS}
        allq = sum(tot.values()) or D(1)
        out["rolling"]["n=%d" % k] = {
            "ec_matched_gross_per_window": str(
                (sum(D(x["ec_matched_gross"]) for x in w) / k).quantize(D("0.0001"))),
            "passive_matched_gross_per_window": str(
                (sum(D(x["passive_matched_gross"]) for x in w) / k).quantize(D("0.0001"))),
            "pair_cost_distribution_share": {
                b: str((tot[b] / allq).quantize(D("0.0001"))) for b, _, _ in BUCKETS},
            "pair_cost_qty": {b: str(tot[b]) for b, _, _ in BUCKETS},
        }
    return out


def main(argv):
    by = load()
    for clip in (D(25), D(50)):
        doc = summarise(clip)
        # Totals are reconciled against the scorer BEFORE the file is written.
        # The EC leg is priced at the scorer's own running-average basis, so it
        # ties exactly; only the passive leg carries the documented average-cost
        # vs FIFO convention band (MATCHED_001).
        for row, (slug, w) in zip(doc["per_window"], USABLE.items()):
            r = S.run_window(by[slug], clip=clip, theta=THETA, winner=w,
                             variant=VARIANT, fill_model=FILL)
            # AUTHORITATIVE mechanism split, taken from the scorer itself and
            # therefore reconciling EXACTLY by construction:
            #   EC      = `locked`/`gross_margin`, the completion economics
            #   PASSIVE = matched_pnl - locked, i.e. m*(1 - avg_up - avg_down)
            ec_auth = r["gross_margin"]
            pa_auth = r["matched_pnl"] - ec_auth
            row["ec_matched_gross_avg_cost"] = str(ec_auth)
            row["passive_matched_gross_avg_cost"] = str(pa_auth)
            row["scorer_matched_gross_avg_cost"] = str(r["matched_pnl"])
            # the EC leg of the per-unit path reconstruction is priced at the
            # same running-average basis, so it must tie the authoritative EC
            # figure to the cent; a mismatch is a defect, not a convention.
            assert abs(D(row["ec_matched_gross"]) - ec_auth) < D("1e-9"), (
                row["window"], row["ec_matched_gross"], ec_auth)
            # the PASSIVE leg does NOT tie: the path prices surviving lots FIFO
            # while the scorer prices them at running average. Totals are
            # invariant across matched+residual, the SPLIT is not (MATCHED_001).
            # The FIFO path is what makes an individual >$1 pair visible at all,
            # so it is kept for the bucket distribution and disclosed here.
            row["passive_matched_gross_fifo_path"] = row["passive_matched_gross"]
            row["passive_convention_delta"] = str(
                D(row["passive_matched_gross"]) - pa_auth)
            print("%s q=%d EC %.4f (ties) | PASSIVE avg %.4f fifo %.4f delta %+.4f"
                  % (row["window"], int(clip), ec_auth, pa_auth,
                     D(row["passive_matched_gross"]),
                     D(row["passive_matched_gross"]) - pa_auth))
        # rolling checkpoints on the AUTHORITATIVE (average-cost) split
        nw = len(doc["per_window"])
        for k in (5, 10, 15, 20, 25):
            key = "n=%d" % k
            if nw < k:
                continue
            w = doc["per_window"][:k]
            doc["rolling"][key]["ec_matched_gross_per_window_avg_cost"] = str(
                (sum(D(x["ec_matched_gross_avg_cost"]) for x in w) / k)
                .quantize(D("0.0001")))
            doc["rolling"][key]["passive_matched_gross_per_window_avg_cost"] = str(
                (sum(D(x["passive_matched_gross_avg_cost"]) for x in w) / k)
                .quantize(D("0.0001")))
            doc["rolling"][key]["matched_gross_per_window_total"] = str(
                (sum(D(x["scorer_matched_gross_avg_cost"]) for x in w) / k)
                .quantize(D("0.0001")))
        p = "analysis/pmus/pmus_matched_decomposition_q%d.json" % int(clip)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2); fh.write("\n")
        print("wrote %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
