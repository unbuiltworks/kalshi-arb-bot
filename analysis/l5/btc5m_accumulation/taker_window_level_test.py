"""Taker entry, ONE trade per window, fees included, window-level t.

The bucketed view inflates n ~12x because observations inside a window
share one outcome. This takes a single entry per window so each
observation is independent, which is the only honest basis for a t.

fee per share = 0.0695 * p * (1-p)   (l5_live_validation_gates.fee_worst)
"""
import json, collections, math

F = 'analysis/l5/btc5m_accumulation/pmus_btc5m_quotes_20261112.jsonl'
THETA = 0.0695
w = collections.defaultdict(list)
for l in open(F):
    l = l.strip()
    if not l:
        continue
    try:
        r = json.loads(l)
    except ValueError:
        continue
    if r.get('bid') is None or r.get('ask') is None:
        continue
    w[r['slug']].append(r)
wins = {}
for s, rs in w.items():
    rs.sort(key=lambda x: x.get('t') or 0)
    b, a = float(rs[-1]['bid']), float(rs[-1]['ask'])
    if b >= 0.90:
        wins[s] = 'UP'
    elif a <= 0.10:
        wins[s] = 'DOWN'

def fee(p):
    return THETA * p * (1 - p)

def run(target, maker=False):
    res = []
    for s, rs in w.items():
        if s not in wins:
            continue
        for r in rs:
            b, a = float(r['bid']), float(r['ask'])
            # taker pays up; maker rests and is come to
            cands = (('UP', b), ('DOWN', 1.0 - a)) if maker else \
                    (('UP', a), ('DOWN', 1.0 - b))
            got = None
            for side, p in cands:
                if p <= target and p > 0.01:
                    got = (side, p)
                    break
            if got:
                side, p = got
                pay = 0.0 if maker else fee(p)
                res.append(((1.0 - p) if wins[s] == side else (-p)) - pay)
                break
    if len(res) < 20:
        return None
    n = len(res); m = sum(res)/n
    sd = (sum((x-m)**2 for x in res)/(n-1))**0.5
    return n, m, sd, (m/(sd/math.sqrt(n)) if sd > 0 else 0)

print('ONE entry per window, held to settlement, window-level t')
print()
print('%-8s %-24s %s' % ('', 'TAKER (fees paid)', 'MAKER (no fee, for contrast)'))
print('%-8s %-7s %-10s %-7s %-7s %-10s %s'
      % ('target', 'n', 'c/share', 't', 'n', 'c/share', 't'))
G = [0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.30, 0.50]
for g in G:
    tk = run(g, maker=False)
    mk = run(g, maker=True)
    if not tk or not mk:
        continue
    print('%-8.2f %-7d %-10s %-7s %-7d %-10s %s'
          % (g, tk[0], '%+.2f' % (100*tk[1]), '%+.2f' % tk[3],
             mk[0], '%+.2f' % (100*mk[1]), '%+.2f' % mk[3]))
print()
print('multiple-comparisons bar for %d targets: sqrt(2 ln %d) = %.2f'
      % (len(G), len(G), math.sqrt(2*math.log(len(G)))))
