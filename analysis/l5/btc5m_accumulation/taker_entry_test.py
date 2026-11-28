"""TAKER entry: we cross the spread and CHOOSE the moment.

maker buy  UP = bid    DOWN = 1 - ask   (filled when price comes to us)
taker buy  UP = ask    DOWN = 1 - bid   (we pay up, at a time we pick)

If adverse selection is what killed maker entry, the taker realized win
rate should TRACK the price paid instead of saturating near 44%.
Read-only, offline, on the 189-window tape.
"""
import json, collections, math

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

# bucket EVERY taker-buyable observation by the price paid
buck = collections.defaultdict(lambda: [0, 0])     # price bucket -> [wins, n]
spreads = []
for s, rs in w.items():
    if s not in wins:
        continue
    for r in rs:
        b, a = float(r['bid']), float(r['ask'])
        spreads.append(a - b)
        for side, p in (('UP', a), ('DOWN', 1.0 - b)):
            if not (0.02 <= p <= 0.98):
                continue
            k = round(p * 20) / 20.0          # 5c buckets
            buck[k][1] += 1
            if wins[s] == side:
                buck[k][0] += 1

import statistics as st
print('median spread %.4f   mean %.4f' % (st.median(spreads), sum(spreads)/len(spreads)))
print()
print('TAKER BUY AT p, HELD TO SETTLEMENT -- every observation, bucketed')
print('%-7s %-8s %-10s %-10s %-11s %s' % ('p', 'n', 'needs', 'realized', 'EV c/share', 'gap'))
for k in sorted(buck):
    kw, n = buck[k]
    if n < 150:
        continue
    wr = kw / n
    ev = wr * (1 - k) - (1 - wr) * k
    print('%-7.2f %-8d %-10s %-10s %-11s %+.0f pts'
          % (k, n, '%.0f%%' % (100*k), '%.0f%%' % (100*wr),
             '%+.2f' % (100*ev), 100*(wr - k)))
