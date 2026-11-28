"""Replay the SHADOW submit-time freshness gate over every historical order
for which the required evidence exists. Read-only; deploys nothing."""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from src import l5_submit_freshness as F          # noqa: E402

DUMPS = ("/root/.claude/projects/-home-user-kalshi-arb-bot/*/tool-results/"
         "mcp-Railway-get-logs-*.txt")
COHORT = os.path.join(os.path.dirname(__file__), "..",
                      "cross_shadow_live", "cohort.jsonl")


def orders_from_dumps(paths):
    """Every order row the probe logged, from session_end `orders[]`.

    Deduped by venue order id: the same session_end can appear in more than
    one dump, and an order counted twice is a measurement error.
    """
    seen, reject_rows = {}, []
    for p in paths:
        try:
            doc = json.load(open(p))
        except Exception:                                    # noqa: BLE001
            continue
        for e in doc.get("deploy") or []:
            msg = e.get("message") or ""
            if "PMUS_ORDER_REJECTED" in msg:
                i = msg.find("{")
                if i >= 0:
                    try:
                        reject_rows.append(json.loads(msg[i:]))
                    except Exception:                        # noqa: BLE001
                        pass
                continue
            m = re.search(r"PMUS_L5_CAMPAIGN (\{.*\})", msg, re.S)
            if not m:
                continue
            try:
                j = json.loads(m.group(1))
            except Exception:                                # noqa: BLE001
                continue
            if j.get("kind") != "session_end":
                continue
            for od in j.get("orders") or []:
                oid = od.get("order_id")
                if oid and oid not in seen:
                    o = dict(od)
                    o["_slug"] = j.get("slug")
                    o["_session_end"] = e.get("timestamp")
                    seen[oid] = o
    return list(seen.values()), reject_rows


def cohort_rows():
    if not os.path.exists(COHORT):
        return []
    out = []
    for line in open(COHORT):
        if line.strip():
            out.append(json.loads(line))
    return out


def show(rep, title):
    print("=" * 78)
    print(title)
    print("=" * 78)
    print(f"submissions seen        : {rep['submissions_seen']}")
    print(f"EVALUABLE (have age)    : {rep['evaluable']}")
    print(f"UNKNOWN (no age evid.)  : {rep['unknown_no_age_evidence']}"
          f"  ({rep['unknown_no_age_evidence']/max(1,rep['submissions_seen'])*100:.1f}%)")
    q = rep["age_quantiles"]
    if q:
        print(f"\nage at submission (LOWER BOUND), seconds:")
        print(f"  n {q['n']}  min {q['min']:.2f}  p10 {q['p10']:.2f}  "
              f"p25 {q['p25']:.2f}  p50 {q['p50']:.2f}  p75 {q['p75']:.2f}  "
              f"p90 {q['p90']:.2f}  max {q['max']:.2f}")
    for rel, qq in (rep["age_quantiles_by_relation"] or {}).items():
        if qq:
            print(f"    {rel:9s} n {qq['n']:4d}  p50 {qq['p50']:6.2f}  "
                  f"p90 {qq['p90']:6.2f}  max {qq['max']:6.2f}")
    print("\nthreshold sweep (BLOCK = would refresh+recompute before sending)")
    hdr = (f"{'thr':>5} {'pass':>6} {'block':>6} {'blk%':>6} "
           f"{'prevented':>10} {'suppr.fills':>12} {'blk_nofill':>11} "
           f"{'passed_fills':>13}")
    print(hdr)
    print("-" * len(hdr))
    for t, v in rep["thresholds"].items():
        b = v["overall"]
        ev = b["pass"] + b["block"]
        print(f"{t:>5} {b['pass']:>6} {b['block']:>6} "
              f"{(b['block']/ev*100 if ev else 0):>5.1f}% "
              f"{b['prevented']:>10} {b['suppressed_fills']:>12} "
              f"{b['blocked_no_fill']:>11} {b['passed_fills']:>13}")
    print("\nper-relation at the DECLARED 5.0 s invariant:")
    v = rep["thresholds"].get("5.0")
    if v:
        for rel, b in v["by_relation"].items():
            ev = b["pass"] + b["block"]
            if not ev and not b["unknown"]:
                continue
            print(f"  {rel:9s} evaluable {ev:4d}  block {b['block']:4d} "
                  f"({(b['block']/ev*100 if ev else 0):5.1f}%)  "
                  f"prevented {b['prevented']:3d}  suppressed_fills "
                  f"{b['suppressed_fills']:3d}  blocked_no_fill "
                  f"{b['blocked_no_fill']:4d}  UNKNOWN {b['unknown']:4d}")
        for key, label, denom in (
                ("suppressed_fill_pair_costs", "pair cost of SUPPRESSED FILLS",
                 sum(b["suppressed_fills"] for b in v["by_relation"].values())),
                ("prevented_pair_costs", "pair cost of PREVENTED rejections",
                 sum(b["prevented"] for b in v["by_relation"].values()))):
            qq = v.get(key)
            if qq:
                print(f"  {label}: n {qq['n']}/{denom} covered  "
                      f"p25 {qq['p25']:.4f} p50 {qq['p50']:.4f} "
                      f"p75 {qq['p75']:.4f}")
            else:
                print(f"  {label}: UNKNOWN -- 0 of {denom} carry "
                      f"econ_pair_cost (coverage gap, not absence)")
    print()


def main():
    paths = sorted(glob.glob(DUMPS))
    orders, rejects = orders_from_dumps(paths)
    n_rej = sum(1 for o in orders if F.outcome_of(o) == "rejected")
    n_fill = sum(1 for o in orders if F.outcome_of(o) == "filled")
    print(f"dumps read: {len(paths)}   distinct order rows: {len(orders)}")
    print(f"  venue-rejected: {n_rej}    filled: {n_fill}    "
          f"no-fill cancelled: {len(orders) - n_rej - n_fill}\n")
    show(F.replay(orders), "A. ALL LOGGED ORDERS (open + extend + complete)")
    show(F.replay(cohort_rows()),
         "B. DURABLE COHORT (COMPLETE-stage attempts only)")
    d = F.dedupe_rejections(rejects)
    print("=" * 78)
    print("C. PMUS_ORDER_REJECTED TELEMETRY DEDUPE (read-side, by order id)")
    print("=" * 78)
    print(f"  raw rows                {d['raw_rows']}")
    print(f"  distinct order ids      {d['distinct_order_ids']}")
    print(f"  duplicate rows dropped  {d['duplicate_rows_dropped']}")
    print(f"  inflation factor        {d['inflation_factor']}x")
    print(f"  emissions per order     {d['emissions_per_order']}")
    print(f"  {d['labels']}")


if __name__ == "__main__":
    main()
