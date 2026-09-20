"""Causal taxonomy for the residual that SURVIVES the historically faithful
completion rule (arm 3 = B2 continuous inventory + queue-preserving complements
+ L5_HISTORICAL completion, i.e. the 0.98 pair cap removed).

SHADOW / REPLAY ONLY. Model A is unchanged, no threshold is fitted, no settlement
outcome influences any action, and the causality invariant holds.

Partition, mutually exclusive, first match wins, per residual unit over the
window from its acquisition to end of observation:

  WINDOW_CENSORING     market never reached MARKET_STATE_EXPIRED, or fewer than
                       one decision gap of observation remained after acquisition
  MARKET_LIMIT         no print of ANY price occurred afterwards
  PRICE_POLICY_LIMIT   prints occurred but none was compatible with the DEFICIT
                       SIDE MAKER TOUCH prevailing at that print's own time.
                       Arm 3 has no pair cap, so this is the market never trading
                       at/through our join price -- not a cap refusal.
  INVENTORY_EXTEND     acquired by OPEN/EXTEND while a complement was already
                       resting unfilled: residual grew faster than it recycled
  ORDER_ABSENT         a compatible print arrived with NO complement resting
  QUEUE_REACH_LIMIT    a compatible print reached a RESTING complement but the
                       queue frozen at placement was never cleared
  OTHER                none of the above
"""
import sys, json, pickle, bisect, collections
from decimal import Decimal as D
SCR = "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
sys.path.insert(0, "/home/user/kalshi-arb-bot"); sys.path.insert(0, SCR)
from src import pmus_sports_l5_decision as dec

GAP = float(dec.DECISION_MIN_GAP_S)


def _ok(p_px, px, sy):
    lim = px if sy else (1.0 - px)
    return (p_px <= lim) if sy else (p_px >= lim)


def touches(book):
    """(times, maker_touch_yes, maker_touch_no, cross_yes, cross_no) aligned."""
    ts = [b[0] for b in book]
    mk_y, mk_n, cx_y, cx_n = [], [], [], []
    for t, bid, ask, bq, aq in book:
        bk = {"bid": D(str(bid)), "ask": D(str(ask))}
        ty = dec._touch_for(True, bk); tn = dec._touch_for(False, bk)
        mk_y.append(None if ty is None else float(ty))
        mk_n.append(None if tn is None else float(tn))
        cx_y.append(None if ask is None else float(ask))          # buy YES marketable
        cx_n.append(None if bid is None else 1.0 - float(bid))    # buy NO marketable
    return ts, mk_y, mk_n, cx_y, cx_n


def classify(res, settle):
    rows = []
    for x in res:
        ys = [a for a in x["acqs"] if a["sy"]]
        ns = [a for a in x["acqs"] if not a["sy"]]
        unm = ys[len(ns):] if len(ys) > len(ns) else ns[len(ys):]
        if not unm:
            continue
        s = settle.get(x["slug"])
        settled = bool(s and s.get("settled") and s.get("yes_settle") is not None)
        end = x["end"]
        prints = sorted(x["prints"], key=lambda p: p["t"])
        pts = [p["t"] for p in prints]
        ts, mk_y, mk_n, cx_y, cx_n = touches(x["book"])
        for a in unm:
            sy = a["sy"]; dsy = not sy          # side we still need to buy
            mk = mk_y if dsy else mk_n
            cx = cx_y if dsy else cx_n
            i0 = bisect.bisect_right(pts, a["t"])
            post = prints[i0:]
            post = [p for p in post if p["t"] <= end]
            comps = [c for c in x["comp_log"] if c[2] == dsy and c[1] > a["t"]]

            def touch_at(tt, arr):
                j = bisect.bisect_right(ts, tt) - 1
                return arr[j] if j >= 0 else None

            acc, acc_cross = [], []
            for p in post:
                m = touch_at(p["t"], mk)
                if m is not None and _ok(p["px"], m, dsy):
                    acc.append(p)
                    continue
                c_ = touch_at(p["t"], cx)
                if c_ is not None and _ok(p["px"], c_, dsy):
                    acc_cross.append(p)
            resting = lambda tt: any(c[0] <= tt <= c[1] for c in comps)
            acc_rest = [p for p in acc if resting(p["t"])]
            qcleared = False
            for c in comps:
                cum = 0.0
                for p in acc:
                    if p["t"] < c[0] or p["t"] > c[1]:
                        continue
                    cum += p["size"]
                    if cum > c[4]:
                        qcleared = True
                        break
                if qcleared:
                    break
            pnl = None
            if settled:
                val = s["yes_settle"] if sy else 1.0 - s["yes_settle"]
                pnl = val - a["px"]
            if (not settled) or (end - a["t"]) < GAP:      cause = "WINDOW_CENSORING"
            elif not post:                                  cause = "MARKET_LIMIT"
            elif not acc:                                   cause = "PRICE_POLICY_LIMIT"
            elif a["comp_live"] and a["rel"] in ("open", "extend"): cause = "INVENTORY_EXTEND"
            elif not acc_rest:                              cause = "ORDER_ABSENT"
            elif not qcleared:                              cause = "QUEUE_REACH_LIMIT"
            else:                                           cause = "OTHER"
            rows.append({"cause": cause, "slug": x["slug"], "rel": a["rel"], "sy": sy,
                         "px": a["px"], "pnl": pnl, "settled": settled,
                         "t": a["t"], "obs_left": end - a["t"],
                         "n_post": len(post), "n_acc": len(acc),
                         "n_acc_cross_only": len(acc_cross),
                         "acc_vol": sum(p["size"] for p in acc),
                         "n_comp_rungs": len(comps), "resid_before": a["resid_before"],
                         "comp_live": a["comp_live"]})
    return rows
