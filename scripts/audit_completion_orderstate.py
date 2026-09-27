"""Per-completion order-state audit. Read-only, analysis only.

Mirrors run_window under queue-aware fills and records, at every completion:
inventory before -> completion -> outstanding passive orders after -> next
acquisition -> resulting residual. Each acquisition fill is tagged PRE (the
order was already resting at that exact level before the most recent
completion) or NEW (the level was joined after it).
"""
import json, collections, sys
from decimal import Decimal as D
sys.path.insert(0, __import__('os').path.dirname(
    __import__('os').path.dirname(__import__('os').path.abspath(__file__))))
from src import pmus_completion_shadow as S

TAPE = 'data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl'


def load():
    by = collections.defaultdict(list)
    for l in open(TAPE):
        r = json.loads(l)
        if r.get('slug'):
            by[r['slug']].append(r)
    for v in by.values():
        v.sort(key=lambda x: str(x['t']))
    return by


def walk(rows, clip, theta, variant, winner, atomic_recycle=False,
         hold_s=60.0, cap_k=D(2)):
    q = {'up': D(0), 'down': D(0)}
    c = {'up': D(0), 'down': D(0)}
    cash = D(0); fees = D(0); completed = D(0)
    qstate = {}
    # (side, px) -> True if that resting order predates the last completion
    resting_since_before_completion = {}
    events = []
    fills = []          # (side, qty, px, tag)
    cancels = 0
    blocked_until = 0.0; need_flat = False; max_clip = D(0)
    ladder = None

    for row in rows:
        if row.get('kind') == 'book':
            ladder = row; continue
        if row.get('kind') != 'bbo': continue
        if str(row.get('state') or '') != 'MARKET_STATE_OPEN': continue
        t = row['t']; now = S._epoch(t)

        r = q['up'] - q['down']
        if theta is not None and r != 0:
            held = 'up' if r > 0 else 'down'
            comp = 'down' if held == 'up' else 'up'
            basis = (c[held] / q[held]) if q[held] > 0 else D(0)
            lv = S.buy_levels(ladder, comp) if ladder else []
            n, cost, fee, short = S.marginal_complete(lv, abs(r), basis, theta)
            if n > 0:
                before = dict(q)
                cash += cost; fees += fee
                q[held] -= n; c[held] -= n * basis
                completed += n
                outstanding_before_cancel = {
                    k: dict(v) for k, v in qstate.items()
                    if v and v.get('px') is not None}
                if atomic_recycle and qstate:
                    cancels += len(outstanding_before_cancel)
                    qstate.clear()
                    resting_since_before_completion.clear()
                else:
                    # every order still resting now PREDATES this completion
                    for k, v in outstanding_before_cancel.items():
                        resting_since_before_completion[(k, v['px'])] = True
                events.append(dict(
                    t=t, kind='COMPLETE', side=comp, qty=n,
                    inv_before=before, inv_after=dict(q),
                    outstanding=outstanding_before_cancel,
                    cancelled=(len(outstanding_before_cancel)
                               if atomic_recycle else 0)))
                if variant == 'B': blocked_until = now + hold_s
                elif variant == 'C': need_flat = True

        if need_flat and (q['up'] - q['down']) != 0: continue
        need_flat = False
        for side in ('up', 'down'):
            if variant == 'B' and now < blocked_until:
                rr = q['up'] - q['down']
                if rr != 0 and ((rr > 0) == (side == 'up')): continue
            venue_px = S.resting_venue_price(row, side)
            pre_existing = resting_since_before_completion.get(
                (side, venue_px), False)
            qty, px = S.maker_fill(row, side, clip, fill_model='queue_aware',
                                   ladder=ladder, qstate=qstate)
            if qty <= 0 or px is None: continue
            rr = q['up'] - q['down']
            signed = qty if side == 'up' else -qty
            after = rr + signed
            if rr != 0 and abs(after) > abs(rr):
                mc = max_clip if max_clip > qty else qty
                if abs(after) > cap_k * mc: continue
            q[side] += qty; c[side] += qty * px; cash += qty * px
            if qty > max_clip: max_clip = qty
            tag = 'PRE' if pre_existing else 'NEW'
            fills.append((side, qty, px, tag, t))
            events.append(dict(t=t, kind='ACQUIRE', side=side, qty=qty,
                               px=px, tag=tag, inv_after=dict(q)))
            resting_since_before_completion.pop((side, venue_px), None)

    m = min(q['up'], q['down'])
    resid = q['up'] - q['down']
    payout = completed * D(1) + m * D(1)
    if resid > 0 and winner == 'up': payout += resid * D(1)
    if resid < 0 and winner == 'down': payout += (-resid) * D(1)
    net = payout - cash - fees

    # attribute the residual to PRE vs NEW fills on the residual side
    rside = 'up' if resid > 0 else ('down' if resid < 0 else None)
    pre_q = new_q = D(0)
    if rside:
        # residual is the un-matched tail of that side: attribute LIFO
        need = abs(resid)
        for (side, qty, px, tag, t) in reversed(fills):
            if side != rside or need <= 0: continue
            take = qty if qty < need else need
            need -= take
            if tag == 'PRE': pre_q += take
            else: new_q += take
    return dict(net=net, fees=fees, cash=cash, completed=completed,
                resid=resid, resid_side=rside, matched_held=m,
                events=events, fills=fills, cancels=cancels,
                resid_pre=pre_q, resid_new=new_q, q=dict(q), c=dict(c))
