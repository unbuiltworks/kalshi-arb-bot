"""L5 vs our engine in TWO LAYERS, with our own inventory reconstructed.

FIXES THE FLAW IN compare.py. That script fed L5's actual inventory into our
gates and reported that our 5-contract limit refused his 117-contract residual.
That is partly tautological: his inventory is the product of HIS earlier
decisions and our engine would never have reached it. Here our inventory starts
flat in every window and advances ONLY through actions our own policy would have
taken and that would realistically have filled.

LAYER 1 -- POLICY FIDELITY, before any risk limit. At each L5 decision we ask
what our DECISION POLICY would choose given OUR inventory and the market state
his fill reveals. Size and inventory are normalised to deployable capital so the
comparison survives a 187x bankroll difference.

LAYER 2 -- EXECUTABILITY. The deployed PMUS risk gates, our available capital,
a measured fill probability, PMUS fees and settlement economics, applied to OUR
reconstructed book. Every rejection is attributed to one gate.

CAPITAL NORMALISATION, measured not assumed:
    L5 median peak capital per window   $937.68   (p10 $49, p90 $2239)
    our aggregate exposure limit        $5.00     -> 187.5x
    L5 median clip                      0.2514% of his window capital
    our opening notional $0.80          16.00%   of our aggregate -> 64x larger
    L5 median unmatched residual        3.53%  of capital
    our max_unmatched 5 ct (~$2.50)     50.0%  of capital -> 14.2x looser

FILLS: probability 0.4325 per resting leg at 30s, the rate measured on 965 of
our own real submitted orders. Never "the quote was touched". PMUS carries no
print rows so no queue is simulated, and that is a stated limit not a modelled one.

FEES: src/pmus_fees.py only -- maker fee zero, no per-order term. Credited rebate
is cent-rounded per print via pmus_incentive_audit.rebate_at.
"""
from __future__ import annotations

import json
import os
import random
import sys
from collections import Counter
from decimal import Decimal as D

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "analysis", "l5", "inventory_trajectory"))

import trajectory as TJ
from src.pmus_incentive_audit import rebate_at

CAP = D("0.96")                 # production pair ceiling, UNCHANGED
MIN_PX = D("0.20")              # ts_min_first_leg_own_price
OPEN_NOTIONAL = D("0.80")       # opening_notional_budget_usd
OPEN_MAX_CT = D("2")            # ts_opening_leg_max_contracts
MAX_UNMATCHED = D("5")          # max_unmatched_contracts
MAX_CASH = D("5")               # max_cash_deployed_usd
ENTRY_MIN_TTE = 300.0           # ts_entry_cutoff_s 600 on a 900s window
COMPLETE_MIN_TTE = 180.0        # ts_complete_cutoff_s 720
P_FILL = 0.4325
REB_K = "credited_after_rounding_usd"


def l5_action(prev_u, prev_d, side, size):
    if prev_u == 0 and prev_d == 0:
        return "OPENING"
    before = abs(prev_u - prev_d)
    nu = prev_u + (size if side == "Up" else D(0))
    nd = prev_d + (size if side != "Up" else D(0))
    return "COMPLETION" if abs(nu - nd) < before else "EXTENSION"


def our_policy(u, d, cu, cd, px, side, tte):
    """What our POLICY wants at OUR state. No risk gate applied here."""
    if u == 0 and d == 0:
        if tte < ENTRY_MIN_TTE:
            return "WAIT", "TIMING_ENTRY_WINDOW"
        if px < MIN_PX:
            return "WAIT", "PRICE_BELOW_MIN_FIRST_LEG"
        return "OPENING", None
    # holding something: our policy prefers to COMPLETE the short side
    need = "Down" if u > d else "Up"
    if side != need:
        return "EXTEND_REFUSED", "POLICY_PREFERS_COMPLETION"
    if tte < COMPLETE_MIN_TTE:
        return "WAIT", "TIMING_COMPLETE_CUTOFF"
    held = (cu / u) if need == "Down" else (cd / d)
    if held + px > CAP:
        return "WAIT", "PAIR_CEILING"
    return "COMPLETION", None


def gates(u, d, cu, cd, px, qty, kind):
    cash = cu + cd
    if kind == "OPENING":
        if qty > OPEN_MAX_CT:
            return "OPENING_LEG_MAX_CONTRACTS"
        if qty * px > OPEN_NOTIONAL:
            return "OPENING_NOTIONAL_BUDGET"
    if cash + qty * px > MAX_CASH:
        return "MAX_CASH_DEPLOYED"
    nu = u + (qty if kind != "COMPLETION" else D(0))
    nd = d + (qty if kind == "COMPLETION" else D(0))
    if abs(nu - nd) > MAX_UNMATCHED:
        return "MAX_UNMATCHED_CONTRACTS"
    return None


def run(seed=0):
    rng = random.Random(seed)
    wins = TJ.load(os.path.join(ROOT, "data", "l5", "raw", "activity.jsonl"))
    traj = []
    for slug, fills in sorted(wins.items()):
        t = TJ.trajectory(slug, fills)
        if t:
            traj.append((slug, t))
    traj.sort(key=lambda s: s[1][0]["t"])

    L1 = Counter()        # policy-layer agreement
    L2 = Counter()        # executability
    mismatch = Counter()  # cause attribution
    econ = {"pairs": D(0), "pair_net": D(0), "rebate": D(0),
            "resid_qty": D(0), "resid_cost": D(0), "windows_traded": 0,
            "windows_stranded": 0}
    sizes_ours = []

    for slug, t in traj:
        # OUR book, reconstructed. Starts flat. Never takes L5's inventory.
        u = d = cu = cd = D(0)
        acted = False
        for r in t:
            px = D(str(r["price"]))
            tte = float(r["tte_s"])
            l5side = r["side"]
            l5size = D(str(r["size"]))
            # L5's action, from HIS book (for comparison only)
            pu = D(str(r["cum_UP_qty"])) - (l5size if l5side == "Up" else D(0))
            pdn = D(str(r["cum_DOWN_qty"])) - (l5size if l5side != "Up" else D(0))
            lk = l5_action(pu, pdn, l5side, l5size)

            want, why = our_policy(u, d, cu, cd, px, l5side, tte)

            # ---- LAYER 1: do our POLICY and L5 agree on the action?
            if want == lk:
                L1["AGREE"] += 1
            else:
                L1["DISAGREE"] += 1
                if want == "WAIT" and why == "PAIR_CEILING":
                    mismatch["COMPLETION_price_ceiling"] += 1
                elif want == "WAIT" and why and why.startswith("TIMING"):
                    mismatch["TIMING"] += 1
                elif want == "WAIT" and why == "PRICE_BELOW_MIN_FIRST_LEG":
                    mismatch["PRICE_entry_floor"] += 1
                elif want == "EXTEND_REFUSED":
                    mismatch["EXTENSION_we_never_extend"] += 1
                elif lk == "OPENING" and want != "OPENING":
                    mismatch["ENTRY_SELECTION"] += 1
                else:
                    mismatch["OTHER_%s_vs_%s" % (want, lk)] += 1

            if want not in ("OPENING", "COMPLETION"):
                continue

            # our own size, from OUR budget -- never L5's size
            qty = (OPEN_NOTIONAL / px) if want == "OPENING" else min(
                abs(u - d), OPEN_NOTIONAL / px)
            if qty <= 0:
                continue
            g = gates(u, d, cu, cd, px, qty, want)
            if g:
                L2[g] += 1
                continue
            L2["PERMITTED"] += 1
            if rng.random() > P_FILL:
                L2["PERMITTED_NO_FILL"] += 1
                continue
            L2["FILLED"] += 1
            sizes_ours.append(float(qty * px))
            acted = True
            if want == "OPENING":
                if l5side == "Up":
                    u += qty; cu += qty * px
                else:
                    d += qty; cd += qty * px
            else:
                if l5side == "Up":
                    u += qty; cu += qty * px
                else:
                    d += qty; cd += qty * px
                econ["rebate"] += D(rebate_at(qty, px)[REB_K])

        if acted:
            econ["windows_traded"] += 1
            m = min(u, d)
            econ["pairs"] += m
            if m > 0:
                # a matched pair redeems 1.00; its cost is the two VWAP legs
                pc = (cu / u if u else D(0)) + (cd / d if d else D(0))
                econ["pair_net"] += m * (D(1) - pc)
            rq = abs(u - d)
            if rq > 0:
                econ["windows_stranded"] += 1
                econ["resid_qty"] += rq
                econ["resid_cost"] += rq * ((cu / u) if u > d else (cd / d))
    return traj, L1, L2, mismatch, econ, sizes_ours


if __name__ == "__main__":
    traj, L1, L2, mm, econ, sz = run()
    out = {"windows": len(traj), "layer1": dict(L1), "layer2": dict(L2),
           "mismatch": dict(mm),
           "econ": {k: str(v) for k, v in econ.items()}}
    with open(os.path.join(ROOT, "analysis", "l5", "fidelity_cap",
                           "two_layer.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    tot = L1["AGREE"] + L1["DISAGREE"]
    print("windows %d   L5 decisions %d" % (len(traj), tot))
    print("LAYER 1 policy agreement: %d / %d = %.2f%%" % (L1["AGREE"], tot, 100*L1["AGREE"]/tot))
    print("mismatch causes:")
    for k, v in mm.most_common():
        print("   %-34s %7d  %5.2f%%" % (k, v, 100*v/tot))
    print("LAYER 2:", dict(L2))
    print("econ:", {k: str(v)[:12] for k, v in econ.items()})
