"""Whole-window high-price favourite: does this Bonereaper behaviour
transfer to our venue?

THE RULE (fixed; nothing is fitted)
  In each window, at the FIRST recorded quote where either side's ASK is
  0.90 or above while the market is open, send a buy for ONE share of
  that side. It is matched at the ask of the first quote at least DELAY
  seconds later, whatever that ask then is (up to 0.99); if no open quote
  follows, the order is MISSED. One purchase per window, held to
  settlement. Taker fee 0.095 x p x (1 - p).
  The signal uses only that quote. Outcome = the venue's own rule (next
  window's price-to-beat at or above this one's = UP), used to score only.

DATA  public recorder quotes: ~2 s on 2026-11-17..20, ~10 s on 11-11..16
      (so on the older days the shortest executable delay is ~10 s).
      The recorder logs no size: one share is ASSUMED available.

ACCOUNTING  research_accounting.Ledger: settlement proceeds minus cost
minus fees over every fill, per window RUN (windows that never reach
0.90, or miss, count as zero). Intervals resample windows.
"""
import bisect
import collections
import datetime as dt
import gzip
import json
import sys

sys.path.insert(0, "analysis/l5/btc5m")
from research_accounting import Ledger  # noqa: E402

K = 0.095
TRIGGER = 0.90
E = "analysis/l5/btc5m/evidence/"
FILES = (E + "recorder_10s_quotes_20261111_20261120.jsonl.gz",
         E + "recorder_2s_quotes_rest_20261117_20261120.jsonl.gz")
BUCKETS = ((240, 301), (180, 240), (120, 180), (60, 120), (30, 60),
           (10, 30), (0, 10))


def main():
    q = collections.defaultdict(dict)
    tb = {}
    for path in FILES:
        for line in gzip.open(path, "rt"):
            r = json.loads(line)
            if r.get("to_beat") is not None:
                tb.setdefault(r["slug"], float(r["to_beat"]))
            if r.get("closed") or r.get("bid") is None \
                    or r.get("ask") is None:
                continue
            t, b, a = float(r["t"]), float(r["bid"]), float(r["ask"])
            if 0 <= t < 300 and 0 < b < a <= 1:
                q[r["slug"]][r["utc"]] = (t, b, a)

    def nxt(s):
        d = dt.datetime.strptime(s[-16:], "%Y-%m-%d-%H%Mz") \
            + dt.timedelta(minutes=5)
        return "cpc-btc-updown-5m-" + d.strftime("%Y-%m-%d-%H%Mz")

    res = {s: ("UP" if tb[nxt(s)] >= tb[s] else "DOWN")
           for s in q if s in tb and nxt(s) in tb}
    rows = {s: sorted(q[s].values()) for s in res}
    day = lambda s: s[-16:-6]
    sets = (("DISCOVERY 11-17/18 (2 s quotes)",
             [s for s in rows if day(s) in ("2026-11-17", "2026-11-18")],
             (2, 4, 10, 0)),
            ("HELD OUT 11-19/20 (2 s quotes)",
             [s for s in rows if day(s) in ("2026-11-19", "2026-11-20")],
             (2, 4, 10, 0)),
            ("OLDER DAYS 11-11..16 (10 s quotes, not refitted)",
             [s for s in rows if day(s) < "2026-11-17"], (10, 0)))

    def trade(s, delay):
        """(signal time left, side, signal ask, fill price or None)."""
        rs = rows[s]
        for i, (t, b, a) in enumerate(rs):
            side = "UP" if a >= TRIGGER else \
                "DOWN" if 1 - b >= TRIGGER else None
            if side is None:
                continue
            sig = a if side == "UP" else 1 - b
            if delay == 0:
                j = i
            else:
                j = bisect.bisect_left(rs, (t + delay - 0.25,))
            if j >= len(rs) or rs[j][0] - t > delay + 8:
                return 300 - t, side, sig, None
            px = rs[j][2] if side == "UP" else 1 - rs[j][1]
            px = round(px, 4)
            if not (0 < px <= 0.99):
                return 300 - t, side, sig, None
            return 300 - t, side, sig, px
        return None

    P = print
    for name, ws, delays in sets:
        ws = sorted(ws)
        P("\n" + "=" * 100)
        P("%s: %d windows" % (name, len(ws)))
        for delay in delays:
            led = Ledger(ws, size_known=False)
            byb = {b: [] for b in BUCKETS}
            trig = miss = wins = 0
            worst = 0.0
            sig_px = []
            for s in ws:
                tr = trade(s, delay)
                if tr is None:
                    continue
                trig += 1
                left, side, sig, px = tr
                bk = next(b for b in BUCKETS if b[0] <= left < b[1])
                if px is None:
                    miss += 1
                    byb[bk].append((s, None, None))
                    continue
                won = res[s] == side
                f = K * px * (1 - px)
                led.fill(s, 1.0, px, f, won)
                wins += won
                worst = min(worst, (1.0 if won else 0.0) - px - f)
                sig_px.append(px - sig)
                byb[bk].append((s, px, won))
            t = led.totals()
            tag = "  (NOT executable: a ceiling)" if delay == 0 else ""
            P("\n-- matched %d s after the signal%s --" % (delay, tag))
            P("   windows that reached 0.90: %d of %d; missed %d; bought %d"
              % (trig, len(ws), miss, t["fills"]))
            if not t["fills"]:
                continue
            P("   " + led.report("result").replace("\n", "\n   "))
            P("   win rate %.1f%% (%d of %d); average price paid %.4f "
              "(break-even win rate incl. fee %.1f%%); price moved %+.4f "
              "between signal and fill; worst window %+.3f" % (
                  100.0 * wins / t["fills"], wins, t["fills"],
                  t["cost"] / t["shares"],
                  100.0 * (t["cost"] + t["fees"]) / t["shares"],
                  sum(sig_px) / len(sig_px), worst))
            P("   by time remaining at the signal:")
            P("     %-12s %7s %6s %9s %9s %10s %11s %s" % (
                "left", "bought", "missed", "win rate", "avg price",
                "net USD", "per share", "[95%]"))
            for bk in BUCKETS:
                got = [x for x in byb[bk] if x[1] is not None]
                if not byb[bk]:
                    continue
                sub = Ledger([x[0] for x in byb[bk]], size_known=False)
                for s_, px, won in got:
                    sub.fill(s_, 1.0, px, K * px * (1 - px), won)
                if not got:
                    P("     %3d-%3d s    %7d %6d" % (
                        bk[0], min(bk[1], 300), 0, len(byb[bk])))
                    continue
                tt = sub.totals()
                ci = sub._boot(lambda r: (
                    (sum(x[3] - x[1] - x[2] for x in r)
                     / sum(x[0] for x in r)) if sum(x[0] for x in r)
                    else None))
                P("     %3d-%3d s    %7d %6d %8.1f%% %9.4f %+10.2f %+11.4f %s"
                  % (bk[0], min(bk[1], 300), len(got),
                     len(byb[bk]) - len(got),
                     100.0 * sum(1 for x in got if x[2]) / len(got),
                     tt["cost"] / tt["shares"], tt["net"],
                     tt["net"] / tt["shares"],
                     "[%+.4f,%+.4f]" % ci if ci and len(got) >= 15
                     else "[few]"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
