#!/usr/bin/env python3
"""Entry-depth diagnostic: MAE/MFE per first leg, and what joining deeper does
to fill frequency and subsequent toxicity.

DIAGNOSTIC ONLY. No depth is selected. Nothing in the frozen policy
(eb706bcc) or the pre-registration (82714061) is altered.

THE BINDING DATA LIMIT, stated before any number is produced: the authenticated
tape carries bid_qty/ask_qty for the TOUCH ONLY. The resting size at bid−1 tick
is unobserved, so queue-ahead at a deeper level cannot be established and the
frozen rule forbids inferring a fill there from a trade printing AT that price.

There is exactly one defensible route, and it is the one used here:

  a trade printing STRICTLY THROUGH a price level proves every resting order at
  that level was consumed. So a quote resting at P fills on a print strictly
  better-than-P (below P for a bid, above P for an ask) REGARDLESS of our queue
  position. No queue assumption is needed.

That gives a defensible LOWER BOUND on deeper-quote fill frequency: deeper fills
counted here are certain; fills that would have occurred via partial queue
consumption at the level are UNKNOWN and are not counted. Deeper fill
frequencies are therefore understated, and that direction is stated rather than
corrected.

Four entry variants, all evaluated on the identical tape:
  D0_TOUCH     rest at the touch; fill by evidenced queue consumption (the
               frozen baseline rule)
  D1_DEEPER    rest 1 tick behind the touch; fill only on a print strictly
               through that price
  D2_DEEPER    rest 2 ticks behind the touch; same rule
  MOVE_IN      rest at the touch but require a print strictly THROUGH the touch
               -- i.e. make the market come to us rather than relying on the
               queue ahead of us being exhausted

MAE / MFE are computed from the executable exit price over the lot's life:
for a long, MAE = min(bid) − entry (most adverse), MFE = max(bid) − entry.
"""
from __future__ import annotations
import json, os, sys, collections, statistics
from decimal import Decimal as D, getcontext

getcontext().prec = 28
sys.path.insert(0, os.getcwd())
from scripts.pmus_pair_or_unwind import load, scorable, window_end, fee, reb, DEADLINE_S  # noqa

OUT = "analysis/l5/pmus_rewards/PMUS_ENTRY_DEPTH_STUDY.json"
CEIL = D("0.99")
Q = 25
VARIANTS = ("D0_TOUCH", "D1_DEEPER", "D2_DEEPER", "MOVE_IN")


def infer_tick(sc):
    pxs = set()
    for s in sc:
        for x in sc[s]:
            pxs.add(D(str(x["bid_at_trade"]))); pxs.add(D(str(x["ask_at_trade"])))
    o = sorted(pxs)
    gaps = [o[i + 1] - o[i] for i in range(len(o) - 1)]
    gaps = [g for g in gaps if g > 0]
    return min(gaps) if gaps else D("0.01")


def run(sc, slug, variant, tick, q=Q):
    end = window_end(slug); dead = end - DEADLINE_S
    lots = []
    entry = {"buy": None, "sell": None}
    for ev in sc[slug]:
        t = ev["t"]; px = D(str(ev["px"])); qty = D(str(ev["qty"]))
        bid = D(str(ev["bid_at_trade"])); ask = D(str(ev["ask_at_trade"]))
        br = ev["book"]; bq = D(str(br["bid_qty"])); aq = D(str(br["ask_qty"]))
        consumed = "sell" if ev["dir"] == "buy" else "buy"
        past = t >= dead
        avail = qty

        # ---- MAE / MFE tracking on every open lot, from executable prices
        for lot in lots:
            if lot["open"] <= 0 and lot["exit_t"] is not None:
                continue
            ex = bid if lot["side"] == "buy" else ask
            mv = (ex - lot["px"]) if lot["side"] == "buy" else (lot["px"] - ex)
            lot["mae"] = min(lot["mae"], mv)
            lot["mfe"] = max(lot["mfe"], mv)

        # ---- exits: frozen baseline policy, identical across variants
        for lot in lots:
            if lot["open"] <= 0 or avail <= 0 or lot["rest"][0] is None: continue
            if lot["exit_side"] != consumed: continue
            epx, ahead, _ = lot["rest"]
            if consumed == "buy" and px > epx: continue
            if consumed == "sell" and px < epx: continue
            if ahead > 0:
                eat = min(ahead, avail); ahead -= eat; avail -= eat
            if avail > 0:
                take = min(avail, lot["open"]); avail -= take
                lot["pnl"] += ((epx - lot["px"]) if lot["side"] == "buy"
                               else (lot["px"] - epx)) * take + reb(epx, take)
                lot["open"] -= take; lot["kind"] = "PAIR_MAKER"; lot["exit_t"] = t
            lot["rest"] = (epx, ahead, D(0))

        # ---- ENTRY under the variant's rule
        if not past and avail > 0:
            st = entry.get(consumed)
            if st:
                mypx, ahead, got = st
                if variant == "D0_TOUCH":
                    hit = (px <= mypx) if consumed == "buy" else (px >= mypx)
                    use_queue = True
                else:
                    # strictly through our price: proves our level was cleared
                    hit = (px < mypx) if consumed == "buy" else (px > mypx)
                    use_queue = False
                if hit:
                    if use_queue and ahead > 0:
                        eat = min(ahead, avail); ahead -= eat; avail -= eat
                    if avail > 0 and got < q:
                        take = min(avail, D(q) - got); got += take
                        lots.append({"px": mypx, "qty": take, "open": take,
                            "side": consumed, "t": t,
                            "exit_side": "sell" if consumed == "buy" else "buy",
                            "rest": (None, D(0), D(0)), "kind": None, "exit_t": None,
                            "pnl": reb(mypx, take), "mae": D(0), "mfe": D(0),
                            "entry_offset_ticks": lot_off(variant),
                            "touch_size_at_entry": float(bq if consumed == "buy" else aq),
                            "spread_at_entry": float(ask - bid)})
                    entry[consumed] = (mypx, ahead, got) if got < q else None

        if not past:
            for side in ("buy", "sell"):
                base = bid if side == "buy" else ask
                off = lot_off(variant)
                qp = (base - off * tick) if side == "buy" else (base + off * tick)
                qsz = (bq if side == "buy" else aq) if off == 0 else D(0)
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
                    lot["open"] -= take; lot["kind"] = "FORCED"; lot["exit_t"] = t
                continue
            if pair_cost <= CEIL and gross * take - fee(ex_px, take) > 0 and depth > 0:
                lot["pnl"] += gross * take - fee(ex_px, take)
                lot["open"] -= take; lot["kind"] = "PAIR_TAKER"; lot["exit_t"] = t
                continue
            need = (D(1) - CEIL + lot["px"]) if long_ else (lot["px"] - (D(1) - CEIL))
            rest_px = max(need, ask) if long_ else min(need, bid)
            lot["rest"] = (rest_px, (aq if long_ else bq), D(0))
    for l in lots:
        if l["kind"] is None: l["kind"] = "UNRESOLVED"
    return lots


def lot_off(variant):
    return {"D0_TOUCH": 0, "MOVE_IN": 0, "D1_DEEPER": 1, "D2_DEEPER": 2}[variant]


def main():
    tr, bk = load()
    sc, _ = scorable(tr, bk)
    new = sorted([s for s in sc if "2026-09-30" not in s
                  and max(x["t"] for x in sc[s]) >= window_end(s) - DEADLINE_S])
    tick = infer_tick(sc)
    print(f"windows {len(new)}   inferred tick {tick}")
    res = {}
    for v in VARIANTS:
        lots = []
        for s in new:
            lots += run(sc, s, v, tick)
        if not lots:
            res[v] = {"fills": 0}; continue
        mae = [float(l["mae"]) for l in lots]; mfe = [float(l["mfe"]) for l in lots]
        pnl = [float(l["pnl"]) for l in lots]
        q = lambda a, p: sorted(a)[min(len(a) - 1, int(len(a) * p))]
        res[v] = {
            "fills": len(lots),
            "qualifying": sum(1 for l in lots if l["kind"] in ("PAIR_TAKER", "PAIR_MAKER")),
            "forced": sum(1 for l in lots if l["kind"] == "FORCED"),
            "unresolved": sum(1 for l in lots if l["kind"] == "UNRESOLVED"),
            "MAE_mean": statistics.mean(mae), "MAE_p50": q(mae, .5), "MAE_p90": q(mae, .9),
            "MAE_worst": min(mae),
            "MFE_mean": statistics.mean(mfe), "MFE_p50": q(mfe, .5), "MFE_p90": q(mfe, .9),
            "MFE_best": max(mfe),
            "MFE_plus_MAE_mean": statistics.mean(mfe) + statistics.mean(mae),
            "net": sum(pnl), "net_per_fill": sum(pnl) / len(pnl),
            "worst_lot": min(pnl),
            "median_touch_size_at_entry": statistics.median(
                [l["touch_size_at_entry"] for l in lots]),
            "median_spread_at_entry": statistics.median(
                [l["spread_at_entry"] for l in lots]),
        }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"windows": new, "tick": str(tick),
               "limit": "touch-size only; deeper fills counted ONLY on prints "
                        "strictly through the level, so deeper fill frequency "
                        "is a LOWER BOUND",
               "variants": res}, open(OUT, "w"), indent=2, default=str)
    print(f"\n{'variant':11s} {'fills':>6s} {'qual':>5s} {'frcd':>5s} {'MAE mean':>9s} {'MAE p90':>8s} "
          f"{'MFE mean':>9s} {'MFE p90':>8s} {'MFE+MAE':>8s} {'net':>9s} {'net/fill':>9s} {'worst':>8s}")
    for v in VARIANTS:
        r = res[v]
        if not r.get("fills"):
            print(f"{v:11s} {'0':>6s}  (no defensible fills)"); continue
        print(f"{v:11s} {r['fills']:>6d} {r['qualifying']:>5d} {r['forced']:>5d} "
              f"{r['MAE_mean']:>+9.4f} {r['MAE_p90']:>+8.4f} {r['MFE_mean']:>+9.4f} "
              f"{r['MFE_p90']:>+8.4f} {r['MFE_plus_MAE_mean']:>+8.4f} {r['net']:>+9.2f} "
              f"{r['net_per_fill']:>+9.4f} {r['worst_lot']:>+8.2f}")
    return res


if __name__ == "__main__":
    main()
