"""PHASE 3 -- transfer the FROZEN L5 ladder mechanics into the PMUS causal replay.

The ONLY thing added relative to the arm-3 baseline (B2 continuous inventory +
queue-preserving complements + L5_HISTORICAL completion) is the RUNG COUNT: a
decision rests R simultaneous maker rungs instead of one quote. Everything else
-- Model A, the relation logic, the completion rule, the decision gap, the
inventory cap, the causality invariant -- is byte-identical to arm 3.

FROZEN ON L5 TRAIN DATA, BEFORE ANY PMUS OUTCOME WAS CONSULTED:
    R    = 4 rungs        TRAIN median required-rungs floor (OOS median also 4)
    band = touch -2..+1   TRAIN recapture 69.8%, vs 53.7% purely passive and
                          51.6% purely aggressive
Neither number is re-fitted here. No PMUS threshold is searched, no settlement
outcome influences any action, and nothing is tuned toward PMUS P&L.

VENUE CONSTRAINTS THAT ARE NOT RELAXED. A rung is dropped if it would cross the
opposing touch -- there is no taker crossing, SELL, IOC or FOK anywhere in this
replay. Rung size stays at the arm-3 unit of one share, so the ablation isolates
the rung COUNT and does not smuggle in L5's price-conditional sizing.

QUEUE MODELLING, AND ITS BOUND. The PMUS book we collect is top-of-book only, so
depth at a price away from the touch is NOT observed. Rungs at or below the touch
are charged the observed touch depth; a rung ABOVE the touch is a price
improvement and becomes the best bid, so it is charged zero queue. Charging
touch depth to away-from-touch rungs is OPTIMISTIC, because resting depth
normally grows as you step away from the touch. `depth_mult` scales the charge
for away-from-touch rungs so the optimism can be bounded rather than assumed --
see the simulator audit, which reports 1x, 2x and 4x.
"""
import sys
from decimal import Decimal as D
SCR = "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
sys.path.insert(0, "/home/user/kalshi-arb-bot")
sys.path.insert(0, SCR)
import cohorts as C
from src import pmus_sports_l5_decision as dec

assert dec.MAX_RESIDUAL_UNITS == 3 and str(dec.PAIR_OBSERVATION_CAP) == "0.98"
M = C.M
TICK = 0.01
R_FROZEN = 4
BAND_FROZEN = (2, 1)          # ticks below, ticks above the touch


class Q:
    __slots__ = ("sy", "px", "t", "qa", "cum", "rel")

    def __init__(s, sy, px, t, qa, rel):
        s.sy, s.px, s.t, s.qa, s.cum, s.rel = sy, float(px), t, float(qa or 0.0), 0.0, rel


def _ok(p, px, sy):
    lim = px if sy else (1.0 - px)
    return (p["px"] <= lim) if sy else (p["px"] >= lim)


def _opposing_limit(sy, r):
    """The price at which a buy on side `sy` would CROSS. Buying YES crosses at
    the ask; buying NO (in NO terms) crosses at 1 - bid."""
    return float(r["ask"]) if sy else (1.0 - float(r["bid"]))


def rung_prices(touch, sy, r, rungs, band):
    """Ladder prices in the side's own price, nearest-the-touch first, with any
    crossing rung dropped."""
    lo, hi = band
    lim = _opposing_limit(sy, r)
    out = []
    for k in range(-lo, hi + 1):
        px = round(float(touch) + k * TICK, 4)
        if px <= 0.0 or px >= 1.0 or px >= lim - 1e-9:
            continue
        out.append(px)
        if len(out) >= rungs:
            break
    return out


def _closelog(stats, sy, tt):
    for c in stats.setdefault("comp_log", []):
        if c[2] == sy and c[1] is None:
            c[1] = tt


def _place(live, stats, sy, pxs, t, depth, rel, preserve, touch, depth_mult):
    """Rest the ladder `pxs` on side `sy`. A rung whose price is already resting
    survives untouched -- its timestamp, queue_ahead and cleared volume persist.
    Rungs no longer in the ladder are cancelled."""
    cur = {round(q.px, 4): q for q in live.get(sy, [])}
    keep = []
    for px in pxs:
        k = round(px, 4)
        if k in cur and preserve:
            stats["preserved"] += 1
            keep.append(cur.pop(k))
            continue
        cur.pop(k, None)
        qa = 0.0 if px > float(touch) + 1e-9 else float(depth or 0.0) * depth_mult
        keep.append(Q(sy, px, t, qa, rel))
        stats["placed"] += 1
        if rel == "complete":
            stats.setdefault("comp_log", []).append([t, None, sy, float(px), qa])
    for q in cur.values():
        stats["resets"] += 1
        stats["lifetimes"].append(t - q.t)
        if q.rel == "complete":
            _closelog(stats, sy, t)
    live[sy] = keep


def replay(slug, se, prints, rungs=R_FROZEN, band=BAND_FROZEN, preserve=True,
           completion="L5_HISTORICAL", ladder_sides="both", depth_mult=1.0):
    """ladder_sides: 'acquire' puts the ladder only on the side a decision is
    working and leaves the complement as arm 3's single rung; 'both' ladders the
    complement too. Reported separately -- one mechanic at a time."""
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
            for sy in list(live):
                for q in list(live.get(sy, [])):
                    if p["t"] <= q.t or p["t"] < t_last or not _ok(p, q.px, q.sy):
                        continue
                    q.cum += p["size"]
                    if q.cum <= q.qa:
                        continue
                    if q.rel == "complete":
                        sa = inv.avg(not q.sy)
                        if sa is not None and float(sa) + q.px > 0.98:
                            above98 += 1
                    before_r = int(inv.residual)
                    inv.record(q.sy, D(str(q.px)))
                    fills.append({"t": p["t"], "sy": q.sy, "px": q.px, "rel": q.rel,
                                  "resid_before": before_r, "resid_after": int(inv.residual),
                                  "comp_live": bool([k for k in live if k != sy and live[k]]),
                                  "qa": q.qa})
                    t_last = max(t_last, p["t"]); last_rel = q.rel
                    stats["lifetimes"].append(p["t"] - q.t)
                    if q.rel == "complete":
                        _closelog(stats, sy, p["t"])
                    live[sy] = [x for x in live[sy] if x is not q]
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
            n = rungs if (ladder_sides == "both" or rl != "complete") else 1
            pxs = rung_prices(px, sy, r, n, band if n > 1 else (0, 0))
            if pxs:
                _place(live, stats, sy, pxs, t, depth, rl, preserve, px, depth_mult)
            last_rel = rl
        if inv.residual != 0:
            dsy = inv.residual < 0
            sa = inv.avg(not dsy)
            if sa is not None:
                tch = dec._touch_for(dsy, bk)
                cpx = tch if completion == "L5_HISTORICAL" else dec.complement_target(sa, tch)[0]
                if cpx is not None:
                    depth = float((r["bid_qty"] if dsy else r["ask_qty"]) or 0.0)
                    n = rungs if ladder_sides == "both" else 1
                    pxs = rung_prices(float(cpx), dsy, r, n, band if n > 1 else (0, 0))
                    if pxs:
                        _place(live, stats, dsy, pxs, t, depth, "complete", preserve,
                               float(cpx), depth_mult)
    for c in stats["comp_log"]:
        if c[1] is None:
            c[1] = se[-1]["t"]
    yq = [f for f in fills if f["sy"]]; nq = [f for f in fills if not f["sy"]]
    yc = sum(f["px"] for f in yq); nc = sum(f["px"] for f in nq)
    return {"slug": slug, "fills": len(fills), "yes_n": len(yq), "no_n": len(nq),
            "yes_cost": yc, "no_cost": nc, "matched": min(len(yq), len(nq)),
            "resid": abs(len(yq) - len(nq)), "above98": above98, "acqs": fills,
            "comp_log": stats["comp_log"], "end": se[-1]["t"],
            "book": [(x["t"], x["bid"], x["ask"], x["bid_qty"], x["ask_qty"]) for x in se],
            "pair_basis": ((yc / len(yq) + nc / len(nq)) if yq and nq else None),
            **{"st_" + k: v for k, v in stats.items() if k != "lifetimes"},
            "lifetimes": stats["lifetimes"]}


def run(since, venue="PMUS", **kw):
    bks, fls = C.books(), C.flow()
    out = []
    for (v, slug), se in bks.items():
        if v != venue:
            continue
        s = [x for x in se if x["t"] > since]
        if len(s) < 3:
            continue
        rr = replay(slug, s, fls.get((v, slug), []), **kw)
        if rr["fills"]:
            rr["prints"] = [p for p in fls.get((v, slug), []) if p["t"] > since]
            out.append(rr)
    return out
