"""Continuous TWO-SIDED maker replay -- the experiment that was never actually run.

The existing L5_SHADOW_TWO_SIDED arm says of itself: "PERMISSION to rest a second
side (the arm never seeks one)". It had permission and never quoted both sides, so
its -$0.1868/window verdict is NOT a verdict on market making.

Here both sides are quoted at all times and re-quoted immediately after each fill,
which is what produces many fills per window (L5: 32.48/window; the single-order
engine: 0.25-0.50).

Own-cost convention identical to replay_e:
    maker buy UP   rests a YES bid at yb; consumed by taker_side=='no'  px <= yb
    maker buy DOWN rests a NO  bid at nb; consumed by taker_side=='yes' px >= 1-nb
A maker queue clears by VOLUME only. A pair of maker legs costs yb+nb = 1-spread,
so every completed pair grosses the spread and settlement pays exactly 1.00.

THE RESIDUAL CONTROL is imb_cap: never add to a side whose inventory already
exceeds the other by that many contracts. That is the lever the single-order
engine does not have, because it can only hold one order at a time.
"""
import json, collections
from decimal import Decimal as D, ROUND_CEILING, ROUND_HALF_EVEN

TAKER_THETA = D("0.0695")
MAKER_THETA = D("-0.0125")
PER_ORDER   = D("0.01")
CENT        = D("0.01")
REBATE_RATE = D("0.0125")          # src/pmus_incentive_audit.MAKER_REBATE_RATE


def fee(theta, qty, wire_px):
    raw = theta * qty * wire_px * (D(1) - wire_px)
    if raw > 0:  return raw.quantize(CENT, rounding=ROUND_CEILING)
    if raw < 0:  return -((-raw).quantize(CENT))
    return D(0)


def credited_rebate(qty, own_px):
    """Venue formula, cent-rounded PER PRINT -- how the 25 real credits paid."""
    raw = REBATE_RATE * qty * own_px * (D(1) - own_px)
    return raw.quantize(CENT, rounding=ROUND_HALF_EVEN)


class MMOrder:
    __slots__ = ("side", "own", "wire", "qty", "filled", "t_live", "queue", "cleared", "charged")

    def __init__(s, side, own, wire, qty, t_live, queue):
        s.side, s.own, s.wire, s.qty = side, own, wire, qty
        s.filled, s.t_live, s.queue, s.cleared = D(0), t_live, queue, D(0)
        s.charged = False


def consumes(o, px, taker_side):
    if o.side == "UP":  return taker_side == "no"  and px <= o.wire
    else:               return taker_side == "yes" and px >= o.wire


class MMCfg:
    def __init__(s, **kw):
        s.pair_cap = D("0.99")      # quote only while yb+nb <= this
        s.notional = D("4.00")      # per order
        s.min_qty = D("0.50")
        s.imb_cap = D("3")          # THE residual control
        s.ttl = 60.0
        s.max_cash = D("40")        # bound on cash deployed in the window
        s.tte_stop = 60.0           # stop opening this close to expiry
        s.requote_on_move = True
        s.offset_ticks = 0          # quote N ticks BEHIND the touch
        s.tick = D('0.01')
        s.__dict__.update(kw)


def run_mm(w, cfg):
    frames = [(t, D(a), D(b), D(c), D(d)) for t, a, b, c, d in w["bbo"]]
    prints = [(t, D(p), D(q), sd) for t, p, q, sd in w["prints"]]
    close, yes_won = w["close"], w["yes_won"]
    pi = 0
    up = dn = D(0); cost = D(0); fees = D(0); reb = D(0)
    live = {"UP": None, "DOWN": None}
    fills = []; n_orders = 0; requotes = 0

    for i, (t, yb, ybq, nb, nbq) in enumerate(frames):
        if t >= close: break

        # ---- advance queues with volume that actually consumes each resting order
        while pi < len(prints) and prints[pi][0] <= t:
            pt, px, q, sd = prints[pi]
            for o in (live["UP"], live["DOWN"]):
                if o is not None and pt >= o.t_live and consumes(o, px, sd):
                    o.cleared += q
            pi += 1

        # ---- realise fills
        for side in ("UP", "DOWN"):
            o = live[side]
            if o is None: continue
            if o.cleared >= o.queue + o.qty:            # CONSERVATIVE: full queue + our size
                newfill = min(o.qty, o.cleared - o.queue) - o.filled
                if newfill > 0:
                    o.filled += newfill
                    cost += newfill * o.own
                    fees += -fee(MAKER_THETA, newfill, o.wire) * 0   # rebate handled separately
                    if not o.charged:
                        fees += PER_ORDER; o.charged = True
                    reb += credited_rebate(newfill, o.own)
                    if side == "UP": up += newfill
                    else:            dn += newfill
                    fills.append(dict(t=t, side=side, own=str(o.own), qty=str(newfill)))
                if o.filled >= o.qty:
                    live[side] = None

        # ---- cancel on TTL or a touch move
        for side, touch in (("UP", yb), ("DOWN", nb)):
            o = live[side]
            if o is None: continue
            if (t - o.t_live) >= cfg.ttl or (cfg.requote_on_move and o.own != touch):
                live[side] = None
                requotes += 1

        # ---- quote both sides
        if (close - t) <= cfg.tte_stop: continue
        if (yb + nb) > cfg.pair_cap: continue            # pair would not be economic
        for side, touch0, depth in (("UP", yb, ybq), ("DOWN", nb, nbq)):
            if live[side] is not None: continue
            touch = touch0 - cfg.tick * D(cfg.offset_ticks)
            if touch <= 0: continue
            inv_this, inv_other = (up, dn) if side == "UP" else (dn, up)
            if (inv_this - inv_other) >= cfg.imb_cap: continue   # residual control
            qty = cfg.notional / touch
            if qty < cfg.min_qty: continue
            if cost + qty * touch > cfg.max_cash: continue
            wire = touch if side == "UP" else (D(1) - touch)
            live[side] = MMOrder(side, touch, wire, qty, t, depth)
            n_orders += 1

    matched = min(up, dn); resid = up - dn; rq = abs(resid)
    rwin = (resid > 0 and yes_won == 1) or (resid < 0 and yes_won == 0)
    redeem = matched + (rq if rwin else D(0))
    net = redeem - cost - fees
    return dict(market=w["market"], series=w["series"],
                up=str(up), dn=str(dn), matched=str(matched), resid=str(resid),
                cost=str(cost), fees=str(fees), rebate=str(reb),
                net=str(net), net_inc_rebate=str(net + reb),
                n_fills=len(fills), n_orders=n_orders, requotes=requotes,
                stranded=bool(rq > 0), traded=bool(up > 0 or dn > 0), fills=fills)
