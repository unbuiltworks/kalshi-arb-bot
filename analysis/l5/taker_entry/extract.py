"""Extract per-window Kalshi 15m books+prints from the independent Aug-2026 tapes.

Produces one record per market: time-ordered top-of-book snapshots, aggressor-
tagged prints, window open/close, and a SETTLEMENT verdict that is only emitted
when the evidence is unambiguous. A window whose outcome cannot be established
is dropped rather than guessed, because a guessed settlement would silently
decide every policy comparison downstream.
"""
import json, glob, collections, datetime as dt
from decimal import Decimal as D

def iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

def load():
    mk = collections.defaultdict(lambda: dict(bbo=[], prints=[], meta={}))
    for fn in sorted(glob.glob('data/policy_d_port/tape_*.jsonl')
                     + glob.glob('data/policy_d_port_ext/tape_*.jsonl')):
        for line in open(fn, errors='replace'):
            try: d = json.loads(line)
            except Exception: continue
            if d.get('venue') != 'kalshi':
                continue
            m = d.get('market')
            if not m:
                continue
            e = mk[m]
            k = d.get('kind')
            if k == 'window':
                e['meta'].update(series=d.get('series'), close=d.get('close_time'),
                                 open=d.get('open_time'), strike=d.get('strike'))
            elif k == 'bbo':
                yb = d.get('yes_bids') or []
                nb = d.get('no_bids') or []
                if not yb or not nb:
                    continue                      # one-sided book: no pair is priceable
                e['bbo'].append((d['t'], D(yb[0][0]), D(yb[0][1]), D(nb[0][0]), D(nb[0][1])))
            elif k == 'print':
                e['prints'].append((d['t'], D(str(d['yes_price'])),
                                    D(str(d.get('count') or 0)), d.get('taker_side')))
    return mk

def settlement(e):
    """YES won (1), NO won (0), or None when the tape cannot establish it.

    Evidence used, in order: a terminal print inside 2c of a boundary that is
    also within 60s of close; otherwise the last print only if the final 20
    prints agree and sit inside 1c of the same boundary.
    """
    pr = sorted(e['prints'])
    if not pr or not e['meta'].get('close'):
        return None
    close = iso(e['meta']['close'])
    t, px, _, _ = pr[-1]
    if close - t <= 60:
        if px <= D("0.02"): return 0
        if px >= D("0.98"): return 1
    tail = [p for _, p, _, _ in pr[-20:]]
    if len(tail) >= 20:
        if all(p <= D("0.01") for p in tail): return 0
        if all(p >= D("0.99") for p in tail): return 1
    return None

if __name__ == "__main__":
    STRIKE = {k: int(v) for k, v in
              json.load(open('analysis/l5/taker_entry/strike_resolved.json')).items()}
    mk = load()
    out, drop = [], collections.Counter()
    for m, e in sorted(mk.items()):
        if len(e['bbo']) < 20 or len(e['prints']) < 20:
            drop['too_few_rows'] += 1; continue
        if not e['meta'].get('close') or not e['meta'].get('open'):
            drop['no_window_times'] += 1; continue
        s = settlement(e)
        if s is None:
            s = STRIKE.get(m)          # cross-validated at 47/47 on the overlap
            if s is None:
                drop['settlement_unknown'] += 1; continue
            src = 'strike_vs_reference'
        else:
            src = 'terminal_print'
        e['bbo'].sort(); e['prints'].sort()
        out.append(dict(market=m, series=e['meta']['series'],
                        open=iso(e['meta']['open']), close=iso(e['meta']['close']),
                        yes_won=s, settle_src=src,
                        bbo=[[t, str(a), str(b), str(c), str(dd)] for t,a,b,c,dd in e['bbo']],
                        prints=[[t, str(p), str(q), sd] for t,p,q,sd in e['prints']]))
    with open('analysis/l5/taker_entry/windows.json', 'w') as f:
        json.dump(out, f)
    print("usable windows: %d" % len(out))
    print("dropped:", dict(drop))
    bys = collections.Counter(w['series'] for w in out)
    print("by series:", dict(bys))
    print("yes_won distribution:", collections.Counter(w['yes_won'] for w in out))
    print("settlement source:", collections.Counter(w['settle_src'] for w in out))
    import statistics as st
    print("median bbo rows %d, median prints %d" %
          (st.median(len(w['bbo']) for w in out), st.median(len(w['prints']) for w in out)))
