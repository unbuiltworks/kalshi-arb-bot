"""P9-C: does refusing to open where completion looks unlikely pay?

    python3 analysis/gemini/p9c_gate.py analysis/gemini/p7_bulk_tape.json

Four arms, with the COMPLETION rule held constant at the P8-A frozen ceiling so
that the only thing varying is what we agree to open:

  1. trade all                      no acquisition filter
  2. pair-cost gate only            the P8-A frozen acquisition ceiling
  3. opportunity gate only          open only where completion looks likely
  4. pair-cost + opportunity gate

The gate's model is fitted on DEV ONLY and frozen; its threshold is chosen on
DEV; validation is scored once. The cost of the gate is reported explicitly as
the P&L of the markets it refuses, not only as the P&L of those it keeps.
"""
import math
import os
import sys
from collections import defaultdict
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import p9a_targets as A                                        # noqa: E402
import p9b_predictors as B                                     # noqa: E402
from src.gemini_execution import completion_labels as CL       # noqa: E402
from src.gemini_execution import ev_replay as R                # noqa: E402

ACQ = F(90, 100)      # the P8-A frozen acquisition ceiling
COMP = F(92, 100)     # the P8-A frozen completion ceiling, constant everywhere
TARGET = B.TARGETS[0][1]        # "a complement that pairs at <= 1.00 appears"


def contract_scores(contract, model, gets):
    """One completion-probability estimate per print, causal by construction."""
    prints = sorted(contract.get("prints") or [],
                    key=lambda r: (int(r[0]), str(r[1])))
    out = []
    for i in range(len(prints)):
        try:
            f = CL.features_at(contract, prints, i, prior=contract.get("_prior"))
            if f["offered"] is None or f["price"] is None:
                out.append(None)
                continue
            z = [(g(f) - mu) / sd for (_n, g), mu, sd
                 in zip(gets, model["mu"], model["sd"])]
            lin = model["b"] + sum(w * zi for w, zi in zip(model["w"], z))
            out.append(1 / (1 + math.exp(-max(-30, min(30, lin)))))
        except Exception:                                      # noqa: BLE001
            out.append(None)
    return out


def stats(rows):
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: (r["day"], r["hour"], r["symbol"]))
    net = sum(F(r["net"]) for r in rows)
    spend = sum(F(r["spend"]) for r in rows)
    fills = sum(r["fills"] for r in rows)
    matched = sum(r["matched"] for r in rows)
    eq = peak = dd = F(0)
    byday = defaultdict(F)
    for r in rows:
        eq += F(r["net"]); peak = max(peak, eq); dd = max(dd, peak - eq)
        byday[r["day"]] += F(r["net"])
    traded = [r for r in rows if r["fills"] > 0]
    return {"mkts": len(traded), "fills": fills, "spend": float(spend),
            "net": float(net),
            "matched": float(sum(F(r["matched_pnl"]) for r in rows)),
            "resid": float(sum(F(r["residual_pnl"]) for r in rows)),
            "roi": (float(net / spend) if spend else 0.0),
            "compl": (matched * 2 / fills) if fills else 0.0,
            "residq": sum(r["residual"] for r in rows), "dd": float(dd),
            "worst_day": float(min(byday.values(), default=F(0)))}


HDR = (f"{'arm':>32} {'mkts':>5} {'fills':>6} {'spend':>8} {'matched$':>9} "
       f"{'resid$':>9} {'NET$':>9} {'ROI':>8} {'compl':>6} {'residQ':>7} "
       f"{'maxDD':>7} {'worstDay':>9}")


def show(label, s):
    print(f"{label:>32} {s['mkts']:>5} {s['fills']:>6} {s['spend']:>8.2f} "
          f"{s['matched']:>9.2f} {s['resid']:>9.2f} {s['net']:>9.2f} "
          f"{s['roi']:>8.2%} {s['compl']:>6.2f} {s['residq']:>7} {s['dd']:>7.2f} "
          f"{s['worst_day']:>9.2f}", flush=True)


def run(contracts, split, *, capture=F(1), **kw):
    rows = []
    for c in contracts:
        if not c.get("prints") or A.split_of(c["day"]) != split:
            continue
        r = R.replay(c, policy="pair", max_legs=12, max_residual=4,
                     comp_max=COMP, capture=capture,
                     gate_scores=c.get("_scores"), **kw)
        if r.get("verdict") == "SETTLED":
            rows.append(r)
    return rows


def main(path):
    opens = A.build(path)
    contracts = {}
    rows, _rep = A.DS.load([path])
    rows = [c for c in rows if c["duration"] in ("5m", "15m") and c.get("prints")]
    for c in A.chain(rows):
        contracts.setdefault(c["duration"], []).append(c)

    for lane in ("5m", "15m"):
        dev_opens = [r for r in opens
                     if r["duration"] == lane and r["split"] == "dev"]
        model = B.fit_logistic(dev_opens, B.FEATURES, TARGET, iters=1500, lr=0.5)
        for c in contracts[lane]:
            c["_scores"] = contract_scores(c, model, B.FEATURES)

        print(f"\n{'='*132}\nP9-C  BTC{lane}  -- completion ceiling fixed at "
              f"{float(COMP):.2f}; the gate's model is DEV-fitted and frozen"
              f"\n{'='*132}")
        print("  threshold sweep on DEV (capture 1.00), arm 4 = pair-cost + gate")
        print(HDR)
        best = None
        for th in (0.0, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80):
            s = stats(run(contracts[lane], "dev", acq_max=ACQ,
                          gate_min=(None if th == 0 else th)))
            if not s:
                continue
            show(f"gate>={th:.2f}", s)
            if th and (best is None or s["net"] > best[1]["net"]):
                best = (th, s)
        th = best[0] if best else 0.5
        print(f"  frozen gate threshold from DEV: {th:.2f}")

        for split in ("dev", "val"):
            print(f"\n  four arms, {split.upper()}, capture 1.00 / 0.50 / 0.33")
            print(HDR)
            for cap in (F(1), F(1, 2), F(1, 3)):
                tag = f"@{float(cap):.2f}"
                for name, kw in (
                        ("1 trade all", {}),
                        ("2 pair-cost only", {"acq_max": ACQ}),
                        ("3 opportunity gate only", {"gate_min": th}),
                        ("4 pair-cost + gate", {"acq_max": ACQ, "gate_min": th})):
                    s = stats(run(contracts[lane], split, capture=cap, **kw))
                    if s:
                        show(name + " " + tag, s)

        # what the gate costs: the markets it refuses, priced under arm 2
        a2 = {r["symbol"]: r for r in run(contracts[lane], "dev", acq_max=ACQ)}
        a4 = {r["symbol"]: r for r in run(contracts[lane], "dev", acq_max=ACQ,
                                          gate_min=th)}
        refused = [s for s in a2 if a2[s]["fills"] > 0
                   and (s not in a4 or a4[s]["fills"] == 0)]
        kept = [s for s in a2 if a2[s]["fills"] > 0 and s not in refused]
        rn = sum(F(a2[s]["net"]) for s in refused)
        kn = sum(F(a2[s]["net"]) for s in kept)
        pos = sum(1 for s in refused if F(a2[s]["net"]) > 0)
        print(f"\n  the gate's cost, DEV: it refuses {len(refused)} markets that "
              f"arm 2 traded, worth ${float(rn):+.2f} under arm 2 "
              f"({pos} of them profitable), and keeps {len(kept)} worth "
              f"${float(kn):+.2f}")


if __name__ == "__main__":
    main(sys.argv[1])
