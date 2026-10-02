#!/usr/bin/env python3
"""Mechanism audit of the PRE-DECLARED signal `trade_imbalance >= 0.75 @ 2s`.

NOTHING IS OPTIMIZED HERE. The threshold (0.75), the horizon (2 s), the pair
ceilings and the feature definition are taken exactly as the frozen study
already computed them. No sweep, no combination with other features, no
re-tuning of cancel latency after seeing results.

The question is not "does it separate" -- the frozen study already answered that
on 1730z/1745z. The question is what the feature MEANS, whether a live
controller could actually have it in time, and whether acting on it would have
been worth anything in dollars.

FEATURE, verbatim from scripts/pmus_prefill_realtime_study.py:

    seg = [y for y in rows[:i] if y["t"] >= t - h]        # h = 2.0
    tb  = sum(qty for y in seg if y["dir"] == "buy")      # taker bought
    tsl = sum(qty for y in seg if y["dir"] == "sell")     # taker sold
    adv = tsl if side == "buy" else tb
    trade_imbalance = adv / (tb + tsl)

So it is the ADVERSE share of taker volume over the 2 s before the fill.
`rows[:i]` excludes the filling trade itself and `y["t"] >= t - h` bounds the
window, so every contributing trade is strictly earlier than the fill -- that is
checked here against the data rather than asserted from the code.

ADVERSE DIRECTION. `dir` is the taker's direction. A taker SELL consumes the
BID, so for our resting BUY the adverse volume is the sell volume; a taker BUY
consumes the ASK, so for our resting SELL it is the buy volume. The feature uses
exactly that mapping.

AVOIDABILITY. A fill is counted avoidable ONLY if the signal first became true
at least one cancel round trip before the fill. Latency is the measured
in-repo RTT distribution, not a chosen number: p50 0.266 s and p90 0.612 s, with
1.0 s as a conservative third point. A warning that arrives inside the latency
is NOT avoided and is reported separately.

ECONOMICS. Each lot's realized cash is recomputed from the frozen baseline exit
rules, so "toxic loss avoided" and "qualifying profit sacrificed" are actual lot
P&L, not counts. Per-lot and aggregate, per window and per ceiling.

Diagnostic only. No rule is promoted from these two windows -- they produced the
candidate and so cannot validate it. No live orders.
"""
from __future__ import annotations
import argparse, bisect, collections, json, os, statistics
from decimal import Decimal as D, getcontext

getcontext().prec = 28
OUT = "analysis/l5/pmus_rewards/PMUS_IMBALANCE_MECHANISM_AUDIT.json"
WINDOWS = ("cpc-btc-updown-15m-2026-10-02-1730z",
           "cpc-btc-updown-15m-2026-10-02-1745z")
# FROZEN signal definition -- not parameters to search
IMB_THRESHOLD = 0.75
IMB_HORIZON_S = 2.0
# measured, pre-registered operational latencies
RTT = {"p50": 0.266, "p90": 0.612, "conservative_1s": 1.0}
CEILINGS = (D("0.99"), D("0.98"), D("0.97"), D("0.95"))
Q = 25
DEADLINE_S = 90
TAKER = D("0.07"); MAKER_REB = D("0.0125")

import importlib.util
_s = importlib.util.spec_from_file_location("pou", "scripts/pmus_pair_or_unwind.py")
pou = importlib.util.module_from_spec(_s); _s.loader.exec_module(pou)
_r = importlib.util.spec_from_file_location("rt", "scripts/pmus_prefill_realtime_study.py")
rt = importlib.util.module_from_spec(_r); _r.loader.exec_module(rt)


def imbalance_series(rows, i, t, side, h=IMB_HORIZON_S):
    """The frozen feature, plus the provenance the audit needs."""
    seg = [y for y in rows[:i] if y["t"] >= t - h]
    tb = sum(float(y["qty"]) for y in seg if y["dir"] == "buy")
    tsl = sum(float(y["qty"]) for y in seg if y["dir"] == "sell")
    tot = tb + tsl
    adv = tsl if side == "buy" else tb
    return {
        "value": (adv / tot) if tot > 0 else None,
        "n_trades": len(seg),
        "adverse_qty": adv, "total_qty": tot,
        "newest_contributing_t": max((y["t"] for y in seg), default=None),
        "oldest_contributing_t": min((y["t"] for y in seg), default=None),
        "all_strictly_before_fill": all(y["t"] < t for y in seg),
    }


def onset_before_fill(rows, i, t, side):
    """Earliest instant before the fill at which the frozen signal was already
    true, found by evaluating the SAME feature at each preceding trade event.

    This is what a live controller would have seen: at each event it can compute
    the trailing 2 s adverse share from trades already received. No future
    information is used -- the evaluation at event j uses rows[:j] only.
    """
    first = None
    for j in range(i, -1, -1):
        tj = rows[j]["t"]
        if tj >= t:
            continue
        v = imbalance_series(rows, j, tj, side)["value"]
        if v is not None and v >= IMB_THRESHOLD:
            first = tj                      # keep walking back for the earliest
        elif first is not None and tj < first - IMB_HORIZON_S:
            break                           # signal has been false for a while
    return first


def lots_with_economics(rows, dead, ceiling):
    """RESTING arm first legs plus their frozen-baseline exit economics.

    Entry and exit rules are the frozen ones; this adds the per-lot cash so the
    counterfactual can be priced instead of counted.
    """
    order = {"buy": None, "sell": None}
    lots = []
    for i, x in enumerate(rows):
        t = x["t"]; past = t >= dead
        px = D(str(x["px"])); bid = D(str(x["bid_at_trade"])); ask = D(str(x["ask_at_trade"]))
        bq = D(str(x["book"]["bid_qty"])); aq = D(str(x["book"]["ask_qty"]))
        consumed = "sell" if x["dir"] == "buy" else "buy"
        avail = D(str(x["qty"]))
        o = order.get(consumed)
        if o is not None:
            ok = (px <= o["px"]) if consumed == "buy" else (px >= o["px"])
            if ok:
                through = (px < o["px"]) if consumed == "buy" else (px > o["px"])
                if through:
                    o["queue_ahead"] = D(0)
                if o["queue_ahead"] > 0:
                    eat = min(o["queue_ahead"], avail)
                    o["queue_ahead"] -= eat; avail -= eat
                if avail > 0 and o["filled"] < o["qty"]:
                    take = min(avail, o["qty"] - o["filled"]); o["filled"] += take
                    entry_cash = (-o["px"] * take) if consumed == "buy" else (o["px"] * take)
                    lots.append({"px": o["px"], "qty": take, "open": take,
                                 "side": consumed, "t": t, "idx": i,
                                 "quote_age_s": t - o["placed_t"],
                                 "exit_side": "sell" if consumed == "buy" else "buy",
                                 "rest": (None, D(0)), "rest_kind": "PAIR_MAKER",
                                 "exit_kind": None, "exit_t": None,
                                 "cash": entry_cash, "fees": D(0), "rebate":
                                 MAKER_REB * o["px"] * (D(1) - o["px"]) * take})
                if o["filled"] >= o["qty"]:
                    order[consumed] = None
        if not past:
            for side, qp, qsz in (("buy", bid, bq), ("sell", ask, aq)):
                if order.get(side) is None:
                    order[side] = {"px": qp, "qty": D(Q), "filled": D(0),
                                   "queue_ahead": qsz, "placed_t": t}

    # frozen exit rules, verbatim in structure, carrying cash per lot
    by_idx = {}
    for l in lots:
        by_idx.setdefault(l["idx"], []).append(l)
    live = []
    for i, x in enumerate(rows):
        t = x["t"]
        px = D(str(x["px"])); bid = D(str(x["bid_at_trade"])); ask = D(str(x["ask_at_trade"]))
        bq = D(str(x["book"]["bid_qty"])); aq = D(str(x["book"]["ask_qty"]))
        consumed = "sell" if x["dir"] == "buy" else "buy"
        avail = D(str(x["qty"])); past = t >= dead
        for lot in live:
            if lot["open"] <= 0 or avail <= 0 or lot["rest"][0] is None:
                continue
            if lot["exit_side"] != consumed:
                continue
            epx, ahead = lot["rest"]
            if consumed == "buy" and px > epx:
                continue
            if consumed == "sell" and px < epx:
                continue
            if ahead > 0:
                eat = min(ahead, avail); ahead -= eat; avail -= eat
            if avail > 0:
                take = min(avail, lot["open"]); avail -= take
                lot["cash"] += (epx * take) if lot["exit_side"] == "sell" else (-epx * take)
                lot["rebate"] += MAKER_REB * epx * (D(1) - epx) * take
                lot["open"] -= take
                lot["exit_kind"] = lot["rest_kind"]; lot["exit_t"] = t
            lot["rest"] = (epx, ahead)
        live += by_idx.get(i, [])
        for lot in live:
            if lot["open"] <= 0:
                continue
            long_ = lot["side"] == "buy"
            ex_px = bid if long_ else ask
            ex_depth = bq if long_ else aq
            pair_cost = (lot["px"] + (D(1) - ex_px)) if long_ else ((D(1) - lot["px"]) + ex_px)
            gross = D(1) - pair_cost
            f = TAKER * ex_px * (D(1) - ex_px) * min(lot["open"], ex_depth)
            net_now = gross * min(lot["open"], ex_depth) - f
            if past:
                take = min(lot["open"], ex_depth)
                if take > 0:
                    lot["cash"] += (ex_px * take) if long_ else (-ex_px * take)
                    lot["fees"] += TAKER * ex_px * (D(1) - ex_px) * take
                    lot["open"] -= take
                    lot["exit_kind"] = "FORCED"; lot["exit_t"] = t
                continue
            if pair_cost <= ceiling and net_now > 0 and ex_depth > 0:
                take = min(lot["open"], ex_depth)
                lot["cash"] += (ex_px * take) if long_ else (-ex_px * take)
                lot["fees"] += TAKER * ex_px * (D(1) - ex_px) * take
                lot["open"] -= take
                lot["exit_kind"] = "PAIR_TAKER"; lot["exit_t"] = t
                continue
            need = (D(1) - ceiling + lot["px"]) if long_ else (lot["px"] - (D(1) - ceiling))
            rest_px = max(need, ask) if long_ else min(need, bid)
            lot["rest"] = (rest_px, (aq if long_ else bq))
            lot["rest_kind"] = "PAIR_MAKER"
    for l in lots:
        l["label"] = "TOXIC" if l["exit_kind"] in (None, "FORCED") else "QUALIFYING"
        # residual open inventory is marked at the last touch, labelled as such
        l["net"] = l["cash"] - l["fees"] + l["rebate"]
    return lots


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    tr, bk = pou.load()
    sc, _ = pou.scorable(tr, bk)

    res = {"signal": {"name": "trade_imbalance", "threshold": IMB_THRESHOLD,
                      "horizon_s": IMB_HORIZON_S,
                      "frozen": "taken from the pre-declared study; not tuned here"},
           "rtt_measured": RTT, "windows": list(WINDOWS),
           "ceilings": [str(c) for c in CEILINGS],
           "note": ("These two windows produced the candidate and therefore "
                    "cannot validate it. No rule is promoted here.")}
    per_window = {}
    for w in WINDOWS:
        rows = sc.get(w) or []
        if not rows:
            per_window[w] = {"status": "NO_SCORABLE_ROWS"}
            continue
        dead = pou.window_end(w) - DEADLINE_S
        wres = {"scorable_trades": len(rows)}
        for c in CEILINGS:
            lots = lots_with_economics(rows, dead, c)
            cm = collections.Counter()
            econ = {k: D(0) for k in ("tox_net_flagged", "tox_net_unflagged",
                                      "qual_net_flagged", "qual_net_unflagged")}
            leads = []
            avoid = {k: {"n": 0, "net": D(0)} for k in RTT}
            sacrif = {k: {"n": 0, "net": D(0)} for k in RTT}
            too_late = {k: 0 for k in RTT}
            prov_ok = prov_bad = 0
            for l in lots:
                f = imbalance_series(rows, l["idx"], l["t"], l["side"])
                if f["all_strictly_before_fill"]:
                    prov_ok += 1
                else:
                    prov_bad += 1
                flagged = f["value"] is not None and f["value"] >= IMB_THRESHOLD
                cm[(l["label"], "FLAG" if flagged else "NOFLAG")] += 1
                key = ("tox" if l["label"] == "TOXIC" else "qual") + \
                      ("_net_flagged" if flagged else "_net_unflagged")
                econ[key] += l["net"]
                if not flagged:
                    continue
                onset = onset_before_fill(rows, l["idx"], l["t"], l["side"])
                if onset is None:
                    continue
                lead = l["t"] - onset
                leads.append(lead)
                for k, lat in RTT.items():
                    if lead >= lat:
                        tgt = avoid if l["label"] == "TOXIC" else sacrif
                        tgt[k]["n"] += 1
                        tgt[k]["net"] += l["net"]
                    elif l["label"] == "TOXIC":
                        too_late[k] += 1
            n_tox = cm[("TOXIC", "FLAG")] + cm[("TOXIC", "NOFLAG")]
            n_qual = cm[("QUALIFYING", "FLAG")] + cm[("QUALIFYING", "NOFLAG")]
            wres[str(c)] = {
                "n_toxic": n_tox, "n_qualifying": n_qual,
                "confusion": {"TOXIC_flagged": cm[("TOXIC", "FLAG")],
                              "TOXIC_unflagged": cm[("TOXIC", "NOFLAG")],
                              "QUALIFYING_flagged": cm[("QUALIFYING", "FLAG")],
                              "QUALIFYING_unflagged": cm[("QUALIFYING", "NOFLAG")]},
                "P_flag_given_toxic": round(cm[("TOXIC", "FLAG")] / n_tox, 4) if n_tox else None,
                "P_flag_given_qualifying": round(cm[("QUALIFYING", "FLAG")] / n_qual, 4) if n_qual else None,
                "provenance": {"lots_all_contributing_trades_before_fill": prov_ok,
                               "lots_with_a_contributing_trade_at_or_after_fill": prov_bad},
                "onset_lead_s": {
                    "n": len(leads),
                    "p10": round(sorted(leads)[len(leads)//10], 3) if leads else None,
                    "p50": round(statistics.median(leads), 3) if leads else None,
                    "p90": round(sorted(leads)[len(leads)*9//10], 3) if leads else None,
                    "frac_ge_0.266s": round(sum(1 for x in leads if x >= 0.266)/len(leads), 4) if leads else None,
                    "frac_ge_0.612s": round(sum(1 for x in leads if x >= 0.612)/len(leads), 4) if leads else None,
                    "frac_ge_1.0s": round(sum(1 for x in leads if x >= 1.0)/len(leads), 4) if leads else None},
                "counterfactual": {k: {
                    "avoided_toxic_n": avoid[k]["n"],
                    "avoided_toxic_net_removed": str(avoid[k]["net"]),
                    "sacrificed_qualifying_n": sacrif[k]["n"],
                    "sacrificed_qualifying_net_removed": str(sacrif[k]["net"]),
                    "toxic_flagged_too_late": too_late[k],
                    "delta_net_if_cancelled": str(-(avoid[k]["net"] + sacrif[k]["net"])),
                } for k in RTT},
                "baseline_net_all_lots": str(sum(l["net"] for l in lots)),
                "net_by_group": {k: str(v) for k, v in econ.items()},
            }
        per_window[w] = wres
    res["per_window"] = per_window
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(res, open(a.out, "w"), indent=2, default=str)
    print(json.dumps(res, indent=1, default=str)[:3000])


if __name__ == "__main__":
    main()
