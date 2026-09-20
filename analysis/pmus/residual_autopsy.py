"""Residual autopsy for the B2 continuous-inventory arm.

Model A unchanged. This instruments the SAME replay as continuous_inventory_replay
(B2_PERSIST_BOTH) and records, for every acquisition, the causal context needed to
assign each surviving residual unit to exactly one cause. No threshold is fitted,
no settlement outcome influences any action, and the causality invariant holds.

CAUSE PARTITION, mutually exclusive, evaluated per residual unit over the window
from its acquisition to end of observation:

  MARKET_LIMIT      no opposite-side print of ANY price occurred afterwards
  PRICE_LIMIT       opposite prints occurred but none at or inside the frozen
                    complement_target price (i.e. only above our permitted basis)
  ORDER_ABSENT      an acceptable-priced print occurred while NO complement was
                    resting on the deficit side
  QUEUE_LIMIT       an acceptable print reached a RESTING complement but the
                    queue frozen at placement was never cleared
  INVENTORY_POLICY  the unit was acquired by OPEN/EXTEND while a complement was
                    already resting and unfilled -- residual grew faster than it
                    could be recycled
  WINDOW_CENSORING  the market never settled, or the unit was acquired with less
                    than one decision gap of observation remaining
"""
import sys, json, collections
from decimal import Decimal as D
SCR = "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
sys.path.insert(0, "/home/user/kalshi-arb-bot"); sys.path.insert(0, SCR)
import cohorts as C
from src import pmus_sports_l5_decision as dec

assert dec.MAX_RESIDUAL_UNITS == 3
M = C.M


class Q:
    __slots__ = ("sy", "px", "t", "qa", "cum", "rel")
    def __init__(s, sy, px, t, qa, rel):
        s.sy, s.px, s.t, s.qa, s.cum, s.rel = sy, float(px), t, float(qa or 0.0), 0.0, rel


def _ok(p, px, sy):
    lim = px if sy else (1.0 - px)
    return (p["px"] <= lim) if sy else (p["px"] >= lim)


def replay(slug, se, prints, cap_extends=None):
    """cap_extends: None = B2 unchanged. int = refuse same-side acquisition once
    |residual| >= that bound, leaving the complement resting (the L5 inventory
    discipline ablation). Bounds come from historical L5 behaviour, not search."""
    inv = dec.Inventory(); last_rel = ""; t_prev = 0.0; t_last = 0.0
    live = {}; acqs = []; pi = 0
    comp_log = []          # (t0, t1, side_yes, px, queue_ahead) for complement rungs
    def _close(sy, tt):
        for c in comp_log:
            if c[2] == sy and c[1] is None:
                c[1] = tt
    prints = sorted(prints, key=lambda p: p["t"])
    end = se[-1]["t"]
    for r in se:
        t = r["t"]
        bk = {"bid": D(str(r["bid"])), "ask": D(str(r["ask"])), "t": t}
        while pi < len(prints) and prints[pi]["t"] <= t:
            p = prints[pi]; pi += 1
            for sy, q in list(live.items()):
                if p["t"] <= q.t or p["t"] < t_last or not _ok(p, q.px, q.sy):
                    continue
                q.cum += p["size"]
                if q.cum > q.qa:
                    before = int(inv.residual)
                    inv.record(q.sy, D(str(q.px)))
                    acqs.append({"t": p["t"], "sy": q.sy, "px": q.px, "rel": q.rel,
                                 "resid_before": before, "resid_after": int(inv.residual),
                                 "comp_live": bool([k for k in live if k != sy]),
                                 "qa": q.qa, "matched_at": None, "idx": len(acqs)})
                    t_last = max(t_last, p["t"]); last_rel = q.rel
                    _close(sy, p["t"])
                    live.pop(sy, None)
        if t - t_prev < dec.DECISION_MIN_GAP_S:
            continue
        t_prev = t
        prop = dec._proposal(M, float(r["bid"]), inv, last_rel)
        if prop.get("off_distribution"):
            continue
        rel = prop["recommended_relation"]
        plan = dec.plan_action(rel, inv, bk)
        if plan["decision"] == "quote":
            px = float(plan["_px"]); sy = plan["_side_yes"]; rl = plan["relation"]
            blocked = (cap_extends is not None and rl in ("open", "extend")
                       and abs(int(inv.residual)) >= cap_extends)
            if not blocked:
                depth = float((r["bid_qty"] if sy else r["ask_qty"]) or 0.0)
                if sy in live: _close(sy, t)
                live[sy] = Q(sy, px, t, depth, rl)
                if rl == "complete": comp_log.append([t, None, sy, px, depth])
            last_rel = rl
        if inv.residual != 0:
            dsy = inv.residual < 0
            sa = inv.avg(not dsy)
            if sa is not None and dsy not in live:
                cpx, _ = dec.complement_target(sa, dec._touch_for(dsy, bk))
                if cpx is not None:
                    depth = float((r["bid_qty"] if dsy else r["ask_qty"]) or 0.0)
                    live[dsy] = Q(dsy, float(cpx), t, depth, "complete")
                    comp_log.append([t, None, dsy, float(cpx), depth])
    # pair acquisitions FIFO by side to mark which became matched
    ys = [a for a in acqs if a["sy"]]; ns = [a for a in acqs if not a["sy"]]
    for i in range(min(len(ys), len(ns))):
        tm = max(ys[i]["t"], ns[i]["t"])
        ys[i]["matched_at"] = tm; ns[i]["matched_at"] = tm
    for c in comp_log:
        if c[1] is None: c[1] = end
    return {"slug": slug, "acqs": acqs, "end": end, "comp_log": comp_log,
            "yes_n": len(ys), "no_n": len(ns),
            "yes_cost": sum(a["px"] for a in ys), "no_cost": sum(a["px"] for a in ns),
            "matched": min(len(ys), len(ns)), "resid": abs(len(ys) - len(ns))}


def run(since, cap_extends=None, venue="PMUS"):
    bks, fls = C.books(), C.flow()
    out = []
    for (v, slug), se in bks.items():
        if v != venue:
            continue
        s = [x for x in se if x["t"] > since]
        if len(s) < 3:
            continue
        rr = replay(slug, s, fls.get((v, slug), []), cap_extends)
        rr["prints"] = [p for p in fls.get((v, slug), []) if p["t"] > since]
        if rr["acqs"]:
            out.append(rr)
    return out
