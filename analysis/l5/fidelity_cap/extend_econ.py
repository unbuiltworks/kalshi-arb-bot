"""ECONOMIC CONSEQUENCE of the leading policy mismatch: we never EXTEND.

Layer-1 fidelity (two_layer.py) found the largest mismatch is that L5 adds to the
side he already holds -- 49.99% of his 62,020 decisions -- while our policy always
prefers completion and treats a same-side add as refused. CLAUDE.md's operating
model says the opposite: "Same-side EXTEND is a first-class action, not an error
to flatten immediately."

This prices that mismatch. Two arms over the same 527 windows, chronological,
with OUR inventory reconstructed from flat in every window, OUR clip from OUR
budget, the DEPLOYED risk gates, a measured 0.4325 fill probability, PMUS fees
(src/pmus_fees.py: maker fee zero, no per-order term), cent-rounded credited
rebates, and REAL settlement recovered from the REDEEM rows in activity.jsonl.

    ARM A  our policy today: OPEN then COMPLETE. A same-side add is refused.
    ARM B  EXTEND permitted: OPEN, add same-side when L5 does, COMPLETE when the
           pair clears the ceiling.

The ceiling stays 0.96 and every risk gate stays at its deployed value in BOTH
arms. This is a measurement, not a proposal.
"""
from __future__ import annotations

import json
import os
import random
import statistics as st
import sys
from collections import Counter
from decimal import Decimal as D

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "analysis", "l5", "inventory_trajectory"))

import trajectory as TJ
from src.pmus_incentive_audit import rebate_at

CAP = D("0.96"); MIN_PX = D("0.20"); OPEN_NOTIONAL = D("0.80")
OPEN_MAX_CT = D("2"); MAX_UNMATCHED = D("5"); MAX_CASH = D("5")
ENTRY_MIN_TTE = 300.0; COMPLETE_MIN_TTE = 180.0
P_FILL = 0.4325
REB_K = "credited_after_rounding_usd"


def winners(path):
    """slug -> winning outcome, from the REDEEM rows load() excludes."""
    w = {}
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        for r in json.loads(line).get("payload") or []:
            if r.get("type") != "REDEEM":
                continue
            s = str(r.get("eventSlug") or "")
            o = r.get("outcome")
            if s and o:
                w[s] = o
    return w


def one(traj, win, allow_extend, seed):
    rng = random.Random(seed)
    tot = D(0); reb_tot = D(0)
    pairs = D(0); strand_n = 0; traded = 0
    gate = Counter(); per_window = []
    for slug, t in traj:
        outcome = win.get(slug)
        if outcome is None:
            continue
        u = d = cu = cd = D(0); acted = False
        for r in t:
            px = D(str(r["price"])); tte = float(r["tte_s"]); side = r["side"]
            if u == 0 and d == 0:
                if tte < ENTRY_MIN_TTE or px < MIN_PX:
                    continue
                kind = "OPENING"
            else:
                need = "Down" if u > d else "Up"
                if side == need:
                    if tte < COMPLETE_MIN_TTE:
                        continue
                    held = (cu / u) if need == "Down" else (cd / d)
                    if held + px > CAP:
                        gate["PAIR_CEILING"] += 1
                        continue
                    kind = "COMPLETION"
                else:
                    if not allow_extend:
                        gate["EXTEND_REFUSED_BY_POLICY"] += 1
                        continue
                    kind = "EXTENSION"
            qty = OPEN_NOTIONAL / px
            if kind == "COMPLETION":
                qty = min(abs(u - d), qty)
            if qty <= 0:
                continue
            if kind in ("OPENING", "EXTENSION"):
                if qty > OPEN_MAX_CT:
                    gate["OPENING_LEG_MAX_CONTRACTS"] += 1; continue
                if qty * px > OPEN_NOTIONAL:
                    gate["OPENING_NOTIONAL_BUDGET"] += 1; continue
            if cu + cd + qty * px > MAX_CASH:
                gate["MAX_CASH_DEPLOYED"] += 1; continue
            nu = u + (qty if side == "Up" else D(0))
            nd = d + (qty if side != "Up" else D(0))
            if abs(nu - nd) > MAX_UNMATCHED:
                gate["MAX_UNMATCHED_CONTRACTS"] += 1; continue
            if rng.random() > P_FILL:
                gate["NO_FILL"] += 1; continue
            gate["FILLED"] += 1; acted = True
            if side == "Up":
                u += qty; cu += qty * px
            else:
                d += qty; cd += qty * px
            reb_tot += D(rebate_at(qty, px)[REB_K])
        if not acted:
            continue
        traded += 1
        m = min(u, d); pairs += m
        resid = u - d
        rq = abs(resid)
        if rq > 0:
            strand_n += 1
        # settlement: a matched pair redeems 1.00; a residual redeems 1.00 only
        # if it is on the winning side.
        resid_won = (resid > 0 and outcome == "Up") or (resid < 0 and outcome == "Down")
        redeem = m + (rq if resid_won else D(0))
        net = redeem - (cu + cd)
        tot += net
        per_window.append(float(net))
    return dict(net=tot, rebate=reb_tot, pairs=pairs, traded=traded,
                strand=strand_n, gate=gate, per=per_window)


def main():
    wins = TJ.load(os.path.join(ROOT, "data", "l5", "raw", "activity.jsonl"))
    win = winners(os.path.join(ROOT, "data", "l5", "raw", "activity.jsonl"))
    traj = []
    for slug, fills in sorted(wins.items()):
        t = TJ.trajectory(slug, fills)
        if t:
            traj.append((slug, t))
    traj.sort(key=lambda s: s[1][0]["t"])
    traj = [(s, t) for s, t in traj if s in win]
    print("windows with recoverable settlement: %d of %d" % (len(traj), len(wins)))
    out = {}
    for name, ext in (("A_no_extend", False), ("B_extend", True)):
        nets = []
        agg = None
        for seed in range(12):
            r = one(traj, win, ext, seed)
            nets.append(float(r["net"]))
            if agg is None:
                agg = r
        sd = st.stdev(nets) if len(nets) > 1 else 0.0
        out[name] = dict(mean=st.mean(nets), sd=sd, worst=min(nets),
                         best=max(nets), pairs=str(agg["pairs"]),
                         traded=agg["traded"], strand=agg["strand"],
                         rebate=str(agg["rebate"]), gate=dict(agg["gate"]))
        print("\n%s  net mean %+0.4f  sd %0.4f  worst %+0.4f  best %+0.4f"
              % (name, st.mean(nets), sd, min(nets), max(nets)))
        print("   pairs %s  traded %d  stranded %d (%.1f%%)  rebate %s"
              % (str(agg["pairs"])[:8], agg["traded"], agg["strand"],
                 100*agg["strand"]/max(agg["traded"], 1), agg["rebate"]))
        print("   gates:", dict(agg["gate"]))
    d = out["B_extend"]["mean"] - out["A_no_extend"]["mean"]
    print("\nECONOMIC CONSEQUENCE of permitting EXTEND: %+0.4f over %d windows = %+0.4f/window"
          % (d, len(traj), d/len(traj)))
    with open(os.path.join(ROOT, "analysis", "l5", "fidelity_cap",
                           "extend_econ.json"), "w") as fh:
        json.dump(out, fh, indent=2, default=str)


if __name__ == "__main__":
    main()
