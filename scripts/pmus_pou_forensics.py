#!/usr/bin/env python3
"""Per-lot forensic decomposition of the frozen PMUS PAIR_OR_UNWIND result.

Mirrors scripts/pmus_pair_or_unwind.py exactly -- same acceptance rules, same
queue discipline, same pre-registered ceilings, same deadline. NOTHING about
the strategy, thresholds, fill rules, recorder or data is altered. This only
records, per lot, where each dollar came from, and reconciles the sum to the
reported net.

Per-lot components, signed so they add to the lot's cash:
  entry_cash      -px*q for a long, +px*q for a short   (first-leg acquisition)
  exit_cash       +exit*q for a long, -exit*q for a short
  locked_edge     (exit - px)*q for a long, (px - exit)*q for a short
                  -- the realised gross pair/unwind edge. entry+exit == locked.
  taker_fee       charged only on taker/forced exits
  maker_rebate    on every maker fill (entry, and passive exits)
  residual_cash   for a lot still open at the end: the cash already spent and
                  not recovered. NOT a settlement estimate -- settlement is
                  never imputed.
"""
from __future__ import annotations
import json, os, sys, bisect, collections, statistics
from decimal import Decimal as D, getcontext
import datetime as dt

getcontext().prec = 28
sys.path.insert(0, os.getcwd())
from scripts.pmus_pair_or_unwind import (load, scorable, window_end, fee, reb,
                                         DEADLINE_S, CEILINGS)   # noqa: E402

OUT = "analysis/l5/pmus_rewards/PMUS_POU_FORENSICS.json"


def replay(sc, slug, q, ceiling):
    end = window_end(slug); dead = end - DEADLINE_S
    lots = []
    entry = {"buy": None, "sell": None}
    for x in sc[slug]:
        t = x["t"]; px = D(str(x["px"])); qty = D(str(x["qty"]))
        bid = D(str(x["bid_at_trade"])); ask = D(str(x["ask_at_trade"]))
        br = x["book"]; bq = D(str(br["bid_qty"])); aq = D(str(br["ask_qty"]))
        consumed = "sell" if x["dir"] == "buy" else "buy"
        past = t >= dead
        avail = qty

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
                lot["exit_cash"] += (epx * take) if lot["exit_side"] == "sell" else (-epx * take)
                lot["maker_rebate"] += reb(epx, take)
                lot["locked"] += ((epx - lot["px"]) if lot["side"] == "buy"
                                  else (lot["px"] - epx)) * take
                lot["open"] -= take
                lot["exit_t"] = t; lot["kind"] = "PAIR_MAKER"
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
                        lots.append({
                            "px": mypx, "qty": take, "open": take, "side": consumed,
                            "t": t, "exit_side": "sell" if consumed == "buy" else "buy",
                            "rest": (None, D(0), D(0)), "kind": None, "exit_t": None,
                            "entry_cash": (-mypx * take) if consumed == "buy" else (mypx * take),
                            "exit_cash": D(0), "locked": D(0), "taker_fee": D(0),
                            "maker_rebate": reb(mypx, take), "pair_cost_at_exit": None})
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
            ex_depth = bq if long_ else aq
            pair_cost = (lot["px"] + (D(1) - ex_px)) if long_ else ((D(1) - lot["px"]) + ex_px)
            gross = D(1) - pair_cost
            take_c = min(lot["open"], ex_depth)
            net_now = gross * take_c - fee(ex_px, take_c)
            if past:
                if take_c > 0:
                    lot["exit_cash"] += (ex_px * take_c) if long_ else (-ex_px * take_c)
                    lot["taker_fee"] += fee(ex_px, take_c)
                    lot["locked"] += gross * take_c
                    lot["open"] -= take_c
                    lot["exit_t"] = t; lot["kind"] = "FORCED"
                    lot["pair_cost_at_exit"] = pair_cost
                continue
            if pair_cost <= ceiling and net_now > 0 and ex_depth > 0:
                lot["exit_cash"] += (ex_px * take_c) if long_ else (-ex_px * take_c)
                lot["taker_fee"] += fee(ex_px, take_c)
                lot["locked"] += gross * take_c
                lot["open"] -= take_c
                lot["exit_t"] = t; lot["kind"] = "PAIR_TAKER"
                lot["pair_cost_at_exit"] = pair_cost
                continue
            need = (D(1) - ceiling + lot["px"]) if long_ else (lot["px"] - (D(1) - ceiling))
            rest_px = max(need, ask) if long_ else min(need, bid)
            lot["rest"] = (rest_px, (aq if long_ else bq), D(0))
    for l in lots:
        if l["open"] > 0 and l["kind"] is None:
            l["kind"] = "UNRESOLVED"
    return lots


def main(q=25):
    tr, bk = load()
    sc, _ = scorable(tr, bk)
    res = {}
    for ceiling in CEILINGS:
        allots = []
        for slug in sorted(sc):
            for l in replay(sc, slug, q, ceiling):
                l["slug"] = slug
                allots.append(l)
        comp = collections.Counter()
        for l in allots:
            comp["entry_cash"] += l["entry_cash"]
            comp["exit_cash"] += l["exit_cash"]
            comp["locked_edge"] += l["locked"]
            comp["taker_fee"] += l["taker_fee"]
            comp["maker_rebate"] += l["maker_rebate"]
        cash = comp["entry_cash"] + comp["exit_cash"]
        net_pre = cash - comp["taker_fee"]
        net_post = net_pre + comp["maker_rebate"]
        # reconciliation: locked_edge must equal entry+exit for closed quantity;
        # the gap is the cash of still-open quantity
        resid = cash - comp["locked_edge"]
        buckets = collections.defaultdict(list)
        for l in allots:
            pnl = l["entry_cash"] + l["exit_cash"] - l["taker_fee"] + l["maker_rebate"]
            buckets[l["kind"]].append(float(pnl))
        res[str(ceiling)] = {
            "lots": len(allots),
            "components": {k: str(v) for k, v in comp.items()},
            "cash_entry_plus_exit": str(cash),
            "locked_edge": str(comp["locked_edge"]),
            "residual_cash_open_qty": str(resid),
            "net_before_comp": str(net_pre), "net_after_comp": str(net_post),
            "buckets": {k: {"n": len(v), "sum": round(sum(v), 4),
                            "mean": round(statistics.mean(v), 5),
                            "median": round(statistics.median(v), 5),
                            "min": round(min(v), 4), "max": round(max(v), 4)}
                        for k, v in buckets.items()},
            "lot_rows": [{"slug": l["slug"], "kind": l["kind"], "side": l["side"],
                          "px": str(l["px"]), "qty": str(l["qty"]), "open": str(l["open"]),
                          "locked": str(l["locked"]), "fee": str(l["taker_fee"]),
                          "reb": str(l["maker_rebate"]),
                          "pnl": str(l["entry_cash"] + l["exit_cash"] - l["taker_fee"] + l["maker_rebate"]),
                          "pair_cost_at_exit": str(l["pair_cost_at_exit"]) if l["pair_cost_at_exit"] else None}
                         for l in allots]}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"q": q, "ceilings": res}, open(OUT, "w"), indent=2, default=str)
    for c, r in res.items():
        print(f"\n=== ceiling {c}  lots {r['lots']}")
        print(f"  entry_cash {float(r['components']['entry_cash']):+12.2f}")
        print(f"  exit_cash  {float(r['components']['exit_cash']):+12.2f}")
        print(f"  = cash     {float(r['cash_entry_plus_exit']):+12.2f}")
        print(f"  locked_edge{float(r['locked_edge']):+12.2f}   residual(open qty cash) {float(r['residual_cash_open_qty']):+10.2f}")
        print(f"  taker_fee  {float(r['components']['taker_fee']):12.2f}   maker_rebate {float(r['components']['maker_rebate']):10.2f}")
        print(f"  net_pre {float(r['net_before_comp']):+10.2f}   net_post {float(r['net_after_comp']):+10.2f}")
        print(f"  {'bucket':14s} {'n':>5s} {'sum':>10s} {'mean':>9s} {'median':>9s} {'min':>10s} {'max':>9s}")
        for k, b in sorted(r["buckets"].items()):
            print(f"  {k:14s} {b['n']:>5d} {b['sum']:>10.2f} {b['mean']:>9.4f} {b['median']:>9.4f} {b['min']:>10.2f} {b['max']:>9.2f}")
    return res


if __name__ == "__main__":
    main()
