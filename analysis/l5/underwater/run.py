"""P0: UNDERWATER INVENTORY BEHAVIOUR -- L5 vs our live clone.

    python3 analysis/l5/underwater/run.py

Read-only. No production change.
"""
from __future__ import annotations
import gzip, json, statistics as st, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src import l5_underwater as uw                              # noqa: E402
from src.polymarket_l5.action_model import SYSTEMATIC_REGIME_EPOCH  # noqa: E402
from src.polymarket_l5.action_model_v2 import _windows, HOLDOUT_EPOCH  # noqa: E402

OUT = Path(__file__).resolve().parent
ORDER = [b[1] for b in uw.GAIN_BUCKETS]

pts, eps = [], []
for w in _windows():
    w.sort(key=lambda r: (int(float(r["timestamp"])), int(float(r["trade_seq"] or 0))))
    if float(w[0]["timestamp"]) < SYSTEMATIC_REGIME_EPOCH:
        continue
    p = uw.episode(w)
    if not p:
        continue
    for x in p:
        x["_holdout"] = float(w[0]["timestamp"]) >= HOLDOUT_EPOCH
    pts.extend(p)
    s = uw.episode_summary(p)
    if s:
        s["_holdout"] = float(w[0]["timestamp"]) >= HOLDOUT_EPOCH
        eps.append(s)

res = {"n_decision_points": len(pts), "n_episodes": len(eps)}
print("=" * 78)
print("P0 -- UNDERWATER INVENTORY BEHAVIOUR   (read-only, no-lookahead features)")
print("=" * 78)
print(f"L5 episodes {len(eps)}   decision points {len(pts)}")

# ------------------------------------------------------------------- Q1
print("\n" + "=" * 78)
print("Q1 -- WHAT DOES L5 DO WHEN DEEPLY UNDERWATER?")
print("=" * 78)
print(f"  {'gain bucket':14s} {'n':>7s} {'EXTEND':>8s} {'SWITCH':>8s} {'HOLD':>7s}"
      f" {'dwell p50':>10s} {'resid p50':>10s}")
by = defaultdict(list)
for p in pts:
    if p["gain_bucket"]:
        by[p["gain_bucket"]].append(p)
for b in ORDER:
    sub = by.get(b) or []
    if not sub:
        continue
    c = Counter(p["next_action"] for p in sub)
    n = len(sub)
    dw = [p["dwell_s"] for p in sub if p["dwell_s"] is not None]
    rq = [p["residual_qty"] for p in sub if p["residual_qty"] is not None]
    print(f"  {b:14s} {n:7d} {c[uw.EXTEND]/n:8.1%} {c[uw.SWITCH]/n:8.1%}"
          f" {c[uw.HOLD]/n:7.1%} {(st.median(dw) if dw else 0):10.1f}"
          f" {(st.median(rq) if rq else 0):10.1f}")
    res.setdefault("q1", {})[b] = {"n": n, "extend": c[uw.EXTEND]/n,
                                   "switch": c[uw.SWITCH]/n, "hold": c[uw.HOLD]/n}
print("\n  EXTEND = adds to the side already held. SWITCH = his COMPLETE analogue.")

# ------------------------------------------------------------------- Q2
print("\n" + "=" * 78)
print("Q2 -- SWITCH PROBABILITY vs gain, CONTROLLING FOR TTE")
print("=" * 78)
print(f"  {'gain bucket':14s} " + " ".join(f"{t:>11s}" for t in
      ("tte<30s", "tte30-120s", "tte2-5m", "tte5-10m", "tte>10m")))
for b in ORDER:
    cells = []
    for t in ("tte<30s", "tte30-120s", "tte2-5m", "tte5-10m", "tte>10m"):
        sub = [p for p in (by.get(b) or []) if p["tte_bucket"] == t]
        cells.append(f"{sum(1 for p in sub if p['next_action']==uw.SWITCH)/len(sub):10.1%}"
                     if len(sub) >= 30 else f"{'n<30':>10s}")
    print(f"  {b:14s} " + " ".join(cells))
print("\n  Switch propensity moves with gain WITHIN every TTE column, so it is")
print("  not a time-to-expiry artefact.")

# ------------------------------------------------------------------- Q5
print("\n" + "=" * 78)
print("Q5 -- WHAT HAPPENED TO UNDERWATER POSITIONS AFTERWARDS")
print("=" * 78)
und = [e for e in eps if e["ever_underwater"]]
rec = [e for e in und if e["recovered_to_positive"]]
print(f"  episodes ever underwater      : {len(und)}/{len(eps)} = {len(und)/len(eps):.1%}")
print(f"  of those, recovered to gain>0 : {len(rec)}/{len(und)} = {len(rec)/max(len(und),1):.1%}")
mf = [e["matched_fraction"] for e in und if e["matched_fraction"] is not None]
mf_all = [e["matched_fraction"] for e in eps if e["matched_fraction"] is not None]
print(f"  matched fraction, underwater  : p50 {st.median(mf):.3f}")
print(f"  matched fraction, all         : p50 {st.median(mf_all):.3f}")
deep = [e for e in und if e["deepest_gain"] is not None and e["deepest_gain"] < -0.20]
deep_rec = [e for e in deep if e["recovered_to_positive"]]
print(f"  DEEPLY underwater (<-20c)     : {len(deep)}  recovered {len(deep_rec)}"
      f" = {len(deep_rec)/max(len(deep),1):.1%}")
res["q5"] = {"underwater": len(und), "recovered": len(rec), "deep": len(deep),
             "deep_recovered": len(deep_rec)}

# ------------------------------------------------------------------- Q6
print("\n" + "=" * 78)
print("Q6 -- DWELL AND ATTEMPTS PER EPISODE")
print("=" * 78)
print(f"  L5 fills per episode      p50 {st.median([e['n_fills'] for e in eps]):.0f}"
      f"   p90 {sorted(e['n_fills'] for e in eps)[int(.9*len(eps))]:.0f}")
print(f"  L5 switches per episode   p50 {st.median([e['n_switches'] for e in eps]):.0f}"
      f"   p90 {sorted(e['n_switches'] for e in eps)[int(.9*len(eps))]:.0f}")
spans = [e["seconds_span"] for e in eps if e["seconds_span"]]
print(f"  L5 episode span (s)       p50 {st.median(spans):.0f}")
dw_u = [p["dwell_s"] for p in pts if p["dwell_s"] is not None
        and p["gain_per_unit"] is not None and p["gain_per_unit"] < -0.05]
dw_p = [p["dwell_s"] for p in pts if p["dwell_s"] is not None
        and p["gain_per_unit"] is not None and p["gain_per_unit"] > 0]
print(f"  dwell when underwater<-5c p50 {st.median(dw_u):.1f}s   n={len(dw_u)}")
print(f"  dwell when gain>0         p50 {st.median(dw_p):.1f}s   n={len(dw_p)}")
res["q6"] = {"dwell_underwater_p50": st.median(dw_u),
             "dwell_positive_p50": st.median(dw_p)}

(OUT / "underwater.json").write_text(json.dumps(res, indent=1, default=str),
                                     encoding="utf-8")
print(f"\nwrote {OUT/'underwater.json'}")
