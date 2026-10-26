#!/usr/bin/env python3
"""Does an inventory-aware first-leg admission rule raise realized dollars?

Outcomes are recoverable for 103 of 527 L5 BTC 15m windows: where fills
exist inside 60s of expiry, the volume-mean implied Up price has collapsed
to >=0.90 or <=0.10 and names the winner. Those windows carry REALIZED
settlement. The rest are reported as EV and labelled, never mixed in.

For each first-leg decision (a fill that increases |imbalance|):
  paired     -> realized = qty * (1 - best_pair)      certain, maker fee 0
  not paired -> realized = qty * (payout - own_px)     settlement decides

Complement executability comes from L5's own later fills, which is an UPPER
BOUND: our maker order would have queued behind L5's.
"""
import csv, collections, random
from decimal import Decimal as D

CAP = D("0.96"); ENTRY_CUTOFF = 600.0; COMPLETE_CUTOFF = 720.0

def load():
    byw = collections.defaultdict(list)
    with open("analysis/l5/l5_all_trades.csv") as fh:
        for x in csv.DictReader(fh):
            if (x["asset"] != "BTC" or x["window_minutes"] != "15"
                    or x["side"] != "BUY"):
                continue
            try:
                byw[x["market_slug"]].append(dict(
                    t=float(x["seconds_from_window_start"]),
                    up=(x["outcome"] == "Up"),
                    px=D(x["price"]).quantize(D("0.0001")),
                    sh=D(x["shares"]),
                    tte=float(x["seconds_to_expiry"])))
            except Exception:
                continue
    for w in byw:
        byw[w].sort(key=lambda r: r["t"])
    return byw

def outcome_of(rs):
    late = [r for r in rs if r["tte"] <= 60]
    if not late:
        return None
    imp = [(r["px"] if r["up"] else D(1) - r["px"]) for r in late]
    m = sum(imp) / len(imp)
    if m >= D("0.90"):
        return True
    if m <= D("0.10"):
        return False
    return None

def decisions(rs):
    out = []
    up = dn = D(0)
    for i, r in enumerate(rs):
        imb = up - dn
        matched = min(up, dn)
        nu, nd = ((up + r["sh"], dn) if r["up"] else (up, dn + r["sh"]))
        if abs(nu - nd) > abs(imb) and r["t"] <= ENTRY_CUTOFF:
            best = None
            for s in rs[i + 1:]:
                if s["t"] > COMPLETE_CUTOFF:
                    break
                if s["up"] == r["up"]:
                    continue
                pair = r["px"] + s["px"]
                if best is None or pair < best:
                    best = pair
            out.append(dict(t=r["t"], own=r["px"], sh=r["sh"], up=r["up"],
                            ratio=(float(abs(imb) / matched)
                                   if matched > 0 else 0.0),
                            best=best, paired=bool(best and best <= CAP)))
        up, dn = nu, nd
    return out

RULES = {
    "current (own>=0.20)":        lambda d: d["own"] >= D("0.20"),
    "own>=0.30":                  lambda d: d["own"] >= D("0.30"),
    "own>=0.20 & t<=360":         lambda d: d["own"] >= D("0.20") and d["t"] <= 360,
    "own>=0.30 & t<=360":         lambda d: d["own"] >= D("0.30") and d["t"] <= 360,
    "own>=0.30 & t<=240":         lambda d: d["own"] >= D("0.30") and d["t"] <= 240,
    "own>=0.50 & t<=360":         lambda d: d["own"] >= D("0.50") and d["t"] <= 360,
    "own>=0.30 & t<=360 & ratio<=1": lambda d: (d["own"] >= D("0.30")
                                                and d["t"] <= 360
                                                and d["ratio"] <= 1.0),
    "admit everything":           lambda d: True,
}

def main():
    byw = load()
    known = {w: o for w, o in ((w, outcome_of(rs)) for w, rs in byw.items())
             if o is not None}
    wins = sorted(known)
    random.Random(20261026).shuffle(wins)
    cut = int(0.6 * len(wins))
    train, held = wins[:cut], wins[cut:]
    print(f"outcome-known windows: {len(wins)}  train {len(train)}  "
          f"held-out {len(held)}\n")

    dec = {w: decisions(byw[w]) for w in wins}

    # IS A STRAND EV-NEUTRAL, OR SYSTEMATICALLY NEGATIVE?
    print("=" * 74)
    print("UNPAIRED FIRST LEGS: realized return on cost (train split)")
    print("=" * 74)
    cost = pnl = D(0); n = 0
    for w in train:
        o = known[w]
        for d in dec[w]:
            if d["paired"]:
                continue
            n += 1
            c = d["sh"] * d["own"]
            pay = d["sh"] if (d["up"] == o) else D(0)
            cost += c; pnl += pay - c
    print(f"  n={n}  cost ${cost:.2f}  realized ${pnl:+.2f}  "
          f"return {float(pnl/cost)*100:+.1f}%")
    print("  ~0% means EV-neutral (gating cuts only variance);")
    print("  materially negative means adverse selection is real and")
    print("  an admission rule can raise realized dollars.")

    print()
    print("=" * 74)
    print("RULES on HELD-OUT windows, realized net dollars per window")
    print("=" * 74)
    print(f"  {'rule':34}{'$/window':>11}{'pairs':>8}{'strands':>9}"
          f"{'strand $':>11}{'pair $':>10}")
    base = None
    for name, fn in RULES.items():
        tot = pair_s = str_s = D(0); npair = nstr = 0
        for w in held:
            o = known[w]
            for d in dec[w]:
                if not fn(d):
                    continue
                if d["paired"]:
                    v = d["sh"] * (D(1) - d["best"]); pair_s += v; npair += 1
                else:
                    c = d["sh"] * d["own"]
                    v = (d["sh"] if (d["up"] == o) else D(0)) - c
                    str_s += v; nstr += 1
                tot += v
        per = tot / len(held)
        if base is None:
            base = per
        print(f"  {name:34}{per:>11.3f}{npair:>8}{nstr:>9}"
              f"{str_s:>11.1f}{pair_s:>10.1f}")
    print(f"\n  baseline is the first row; all figures are REALIZED using the")
    print(f"  recovered settlement outcome, including stranded inventory.")

if __name__ == "__main__":
    main()
