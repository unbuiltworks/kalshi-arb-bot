"""Timestamp-causal replay: maker-open vs taker-open, on independent Aug-2026 windows.

Own-cost convention throughout. The wire is YES.
    taker buy UP   -> lift the YES ask = 1 - no_bid, depth no_bid_qty
    taker buy DOWN -> lift the NO  ask = 1 - yes_bid, depth yes_bid_qty
    maker buy UP   -> rest a YES bid at yb; consumed by taker_side=='no' prints at px <= yb
    maker buy DOWN -> rest a NO  bid at nb (sell YES at 1-nb);
                      consumed by taker_side=='yes' prints at px >= 1-nb
A maker queue is cleared by VOLUME only -- never by a print merely passing through,
which would credit a cancellation ahead of us as queue advancement.
"""
import json, collections
import collections
from decimal import Decimal as D, ROUND_CEILING

TAKER_THETA = D("0.0695")
MAKER_THETA = D("-0.0125")
PER_ORDER   = D("0.01")

def fee(theta, qty, wire_px):
    raw = theta * qty * wire_px * (D(1) - wire_px)
    if raw > 0:  return raw.quantize(D("0.01"), rounding=ROUND_CEILING)
    if raw < 0:  return -((-raw).quantize(D("0.01")))
    return D(0)

class Order:
    __slots__ = ("side","kind","own","wire","qty","filled","t_live","queue",
                 "cleared","fees","cx_at")
    def __init__(s, side, kind, own, wire, qty, t_live, queue):
        s.side, s.kind, s.own, s.wire, s.qty = side, kind, own, wire, qty
        s.filled, s.t_live, s.queue, s.cleared, s.fees = D(0), t_live, queue, D(0), D(0)
        s.cx_at = None

def consumes(o, px, taker_side):
    if o.side == "UP":   return taker_side == "no"  and px <= o.wire
    else:                return taker_side == "yes" and px >= o.wire

class Cfg:
    def __init__(s, **kw):
        s.lam=D("1.0"); s.ttl=120.0; s.tte_min=240.0; s.notional=D("0.80")
        s.entry_pair_cap=D("0.96"); s.complete_pair_cap=D("0.96")
        s.min_qty=D("0.50"); s.scenario="CONSERVATIVE"
        # BOUNDED DEFENSIVE COMPLETION: cross to close an EXISTING residual when
        # the fee-inclusive pair clears this ceiling. None disables it entirely.
        # It never opens anything; it only retires inventory already held.
        s.defensive_ceiling=None
        s.preempt_s=None            # preempt a resting maker only inside the last N s
        s.defensive_arm_s=540.0      # only inside the last N seconds of the window
        s.entry_filter=None          # callable(frame)->bool for selective entry
        s.imb_min=D("0")             # minimum |book imbalance| conviction
        # CANCELLATION RULE. Withdraw a resting order when book imbalance turns
        # against the side it is resting on. None disables it.
        #   cancel_imb   threshold on signed imbalance against our side
        #   cancel_lat_s signal processing + submit + venue ack latency; the
        #                order remains fillable throughout, which is the
        #                fill-before-cancel race
        s.cancel_imb=None
        s.cancel_lat_s=1.0
        s.__dict__.update(kw)

def mid_at(frames, t):
    best=None
    for fr in frames:
        if fr[0] <= t: best=fr
        else: break
    if best is None: return None
    return (best[1] + (D(1)-best[3])) / D(2)      # (yes_bid + yes_ask)/2

def run_window(w, policy, cfg):
    frames=[(t, D(a), D(b), D(c), D(d)) for t,a,b,c,d in w["bbo"]]
    prints=[(t, D(p), D(q), sd) for t,p,q,sd in w["prints"]]
    close=w["close"]; yes_won=w["yes_won"]
    pi=0
    up=dn=D(0); cost=D(0); cup=D(0); cdn=D(0); fees=D(0); rebate=D(0)
    order=None; fills=[]; opened=False; efeat=None
    cxstats=collections.Counter()
    for i,(t, yb, ybq, nb, nbq) in enumerate(frames):
        if t >= close: break
        own_up_t, own_dn_t = D(1)-nb, D(1)-yb        # taker own costs
        own_up_m, own_dn_m = yb, nb                  # maker own costs (at touch)
        # ---- advance a live maker order's queue with volume that actually consumes it
        if order is not None and order.kind=="MAKER":
            while pi < len(prints) and prints[pi][0] <= t:
                pt,px,q,sd = prints[pi]
                if pt >= order.t_live and consumes(order, px, sd):
                    order.cleared += q
                pi += 1
            thresh = order.queue + (order.qty if cfg.scenario=="CONSERVATIVE" else D(0))
            if order.cleared >= thresh:
                newfill = min(order.qty, order.cleared - order.queue)
                newfill = max(D(0), newfill - order.filled)
                if newfill > 0:
                    if getattr(order,"cx_at",None) is not None:
                        cxstats["lost_race"]+=1
                    order.filled += newfill
                    cost += newfill*order.own
                    f=fee(MAKER_THETA, newfill, order.wire); rebate += -f
                    fees += PER_ORDER if order.filled==newfill else D(0)
                    if order.side=="UP": up+=newfill; cup+=newfill*order.own
                    else:               dn+=newfill; cdn+=newfill*order.own
                    fills.append(dict(t=t,side=order.side,kind="MAKER",
                                      own=str(order.own),qty=str(newfill),frame=i))
                    if order.filled >= order.qty: order=None
            # ---- cancellation trigger, evaluated on information available NOW
            if (order is not None and cfg.cancel_imb is not None
                    and getattr(order,"cx_at",None) is None):
                tot=ybq+nbq
                if tot>0:
                    imb=(ybq-nbq)/tot
                    against=(-imb) if order.side=="UP" else imb
                    if against >= cfg.cancel_imb:
                        order.cx_at=t+float(cfg.cancel_lat_s)
                        cxstats["triggered"]+=1
            # the cancel only takes effect after the latency; until then the
            # order is still live and can still fill -- the race is real
            if (order is not None and getattr(order,"cx_at",None) is not None
                    and t >= order.cx_at):
                cxstats["effective"]+=1
                cxstats["qty_withdrawn"]+=float(order.qty-order.filled)
                order=None
                continue
            if order is not None and (t - order.t_live) >= cfg.ttl:
                order=None                                    # TTL cancel
        while pi < len(prints) and prints[pi][0] <= t: pi += 1
        tte = close - t
        resid = up - dn
        # ---------------- COMPLETE an existing residual (always MAKER) -------------
        if resid != 0 and cfg.defensive_ceiling is not None and \
           (close - t) <= cfg.defensive_arm_s:
            need2  = "DOWN" if resid>0 else "UP"
            held2  = (cup/up) if resid>0 else (cdn/dn)
            own2   = own_dn_t if need2=="DOWN" else own_up_t
            depth2 = (ybq if need2=="DOWN" else nbq)
            q2     = min(abs(resid), depth2)
            _pre = (cfg.preempt_s is not None and (close-t) <= cfg.preempt_s)
            if q2 > 0 and (order is None or _pre):
                wire2 = (D(1)-own2) if need2=="DOWN" else own2
                f2    = fee(TAKER_THETA, q2, wire2) + PER_ORDER
                allin = held2 + own2 + f2/q2
                if allin <= cfg.defensive_ceiling:
                    order=None          # preempt: pull the resting maker
                    cost += q2*own2; fees += f2
                    if need2=="UP": up+=q2; cup+=q2*own2
                    else:           dn+=q2; cdn+=q2*own2
                    fills.append(dict(t=t,side=need2,kind="DEFENSIVE",
                                      own=str(own2),qty=str(q2),frame=i,
                                      allin=str(allin)))
                    continue
        if order is not None: continue
        if resid != 0:
            need   = "DOWN" if resid>0 else "UP"
            held_v = (cup/up) if resid>0 else (cdn/dn)      # per-unit cost of the HELD leg
            allow  = cfg.complete_pair_cap - held_v
            touch  = own_dn_m if need=="DOWN" else own_up_m
            px     = min(touch, allow)
            if px <= 0 or tte <= 0: continue
            # a NO bid at own px is an offer to sell YES at 1-px; a YES bid at own px is at px
            wire = (D(1)-px) if need=="DOWN" else px
            qa   = (nbq if need=="DOWN" else ybq) if px>=touch else D(0)
            order = Order(need,"MAKER",px,wire,abs(resid), t+float(cfg.lam), qa)
            continue
        # ---------------- OPEN -----------------------------------------------------
        if opened or tte < cfg.tte_min: continue
        if cfg.entry_filter is not None and not cfg.entry_filter(
                dict(yb=yb,ybq=ybq,nb=nb,nbq=nbq,tte=tte,t=t)): continue
        if min(yb,nb) < D("0.05") or max(yb,nb) > D("0.95"): continue
        # 2x2: execution type {MAKER,TAKER} x selection {cheapest-first, L5 band}
        kind = "MAKER" if policy in ("A","D") else "TAKER"
        oup, odn = (own_up_m, own_dn_m) if kind=="MAKER" else (own_up_t, own_dn_t)
        if policy == "IMB":
            # BOOK-IMBALANCE SIDE SELECTION. Rest on the side the book is moving
            # TOWARD: a fill then arrives on a dip into a rising mid rather than
            # in front of a falling one. Signal measured out-of-sample at
            # beta +0.906c / 6.5s, 48 of 54 markets positive, t=+6.41.
            imb = (ybq-nbq)/(ybq+nbq) if (ybq+nbq) > 0 else D(0)
            if abs(imb) < cfg.imb_min: continue          # no conviction -> WAIT
            side = "UP" if imb > 0 else "DOWN"
            own  = oup if side=="UP" else odn
        elif policy in ("A","B"):                            # cheapest-first (our engine)
            side = "UP" if oup <= odn else "DOWN"
            own  = oup if side=="UP" else odn
        else:                                                # L5's own opening band
            inband=[c for c in (("UP",oup),("DOWN",odn))
                    if D("0.5")<=c[1]<=D("0.8")]
            if not inband: continue                          # L5 does not open here
            side,own = min(inband, key=lambda c: abs(c[1]-D("0.59")))
        if kind=="MAKER":
            # rest at the touch when the touch already permits the pair, else behind it
            other_touch = own_dn_m if side=="UP" else own_up_m
            own = min(own, cfg.entry_pair_cap - other_touch)
            if own < D("0.05"): continue
        else:
            if own > cfg.entry_pair_cap - D("0.05"): continue   # no room for a complement
        dear = max(own, cfg.entry_pair_cap-own)
        qty  = (cfg.notional/dear) if dear>0 else D(0)
        if qty < cfg.min_qty: continue
        if kind=="TAKER":
            # latency: execute against the first frame at or after t+lam
            j=i
            while j<len(frames) and frames[j][0] < t+float(cfg.lam): j+=1
            if j>=len(frames): continue
            _,yb2,ybq2,nb2,nbq2 = frames[j]
            own2  = (D(1)-nb2) if side=="UP" else (D(1)-yb2)
            depth = (nbq2 if side=="UP" else ybq2)
            exe   = min(qty, depth)                          # partial fill on thin depth
            if exe < cfg.min_qty: continue
            wire  = own2 if side=="UP" else D(1)-own2
            cost += exe*own2; fees += fee(TAKER_THETA,exe,wire)+PER_ORDER
            if side=="UP": up+=exe; cup+=exe*own2
            else:          dn+=exe; cdn+=exe*own2
            fills.append(dict(t=frames[j][0],side=side,kind="TAKER",own=str(own2),
                              qty=str(exe),frame=j,slip=str(own2-own)))
            opened=True
        else:
            wire  = own if side=="UP" else D(1)-own
            touch = own_up_m if side=="UP" else own_dn_m
            qa    = (ybq if side=="UP" else nbq) if own >= touch else D(0)
            order = Order(side,"MAKER",own,wire,qty,t+float(cfg.lam),qa)
            opened=True
            efeat=dict(spread=str(D(1)-yb-nb), tte=tte, mid=str((yb+(D(1)-nb))/D(2)),
                       absmid=str(abs((yb+(D(1)-nb))/D(2)-D("0.5"))),
                       depth=str(min(ybq,nbq)), own=str(own), side=side)
    # adverse selection at SEVERAL horizons. Our production figure uses
    # placement->window close; L5's benchmark used placement->his next action
    # (median 3-30s). A longer horizon captures more drift by construction, so
    # the rates are only comparable at a matched horizon.
    adv=0
    for f in fills:
        m0=mid_at(frames,f["t"])
        if m0 is None: continue
        f["mid0"]=str(m0)
        for H in (10.0,30.0,60.0):
            m1=mid_at(frames,f["t"]+H)
            if m1 is None: continue
            bad=(f["side"]=="UP" and m1<m0) or (f["side"]=="DOWN" and m1>m0)
            f["adv_%d"%int(H)]=bool(bad)
        mc=mid_at(frames,close-1.0)          # to window close, our production def
        if mc is not None:
            bad=(f["side"]=="UP" and mc<m0) or (f["side"]=="DOWN" and mc>m0)
            f["adv_close"]=bool(bad)
            if bad: adv+=1
    matched = min(up,dn); resid = up-dn
    rq = abs(resid)
    rwin = (resid>0 and yes_won==1) or (resid<0 and yes_won==0)
    redeem = matched + (rq if rwin else D(0))
    return dict(market=w["market"], series=w["series"], close=close,
                up=str(up), dn=str(dn), matched=str(matched), resid=str(resid),
                cost=str(cost), fees=str(fees), rebate=str(rebate),
                redeem=str(redeem),
                net=str(redeem-cost-fees), net_pre_fee=str(redeem-cost),
                stranded=bool(rq>0), traded=bool(up>0 or dn>0), fills=fills, efeat=efeat,
                cx=dict(cxstats),
                adverse=adv, nfills=len(fills))

def summarise(rs, label):
    tr=[r for r in rs if r["traded"]]
    net=sum(D(r["net"]) for r in tr); cost=sum(D(r["cost"]) for r in tr)
    mu=sum(D(r["matched"]) for r in tr); sq=sum(abs(D(r["resid"])) for r in tr)
    st=[r for r in tr if r["stranded"]]
    run=D(0); peak=D(0); dd=D(0)
    for r in sorted(tr,key=lambda r:r["close"]):
        run+=D(r["net"]); peak=max(peak,run); dd=max(dd,peak-run)
    return dict(label=label, eligible=len(rs), traded=len(tr),
                net=net, per_window=(net/len(tr) if tr else D(0)),
                cost=cost, roc=(net/cost*100 if cost else D(0)),
                matched=mu, stranded_units=sq, stranded_windows=len(st),
                clean=len(tr)-len(st), max_dd=dd,
                fees=sum(D(r["fees"]) for r in tr),
                rebate=sum(D(r["rebate"]) for r in tr),
                takerfills=sum(1 for r in tr for f in r["fills"] if f["kind"]=="TAKER"),
                makerfills=sum(1 for r in tr for f in r["fills"] if f["kind"]=="MAKER"),
                defensive=sum(1 for r in tr for f in r["fills"] if f["kind"]=="DEFENSIVE"),
                fills=sum(r["nfills"] for r in tr),
                adverse=sum(r["adverse"] for r in tr),
                adv_rate=(D(sum(r["adverse"] for r in tr))/D(sum(r["nfills"] for r in tr))
                          if sum(r["nfills"] for r in tr) else D(0)))
