#!/usr/bin/env python3
"""Score the 13 PRE-REGISTERED exit-rule candidates (82714061) on NEW windows.

BASELINE is PAIR_OR_UNWIND unchanged and is run on every window alongside every
candidate. Discovery windows are excluded by name. No cutoff is altered.

Rule A  accept a first leg only if time_remaining_s >= A at the fill
Rule B  at horizon h after the fill, if first_leg_move <= -X, close immediately
        at the evidenced executable price (fires at most once per lot)
Rule C  A=420 and h=5 and X=0.02

false_rejects are lot-for-lot: BASELINE lots are keyed by
(slug, side, entry px, entry t); a candidate "false rejects" a lot when BASELINE
closed that same lot profitably and the candidate either never opened it (A) or
closed it early at a worse price (B).
"""
from __future__ import annotations
import json, os, sys, collections, statistics
from decimal import Decimal as D, getcontext

getcontext().prec = 28
sys.path.insert(0, os.getcwd())
from scripts.pmus_pair_or_unwind import load, scorable, window_end, fee, reb, DEADLINE_S  # noqa

OUT = "analysis/l5/pmus_rewards/PMUS_POU_CANDIDATES.json"
DISCOVERY = ("2026-09-30",)          # whole discovery day excluded
CEIL = D("0.99")
CFG = [("BASELINE", None, None, None)]
for a in (300, 420, 540):
    CFG.append((f"A{a}", a, None, None))
for h in (5, 10):
    for x in ("0.01", "0.02", "0.03"):
        CFG.append((f"B{h}-{int(float(x)*100)}c", None, h, D(x)))
CFG += [("C", 420, 5, D("0.02")),
        ("A300+B5-2c", 300, 5, D("0.02")),
        ("A540+B5-2c", 540, 5, D("0.02"))]


def replay(sc, slug, q, ceiling, A, h, X):
    end = window_end(slug); dead = end - DEADLINE_S
    lots = []; rejected = 0
    entry = {"buy": None, "sell": None}
    for ev in sc[slug]:
        t = ev["t"]; px = D(str(ev["px"])); qty = D(str(ev["qty"]))
        bid = D(str(ev["bid_at_trade"])); ask = D(str(ev["ask_at_trade"]))
        br = ev["book"]; bq = D(str(br["bid_qty"])); aq = D(str(br["ask_qty"]))
        consumed = "sell" if ev["dir"] == "buy" else "buy"
        past = t >= dead
        avail = qty

        # ---- Rule B guard: one shot at horizon h
        if h is not None:
            for lot in lots:
                if lot["open"] <= 0 or lot["guarded"]:
                    continue
                if t - lot["t"] < h:
                    continue
                lot["guarded"] = True
                long_ = lot["side"] == "buy"
                ex_px = bid if long_ else ask
                move = (ex_px - lot["px"]) if long_ else (lot["px"] - ex_px)
                if move <= -X:
                    depth = bq if long_ else aq
                    take = min(lot["open"], depth)
                    if take > 0:
                        gross = move
                        lot["pnl"] += gross * take - fee(ex_px, take)
                        lot["open"] -= take
                        lot["kind"] = "GUARD_CLOSE"

        for lot in lots:
            if lot["open"] <= 0 or avail <= 0 or lot["rest"][0] is None:
                continue
            if lot["exit_side"] != consumed:
                continue
            epx, ahead, _ = lot["rest"]
            if consumed == "buy" and px > epx: continue
            if consumed == "sell" and px < epx: continue
            if ahead > 0:
                eat = min(ahead, avail); ahead -= eat; avail -= eat
            if avail > 0:
                take = min(avail, lot["open"]); avail -= take
                lot["pnl"] += ((epx - lot["px"]) if lot["side"] == "buy"
                               else (lot["px"] - epx)) * take + reb(epx, take)
                lot["open"] -= take; lot["kind"] = "PAIR_MAKER"
            lot["rest"] = (epx, ahead, D(0))

        if not past and avail > 0:
            st = entry.get(consumed)
            if st:
                mypx, ahead, got = st
                ok = (px <= mypx) if consumed == "buy" else (px >= mypx)
                if ok:
                    if ahead > 0:
                        eat = min(ahead, avail); ahead -= eat; avail -= eat
                    if avail > 0 and got < q:
                        take = min(avail, D(q) - got); got += take
                        tr_rem = dead - t
                        if A is not None and tr_rem < A:
                            rejected += 1
                        else:
                            lots.append({"px": mypx, "qty": take, "open": take,
                                         "side": consumed, "t": t,
                                         "exit_side": "sell" if consumed == "buy" else "buy",
                                         "rest": (None, D(0), D(0)), "kind": None,
                                         "pnl": reb(mypx, take), "guarded": False,
                                         "key": (slug, consumed, str(mypx), round(t, 3))})
                    entry[consumed] = (mypx, ahead, got) if got < q else None

        if not past:
            for side, qp, qsz in (("buy", bid, bq), ("sell", ask, aq)):
                cur = entry.get(side)
                if cur is None or cur[0] != qp:
                    entry[side] = (qp, qsz, D(0))

        for lot in lots:
            if lot["open"] <= 0: continue
            long_ = lot["side"] == "buy"
            ex_px = bid if long_ else ask
            depth = bq if long_ else aq
            pair_cost = (lot["px"] + (D(1) - ex_px)) if long_ else ((D(1) - lot["px"]) + ex_px)
            gross = D(1) - pair_cost
            take = min(lot["open"], depth)
            if past:
                if take > 0:
                    lot["pnl"] += gross * take - fee(ex_px, take)
                    lot["open"] -= take; lot["kind"] = "FORCED"
                continue
            if pair_cost <= ceiling and gross * take - fee(ex_px, take) > 0 and depth > 0:
                lot["pnl"] += gross * take - fee(ex_px, take)
                lot["open"] -= take; lot["kind"] = "PAIR_TAKER"
                continue
            need = (D(1) - ceiling + lot["px"]) if long_ else (lot["px"] - (D(1) - ceiling))
            rest_px = max(need, ask) if long_ else min(need, bid)
            lot["rest"] = (rest_px, (aq if long_ else bq), D(0))
    for l in lots:
        if l["kind"] is None: l["kind"] = "UNRESOLVED"
    return lots, rejected


def main(q=25):
    tr, bk = load()
    sc, _ = scorable(tr, bk)
    new = [s for s in sc
           if not any(d in s for d in DISCOVERY)
           and max(x["t"] for x in sc[s]) >= window_end(s) - DEADLINE_S]
    new.sort()
    print(f"NEW complete windows scored: {len(new)}")
    for s in new: print("  ", s)
    base = {}
    for s in new:
        lots, _ = replay(sc, s, q, CEIL, None, None, None)
        for l in lots:
            base[l["key"]] = l
    res = []
    for name, A, h, X in CFG:
        allots = []; rej = 0
        for s in new:
            lots, r = replay(sc, s, q, CEIL, A, h, X)
            allots += lots; rej += r
        fr = 0; avoided = D(0); sacrificed = D(0)
        for l in allots:
            b = base.get(l["key"])
            if b and float(b["pnl"]) > 0 and float(l["pnl"]) < float(b["pnl"]):
                fr += 1; sacrificed += D(str(b["pnl"])) - D(str(l["pnl"]))
            if b and float(b["pnl"]) < 0 and float(l["pnl"]) > float(b["pnl"]):
                avoided += D(str(l["pnl"])) - D(str(b["pnl"]))
        if A is not None:
            for k, b in base.items():
                if k not in {l["key"] for l in allots}:
                    if float(b["pnl"]) > 0:
                        fr += 1; sacrificed += D(str(b["pnl"]))
                    else:
                        avoided += -D(str(b["pnl"]))
        net = sum(D(str(l["pnl"])) for l in allots)
        cap = max([sum(float(l["px"]) * float(l["qty"]) for l in allots)], default=0)
        res.append({"name": name, "A": A, "h": h, "X": str(X) if X else None,
            "accepted": len(allots), "rejected": rej,
            "qualifying": sum(1 for l in allots if l["kind"] in ("PAIR_TAKER", "PAIR_MAKER")),
            "forced": sum(1 for l in allots if l["kind"] == "FORCED"),
            "guard_closed": sum(1 for l in allots if l["kind"] == "GUARD_CLOSE"),
            "unresolved": sum(1 for l in allots if l["kind"] == "UNRESOLVED"),
            "false_rejects": fr, "forced_losses_avoided": str(avoided),
            "profits_sacrificed": str(sacrificed), "net": str(net),
            "net_per_accepted_leg": str(net / len(allots)) if allots else None,
            "net_per_window": str(net / len(new)),
            "worst_lot": str(min([D(str(l["pnl"])) for l in allots] or [D(0)])),
            "capital_required": round(cap, 2)})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"q": q, "ceiling": str(CEIL), "new_windows": new,
               "preregistration": "analysis/l5/pmus_rewards/PREREGISTRATION_EXIT_RULES.md",
               "rows": res}, open(OUT, "w"), indent=2, default=str)
    bl = next(r for r in res if r["name"] == "BASELINE")
    print(f"\n{'config':12s} {'acc':>5s} {'rej':>5s} {'qual':>5s} {'frcd':>5s} {'grd':>4s} {'unr':>4s} "
          f"{'fRej':>5s} {'avoid':>8s} {'sacr':>8s} {'net':>9s} {'net/leg':>9s} {'net/win':>9s} {'worst':>8s} {'cap':>9s}")
    for r in res:
        print(f"{r['name']:12s} {r['accepted']:>5d} {r['rejected']:>5d} {r['qualifying']:>5d} "
              f"{r['forced']:>5d} {r['guard_closed']:>4d} {r['unresolved']:>4d} {r['false_rejects']:>5d} "
              f"{float(r['forced_losses_avoided']):>+8.2f} {float(r['profits_sacrificed']):>+8.2f} "
              f"{float(r['net']):>+9.2f} {float(r['net_per_accepted_leg'] or 0):>+9.4f} "
              f"{float(r['net_per_window']):>+9.2f} {float(r['worst_lot']):>+8.2f} {r['capital_required']:>9.0f}")
    print(f"\nBASELINE accepted {bl['accepted']}; candidates accepting <50% of that are capacity reductions.")
    return res


if __name__ == "__main__":
    main()
