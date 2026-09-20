"""Item 4: frozen-L5 CONTINUOUS-INVENTORY replay against the real PMUS tape.

MODEL A IS UNCHANGED. Every action still comes from dec._proposal +
dec.plan_action + dec.complement_target with the shipped MAX_RESIDUAL_UNITS.
What varies between arms is ORDER MECHANICS ONLY -- how long a quote rests and
how many rest at once -- which is exactly the continuous-inventory behaviour the
owner described. No PMUS-specific parameter is introduced or tuned.

CAUSALITY. A decision sees only the book at its own timestamp. A resting quote
fills only when cumulative COMPATIBLE volume printed AFTER its placement exceeds
the depth resting at its price WHEN IT JOINED -- the same queue-aware rule used
everywhere else. No settlement outcome and no future flow influences any action.
A fill may never be credited before the previous fill (the 040a1433 invariant).

ARMS
  A_SINGLE_SHOT   each decision's quote is tested once over a bounded window and
                  then abandoned -- the current implementation's mechanics.
  B1_PERSIST      the quote RESTS from placement until it fills or the next
                  decision on that side replaces it. Nothing else differs.
  B2_PERSIST_BOTH B1 plus: whenever a directional surplus exists, the frozen
                  complement_target rung also rests on the deficit side at the
                  same time, so both sides can be live together.
"""
import sys, json, collections
from decimal import Decimal as D
SCR = "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
sys.path.insert(0, "/home/user/kalshi-arb-bot"); sys.path.insert(0, SCR)
import cohorts as C
from src import pmus_sports_l5_decision as dec

assert dec.MAX_RESIDUAL_UNITS == 3, "frozen cap must be unchanged"
M = C.M
LOOKAHEAD = 600.0


class Quote:
    __slots__ = ("side_yes", "px", "t", "queue_ahead", "cum", "rel")

    def __init__(self, side_yes, px, t, queue_ahead, rel):
        self.side_yes, self.px, self.t = side_yes, float(px), t
        self.queue_ahead, self.cum, self.rel = float(queue_ahead or 0.0), 0.0, rel


def _compatible(p, px, side_yes):
    lim = px if side_yes else (1.0 - px)
    return (p["px"] <= lim) if side_yes else (p["px"] >= lim)


def replay(slug, se, prints, arm):
    inv = dec.Inventory(); last_rel = ""; t_prev = 0.0; t_last_fill = 0.0
    live = {}                       # side_yes -> Quote
    fills = []
    pi = 0                          # pointer into prints
    prints = sorted(prints, key=lambda p: p["t"])
    for r in se:
        t = r["t"]
        bk = {"bid": D(str(r["bid"])), "ask": D(str(r["ask"])), "t": t}
        # ---- 1. age resting quotes against prints that arrived since last obs
        while pi < len(prints) and prints[pi]["t"] <= t:
            p = prints[pi]; pi += 1
            for sy, q in list(live.items()):
                if p["t"] <= q.t or p["t"] < t_last_fill:
                    continue
                if not _compatible(p, q.px, q.side_yes):
                    continue
                q.cum += p["size"]
                if q.cum > q.queue_ahead:
                    inv.record(q.side_yes, D(str(q.px)))
                    fills.append({"t": p["t"], "side_yes": q.side_yes,
                                  "px": q.px, "rel": q.rel})
                    t_last_fill = max(t_last_fill, p["t"])
                    last_rel = q.rel
                    live.pop(sy, None)
        # ---- 2. decision cadence, unchanged
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
            depth = float((r["bid_qty"] if sy else r["ask_qty"]) or 0.0)
            if arm == "A_SINGLE_SHOT":
                cum = 0.0; hit = None
                for p in prints:
                    if p["t"] <= max(t, t_last_fill) or p["t"] > t + LOOKAHEAD:
                        continue
                    if not _compatible(p, px, sy):
                        continue
                    cum += p["size"]
                    if cum > depth:
                        hit = p["t"]; break
                if hit is not None:
                    inv.record(sy, D(str(px)))
                    fills.append({"t": hit, "side_yes": sy, "px": px, "rel": rl})
                    t_last_fill = max(t_last_fill, hit)
                last_rel = rl
            else:
                live[sy] = Quote(sy, px, t, depth, rl)
                last_rel = rl
        # ---- 3. B2 only: rest the frozen complement alongside
        if arm == "B2_PERSIST_BOTH" and inv.residual != 0:
            dsy = inv.residual < 0                     # deficit side
            surplus_avg = inv.avg(not dsy)
            if surplus_avg is not None and dsy not in live:
                cpx, _ = dec.complement_target(
                    surplus_avg, dec._touch_for(dsy, bk))
                if cpx is not None:
                    depth = float((r["bid_qty"] if dsy else r["ask_qty"]) or 0.0)
                    live[dsy] = Quote(dsy, float(cpx), t, depth, "complete")
    yq = sum(1 for f in fills if f["side_yes"])
    nq = len(fills) - yq
    ycost = sum(f["px"] for f in fills if f["side_yes"])
    ncost = sum(f["px"] for f in fills if not f["side_yes"])
    return {"slug": slug, "fills": len(fills), "yes_n": yq, "no_n": nq,
            "yes_cost": ycost, "no_cost": ncost,
            "matched": min(yq, nq), "resid": abs(yq - nq),
            "pair_basis": ((ycost / yq + ncost / nq) if yq and nq else None),
            "t_first": (fills[0]["t"] if fills else None),
            "t_end": (se[-1]["t"] if se else None),
            "extends": sum(1 for f in fills if f["rel"] == "extend"),
            "completes": sum(1 for f in fills if f["rel"] == "complete")}


def run(arm, since, venue="PMUS"):
    bks, fls = C.books(), C.flow()
    out = []
    for (v, slug), se in bks.items():
        if v != venue:
            continue
        s = [x for x in se if x["t"] > since]
        if len(s) < 3:
            continue
        rr = replay(slug, s, fls.get((v, slug), []), arm)
        if rr["fills"]:
            out.append(rr)
    return out
