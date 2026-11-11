"""P8-B: does WAIT survive on corrected, deduplicated data? DEV only.

    python3 analysis/gemini/p8b_wait.py <raw_bulk.json>

Every "decision moment" is an instant at which we held an unpaired leg and the
tape offered its complement. The forward tape is read ONLY to score what a
WAIT rule would have been worth after the fact; no decision below consults it.
WAIT pays the settlement outcome in full whenever its target is never reached.
"""
import os
import sys
from collections import defaultdict
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import p8ab_run as A                                           # noqa: E402
from src.gemini_execution import ev_replay as R

by_dur = defaultdict(list)


def _load(path):
    rows, rep = A.DS.load([path])
    rep.pop("quarantined_detail", None)
    print(rep)
    for c in rows:
        by_dur[c["duration"]].append(c)

CAND = A.CAND

def episodes(dur, split):
    a,c = CAND[dur]
    out=[]
    for k in by_dur[dur]:
        if not k.get("prints") or A.split_of(k["day"])!=split: continue
        r=R.replay(k, policy="pair", acq_max=a, comp_max=c, max_legs=12,
                   max_residual=4, diagnostics=True)
        if r.get("verdict")!="SETTLED": continue
        won=r["winning_leg"]
        for d in r.get("completion_diagnostics") or []:
            d=dict(d); d["won"]=won; d["symbol"]=r["symbol"]
            out.append(d)
    return out

if __name__ == "__main__":
  _load(sys.argv[1])
  for dur in ("5m","15m"):
      eps=episodes(dur,"dev")
      print(f"\n{'='*104}\nP8-B  BTC{dur}  DEV  --  {len(eps)} moments holding an unpaired "
            f"leg with a complement on offer\n{'='*104}")
      if not eps: continue
      # ---- 1. does a cheaper complement appear later at all?
      n_any=n_cheaper=0
      gains=[]
      for d in eps:
          q=F(d["complement_offered"]); fut=[F(x) for x in d["future_complement_prices"]]
          if fut: n_any+=1
          if fut and min(fut)<q:
              n_cheaper+=1; gains.append(q-min(fut))
      print(f"  1. a later complement print exists at all      : {n_any}/{len(eps)} "
            f"({n_any/len(eps):.1%})")
      print(f"     a STRICTLY CHEAPER one exists (hindsight)   : {n_cheaper}/{len(eps)} "
            f"({n_cheaper/len(eps):.1%})")
      if gains:
          gs=sorted(float(g) for g in gains)
          print(f"     hindsight best improvement, median {gs[len(gs)//2]:.3f}  "
                f"mean {sum(gs)/len(gs):.3f}  max {gs[-1]:.3f}")
      # ---- 2/3. a PRE-COMMITTED passive target, reachability, then capture
      print(f"\n  2/3. a passive order pre-committed at q-delta, and what it is worth.")
      print(f"     WAIT pays the full price of failing: if the target is never "
            f"reached the leg settles directional.")
      print(f"     {'delta':>6} {'cap':>5} {'reached':>9} {'filled':>8} {'WAIT$':>9} "
            f"{'NOW$':>9} {'HOLD$':>9} {'WAIT-NOW':>9}")
      for delta in (F(1,100),F(2,100),F(3,100),F(5,100)):
          for cap in (F(1),F(1,2),F(1,3)):
              take_every=int(1/float(cap)) if float(cap)>0 else 1
              reached=filled=0
              v_wait=v_now=v_hold=F(0)
              for d in eps:
                  q=F(d["complement_offered"]); tgt=q-delta
                  won = (d["held_leg"]==d["won"])
                  settle=F(1) if won else F(0)
                  v_now += F(1)-q
                  v_hold += settle
                  fut=[F(x) for x in d["future_complement_prices"]]
                  hits=[i for i,x in enumerate(fut) if x<=tgt]
                  if hits: reached+=1
                  got=None
                  seen=0
                  for i,x in enumerate(fut):
                      if x<=tgt:
                          seen+=1
                          if seen%take_every==0: got=x; break
                  if got is not None:
                      filled+=1; v_wait += F(1)-got
                  else:
                      v_wait += settle
              n=len(eps)
              print(f"     {float(delta):>6.2f} {float(cap):>5.2f} {reached:>4}/{n:<4} "
                    f"{filled:>4}/{n:<3} {float(v_wait):>9.2f} {float(v_now):>9.2f} "
                    f"{float(v_hold):>9.2f} {float(v_wait-v_now):>9.2f}",flush=True)


  # ---------------------------------------------------------------------
  # where the residual comes from, and how much of it any completion policy
  # could even have reached
  # ---------------------------------------------------------------------
  BANDS = ((F(0), F(20, 100), "<0.20"), (F(20, 100), F(35, 100), "0.20-0.35"),
           (F(35, 100), F(50, 100), "0.35-0.50"),
           (F(50, 100), F(65, 100), "0.50-0.65"), (F(65, 100), F(2), ">0.65"))

  def band(p):
      for lo, hi, nm in BANDS:
          if lo <= p < hi:
              return nm
      return ">0.65"

  for dur in ("5m", "15m"):
      a, c = CAND[dur]
      agg = defaultdict(lambda: {"n": 0, "q": 0, "rp": F(0), "cost": F(0), "won": 0})
      reach = defaultdict(lambda: {"m": 0, "q": 0, "rp": F(0)})
      flat = 0
      flatnet = F(0)
      for k in by_dur[dur]:
          if not k.get("prints") or A.split_of(k["day"]) != "dev":
              continue
          r = R.replay(k, policy="pair", acq_max=a, comp_max=c, max_legs=12,
                       max_residual=4, diagnostics=True)
          if r.get("verdict") != "SETTLED":
              continue
          if r["residual"] == 0:
              flat += 1
              flatnet += F(r["net"])
              continue
          u = F(r["residual_unit"])
          z = agg[band(u)]
          z["n"] += 1; z["q"] += r["residual"]; z["rp"] += F(r["residual_pnl"])
          z["cost"] += u * r["residual"]
          z["won"] += int(r["dominant"] == r["winning_leg"])
          offers = len(r.get("completion_diagnostics") or [])
          kk = ("no complement ever offered" if offers == 0
                else ("1-2 offers" if offers <= 2 else "3+ offers"))
          w = reach[kk]
          w["m"] += 1; w["q"] += r["residual"]; w["rp"] += F(r["residual_pnl"])
      print(f"\n  BTC{dur} DEV residual origin -- fully matched markets: {flat}, "
            f"net ${float(flatnet):.2f}")
      print(f"    {'residual leg':>13} {'mkts':>5} {'qty':>5} {'cost$':>8} "
            f"{'residual$':>10} {'ret on cost':>11} {'dom won':>9}")
      for _, _, nm in BANDS:
          z = agg.get(nm)
          if not z:
              continue
          ret = float(z["rp"] / z["cost"]) if z["cost"] else 0.0
          print(f"    {nm:>13} {z['n']:>5} {z['q']:>5} {float(z['cost']):>8.2f} "
                f"{float(z['rp']):>10.2f} {ret:>10.1%} {z['won']:>4}/{z['n']}")
      print(f"    reachability of the residual by ANY completion policy:")
      for kk in ("no complement ever offered", "1-2 offers", "3+ offers"):
          w = reach.get(kk)
          if w:
              print(f"      {kk:>28} {w['m']:>4} mkts {w['q']:>4} units "
                    f"${float(w['rp']):>7.2f}")
