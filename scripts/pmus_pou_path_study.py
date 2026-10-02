#!/usr/bin/env python3
"""Pre-exit path study: do eventual FORCED losers look different, early, from
lots that later achieve a profitable exit?

Replays the FROZEN policy (3e243e4b) unchanged on the 6 COMPLETE PMUS windows.
Nothing is fitted. The label (QUALIFYING / FORCED) is the eventual outcome; the
features are strictly contemporaneous -- each horizon snapshot uses only the
book and trade flow observable at that instant, never anything later.

Horizons after the first-leg fill: 1, 5, 10, 20, 30, 60, 120 s where available.

On this venue the book is single and YES-denominated, so the complement price is
1 - YES. "Complement move" is therefore the exact negative of the first-leg
move and is reported for completeness, not as independent information.
"""
from __future__ import annotations
import json, os, sys, collections, statistics, math
from decimal import Decimal as D, getcontext

getcontext().prec = 28
sys.path.insert(0, os.getcwd())
from scripts.pmus_pair_or_unwind import load, scorable, window_end, fee, reb, DEADLINE_S  # noqa

OUT = "analysis/l5/pmus_rewards/PMUS_POU_PATH_STUDY.json"
HZ = (1, 5, 10, 20, 30, 60, 120)
CEIL = D("0.99")
Q = 25


def complete_windows(sc):
    out = []
    for s in sc:
        hi = max(x["t"] for x in sc[s])
        if hi >= window_end(s) - DEADLINE_S:
            out.append(s)
    return sorted(out)


def replay(sc, slug, q, ceiling):
    end = window_end(slug); dead = end - DEADLINE_S
    lots = []
    entry = {"buy": None, "sell": None}
    flow = collections.deque()
    for x in sc[slug]:
        t = x["t"]; px = D(str(x["px"])); qty = D(str(x["qty"]))
        bid = D(str(x["bid_at_trade"])); ask = D(str(x["ask_at_trade"]))
        br = x["book"]; bq = D(str(br["bid_qty"])); aq = D(str(br["ask_qty"]))
        consumed = "sell" if x["dir"] == "buy" else "buy"
        past = t >= dead
        avail = qty
        flow.append((t, float(qty) * (1 if x["dir"] == "buy" else -1)))
        while flow and t - flow[0][0] > 30:
            flow.popleft()
        net_flow = sum(f[1] for f in flow)

        # ---- snapshot every open lot whose next horizon has elapsed
        for lot in lots:
            if lot["open"] <= 0:
                continue
            el = t - lot["t"]
            while lot["hz_i"] < len(HZ) and el >= HZ[lot["hz_i"]]:
                h = HZ[lot["hz_i"]]; lot["hz_i"] += 1
                long_ = lot["side"] == "buy"
                ex_px = bid if long_ else ask
                ex_depth = bq if long_ else aq
                pair_cost = (lot["px"] + (D(1) - ex_px)) if long_ else ((D(1) - lot["px"]) + ex_px)
                take = min(lot["open"], ex_depth)
                unwind = float((D(1) - pair_cost) * take - fee(ex_px, take))
                lot["snap"].append({
                    "h": h, "t": t,
                    "unwind_pnl_executable": unwind,
                    "best_pair_cost": float(pair_cost),
                    "dist_from_ceiling": float(pair_cost - ceiling),
                    "first_leg_move": float(ex_px - lot["px"]) * (1 if long_ else -1),
                    "complement_move": -float(ex_px - lot["px"]) * (1 if long_ else -1),
                    "spread": float(ask - bid),
                    "exec_depth": float(ex_depth),
                    "elapsed_s": el, "time_remaining_s": dead - t,
                    "net_flow_30s": net_flow,
                    "queue_ahead_at_exit_px": float(ex_depth),
                })

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
                lot["pnl"] += ((epx - lot["px"]) if lot["side"] == "buy" else (lot["px"] - epx)) * take
                lot["pnl"] += reb(epx, take)
                lot["open"] -= take
                lot["kind"] = "PAIR_MAKER"
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
                        lots.append({"px": mypx, "qty": take, "open": take, "side": consumed,
                                     "t": t, "exit_side": "sell" if consumed == "buy" else "buy",
                                     "rest": (None, D(0), D(0)), "kind": None,
                                     "pnl": reb(mypx, take), "hz_i": 0, "snap": []})
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
            take = min(lot["open"], ex_depth)
            if past:
                if take > 0:
                    lot["pnl"] += gross * take - fee(ex_px, take)
                    lot["open"] -= take; lot["kind"] = "FORCED"
                continue
            if pair_cost <= ceiling and gross * take - fee(ex_px, take) > 0 and ex_depth > 0:
                lot["pnl"] += gross * take - fee(ex_px, take)
                lot["open"] -= take; lot["kind"] = "PAIR_TAKER"
                continue
            need = (D(1) - ceiling + lot["px"]) if long_ else (lot["px"] - (D(1) - ceiling))
            rest_px = max(need, ask) if long_ else min(need, bid)
            lot["rest"] = (rest_px, (aq if long_ else bq), D(0))
    for l in lots:
        if l["kind"] is None: l["kind"] = "UNRESOLVED"
    return lots


def main():
    tr, bk = load()
    sc, _ = scorable(tr, bk)
    comp = complete_windows(sc)
    lots = []
    for s in comp:
        lots += replay(sc, s, Q, CEIL)
    qual = [l for l in lots if l["kind"] in ("PAIR_TAKER", "PAIR_MAKER")]
    forced = [l for l in lots if l["kind"] == "FORCED"]
    print(f"complete windows {len(comp)}  lots {len(lots)}  "
          f"QUALIFYING {len(qual)}  FORCED {len(forced)}")

    FEATS = ["unwind_pnl_executable", "best_pair_cost", "dist_from_ceiling",
             "first_leg_move", "spread", "exec_depth", "time_remaining_s",
             "net_flow_30s"]
    study = {}
    print(f"\n{'feature':24s} {'h':>4s} {'nQ':>5s} {'nF':>5s} {'QUAL mean':>11s} "
          f"{'FORCED mean':>12s} {'diff':>10s} {'sigma':>7s}")
    for feat in FEATS:
        study[feat] = {}
        for h in HZ:
            qv = [s[feat] for l in qual for s in l["snap"] if s["h"] == h]
            fv = [s[feat] for l in forced for s in l["snap"] if s["h"] == h]
            if len(qv) < 5 or len(fv) < 5:
                continue
            mq, mf = statistics.mean(qv), statistics.mean(fv)
            se = math.sqrt(statistics.variance(qv) / len(qv) + statistics.variance(fv) / len(fv))
            sig = (mf - mq) / se if se else 0.0
            study[feat][h] = {"n_qual": len(qv), "n_forced": len(fv),
                              "qual_mean": mq, "forced_mean": mf,
                              "diff": mf - mq, "sigma": sig}
            print(f"{feat:24s} {h:>4d} {len(qv):>5d} {len(fv):>5d} {mq:>+11.4f} "
                  f"{mf:>+12.4f} {mf-mq:>+10.4f} {sig:>+7.2f}")

    # ---- EXPLORATORY counterfactual frontier
    print("\n=== EXPLORATORY counterfactual: close EVERY still-open lot at the")
    print("    evidenced executable price at horizon h. Diagnostic only.")
    print(f"{'h':>5s} {'lots open':>10s} {'avoided on FORCED':>18s} {'sacrificed on QUAL':>19s} "
          f"{'net diff':>10s} {'winners killed':>15s} {'worst lot':>10s}")
    cf = {}
    for h in HZ:
        av = sac = 0.0; nk = 0; nq = 0; opened = 0; worst = 0.0
        for l in lots:
            s = next((s for s in l["snap"] if s["h"] == h), None)
            if s is None: continue
            opened += 1
            actual = float(l["pnl"])
            now = s["unwind_pnl_executable"]
            worst = min(worst, now)
            if l["kind"] == "FORCED":
                av += now - actual
            elif l["kind"] in ("PAIR_TAKER", "PAIR_MAKER"):
                nq += 1
                sac += actual - now
                if now < actual: nk += 1
        cf[h] = {"lots_open_at_h": opened, "losses_avoided_on_forced": av,
                 "profits_sacrificed_on_qual": sac, "net_difference": av - sac,
                 "pct_future_winners_killed": (nk / nq * 100) if nq else None,
                 "worst_lot_if_closed_now": worst}
        print(f"{h:>5d} {opened:>10d} {av:>+18.2f} {sac:>+19.2f} {av-sac:>+10.2f} "
              f"{(nk/nq*100 if nq else 0):>14.1f}% {worst:>+10.2f}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"ceiling": str(CEIL), "q": Q, "complete_windows": comp,
               "lots": len(lots), "qualifying": len(qual), "forced": len(forced),
               "separation": study, "counterfactual_EXPLORATORY": cf},
              open(OUT, "w"), indent=2, default=str)
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
