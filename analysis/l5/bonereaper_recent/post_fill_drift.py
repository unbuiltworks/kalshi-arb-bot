"""After he buys, does the price keep going his way (information) or come
back (his own buying pushed it)? Dollar-weighted, summed over fills:
price of the side he bought N seconds after the fill, minus what he paid
per share. Tape prints only; intervals resample windows. Fills in the
last 60 s of a window are left out (the price is running to 0 or 1).
"""
import sys, datetime as dt
sys.path.insert(0, "analysis/l5/bonereaper_recent")
import inventory_trajectory as T
act, tape = sys.argv[1], sys.argv[2]
f, w = T.load_activity(act); tp = T.load_tape(tape)
day = lambda s: dt.datetime.fromtimestamp(int(s.rsplit("-", 1)[1]), dt.timezone.utc).strftime("%m-%d")
for name, days in (("11-17/18", ("11-17", "11-18")), ("11-19/20", ("11-19", "11-20"))):
    for N in (5, 15, 30, 60):
        rows = []
        for s in f:
            if s not in tp or day(s) not in days:
                continue
            t0 = int(s.rsplit("-", 1)[1]); a = d = 0.0
            for ts, oc, px, sh, usd in f[s]:
                if not (0 <= ts - t0 < 240 - N + 60) or ts - t0 >= 240:
                    continue
                p = T.price_at(tp[s], ts + N)
                if p is None:
                    continue
                a += sh * ((p if oc == "UP" else 1 - p) - px); d += sh
            if d:
                rows.append((a, d))
        c = T.boot_ratio(rows)
        print("%s  %2d s after: %+.4f a share [%+.4f,%+.4f]  windows %d" % (name, N, sum(r[0] for r in rows) / sum(r[1] for r in rows), c[0], c[1], len(rows)))
