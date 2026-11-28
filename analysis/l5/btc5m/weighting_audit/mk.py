import sys, re
A = sys.argv[1]
POOL = '''
def _pool(pairs, n=2000, seed=7):
    """pairs: list of per-window (num, den). ratio of sums, window bootstrap."""
    pairs = [p for p in pairs if p[1] > 0]
    if not pairs:
        return None
    k = len(pairs)
    pt = sum(a for a, _ in pairs) / sum(b for _, b in pairs)
    if k < 8:
        return pt, None, k
    rng = random.Random(seed)
    ms = []
    for _ in range(n):
        sa = sb = 0.0
        for _i in range(k):
            a, b = pairs[rng.randrange(k)]
            sa += a; sb += b
        ms.append(sa / sb)
    ms.sort()
    return pt, (ms[int(.025 * n)], ms[int(.975 * n)]), k


def _pfmt(r):
    if r is None:
        return "n/a"
    pt, c, k = r
    return "%+.4f %-17s w=%4d" % (pt, "[%+.3f,%+.3f]" % c if c else "[few]", k)

'''
# ---- selection test
s = open("analysis/l5/btc5m/bonereaper_selection_test.py").read()
old = s[s.index("def fmt(by_window):"):s.index("def num(x):")]
new = POOL + '''
def fmt(by_window):
    """POOLED: sum(weight*value)/sum(weight) over all windows."""
    return _pfmt(_pool([(sum(a * b for a, b in rows), sum(a for a, _ in rows))
                        for rows in by_window.values()]))


'''
s = s.replace(old, new)
# lines(): add share-weighted (weight/ask) variant = true P&L per share bought
old = '''        return "   %-34s n=%6d px %.3f  pre-fee %s   after our fee %s" % (
            label, n, px, fmt(pre), fmt(net))'''
new = '''        preS = {s: [(wt / a, w - a) for wt, a, w in r] for s, r in data.items()}
        netS = {s: [(wt / a, w - a - fee(a)) for wt, a, w in r]
                for s, r in data.items()}
        netD = {s: [(wt, (w - a - fee(a)) / a) for wt, a, w in r]
                for s, r in data.items()}
        return ("   %-34s n=%6d px %.3f\\n        script-weights: pre %s | net %s"
                "\\n        P&L/SHARE (w/ask): pre %s | net %s\\n        net P&L per $ spent: %s") % (
            label, n, px, fmt(pre), fmt(net), fmt(preS), fmt(netS), fmt(netD))'''
assert old in s; s = s.replace(old, new)
old = s[s.index("    def paired(x, y):"):s.index("    def grid(ws, rule):")]
new = '''    def paired(x, y):
        """POOLED difference: pooled(x) - pooled(y) on common windows."""
        ks = [s for s in x if s in y and wmean(x[s]) is not None
              and wmean(y[s]) is not None]
        def nd(r):
            return (sum(w * (b - a) for w, a, b in r), sum(w for w, _a, _b in r))
        P_ = [(nd(x[s]), nd(y[s])) for s in ks]
        def d(ps):
            return (sum(p[0][0] for p in ps) / sum(p[0][1] for p in ps)
                    - sum(p[1][0] for p in ps) / sum(p[1][1] for p in ps))
        rng = random.Random(31)
        k = len(P_)
        ms = sorted(d([P_[rng.randrange(k)] for _ in range(k)])
                    for _ in range(2000))
        return "%+.4f [%+.3f,%+.3f] w=%d" % (d(P_), ms[50], ms[1950], k)

'''
s = s.replace(old, new)
open(A + "/sel_pooled.py", "w").write(s)
# ---- spot discrepancy
s = open("analysis/l5/btc5m/bonereaper_spot_discrepancy.py").read()
old = s[s.index("def stat(byw):"):s.index("class Spot:")]
new = POOL + '''
def stat(byw):
    """POOLED: sum(weight*value)/sum(weight) over all windows."""
    return _pfmt(_pool([(sum(a * b for a, b in rows), sum(a for a, _ in rows))
                        for rows in byw.values()]))


'''
s = s.replace(old, new)
# share-weighted variant of ret for section 1
old = '''    P("\\n== 1. what each group earned per share'''
new = '''    def retS(group, sel, fee=0.0):
        out = collections.defaultdict(list)
        for r in rows:
            if r[0] in group and sel(r):
                out[r[0]].append((r[5] / r[4], r[9] - r[4]
                                  - fee * r[4] * (1 - r[4])))
        return out

    P("\\n== 1S. TRUE P&L per SHARE (share-weighted, pooled), before fee | after 0.07 fee ==")
    for name, g in halves:
        P("  %s" % name)
        for b in ("0.00-0.30", "0.30-0.50", "0.50-0.70", "0.70-0.85",
                  "0.85-0.95", "0.95-1.00", None):
            line = "   %-10s" % (b or "ALL")
            for who in ("HIS_TAKER", "OTHERS"):
                sel = lambda r, who=who, b=b: r[1] == who and (b is None or band(r[4]) == b)
                line += "  %s %s | %s" % (who[:9], stat(retS(g, sel)), stat(retS(g, sel, 0.07)))
            P(line)
    P("\\n== 1. what each group earned per share'''
assert old in s; s = s.replace(old, new)
old = s[s.index("            if den > 0:\n                v.append(num / den)"):s.index('    P("\\n== 2. what spot said')]
new = '''            if den > 0:
                v.append((num, den))
        P("   POOLED his taker buys minus other takers, same window and price "
          "band: %s" % _pfmt(_pool(v)))

'''
s = s.replace(old, new)
open(A + "/spot_pooled.py", "w").write(s)
