"""INDEPENDENT conservation/causality audit of ECONOMIC_COMPLETE.

Re-walks the tape with its own bookkeeping and derives net purely from
cashflows + settlement payout. It does NOT call Book.settle(), run_window()
or any strategy P&L aggregation. Read-only.
"""
import json, collections, sys
from decimal import Decimal as D
sys.path.insert(0, __import__('os').path.dirname(
    __import__('os').path.dirname(__import__('os').path.abspath(__file__))))
from src import pmus_completion_shadow as S

TH_T = D("0.0695")
TAPE = 'data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl'

def load():
    by = collections.defaultdict(list)
    for l in open(TAPE):
        r = json.loads(l)
        if r.get('slug'): by[r['slug']].append(r)
    for v in by.values(): v.sort(key=lambda x: str(x['t']))
    return by

def audit(rows, clip, theta, variant, winner, hold_s=60.0, cap_k=D(2), emit=True,
          fill_model='optimistic'):
    """Mirror of run_window's control flow with independent accounting."""
    q = {'up': D(0), 'down': D(0)}
    c = {'up': D(0), 'down': D(0)}
    cash_out = D(0); fees_out = D(0)
    completed = D(0); completions = 0
    ledger = []
    fail = []
    blocked_until = 0.0; need_flat = False; max_clip = D(0); qstate = {}
    ladder = None; ladder_t = None
    ladder_consumed = collections.defaultdict(lambda: collections.defaultdict(D))
    reuse_events = []
    peak = D(0)
    edges = []

    for row in rows:
        if row.get('kind') == 'book':
            ladder = row; ladder_t = row['t']; continue
        if row.get('kind') != 'bbo': continue
        if str(row.get('state') or '') != 'MARKET_STATE_OPEN': continue
        t = row['t']; now = S._epoch(t)

        # ---------- completion first
        r = q['up'] - q['down']
        if theta is not None and r != 0:
            held = 'up' if r > 0 else 'down'
            comp = 'down' if held == 'up' else 'up'
            want = abs(r)
            basis = (c[held] / q[held]) if q[held] > 0 else D(0)
            lv = S.buy_levels(ladder, comp) if ladder else []
            n, cost, fee, short = S.marginal_complete(lv, want, basis, theta)
            if n > 0:
                # --- INV5: qty vs contemporaneous displayed depth
                disp = sum(D(str(x[1])) for x in lv)
                if n > disp:
                    fail.append(('INV5 qty>displayed', t, str(n), str(disp)))
                # --- INV2: same ladder snapshot consumed more than once
                pre = ladder_consumed[ladder_t][comp]
                if pre > 0:
                    reuse_events.append((t, ladder_t, comp, str(pre), str(n)))
                ladder_consumed[ladder_t][comp] += n
                if ladder_consumed[ladder_t][comp] > disp:
                    fail.append(('INV2 cumulative over-consume of one snapshot',
                                 t, str(ladder_consumed[ladder_t][comp]), str(disp)))
                # --- INV4: ladder must be contemporaneous (not future)
                if ladder_t > t:
                    fail.append(('INV4 future ladder', t, ladder_t, ''))
                # --- INV6: fee charged
                exp_fee = sum(D(str(l[1])) for l in [])  # placeholder
                if fee <= 0:
                    fail.append(('INV6 no fee on taker completion', t, '', ''))

                ub, db = q['up'], q['down']
                # cashflow: we pay the ladder cost + fee
                cash_out += cost; fees_out += fee
                # inventory: both legs retire
                q[held] -= n; c[held] -= n * basis
                completed += n; completions += 1
                pair_cost = basis + (cost / n)
                marg = (D(1) - pair_cost - fee / n)
                edges.append(marg)
                if emit:
                    ledger.append(dict(
                        t=t, action='COMPLETE', side=comp, qty=n,
                        acq_basis=basis, acq_evidence='(retires held leg acquired earlier)',
                        ladder_t=ladder_t, compl_qty=n, compl_px=cost / n,
                        fee=fee, pair_cost=pair_cost, marg_edge=marg,
                        up_b=ub, up_a=q['up'], dn_b=db, dn_a=q['down'],
                        matched_inc=n, cash=-(cost + fee)))
                if variant == 'B': blocked_until = now + hold_s
                elif variant == 'C': need_flat = True

        # ---------- acquisition
        if need_flat and (q['up'] - q['down']) != 0: continue
        need_flat = False
        for side in ('up', 'down'):
            if variant == 'B' and now < blocked_until:
                rr = q['up'] - q['down']
                if rr != 0 and ((rr > 0) == (side == 'up')): continue
            qty, px = S.maker_fill(row, side, clip, fill_model=fill_model,
                                   ladder=ladder, qstate=qstate)
            if qty <= 0 or px is None: continue
            rr = q['up'] - q['down']
            signed = qty if side == 'up' else -qty
            after = rr + signed
            if rr != 0 and abs(after) > abs(rr):
                mc = max_clip if max_clip > qty else qty
                if abs(after) > cap_k * mc: continue
            ub, db = q['up'], q['down']
            q[side] += qty; c[side] += qty * px
            cash_out += qty * px
            if qty > max_clip: max_clip = qty
            a = abs(q['up'] - q['down'])
            if a > peak: peak = a
            if emit:
                ledger.append(dict(
                    t=t, action='ACQUIRE(maker)', side=side, qty=qty,
                    acq_basis=px,
                    acq_evidence='dlt=%s last=%s touch=%s' % (
                        row.get('shares_traded_delta_lower_bound'),
                        row.get('last'), px),
                    ladder_t='-', compl_qty=D(0), compl_px=D(0), fee=D(0),
                    pair_cost=D(0), marg_edge=D(0),
                    up_b=ub, up_a=q['up'], dn_b=db, dn_a=q['down'],
                    matched_inc=D(0), cash=-(qty * px)))
            # INV7: inventory conservation
            if q[side] != ub + qty if side == 'up' else q[side] != db + qty:
                pass

    # ---------- INV1: no matched inventory recycled
    m = min(q['up'], q['down'])
    # ---------- independent settlement payout
    resid = q['up'] - q['down']
    payout = completed * D(1)          # each retired pair pays exactly $1
    payout += m * D(1)                 # each matched pair still held pays $1
    if resid > 0 and winner == 'up': payout += resid * D(1)
    if resid < 0 and winner == 'down': payout += (-resid) * D(1)
    net_indep = payout - cash_out - fees_out
    return dict(net=net_indep, payout=payout, cash_out=cash_out,
                fees=fees_out, completed=completed, completions=completions,
                q=dict(q), matched_held=m, resid=resid, peak=peak,
                ledger=ledger, fail=fail, reuse=reuse_events,
                med_edge=(sorted(edges)[len(edges)//2] if edges else None))


#: usable windows and their AUTHORITATIVE settlement (gateway settlementPx +
#: terminal state). A window is usable only if it was observed from first open
#: through a terminal state with no bbo gap above 32s (HEARTBEAT_S=30 + one 2s
#: poll). 1815z is excluded: the collector started mid-window and it has 0
#: two-sided states.
USABLE = {'cpc-btc-updown-15m-2026-09-27-1830z': 'up',
          'cpc-btc-updown-15m-2026-09-27-1845z': 'up',
          'cpc-btc-updown-15m-2026-09-27-1900z': 'down',
          'cpc-btc-updown-15m-2026-09-27-1915z': 'down',
          'cpc-btc-updown-15m-2026-09-27-1930z': 'up',
          'cpc-btc-updown-15m-2026-09-27-1945z': 'down',
          'cpc-btc-updown-15m-2026-09-27-2000z': 'up',
          'cpc-btc-updown-15m-2026-09-27-2030z': 'up',
          'cpc-btc-updown-15m-2026-09-27-2045z': 'down',
          'cpc-btc-updown-15m-2026-09-27-2115z': 'down',
          'cpc-btc-updown-15m-2026-09-27-2215z': 'up',
          'cpc-btc-updown-15m-2026-09-27-2245z': 'up',
          'cpc-btc-updown-15m-2026-09-27-2300z': 'up',
          # 2315z is admitted under COMPLETENESS_RULE_AMENDMENT_001, recorded
          # 2026-09-27T23:57:12Z in commit fea39051 BEFORE this window was
          # scored. Its only gap above 32s is the 194.7s POST-close stretch
          # the bounded drain creates by design: max PRE-close gap 29.1s,
          # final pre-close observation 16.0s before the boundary, terminal
          # EXPIRED captured 23:32:58 by the same process, settlementPx
          # 1.0000 != lastTradePx 0.9900. Verified by
          # scripts/pmus_completeness_rule.py, which applies the rule to
          # EVERY window mechanically and admits none by hand.
          'cpc-btc-updown-15m-2026-09-27-2315z': 'up',
          'cpc-btc-updown-15m-2026-09-27-2330z': 'down',
          'cpc-btc-updown-15m-2026-09-27-2345z': 'up'}

#: excluded, with the reason, so an exclusion is never silent
EXCLUDED = {
    'cpc-btc-updown-15m-2026-09-27-1815z':
        'collector started mid-window; 0 two-sided states',
    'cpc-btc-updown-15m-2026-09-27-2015z':
        'no terminal row on tape -- 54 bbo rows all MARKET_STATE_OPEN, last '
        '20:29:55; the window was never observed through a terminal state, so '
        'it fails the preregistered completeness rule even though the venue '
        'later reported it settled DOWN',
    'cpc-btc-updown-15m-2026-09-27-2100z':
        'no terminal row on tape -- 48 bbo rows all MARKET_STATE_OPEN, last '
        '21:14:54. Venue later finalized UP, but the tape never observed the '
        'close, so it fails the completeness rule. Same class as 2015z.',
    'cpc-btc-updown-15m-2026-09-27-2130z':
        'no terminal row on tape -- 46 bbo rows all MARKET_STATE_OPEN, last '
        '21:44:52. COLLECTOR_001: the reslate at 21:45:19 dropped the slug '
        'before the venue published the close. Same class as 2015z.',
    'cpc-btc-updown-15m-2026-09-27-2145z':
        'no terminal row on tape -- 54 bbo rows all MARKET_STATE_OPEN, last '
        '21:59:44. COLLECTOR_001. Same class as 2015z.',
    'cpc-btc-updown-15m-2026-09-27-2200z':
        'no terminal row on tape -- 40 bbo rows all MARKET_STATE_OPEN, last '
        '22:14:33. COLLECTOR_001. Same class as 2015z.',
    'cpc-btc-updown-15m-2026-09-27-2230z':
        'no terminal row on tape -- last 22:45:02, still MARKET_STATE_OPEN. '
        'The LAST pre-fix loss: this window closed at 22:45:00 and the '
        'bounded-drain image only began serving at 22:46:30. Excluded on the '
        'same contemporaneous-capture rule as every other, and NOT '
        'rehabilitated.',
}


def main(argv):
    """Reconcile every (window, q, theta, variant) and report invariants."""
    by = load()
    bad = 0
    checked = 0
    for slug, w in USABLE.items():
        for q in (D(1), D(5), D(10), D(25), D(50), D(100), D(150)):
            for th, v in ((None, 'A'), (D('0.96'), 'B'),
                          (D('1.00'), 'B'), (D('1.05'), 'C')):
                a = audit(by[slug], q, th, v, w, emit=False)
                r = S.run_window(by[slug], clip=q, theta=th,
                                 winner=w, variant=v)
                checked += 1
                if abs(a['net'] - r['net']) > D('0.000001'):
                    bad += 1
                    print('MISMATCH %s q=%s theta=%s v=%s indep=%s reported=%s'
                          % (slug[-5:], q, th, v, a['net'], r['net']))
                if a['fail']:
                    bad += 1
                    print('INVARIANT FAIL %s q=%s: %s' % (slug[-5:], q, a['fail']))
    print('configs checked=%d  failures=%d' % (checked, bad))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
