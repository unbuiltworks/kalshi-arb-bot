#!/usr/bin/env python3
"""Pre-fill cancellability diagnostic: before a toxic first-leg fill, did
observable information move against our resting quote early enough to cancel?

DIAGNOSTIC ONLY. No threshold fitted, no strategy change, no live orders.
62222985 frozen.

FEASIBILITY, established before any result is interpreted:

  timestamp precision   PMUS trade rows carry `t` (our receipt, microsecond)
                        and `trade_time` (venue, NANOSECOND). Book rows carry
                        `t` at microsecond precision.
  book resolution       inter-arrival p10 0.098s, p50 0.101s, p90 0.200s
                        -> ~10 Hz. Supports 250ms+ cleanly; 100ms is marginal
                        because a single book update spans ~101ms.
  trade resolution      inter-arrival p50 0.103s, p10 0.0002s.
  EXTERNAL BTC REF      **UNAVAILABLE at this resolution.** The only BTC feed in
                        the repo is data/btc15m/coinbase_1m.jsonl -- ONE MINUTE
                        candles, 60,000ms. Every requested horizon (100ms-5s) is
                        1-3 orders of magnitude finer. The external-BTC leg of
                        this diagnostic CANNOT BE RUN, and no proxy is
                        substituted.
  cancel latency        measured from 4,567 RTT samples already in the repo:
                        min 0.187s, p50 0.266s, p90 0.612s.

  => A signal observed H seconds before the fill is ACTIONABLE only if a cancel
     can land inside H. On the measured RTT, H=0.1s and H=0.25s are NOT
     actionable at all; H=0.5s is actionable only at the optimistic p50; H>=1s
     is actionable at p90. Horizons are labelled accordingly and the
     unactionable ones are reported but never counted as avoidable.

Signals, all strictly pre-fill and PMUS-internal:
  mid_move          change in (bid+ask)/2 over the horizon, signed against us
  spread_change     change in (ask-bid)
  signed_flow       sum of signed traded qty (taker buy +, taker sell -)
  sweep_seen        any print strictly through the touch inside the horizon
  queue_depletion   change in resting size at the side we are resting on
  quote_staleness   our resting price minus the current touch on our side
"""
from __future__ import annotations
import json, os, sys, bisect, collections, statistics, math
from decimal import Decimal as D, getcontext

getcontext().prec = 28
sys.path.insert(0, os.getcwd())
from scripts.pmus_pair_or_unwind import load, scorable, window_end, fee, reb, DEADLINE_S  # noqa

OUT = "analysis/l5/pmus_rewards/PMUS_PREFILL_CANCEL_STUDY.json"
CEIL = D("0.99")
Q = 25
HZ = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0)
RTT_P50, RTT_P90 = 0.266, 0.612


def actionability(h):
    if h < RTT_P50:
        return "NOT_ACTIONABLE"
    if h < RTT_P90:
        return "OPTIMISTIC_ONLY"
    return "ACTIONABLE"


def run(sc, slug):
    """Frozen baseline policy; record pre-fill state for every first leg."""
    end = window_end(slug); dead = end - DEADLINE_S
    evs = sc[slug]
    bts = [e["book"]["t"] for e in evs]
    lots = []
    entry = {"buy": None, "sell": None}
    hist = []           # (t, bid, ask, bq, aq)
    trades = []         # (t, signed_qty, through)
    for ev in evs:
        t = ev["t"]; px = D(str(ev["px"])); qty = D(str(ev["qty"]))
        bid = D(str(ev["bid_at_trade"])); ask = D(str(ev["ask_at_trade"]))
        br = ev["book"]; bq = D(str(br["bid_qty"])); aq = D(str(br["ask_qty"]))
        consumed = "sell" if ev["dir"] == "buy" else "buy"
        past = t >= dead
        avail = qty
        through = (px < bid) or (px > ask)
        trades.append((t, float(qty) * (1 if ev["dir"] == "buy" else -1), through))
        hist.append((t, float(bid), float(ask), float(bq), float(aq)))

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
                    if avail > 0 and got < Q:
                        take = min(avail, D(Q) - got); got += take
                        # ---- PRE-FILL SNAPSHOTS, strictly before this instant
                        snaps = {}
                        for h in HZ:
                            t0 = t - h
                            j = bisect.bisect_left([x[0] for x in hist], t0) - 1
                            if j < 0 or len(hist) < 2:
                                continue
                            b0 = hist[j]
                            # last state STRICTLY before the fill
                            cur = hist[-2] if len(hist) >= 2 else hist[-1]
                            mid0 = (b0[1] + b0[2]) / 2
                            mid1 = (cur[1] + cur[2]) / 2
                            long_ = consumed == "buy"
                            adverse_mid = (mid0 - mid1) if long_ else (mid1 - mid0)
                            fl = [x for x in trades[:-1] if x[0] >= t0]
                            snaps[str(h)] = {
                                "adverse_mid_move": adverse_mid,
                                "spread_change": (cur[2] - cur[1]) - (b0[2] - b0[1]),
                                "signed_flow": sum(x[1] for x in fl),
                                "adverse_flow": (-sum(x[1] for x in fl) if long_
                                                 else sum(x[1] for x in fl)),
                                "sweep_seen": any(x[2] for x in fl),
                                "queue_depletion": ((cur[3] - b0[3]) if long_
                                                    else (cur[4] - b0[4])),
                                "quote_staleness": (float(mypx) - cur[1]) if long_
                                                   else (cur[2] - float(mypx)),
                                "n_book_states": len(hist) - 1 - j,
                            }
                        lots.append({"px": mypx, "qty": take, "open": take,
                            "side": consumed, "t": t,
                            "exit_side": "sell" if consumed == "buy" else "buy",
                            "rest": (None, D(0), D(0)), "kind": None,
                            "pnl": reb(mypx, take), "pre": snaps})
                    entry[consumed] = (mypx, ahead, got) if got < Q else None

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
            if pair_cost <= CEIL and gross * take - fee(ex_px, take) > 0 and depth > 0:
                lot["pnl"] += gross * take - fee(ex_px, take)
                lot["open"] -= take; lot["kind"] = "PAIR_TAKER"
                continue
            need = (D(1) - CEIL + lot["px"]) if long_ else (lot["px"] - (D(1) - CEIL))
            rest_px = max(need, ask) if long_ else min(need, bid)
            lot["rest"] = (rest_px, (aq if long_ else bq), D(0))
    for l in lots:
        if l["kind"] is None: l["kind"] = "UNRESOLVED"
    return lots


def main():
    tr, bk = load()
    sc, _ = scorable(tr, bk)
    new = sorted([s for s in sc if "2026-09-30" not in s
                  and max(x["t"] for x in sc[s]) >= window_end(s) - DEADLINE_S])
    lots = []
    for s in new:
        lots += run(sc, s)
    tox = [l for l in lots if l["kind"] == "FORCED"]
    good = [l for l in lots if l["kind"] in ("PAIR_TAKER", "PAIR_MAKER")]
    print(f"windows {len(new)}  first legs {len(lots)}  TOXIC(FORCED) {len(tox)}  QUALIFYING {len(good)}")
    print(f"\ncancel latency measured in-repo: p50 {RTT_P50}s, p90 {RTT_P90}s")
    print(f"EXTERNAL BTC REFERENCE: UNAVAILABLE (only 1-minute candles) -- leg not run\n")
    FE = ["adverse_mid_move", "spread_change", "adverse_flow", "queue_depletion",
          "quote_staleness"]
    res = {}
    print(f"{'feature':20s} {'h':>6s} {'action':>15s} {'nTox':>5s} {'nQual':>6s} "
          f"{'TOX mean':>10s} {'QUAL mean':>10s} {'sigma':>7s}")
    for f in FE:
        res[f] = {}
        for h in HZ:
            tv = [l["pre"][str(h)][f] for l in tox if str(h) in l["pre"]]
            gv = [l["pre"][str(h)][f] for l in good if str(h) in l["pre"]]
            if len(tv) < 5 or len(gv) < 5: continue
            mt, mg = statistics.mean(tv), statistics.mean(gv)
            se = math.sqrt(statistics.variance(tv)/len(tv) + statistics.variance(gv)/len(gv))
            sig = (mt - mg)/se if se else 0.0
            res[f][str(h)] = {"n_tox": len(tv), "n_qual": len(gv), "tox_mean": mt,
                              "qual_mean": mg, "sigma": sig,
                              "actionability": actionability(h)}
            print(f"{f:20s} {h:>6.2f} {actionability(h):>15s} {len(tv):>5d} {len(gv):>6d} "
                  f"{mt:>+10.4f} {mg:>+10.4f} {sig:>+7.2f}")
    # sweep incidence
    print(f"\n{'sweep seen in horizon':20s} {'h':>6s} {'action':>15s} {'TOX %':>7s} {'QUAL %':>7s}")
    sw = {}
    for h in HZ:
        tv = [l["pre"][str(h)]["sweep_seen"] for l in tox if str(h) in l["pre"]]
        gv = [l["pre"][str(h)]["sweep_seen"] for l in good if str(h) in l["pre"]]
        if len(tv) < 5 or len(gv) < 5: continue
        a, b = sum(tv)/len(tv)*100, sum(gv)/len(gv)*100
        sw[str(h)] = {"tox_pct": a, "qual_pct": b, "actionability": actionability(h)}
        print(f"{'':20s} {h:>6.2f} {actionability(h):>15s} {a:>6.1f}% {b:>6.1f}%")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"windows": new, "first_legs": len(lots), "toxic": len(tox),
               "qualifying": len(good), "rtt_p50": RTT_P50, "rtt_p90": RTT_P90,
               "external_btc": "UNAVAILABLE -- 1-minute candles only",
               "separation": res, "sweep": sw}, open(OUT, "w"), indent=2, default=str)
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
