"""Favorite accumulation + opposite-side completion, on the 189-window tape.

MAKER ONLY. We rest a bid at price p on whichever side is the FAVORITE
(price > 0.50). We fill only when that side's own price falls TO our bid,
which is the same fill rule the straddle shadow uses and carries the same
adverse selection: we are filled by someone selling.

own_price(UP) = bid ; own_price(DOWN) = 1 - ask  (the live convention)

After filling at p we rest the completion leg on the opposite side at
(TARGET - p). It fills if the opposite side ever trades down to there.
Completed -> profit = 1 - TARGET per share, locked.
Not completed -> hold the favorite to settlement: +(1-p) if it won, -p if not.
"""
import json, collections, math, sys

F = 'analysis/l5/btc5m_accumulation/pmus_btc5m_quotes_20261112.jsonl'
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

def px(r):
    b, a = float(r['bid']), float(r['ask'])
    return b, 1.0 - a            # up, down

def run(p, target, min_t=0.0):
    res = []
    comp = held = 0
    for s, rs in w.items():
        if s not in wins:
            continue
        fill = None
        for i, r in enumerate(rs):
            if (r.get('t') or 0) < min_t:
                continue
            up, dn = px(r)
            fav, fp = ('UP', up) if up >= dn else ('DOWN', dn)
            if fp > 0.50 and fp <= p:
                fill = (i, fav)
                break
        if fill is None:
            continue
        i, side = fill
        need = target - p
        done = False
        for r in rs[i+1:]:
            up, dn = px(r)
            opp = dn if side == 'UP' else up
            if opp <= need:
                done = True
                break
        if done:
            res.append(1.0 - target); comp += 1
        else:
            res.append((1.0 - p) if wins[s] == side else (-p)); held += 1
    if not res:
        return None
    n = len(res); m = sum(res)/n
    sd = (sum((x-m)**2 for x in res)/(n-1))**0.5 if n > 1 else 0
    t = m/(sd/math.sqrt(n)) if sd > 0 else 0
    return n, comp, held, m, sd, t

print('tape: %d windows, %d resolved' % (len(w), len(wins)))
print()
for target in (0.92, 0.96, 0.98):
    print('=== completion target pair cost <= %.2f  (locked profit %.0f c/share) ==='
          % (target, 100*(1-target)))
    print('%-7s %-6s %-7s %-7s %-11s %-9s %s' % ('entry','n','compl','held','mean c/sh','sd','t'))
    for p in (0.55,0.60,0.65,0.70,0.75,0.80,0.85,0.90):
        out = run(p, target)
        if not out:
            continue
        n, c, h, m, sd, t = out
        print('%-7.2f %-6d %-7d %-7d %-11.2f %-9.1f %+.2f'
              % (p, n, c, h, 100*m, 100*sd, t))
    print()
