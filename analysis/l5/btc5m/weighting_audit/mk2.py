import sys
A = sys.argv[1]
POOL = '''
def _pool(pairs, n=2000, seed=7):
    import random as _r
    pairs = [p for p in pairs if p[1] > 0]
    if not pairs:
        return None
    k = len(pairs)
    pt = sum(a for a, _ in pairs) / sum(b for _, b in pairs)
    if k < 8:
        return pt, (float("nan"), float("nan")), k
    rng = _r.Random(seed)
    ms = []
    for _ in range(n):
        sa = sb = 0.0
        for _i in range(k):
            a, b = pairs[rng.randrange(k)]
            sa += a; sb += b
        ms.append(sa / sb)
    ms.sort()
    return pt, (ms[int(.025 * n)], ms[int(.975 * n)]), k

'''
# holdout
s = open("analysis/l5/btc5m/favourite_band_holdout.py").read()
s = s.replace("def main():", POOL + "\ndef main():", 1)
old = s[s.index("                    pre.append("):s.index("if __name__")]
new = '''                    pre.append((sum(x for x, _ in rows), len(rows)))
                    net.append((sum(y for _, y in rows), len(rows)))
            if not pre:
                continue
            a, b = _pool(pre), _pool(net)
            print("   %-11s windows %4d signals %5d  before fee %+.4f [%+.3f,%+.3f]   after fee "
                  "%+.4f [%+.3f,%+.3f]" % (name, len(pre), sum(n for _, n in pre), a[0], a[1][0], a[1][1],
                                b[0], b[1][0], b[1][1]))


'''
s = s.replace(old, new)
open(A + "/holdout_pooled.py", "w").write(s)
# execution
s = open("analysis/l5/btc5m/favourite_band_execution.py").read()
s = s.replace("def main():", POOL + "\ndef main():", 1)
old = '''            v = [sum(x for x, _, _ in r) / len(r) for r in d.values()]'''
assert old in s
s = s.replace(old, '''            v = [(sum(x for x, _, _ in r), len(r)) for r in d.values()]''')
old = '''            lo, hi = boot(v)'''
assert old in s
s = s.replace(old, '''            _pt, (lo, hi), _k = _pool(v)''')
old = '''m, 100.0 * f / n, sum(v) / len(v), lo, hi, len(v), pf))'''
assert old in s
s = s.replace(old, '''m, 100.0 * f / n, _pt, lo, hi, len(v), pf))''')
open(A + "/execution_pooled.py", "w").write(s)
# where
s = open("analysis/l5/btc5m/favourite_band_where.py").read()
old = s[s.index("def cell(v):"):s.index("q = collections.defaultdict(set)")]
new = POOL + '''
def cell(v):
    if len(v) < 20:
        return "      -      "
    pt, (lo, hi), k = _pool(v, n=1500, seed=1)
    return "%+.3f%s(%4d)" % (pt, "*" if lo > 0 or hi < 0 else " ", k)


'''
s = s.replace(old, new)
old1 = 'f = [sum(x) / len(x) for x in acc[("F", pb, tbk)].values()]'
old2 = 'h = [sum(x) / len(x) for x in acc[("H", pb, tbk)].values()]'
assert old1 in s and old2 in s
s = s.replace(old1, 'f = [(sum(x), len(x)) for x in acc[("F", pb, tbk)].values()]')
s = s.replace(old2, 'h = [(sum(x), len(x)) for x in acc[("H", pb, tbk)].values()]')
open(A + "/where_pooled.py", "w").write(s)
