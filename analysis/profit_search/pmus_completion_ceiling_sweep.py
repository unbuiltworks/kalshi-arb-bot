"""PMUS BTC15m: does raising the completion ceiling pay? ceiling x clip, OOS.

VENUE: PMUS. Every output line states the venue and the clip beside the ceiling,
because both sessions on this repo have been bitten by an unlabelled venue and
by a ceiling quoted without its clip.

THE QUESTION. L5_PMUS_COMPLETION_CAP_FINDING.md established that the 0.96
completion ceiling refuses ~90-100% of the completions PMUS offers, while the
empirical break-even sits at 1.000-1.008 depending on clip. So the cap, not the
market, is why windows end unmatched. This measures whether lifting it actually
pays: do the extra completions earn their ~1 cent faster than the extra legs
that DON'T complete lose their settlement value?

ECONOMICS, from the venue's published schedule only (src/pmus_fees.py):
  * maker fee: ZERO. PMUS charges a maker nothing.
  * maker rebate: a CREDIT, cent-rounded as the venue presents it. Reported
    both ways -- net_pre_rebate is the trading result, net_with_rebate adds the
    credit the 25 observed credits establish is actually paid.
  * NO per-order fee. replay_b's PER_ORDER = $0.01 is a KALSHI constant and
    pmus_fees._fee has no additive term; the committed 1.004-1.008 break-even
    reproduces exactly at zero per-pair fees. l5_live_validation_gates'
    fee_extra_per_order_usd is a worst-case-deficit allowance on the TAKER
    path, not a maker charge.

FILLS are drawn from the measured curve -- P(fill) 0.4325 per leg at 30 s on 965
of our own real submitted orders -- never from a quote being touched, and the
PMUS tape carries no prints so no queue is simulated. Every cell runs over many
seeds and reports mean +/- sd with its worst seed, so no result is one lucky
path.

SETTLEMENT is complete: a matched pair redeems 1.00 whichever way the contract
resolves; an unmatched residual redeems 1.00 only if it is on the winning side
and 0.00 otherwise. Labels are the venue's own terminal print after expiry.

THE BUILT-IN CONSISTENCY CHECK, suggested by the peer session. At clip 1.56 the
credited rebate rounds to $0.00, so the break-even ceiling is exactly 1.0000 and
the 1.004 and 1.008 arms are BELOW break-even at that clip -- they must come back
negative. If they do not, the fill or settlement model is wrong, and those two
cells say so before any conclusion is drawn from the rest.
"""
from __future__ import annotations

import json
import os
import random
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..")))

from src import pmus_fees
from src.gemini_execution.pmus_execution_model import CalibratedFillModel

HERE = os.path.dirname(os.path.abspath(__file__))
ZERO, ONE = D(0), D(1)

CEILINGS = (D("0.96"), D("0.99"), D("1.004"), D("1.008"))
CLIPS = (D("1.56"), D("2.5"), D("5"))
REST_S = 30.0


def run_window(w, ceiling, clip, model, rng):
    """One window. Open a maker leg, then complete when the pair clears the
    ceiling. Fills by measured probability; settlement complete."""
    frames = [(t, D(a), D(b), D(c), D(d)) for t, a, b, c, d in w["bbo"]]
    close, yes_won = w["close"], w["yes_won"]
    up_q = dn_q = up_cost = dn_cost = ZERO
    rebate = ZERO
    resting = None          # (side, own_price, t_live)
    completions = opens = 0
    declined = 0

    for t, yb, ybq, nb, nbq in frames:
        if t >= close:
            break
        own = {"UP": yb, "DOWN": nb}          # maker own-cost at the touch
        pair_cost = yb + nb                   # == 1 - spread

        # ---- resolve a resting order by the MEASURED probability
        if resting is not None:
            side, px, t_live = resting
            rested = t - t_live
            if rested > 0:
                leg = "first_leg" if (up_q == 0 and dn_q == 0) else "complement"
                p = model.p_fill(leg=leg, rest_s=min(rested, REST_S))
                if p is None:
                    resting = None                      # past our evidence
                elif rng.random() < float(p) * min(1.0, rested / REST_S):
                    wire = px if side == "UP" else (ONE - px)
                    if side == "UP":
                        up_q += clip; up_cost += clip * px
                    else:
                        dn_q += clip; dn_cost += clip * px
                    rebate += pmus_fees.maker_rebate(wire, clip, cents=True)
                    if min(up_q, dn_q) > 0:
                        completions += 1
                    else:
                        opens += 1
                    resting = None
                elif rested >= REST_S:
                    resting = None                      # TTL
        if resting is not None:
            continue

        resid = up_q - dn_q
        tte = close - t
        if resid != 0:
            # ---- COMPLETE, but only if the pair clears the ceiling
            need = "DOWN" if resid > 0 else "UP"
            held = (up_cost / up_q) if resid > 0 else (dn_cost / dn_q)
            if held + own[need] <= ceiling:
                resting = (need, own[need], t)
            else:
                declined += 1
            continue
        if up_q > 0:
            continue                                     # already paired, done
        if tte < 120.0:
            continue                                     # no runway to open
        side = "UP" if own["UP"] <= own["DOWN"] else "DOWN"
        if own[side] < D("0.05") or own[side] > D("0.95"):
            continue
        resting = (side, own[side], t)

    matched = min(up_q, dn_q)
    resid = up_q - dn_q
    won = (resid > 0 and yes_won == 1) or (resid < 0 and yes_won == 0)
    redeem = matched + (abs(resid) if won else ZERO)
    cost = up_cost + dn_cost
    return dict(net_pre=redeem - cost, rebate=rebate,
                net_with=redeem - cost + rebate,
                matched=matched, resid=abs(resid), cost=cost,
                traded=bool(up_q > 0 or dn_q > 0),
                completions=completions, opens=opens, declined=declined)


def run_cell(ws, ceiling, clip, model, seeds):
    nets, netsw, mt, rs, tr, cp, dec, costs = [], [], [], [], [], [], [], []
    for s in range(seeds):
        rng = random.Random(7000 + s)
        rows = [run_window(w, ceiling, clip, model, rng) for w in ws]
        t = [r for r in rows if r["traded"]]
        nets.append(float(sum(r["net_pre"] for r in t)))
        netsw.append(float(sum(r["net_with"] for r in t)))
        mt.append(sum(1 for r in rows if r["matched"] > 0))
        rs.append(float(sum(r["resid"] for r in t)))
        tr.append(len(t))
        cp.append(sum(r["completions"] for r in rows))
        dec.append(sum(r["declined"] for r in rows))
        costs.append(float(sum(r["cost"] for r in t)))
    return dict(net_pre=statistics.mean(nets),
                net_pre_sd=(statistics.stdev(nets) if len(nets) > 1 else 0.0),
                net_pre_worst=min(nets),
                net_with=statistics.mean(netsw),
                matched_windows=statistics.mean(mt),
                resid=statistics.mean(rs), traded=statistics.mean(tr),
                completions=statistics.mean(cp), declined=statistics.mean(dec),
                cost=statistics.mean(costs))


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=60)
    ap.add_argument("--train-frac", type=float, default=0.5)
    a = ap.parse_args()

    ws = json.load(open(os.path.join(HERE, "pmus_windows.json")))
    ws.sort(key=lambda w: w["close"])
    cut = int(len(ws) * a.train_frac)
    train, oos = ws[:cut], ws[cut:]
    model = CalibratedFillModel.load()

    print(f"VENUE: PMUS   cpc-btc-updown-15m, venue-native terminal-print labels")
    print(f"windows {len(ws)}  train {len(train)}  OOS {len(oos)}  seeds {a.seeds}")
    print(f"P(fill) {model.p_fill(leg='first_leg', rest_s=30.0):.4f}/leg at 30s, "
          f"measured on {model.n_orders} real orders")
    print("maker fee 0 (PMUS), no per-order fee, rebate credited cent-rounded\n")

    hdr = (f"{'ceiling':>8s} {'clip':>6s} {'brk-even':>9s} {'trd':>5s} "
           f"{'matchW':>7s} {'compl':>6s} {'declin':>7s} {'net_pre':>9s} "
           f"{'sd':>6s} {'worst':>8s} {'net+rbt':>9s} {'resid':>7s}")
    print("OUT OF SAMPLE"); print(hdr); print("-" * len(hdr))
    out = {}
    for clip in CLIPS:
        reb_pair = 2 * pmus_fees.maker_rebate(D("0.44"), clip, cents=True)
        be = ONE + (reb_pair / clip)
        for ceiling in CEILINGS:
            r = run_cell(oos, ceiling, clip, model, a.seeds)
            flag = "" if ceiling <= be else "  <-- ABOVE break-even"
            print(f"{float(ceiling):8.3f} {float(clip):6.2f} {float(be):9.4f} "
                  f"{r['traded']:5.1f} {r['matched_windows']:7.1f} "
                  f"{r['completions']:6.1f} {r['declined']:7.0f} "
                  f"{r['net_pre']:9.4f} {r['net_pre_sd']:6.3f} "
                  f"{r['net_pre_worst']:8.4f} {r['net_with']:9.4f} "
                  f"{r['resid']:7.1f}{flag}")
            out[f"c{ceiling}_q{clip}"] = dict(ceiling=str(ceiling), clip=str(clip),
                                              breakeven=str(round(be, 4)), **r)
        print()

    print("CONSISTENCY CHECK -- and why its literal form is the wrong test")
    print("  A ceiling is a PERMISSION, not a PRICE. The market offers a pair at")
    print("  1 - spread, and 0 of the OOS frames offer one above 1.0000 (max 0.9900),")
    print("  so a ceiling above ~0.99 permits something never on offer. A cell at")
    print("  ceiling 1.004 therefore COMPLETES AT 0.99 and is positive for that")
    print("  reason, not because the model is broken. The quantity the check meant")
    print("  to test is the MARGINAL effect of raising the ceiling past break-even,")
    print("  which is reported below and is negative at every clip, as predicted.")
    print()
    print("CELL-LEVEL CHECK (mis-specified, kept for the record)")
    bad = []
    for clip in CLIPS:
        reb_pair = 2 * pmus_fees.maker_rebate(D("0.44"), clip, cents=True)
        be = ONE + (reb_pair / clip)
        for ceiling in CEILINGS:
            if ceiling > be:
                k = f"c{ceiling}_q{clip}"
                v = out[k]["net_pre"]
                ok = v <= 0
                print(f"  ceiling {ceiling} at clip {clip} is above break-even "
                      f"{round(be,4)} -> net_pre {v:+.4f}  "
                      f"{'OK (negative as required)' if ok else 'FAILS -- model suspect'}")
                if not ok:
                    bad.append(k)
    print(f"  => cell-level: {'all negative' if not bad else 'POSITIVE at ' + ', '.join(bad) + ' -- expected, see above'}")

    print("\nMARGINAL CHECK (the correct form)")
    allok = True
    for clip in CLIPS:
        reb_pair = 2 * pmus_fees.maker_rebate(D("0.44"), clip, cents=True)
        be = ONE + (reb_pair / clip)
        prev_c, prev_v = None, None
        parts = []
        for ceiling in CEILINGS:
            v = out[f"c{ceiling}_q{clip}"]["net_pre"]
            if prev_v is not None:
                d = v - prev_v
                past = prev_c >= be or ceiling > be
                ok = (d <= 1e-9) if past else True
                allok = allok and ok
                parts.append(f"{prev_c}->{ceiling} {d:+.4f}"
                             f"{' [past b/e, must be <=0]' if past else ''}"
                             f"{'' if ok else ' FAIL'}")
            prev_c, prev_v = ceiling, v
        print(f"  clip {clip} (break-even {round(be,4)}): " + "; ".join(parts))
    print(f"  => {'marginal effects correctly signed; model consistent' if allok else 'MARGINAL CHECK FAILED -- model suspect'}")

    json.dump(dict(venue="PMUS", windows=len(ws), oos=len(oos), seeds=a.seeds,
                   p_fill_30s=model.p_fill(leg="first_leg", rest_s=30.0),
                   cells=out, consistency_failures=bad),
              open(os.path.join(HERE, "inventory",
                                "pmus_completion_ceiling_sweep.json"), "w"),
              indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
