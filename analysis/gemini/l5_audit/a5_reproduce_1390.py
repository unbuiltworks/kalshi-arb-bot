"""A5 -- RECOVER AND EXPLAIN THE 300 / 1,390 RESULT.

Not a new experiment. This runs the ACTUAL historical code
(analysis/l5/gemini_trajectory/continuous_inventory.py, unmodified) over the
ACTUAL historical canonical dataset
(analysis/l5/gemini_trajectory/gemini_5m_canonical.jsonl.gz) to find out
where 300/1,390 came from and whether it reproduces.

The canonical dataset matches the described cohort exactly: 182,119 rows,
735 prints. Earlier in this lane I assumed the 300/1,390 artifacts were
stranded on the Railway volume /state/gemini-5m. The canonical EXTRACT is in
git, so the claim is testable here.

Window shape required by continuous_inventory (from its own book_rows /
print_rows and the test fixtures):
  {"market", "expiry_t", "window_minutes", "one_sided_obs",
   "series":     [(tte_s, bid, ask, bid_sz, ask_sz), ...],
   "print_rows": [(tte_s, price, qty, buyer_is_maker), ...]}
Both series and print_rows are keyed on TIME-TO-EXPIRY, not wall time.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

CANON = Path("analysis/l5/gemini_trajectory/gemini_5m_canonical.jsonl.gz")


def build_windows(asset_prefix=None, require_inwindow=False):
    rows = defaultdict(lambda: {"bbo": [], "prints": [], "expiry": None,
                                "asset": None, "one_sided": 0})
    n = 0
    with gzip.open(CANON, "rt") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            n += 1
            m = r.get("market")
            if not m:
                continue
            if asset_prefix and asset_prefix not in m:
                continue
            w = rows[m]
            if w["expiry"] is None and r.get("expiry_t"):
                w["expiry"] = float(r["expiry_t"])
            w["asset"] = r.get("asset")
            tte = r.get("tte_s")
            if tte is None:
                continue
            if r.get("kind") == "bbo":
                b, a = r.get("bid"), r.get("ask")
                if b is None or a is None:
                    w["one_sided"] += 1
                    continue
                w["bbo"].append((float(tte), float(b), float(a),
                                 float(r.get("bid_sz") or 0.0),
                                 float(r.get("ask_sz") or 0.0)))
            elif r.get("kind") == "print":
                try:
                    w["prints"].append((float(tte), float(r["price"]),
                                        float(r["qty"]),
                                        bool(r.get("buyer_is_maker"))))
                except Exception:
                    pass
    out = []
    for m, w in rows.items():
        if w["expiry"] is None:
            continue
        dur = 300.0
        ser = sorted(w["bbo"], key=lambda x: -x[0])
        prs = sorted(w["prints"], key=lambda x: -x[0])
        if require_inwindow:
            ser = [s for s in ser if 0.0 <= s[0] <= dur]
            prs = [p for p in prs if 0.0 <= p[0] <= dur]
        out.append({"market": m, "expiry_t": w["expiry"],
                    "window_minutes": 5, "one_sided_obs": w["one_sided"],
                    "series": ser, "print_rows": prs,
                    "asset": w["asset"]})
    return out, n


def main():
    if not CANON.exists():
        print(f"canonical dataset not found: {CANON}")
        return 1
    print("=" * 84)
    print("A5  RECOVERING 300 / 1,390 FROM THE HISTORICAL CODE + DATA")
    print("=" * 84)
    allw, nrows = build_windows()
    tot_prints = sum(len(w["print_rows"]) for w in allw)
    tot_bbo = sum(len(w["series"]) for w in allw)
    print(f"\ncanonical rows read        : {nrows}")
    print(f"markets (windows) rebuilt  : {len(allw)}")
    print(f"two-sided bbo observations : {tot_bbo}")
    print(f"prints                     : {tot_prints}")
    print(f"windows with >=1 print     : {sum(1 for w in allw if w['print_rows'])}")
    btc = [w for w in allw if "BTC" in w["market"]]
    print(f"BTC windows                : {len(btc)}  "
          f"(with >=1 print: {sum(1 for w in btc if w['print_rows'])})")

    try:
        from analysis.l5.gemini_trajectory import continuous_inventory as CI
    except Exception as exc:
        print(f"\nCANNOT IMPORT continuous_inventory: {type(exc).__name__}: {exc}")
        return 2

    # Cohorts tried, in order of how the historical text describes the run.
    cohorts = [
        ("ALL windows, full tape", allw),
        ("BTC only, full tape", btc),
        ("windows with >=1 print", [w for w in allw if w["print_rows"]]),
        ("BTC, >=1 print", [w for w in btc if w["print_rows"]]),
    ]
    for label, ws in cohorts:
        if not ws:
            continue
        print("\n" + "-" * 84)
        print(f"COHORT: {label}   (n={len(ws)})")
        try:
            res = CI.run(ws)
        except Exception as exc:
            print(f"  run() FAILED: {type(exc).__name__}: {exc}")
            continue
        print(f"  model={res.get('model')}  policy={res.get('policy_source')}")
        hdr = (f"  {'tier':11s} {'usable':>7s} {'matched':>8s} {'resid':>7s} "
               f"{'no_fill':>8s} {'win_fill':>9s} {'FILLS':>7s} {'compl':>7s} "
               f"{'requotes':>9s}")
        print(hdr)
        for tier, s in res["tiers"].items():
            print(f"  {tier:11s} {s['usable']:7d} {s['matched_campaigns']:8d} "
                  f"{s['residual_hold']:7d} {s['no_fill']:8d} "
                  f"{s['windows_with_any_fill']:9d} {s['fills_total']:7d} "
                  f"{s['complement_fills']:7d} {s['requotes_total']:9d}")
            # flag any (300, 1390)-shaped pair
            vals = {"fills_total": s["fills_total"],
                    "requotes_total": s["requotes_total"],
                    "usable": s["usable"],
                    "windows_with_any_fill": s["windows_with_any_fill"]}
            for k, v in vals.items():
                if v in (300, 1390):
                    print(f"      *** {k} == {v}  (matches a 300/1,390 component)")
    print("\n" + "=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
