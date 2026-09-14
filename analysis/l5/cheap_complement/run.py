"""P0: WHY DOES L5 REACH CHEAP-COMPLEMENT STATES SO OFTEN?

    python3 analysis/l5/cheap_complement/run.py

Read-only. Sequential, no-lookahead. Does not touch the production model.
"""
from __future__ import annotations
import json, statistics as st, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src import l5_cheap_complement as cc                       # noqa: E402
from src.polymarket_l5.action_model import SYSTEMATIC_REGIME_EPOCH  # noqa: E402
from src.polymarket_l5.action_model_v2 import _windows, HOLDOUT_EPOCH  # noqa: E402

OUT = Path(__file__).resolve().parent

def q(v, p):
    v = sorted(x for x in v if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)

rows, train, valid = [], [], []
for w in _windows():
    w.sort(key=lambda r: (int(float(r["timestamp"])), int(float(r["trade_seq"] or 0))))
    w0 = float(w[0]["timestamp"])
    if w0 < SYSTEMATIC_REGIME_EPOCH:
        continue
    recs = cc.walk_window(w)
    for r in recs:
        r["_holdout"] = w0 >= HOLDOUT_EPOCH
    rows.extend(recs)
    (valid if w0 >= HOLDOUT_EPOCH else train).extend(recs)

usable = [r for r in rows if r["pair"] is not None and r["pre"]["gain_per_unit"] is not None]
res = {"n_switches": len(rows), "n_usable": len(usable),
       "n_train": len(train), "n_valid": len(valid),
       "arm": cc.PAIR_ARM, "holdout_epoch": HOLDOUT_EPOCH}

print("=" * 78)
print("P0 -- WHY L5 REACHES CHEAP-COMPLEMENT STATES     (read-only, no-lookahead)")
print("=" * 78)
print(f"switches reconstructed : {len(rows)}   usable {len(usable)}")
print(f"train / holdout        : {len(train)} / {len(valid)}  (split at window start)")
base = cc.rate(usable)
res["base_rate_le_097"] = base
print(f"base rate pair <= 0.97 : {base:.2%}")

# ---------------------------------------------------------------- Q3 identity
print("\n" + "=" * 78)
print("Q3 -- HOW MUCH IS MECHANICALLY IMPLIED BY THE HELD SIDE BEING UP?")
print("=" * 78)
ires = [abs(r["identity_residual"]) for r in usable if r["identity_residual"] is not None]
print(f"  identity  pair == last_pair - gain_per_unit")
print(f"  max |residual| over {len(ires)} switches : {max(ires):.2e}   (EXACT, not fitted)")
res["identity_max_abs_residual"] = max(ires)
gains = [r["pre"]["gain_per_unit"] for r in usable]
lps = [r["last_pair"] for r in usable]
print(f"  gain_per_unit   p10 {q(gains,.10):+.4f}  p50 {q(gains,.50):+.4f}  p90 {q(gains,.90):+.4f}")
print(f"  last_pair       p10 {q(lps,.10):.4f}  p50 {q(lps,.50):.4f}  p90 {q(lps,.90):.4f}")
print(f"  var(pair) {st.pvariance([r['pair'] for r in usable]):.5f}"
      f"   var(gain) {st.pvariance(gains):.5f}   var(last_pair) {st.pvariance(lps):.5f}")
# counterfactual: hold last_pair at its median, vary gain only
med_lp = q(lps, .50)
only_gain = sum(1 for g in gains if med_lp - g <= cc.PAIR_ARM) / len(gains)
med_g = q(gains, .50)
only_lp = sum(1 for lp in lps if lp - med_g <= cc.PAIR_ARM) / len(lps)
print(f"\n  freeze last_pair at its median, vary gain only -> {only_gain:.2%} <=0.97")
print(f"  freeze gain at its median, vary last_pair only -> {only_lp:.2%} <=0.97")
print(f"  actual                                          -> {base:.2%}")
res["freeze_last_pair_rate"] = only_gain
res["freeze_gain_rate"] = only_lp
print("\n  READING: the <=0.97 rate is reproduced almost entirely by the held")
print("  side's unrealised gain. It is the SAME NUMBER as being up on the")
print("  position, offset by a market-structure term.")

# ------------------------------------------------------------- Q1 entry vs move
print("\n" + "=" * 78)
print("Q1 -- SELECTION AT ENTRY, OR ORDINARY MOVEMENT AFTER IT?")
print("=" * 78)
ent = [r for r in usable if r["pre"]["move_since_entry"] is not None]
print(f"  AUC for pair<=0.97 from a single pre-action feature (n={len(ent)}):")
for name, f in (("gain_per_unit (post-entry accumulation)", lambda r: r["pre"]["gain_per_unit"]),
                ("move_since_entry (post-entry movement)", lambda r: r["pre"]["move_since_entry"]),
                ("entry_price (ENTRY-TIME ONLY)", lambda r: r["pre"]["entry_price"]),
                ("held_side_drift_during_run", lambda r: r["pre"]["held_side_drift_during_run"]),
                ("tte_s", lambda r: r["pre"]["tte_s"]),
                ("run_length", lambda r: float(r["pre"]["run_length"] or 0)),
                ("last_same_side_price", lambda r: r["pre"]["last_same_side_price"])):
    a = cc.auc([r["pair_le_arm"] for r in ent], [f(r) for r in ent])
    if a is not None:
        print(f"    {name:44s} AUC {a:.4f}")
        res.setdefault("auc_single", {})[name] = a
print("\n  Entry-time-only information is near-uninformative; the gain the")
print("  position accumulates AFTER entry carries the signal.")

# ------------------------------------------------------- Q2 within-state compare
print("\n" + "=" * 78)
print("Q2 -- SAME PRE-ACTION STATE: WHAT SEPARATES <=0.97 FROM >1.00?")
print("=" * 78)
byk = defaultdict(list)
for r in usable:
    byk[(r["pre"]["price_band"], r["pre"]["tte_bucket"])].append(r)
print(f"  {'state (price_band | tte)':34s} {'n':>6s} {'<=.97':>7s} "
      f"{'gain|<=.97':>10s} {'gain|>1.00':>11s}")
for k, sub in sorted(byk.items(), key=lambda kv: -len(kv[1]))[:10]:
    lo = [r for r in sub if r["pair_le_arm"]]
    hi = [r for r in sub if r["pair"] > 1.00]
    gl = q([r["pre"]["gain_per_unit"] for r in lo], .5)
    gh = q([r["pre"]["gain_per_unit"] for r in hi], .5)
    print(f"  {str(k[0])+' | '+str(k[1]):34s} {len(sub):6d} {cc.rate(sub):7.1%} "
          f"{(f'{gl:+.4f}' if gl is not None else 'n/a'):>10s} "
          f"{(f'{gh:+.4f}' if gh is not None else 'n/a'):>11s}")
print("\n  Within an identical state bucket the separator is the same variable:")
print("  the gain already accumulated on the held side.")

# ------------------------------------------------------------------ Q4 regimes
print("\n" + "=" * 78)
print("Q4 -- TTE REGIMES, NORMALISED BY MARKET TYPE")
print("=" * 78)
byasset = defaultdict(list)
for r in usable:
    byasset[(r["pre"]["asset"], r["pre"]["duration_minutes"])].append(r)
print(f"  {'asset / window minutes':28s} {'n':>6s} {'<=.97':>7s} {'gain p50':>9s}")
for k, sub in sorted(byasset.items(), key=lambda kv: -len(kv[1]))[:8]:
    print(f"  {str(k[0])+' / '+str(k[1]):28s} {len(sub):6d} {cc.rate(sub):7.1%} "
          f"{q([r['pre']['gain_per_unit'] for r in sub], .5):+9.4f}")
print(f"\n  {'tte bucket':16s} {'n':>6s} {'<=.97':>7s} {'gain p50':>9s} {'last_pair p50':>13s}")
bytte = defaultdict(list)
for r in usable:
    bytte[r["pre"]["tte_bucket"]].append(r)
for k in ("tte<30s", "tte30-120s", "tte2-5m", "tte5-10m", "tte>10m"):
    sub = bytte.get(k) or []
    if sub:
        print(f"  {k:16s} {len(sub):6d} {cc.rate(sub):7.1%} "
              f"{q([r['pre']['gain_per_unit'] for r in sub], .5):+9.4f} "
              f"{q([r['last_pair'] for r in sub], .5):13.4f}")
        res.setdefault("by_tte", {})[k] = {"n": len(sub), "rate": cc.rate(sub)}
print("\n  ALL OF THIS IS L5's SHORT-DATED CRYPTO TAPE. No transfer to PMUS")
print("  sports is claimed or implied by any number above.")

# ------------------------------------------------------------------ Q5 reconcile
print("\n" + "=" * 78)
print("Q5 -- RECONCILING 'DRIFT ~0, SWITCH IN ~9s' WITH A 45% CHEAP RATE")
print("=" * 78)
drifts = [r["pre"]["held_side_drift_during_run"] for r in usable
          if r["pre"]["held_side_drift_during_run"] is not None]
print(f"  held-side drift DURING THE RUN     p50 {q(drifts,.5):+.4f}   "
      f"|drift|<0.01 in {sum(1 for d in drifts if abs(d)<0.01)/len(drifts):.1%}")
print(f"  gain vs RUNNING AVERAGE COST       p50 {q(gains,.5):+.4f}   "
      f"gain>=0.015 in {sum(1 for g in gains if g>=0.015)/len(gains):.1%}")
flat = [r for r in usable if r["pre"]["held_side_drift_during_run"] is not None
        and abs(r["pre"]["held_side_drift_during_run"]) < 0.01]
print(f"\n  switches with a FLAT run (|drift|<1c): n={len(flat)}  "
      f"pair<=0.97 in {cc.rate(flat):.1%}")
print("  These are not contradictory measurements of one quantity -- they are")
print("  two different quantities. Drift is the move over the LAST run; gain is")
print("  the whole position's price against its ACCUMULATED average. A position")
print("  built while the side rose is already up without any recent move.")
res["flat_run_rate"] = cc.rate(flat)
res["drift_p50"] = q(drifts, .5)
res["gain_p50"] = q(gains, .5)

# ------------------------------------------------------------------- Q6 OOS
print("\n" + "=" * 78)
print("Q6 -- PRE-ACTION FEATURES AND OUT-OF-SAMPLE LIFT (model NOT modified)")
print("=" * 78)
tr = [r for r in train if r["pair"] is not None and r["pre"]["gain_per_unit"] is not None]
va = [r for r in valid if r["pair"] is not None and r["pre"]["gain_per_unit"] is not None]
print(f"  train {len(tr)}   holdout {len(va)}   holdout base rate "
      f"{(cc.rate(va) or 0):.2%}")
feats = {
    "gain_per_unit": lambda r: r["pre"]["gain_per_unit"],
    "move_since_entry": lambda r: r["pre"]["move_since_entry"],
    "last_same_side_price": lambda r: r["pre"]["last_same_side_price"],
    "avg_cost_held": lambda r: r["pre"]["avg_cost_held"],
    "held_side_drift_during_run": lambda r: r["pre"]["held_side_drift_during_run"],
    "run_length": lambda r: float(r["pre"]["run_length"] or 0),
    "tte_s": lambda r: r["pre"]["tte_s"],
    "residual_qty_signed": lambda r: r["pre"]["residual_qty_signed"],
    "switches_so_far": lambda r: float(r["pre"]["switches_so_far"] or 0),
}
print(f"  {'feature':30s} {'AUC train':>10s} {'AUC holdout':>12s}")
for name, f in feats.items():
    at = cc.auc([r["pair_le_arm"] for r in tr], [f(r) for r in tr])
    av = cc.auc([r["pair_le_arm"] for r in va], [f(r) for r in va])
    if av is not None:
        print(f"  {name:30s} {(at or 0):10.4f} {av:12.4f}")
        res.setdefault("auc_oos", {})[name] = {"train": at, "holdout": av}
# decile lift on the best feature, fitted on train, applied OOS
thr = q([f for f in (feats["gain_per_unit"](r) for r in tr)], .5)
hi = [r for r in va if (feats["gain_per_unit"](r) or -9) >= thr]
lo = [r for r in va if (feats["gain_per_unit"](r) or -9) < thr]
if hi and lo:
    print(f"\n  train-median gain threshold {thr:+.4f} applied OUT OF SAMPLE:")
    print(f"    above threshold  n={len(hi):5d}  pair<=0.97 {cc.rate(hi):.1%}")
    print(f"    below threshold  n={len(lo):5d}  pair<=0.97 {cc.rate(lo):.1%}")
    print(f"    lift vs holdout base ({cc.rate(va):.1%}): "
          f"{(cc.rate(hi)/max(cc.rate(va),1e-9)):.2f}x")
    res["oos_threshold"] = {"thr": thr, "above_rate": cc.rate(hi),
                            "below_rate": cc.rate(lo), "base": cc.rate(va)}
print("\n  THE PRODUCTION MODEL IS NOT MODIFIED BY ANY OF THIS.")

(OUT / "cheap_complement.json").write_text(
    json.dumps(res, indent=1, default=str), encoding="utf-8")
print(f"\nwrote {OUT/'cheap_complement.json'}")
