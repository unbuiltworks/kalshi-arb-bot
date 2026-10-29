"""q-NORMALIZED L5 inventory behaviour, from the EXISTING canonical sequence.

Unit = the window's OWN median BUY quantity (its action unit), exactly as
l5_cap_override_study defines it. No new pipeline: same file, same loader,
same relation rules (BUY from flat = OPEN, BUY on the residual side = EXTEND,
BUY on the other side = COMPLETE; SELL rows skipped -- there are 2).
"""
import json
from collections import Counter, defaultdict
from statistics import median

from src.l5_concurrency_study import load_rows

rows = load_rows()
by_w = defaultdict(list)
for r in rows:
    by_w[r["window_id"]].append(r)

def f(x):
    try: return float(x)
    except Exception: return None

peak_units, run_lengths = [], []
max_same_side_before_opposite = []
# P(opposite-side next action | same-side inventory is exactly k units)
opp_after = Counter(); tot_after = Counter()
extends_beyond = Counter(); extends_total = 0
windows = 0

for wid, rs in by_w.items():
    rs.sort(key=lambda r: int(r["trade_seq"]))
    buys = [r for r in rs if str(r["side"]).upper() == "BUY"]
    if not buys: continue
    qs = [f(r["quantity"]) for r in buys]
    qs = [q for q in qs if q and q > 0]
    if not qs: continue
    unit = median(qs)
    if unit <= 0: continue
    windows += 1

    up = dn = 0.0
    run_leg, run_len = None, 0
    peak = 0.0
    seen_same_before_opp = 0.0
    for r in buys:
        q = f(r["quantity"]) or 0.0
        leg = "UP" if str(r["outcome"]).strip().lower() in ("up", "yes") else "DN"
        resid_before = (up - dn) / unit          # signed, in units
        held = abs(resid_before)
        same = (resid_before > 0 and leg == "UP") or (resid_before < 0 and leg == "DN")
        opposite = (resid_before > 0 and leg == "DN") or (resid_before < 0 and leg == "UP")

        if held > 0:
            k = min(5, int(held + 1e-9)) if held >= 1 else 0
            if k >= 1:
                tot_after[k] += 1
                if opposite: opp_after[k] += 1
        if same:
            extends_total += 1
            for thr in (1, 2, 3, 4, 5):
                if held >= thr: extends_beyond[thr] += 1
        if opposite and seen_same_before_opp:
            max_same_side_before_opposite.append(seen_same_before_opp)
            seen_same_before_opp = 0.0

        if leg == run_leg: run_len += 1
        else:
            if run_len: run_lengths.append(run_len)
            run_leg, run_len = leg, 1

        if leg == "UP": up += q
        else: dn += q
        resid_after = abs(up - dn) / unit
        peak = max(peak, resid_after)
        seen_same_before_opp = max(seen_same_before_opp, resid_after)
    if run_len: run_lengths.append(run_len)
    peak_units.append(peak)

def pct(xs, p):
    xs = sorted(xs); 
    return round(xs[min(len(xs) - 1, int(p / 100 * len(xs)))], 4) if xs else None

def dist(xs):
    return {"n": len(xs), "p10": pct(xs,10), "p25": pct(xs,25), "p50": pct(xs,50),
            "p75": pct(xs,75), "p90": pct(xs,90), "p95": pct(xs,95),
            "max": round(max(xs),4) if xs else None,
            "mean": round(sum(xs)/len(xs),4) if xs else None}

out = {
  "windows": windows,
  "peak_directional_imbalance_per_window_units": dist(peak_units),
  "coverage_of_a_cap": {
     str(k): round(sum(1 for v in peak_units if v <= k) / len(peak_units), 4)
     for k in (1,2,3,4,5,6,8,10)},
  "same_side_run_length_actions": dist([float(x) for x in run_lengths]),
  "run_length_hist": {str(k): v for k, v in sorted(Counter(run_lengths).items())[:12]},
  "max_same_side_units_before_an_opposite_action": dist(max_same_side_before_opposite),
  "P_opposite_next_given_held_units": {
     str(k): round(opp_after[k] / tot_after[k], 4) for k in sorted(tot_after) if tot_after[k]},
  "n_at_each_held_level": {str(k): tot_after[k] for k in sorted(tot_after)},
  "extends_while_already_at_or_beyond_units": {
     str(k): {"n": extends_beyond[k],
              "share_of_all_same_side_actions": round(extends_beyond[k]/extends_total, 4)}
     for k in (1,2,3,4,5)},
  "same_side_actions_total": extends_total,
}
print(json.dumps(out, indent=2))
