"""Steps 2+3: queue-preserving complements, and a DIAGNOSTIC replay under the
completion discipline L5 actually showed.

MODEL A IS UNCHANGED. Relations still come from dec._proposal and sides/prices
from dec.plan_action. Two orthogonal switches:

  preserve_queue  an already-resting order is NOT cancelled and recreated when a
                  later decision would place an ECONOMICALLY IDENTICAL order
                  (same side, same price). Its placement timestamp, queue_ahead
                  and accumulated cleared volume survive. It is replaced only
                  when the frozen policy actually wants a different side/price.
                  This is EXECUTION CORRECTNESS, not strategy.

  completion      FROZEN_098    complement priced by dec.complement_target, i.e.
                                min(hard bound, 0.98 cap, deficit touch).
                  L5_HISTORICAL complement priced at the deficit-side touch with
                                NO pair cap -- the discipline measured in the
                                corpus audit, where 47.5% of L5's own completions
                                exceeded 0.98 and 42.8% exceeded 1.00.

L5_HISTORICAL IS A DIAGNOSTIC ONLY. CLAUDE.md locks near-$1 pair tolerance as
later incentive-era behaviour pending source-confirmed economics for our account.
Running it here measures the COST OF THE LOCK; it is not a proposal to unlock and
nothing is tuned toward PMUS P&L -- the rule comes from L5 data alone.
"""
import sys
from decimal import Decimal as D
SCR = "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
sys.path.insert(0, "/home/user/kalshi-arb-bot"); sys.path.insert(0, SCR)
import cohorts as C
from src import pmus_sports_l5_decision as dec

assert dec.MAX_RESIDUAL_UNITS == 3 and str(dec.PAIR_OBSERVATION_CAP) == "0.98"
M = C.M


class Q:
    __slots__ = ("sy", "px", "t", "qa", "cum", "rel")
    def __init__(s, sy, px, t, qa, rel):
        s.sy, s.px, s.t, s.qa, s.cum, s.rel = sy, float(px), t, float(qa or 0.0), 0.0, rel


def _ok(p, px, sy):
    lim = px if sy else (1.0 - px)
    return (p["px"] <= lim) if sy else (p["px"] >= lim)


def _closelog(stats, sy, tt):
    """Close every still-open complement interval on side sy at tt."""
    for c in stats.setdefault("comp_log", []):
        if c[2] == sy and c[1] is None:
            c[1] = tt


def _place(live, stats, sy, px, t, depth, rel, preserve):
    """Place or refresh a rung. Returns nothing; mutates live/stats."""
    cur = live.get(sy)
    if cur is not None and preserve and abs(cur.px - float(px)) < 1e-9:
        stats["preserved"] += 1          # identical order: queue state survives
        return
    if cur is not None:
        stats["resets"] += 1
        stats["lifetimes"].append(t - cur.t)
        _closelog(stats, sy, t)          # the superseded rung stops resting HERE
    live[sy] = Q(sy, px, t, depth, rel)
    stats["placed"] += 1
    if rel == "complete": stats.setdefault("comp_log", []).append([t, None, sy, float(px), float(depth or 0)])


def replay(slug, se, prints, preserve=False, completion="FROZEN_098"):
    inv = dec.Inventory(); last_rel = ""; t_prev = 0.0; t_last = 0.0
    live = {}; fills = []; pi = 0
    stats = {"placed": 0, "resets": 0, "preserved": 0, "lifetimes": [], "comp_log": []}
    prints = sorted(prints, key=lambda p: p["t"])
    above98 = 0
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
                    if q.rel == "complete":
                        sa = inv.avg(not q.sy)
                        if sa is not None and float(sa) + q.px > 0.98:
                            above98 += 1
                    before_r = int(inv.residual)
                    inv.record(q.sy, D(str(q.px)))
                    fills.append({"t": p["t"], "sy": q.sy, "px": q.px, "rel": q.rel})
                    t_last = max(t_last, p["t"]); last_rel = q.rel
                    stats["lifetimes"].append(p["t"] - q.t)
                    fills[-1]["resid_before"] = before_r
                    fills[-1]["resid_after"] = int(inv.residual)
                    fills[-1]["comp_live"] = bool([k for k in live if k != sy])
                    fills[-1]["qa"] = q.qa
                    _closelog(stats, sy, p["t"])
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
            sy = plan["_side_yes"]; rl = plan["relation"]
            px = float(plan["_px"])
            if rl == "complete" and completion == "L5_HISTORICAL":
                tch = dec._touch_for(sy, bk)
                if tch is not None:
                    px = float(tch)
            depth = float((r["bid_qty"] if sy else r["ask_qty"]) or 0.0)
            _place(live, stats, sy, px, t, depth, rl, preserve)
            last_rel = rl
        if inv.residual != 0:
            dsy = inv.residual < 0
            sa = inv.avg(not dsy)
            if sa is not None:
                tch = dec._touch_for(dsy, bk)
                if completion == "L5_HISTORICAL":
                    cpx = tch
                else:
                    cpx, _ = dec.complement_target(sa, tch)
                if cpx is not None:
                    depth = float((r["bid_qty"] if dsy else r["ask_qty"]) or 0.0)
                    _place(live, stats, dsy, float(cpx), t, depth, "complete", preserve)
    for c in stats.get("comp_log", []):
        if c[1] is None: c[1] = se[-1]["t"]
    yq = [f for f in fills if f["sy"]]; nq = [f for f in fills if not f["sy"]]
    yc = sum(f["px"] for f in yq); nc = sum(f["px"] for f in nq)
    return {"slug": slug, "fills": len(fills), "yes_n": len(yq), "no_n": len(nq),
            "yes_cost": yc, "no_cost": nc, "matched": min(len(yq), len(nq)),
            "resid": abs(len(yq) - len(nq)),
            "pair_basis": ((yc/len(yq) + nc/len(nq)) if yq and nq else None),
            "above98": above98, "acqs": fills, "comp_log": stats.get("comp_log", []),
            "end": se[-1]["t"], "book": [(r["t"], r["bid"], r["ask"], r["bid_qty"], r["ask_qty"]) for r in se], **{"st_" + k: v for k, v in stats.items() if k != "lifetimes"},
            "lifetimes": stats["lifetimes"]}


def run(since, preserve=False, completion="FROZEN_098", venue="PMUS"):
    bks, fls = C.books(), C.flow()
    out = []
    for (v, slug), se in bks.items():
        if v != venue:
            continue
        s = [x for x in se if x["t"] > since]
        if len(s) < 3:
            continue
        rr = replay(slug, s, fls.get((v, slug), []), preserve, completion)
        if rr["fills"]:
            out.append(rr)
    return out
