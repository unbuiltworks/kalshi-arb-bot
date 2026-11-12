#!/usr/bin/env python3
"""BTC5m under the REAL $0.45 cap: old wiring vs the effective-cap fix vs
simultaneous two-sided quoting. Research only; REPLAY, hypothetical fills.

Same windows, same step (5 s), same fill rules and the same venue-index
probability as `btc5m_reference_replay.py`. The difference is only how the
cap is applied:

  OLD   controller planned against its $5.00 default (FIXED $1.50 reserve);
        the executor's hard $0.45 account cap refused whatever did not fit,
        so a completing order could be refused or cut down.
  FIX   controller given the binding $0.45 and the COMPLETION reserve.
  TWO   simultaneous two-sided: when flat (matched), rest a maker buy on
        BOTH sides with the pair priced at or below PAIR_TARGET; once one
        side fills, the FIX controller works the completion. Same cap,
        same reserve, same hard cap.

Two live orders at most (the executor's `max_live_orders`). Measured per
policy: placed, fills, completed (matched) qty, completion rate, average
completed pair cost, unmatched qty at settlement, completions refused or
cut by the cap, and replay P&L. None of it is realized.

    PYTHONPATH=<checkout with the fix> python3 btc5m_cap_policy_replay.py QUOTES INDEX
"""
from __future__ import annotations

import collections
import json
import os
import sys
from decimal import Decimal as D

import src.l5_btc5m_controller as C  # noqa: E402  (resolved from PYTHONPATH first)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import btc5m_reference_replay as R  # noqa: E402

LABEL = "REPLAY, hypothetical fills, not realized"
CAP = D("0.45")
MAX_LIVE = 2
PAIR_TARGET = D("0.96")
TICK = D("0.01")


def _fill(resting, inv, bid, ask, rule, st):
    ask_of = {C.UP: ask, C.DOWN: (D("1") - bid) if bid is not None else None}
    still = []
    for o in resting:
        a = ask_of[o["side"]]
        hit = a is not None and (a < o["price"] if rule == "through" else a <= o["price"])
        if hit:
            inv[o["side"]]["qty"] += o["qty"]
            inv[o["side"]]["cost"] += o["price"] * o["qty"]
            st["fills"] += 1
        else:
            still.append(o)
    return still


def _total(inv, resting):
    return (inv[C.UP]["cost"] + inv[C.DOWN]["cost"]
            + sum((o["price"] * o["qty"] for o in resting), D("0")))


def simulate(samples, winner, policy, rule, p_of, adverse):
    inv = {C.UP: {"qty": D("0"), "cost": D("0")}, C.DOWN: {"qty": D("0"), "cost": D("0")}}
    resting, n, last = [], 0, None
    st = collections.Counter()
    if policy == "OLD":
        params = C.Params(adverse_selection_per_share=adverse, min_edge_per_share=D("0.01"))
    else:
        params = C.Params(adverse_selection_per_share=adverse, min_edge_per_share=D("0.01"),
                          max_exposure_usd=CAP, reserve_mode="COMPLETION")
    for s in samples:
        t, bid, ask = s["t"], s["bid"], s["ask"]
        resting = _fill(resting, inv, bid, ask, rule, st)
        if last is not None and t - last < R.DECIDE_EVERY_S:
            continue
        last = t
        est = p_of(t)
        p_up = est.get("p_up") if est.get("ok") else None
        view = [{"id": o["id"], "side": o["side"], "price": o["price"], "qty": o["qty"],
                 "age_s": max(0.0, t - o["t"])} for o in resting]
        d = C.decide(book={"bid": bid, "ask": ask, "age_s": 0.0}, t_in_window_s=t,
                     inventory={k: dict(v) for k, v in inv.items()}, resting=view,
                     params=params, p_up=p_up,
                     p_source=("VENUE_INDEX" if p_up is not None else None))
        orders = list(d["orders"])
        matched = inv[C.UP]["qty"] == inv[C.DOWN]["qty"]
        if (policy == "TWO" and matched and not resting and bid is not None
                and ask is not None and t < params.no_new_entry_after_s
                and d["action"] in (C.OPEN_FAVORITE, C.ADD_FAVORITE, C.HOLD_OR_SKIP)):
            # both sides at the touch less ticks until the pair is <= target
            up_px, dn_px = bid, D("1") - ask
            while up_px + dn_px > PAIR_TARGET:
                if up_px >= dn_px:
                    up_px -= TICK
                else:
                    dn_px -= TICK
            q = C._floor_qty(params.clip_usd / max(up_px, dn_px), params)
            if up_px >= D("0.01") and dn_px >= D("0.01") and q >= D("0.01"):
                orders = [{"op": "PLACE", "side": C.UP, "price": up_px, "qty": q,
                           "post_only": True, "intent": "BUY", "reason": "TWO_SIDED"},
                          {"op": "PLACE", "side": C.DOWN, "price": dn_px, "qty": q,
                           "post_only": True, "intent": "BUY", "reason": "TWO_SIDED"}]
        for o in orders:
            if o["op"] == "CANCEL":
                resting = [r for r in resting if r["id"] != o["id"]]
        for o in orders:
            if o["op"] != "PLACE" or not o.get("post_only"):
                continue
            completing = o["reason"] in ("PAIR_AT_OR_BELOW_TARGET",
                                         "FAVORITE_FLIPPED_PAIR_WITHIN_CEILING")
            if len(resting) >= MAX_LIVE:
                st["refused_max_live"] += 1
                if completing:
                    st["completion_refused"] += 1
                continue
            if _total(inv, resting) + o["price"] * o["qty"] > CAP:   # executor's hard cap
                st["refused_cap"] += 1
                if completing:
                    st["completion_refused"] += 1
                continue
            n += 1
            resting.append({"id": "o%d" % n, "side": o["side"], "price": o["price"],
                            "qty": o["qty"], "t": t})
            st["placed"] += 1
            if completing:
                st["completions_placed"] += 1
    up, dn = inv[C.UP], inv[C.DOWN]
    m = min(up["qty"], dn["qty"])
    st_d = dict(st)
    st_d.update(filled_qty=up["qty"] + dn["qty"], matched=m,
                pair_cost=((up["cost"] / up["qty"] + dn["cost"] / dn["qty"]) if m > 0 else None),
                unmatched=abs(up["qty"] - dn["qty"]),
                pnl=inv[winner]["qty"] - up["cost"] - dn["cost"],
                max_exposure=up["cost"] + dn["cost"])
    return st_d


def main(argv):
    os.environ[C.CONTROLLER_ENV] = "ON"
    windows = R.load_quotes(argv[0])
    index = R.Index(json.load(open(argv[1]))["rows"])
    print("%s. windows %d, cap $%s, max live %d" % (LABEL, len(windows), CAP, MAX_LIVE))
    out = {}
    for adverse in (D("0.06"), D("0.03"), D("0.0")):
        for rule in ("through", "touch"):
            for policy in ("OLD", "FIX", "TWO"):
                runs = []
                for slug, samples, winner, strike in windows:
                    ws = R.slug_start(slug)
                    runs.append(simulate(samples, winner, policy, rule,
                                         lambda t, ws=ws, k=strike: R.estimate(index, ws, k, t),
                                         adverse))
                f = sum((r["filled_qty"] for r in runs), D(0))
                m = sum((r["matched"] for r in runs), D(0))
                pcs = [r["pair_cost"] for r in runs if r["pair_cost"] is not None]
                pnl = sum((r["pnl"] for r in runs), D(0))
                pair_pnl = sum((r["matched"] * (D(1) - r["pair_cost"]) for r in runs
                                if r["pair_cost"] is not None), D(0))
                row = {"windows": len(runs),
                       "windows_filled": sum(1 for r in runs if r["filled_qty"] > 0),
                       "windows_completed": sum(1 for r in runs if r["matched"] > 0),
                       "placed": sum(r.get("placed", 0) for r in runs),
                       "fills": sum(r.get("fills", 0) for r in runs),
                       "filled_qty": str(f), "matched_qty": str(m),
                       "completion_rate": (str((2 * m / f).quantize(D("0.001"))) if f else None),
                       "mean_pair_cost": (str((sum(pcs, D(0)) / len(pcs)).quantize(D("0.0001")))
                                          if pcs else None),
                       "locked_pair_pnl": str(pair_pnl.quantize(D("0.0001"))),
                       "unmatched_qty": str(sum((r["unmatched"] for r in runs), D(0))),
                       "completion_refused": sum(r.get("completion_refused", 0) for r in runs),
                       "refused_cap": sum(r.get("refused_cap", 0) for r in runs),
                       "replay_pnl": str(pnl.quantize(D("0.0001"))),
                       "per_window": str((pnl / len(runs)).quantize(D("0.0001"))) if runs else None,
                       "max_window_exposure": str(max(r["max_exposure"] for r in runs))}
                out["%s|%s|%s" % (adverse, rule, policy)] = row
                print("adverse=%s fill=%-7s %-3s %s" % (adverse, rule, policy, json.dumps(row)))
    if len(argv) > 2:
        json.dump({"label": LABEL, "rows": out}, open(argv[2], "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
