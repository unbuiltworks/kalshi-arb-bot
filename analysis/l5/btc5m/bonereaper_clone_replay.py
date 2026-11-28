"""Bonereaper's behaviour as ONE executable rule, scored on our venue.

THE RULE (no knowledge of his trades at run time).
  Every ~10 s from 10 s to 280 s into a window, look at each side's ask.
  Spend  SCALE x RATE[band of that ask][minute of the window]  dollars on
  that side, as a taker, at the ask seen at the NEXT sample (~10 s delay).
  Hold everything to settlement. No sells. Stop at 280 s.
  RATE is fitted once: his dollars in that (band, minute) on 2026-11-17
  and 11-18, measured with OUR quotes at his fill times, divided by the
  number of times our book was in that state. SCALE sets the size: it is
  chosen so the rule spends BUDGET dollars a window on average on the
  fitting days, and is then frozen.

ACCOUNTING (research_accounting.Ledger): settlement proceeds minus cost
minus taker fee 0.095 x p x (1 - p) per share, summed over every fill and
every window the rule ran on. No rebates. Size at the ask is NOT recorded
by our recorder: every order is assumed to fill in full.

FIT 11-17/18.  HELD OUT: 11-19/20 (his later days) and 11-11..16 (earlier
days, never used).
"""
import collections
import datetime as dt
import gzip
import json
import re
import sys

sys.path.insert(0, "analysis/l5/btc5m")
from research_accounting import Ledger  # noqa: E402

K = 0.095
BANDS = (0.0, .10, .20, .30, .40, .50, .60, .70, .80, .90, .95, .98, 1.01)
ACT = "analysis/l5/bonereaper_recent/activity_20261117_20261120.jsonl.gz"
QUOTES = "analysis/l5/btc5m/evidence/recorder_10s_quotes_20261111_20261120.jsonl.gz"


def band(p):
    for i in range(len(BANDS) - 1):
        if BANDS[i] <= p < BANDS[i + 1]:
            return i
    return None


def main():
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    q = collections.defaultdict(set)
    tb = {}
    for line in gzip.open(QUOTES, "rt"):
        r = json.loads(line)
        if r.get("to_beat") is not None:
            tb.setdefault(r["slug"], float(r["to_beat"]))
        if r.get("closed") or r.get("bid") is None or r.get("ask") is None:
            continue
        q[r["slug"]].add((float(r["t"]), float(r["bid"]), float(r["ask"])))
    q = {s: sorted(v) for s, v in q.items()}

    def nxt(s):
        d = dt.datetime.strptime(s[-16:], "%Y-%m-%d-%H%Mz") \
            + dt.timedelta(minutes=5)
        return "cpc-btc-updown-5m-" + d.strftime("%Y-%m-%d-%H%Mz")

    res = {s: ("UP" if tb[nxt(s)] >= tb[s] else "DOWN")
           for s in q if s in tb and nxt(s) in tb}

    def asks(row):
        _t, b, a = row
        return {"UP": a, "DOWN": round(1 - b, 4)}

    def states(s):
        rows = q[s]
        for i, row in enumerate(rows[:-1]):
            if 10 <= row[0] <= 280:
                yield i, row

    fit = [s for s in res if s[-16:-6] in ("2026-11-17", "2026-11-18")]
    # his dollars by (band of OUR ask at his fill time, minute)
    his = collections.Counter()
    for line in gzip.open(ACT, "rt"):
        r = json.loads(line)
        m = re.match(r"btc-updown-5m-(\d+)$", r.get("slug") or "")
        if not m or r["type"] != "TRADE":
            continue
        start = int(m.group(1))
        d0 = dt.datetime.fromtimestamp(start, dt.timezone.utc)
        s = "cpc-btc-updown-5m-" + d0.strftime("%Y-%m-%d-%H%Mz")
        t = r["timestamp"] - start
        if s not in res or s[-16:-6] not in ("2026-11-17", "2026-11-18") \
                or not (10 <= t <= 280):
            continue
        row = next((x for x in q[s] if x[0] >= t), None)
        if row is None:
            continue
        b = band(asks(row)[r["outcome"].upper()])
        if b is not None:
            his[(b, int(t // 60))] += r["usdc_size"]
    seen = collections.Counter()
    for s in fit:
        for _i, row in states(s):
            for oc, a in asks(row).items():
                b = band(a)
                if b is not None:
                    seen[(b, int(row[0] // 60))] += 1
    rate = {k: his[k] / seen[k] for k in his if seen[k] >= 30}
    raw = sum(rate.get(k, 0) * n for k, n in seen.items()) / len(fit)
    scale = budget / raw
    print("his spend inside 10-280 s on the fitting days: $%.0f a window; "
          "rule scaled by %.5f to spend $%.2f a window" % (raw, scale,
                                                           budget))
    print("spend per state, dollars at this scale (rows: our ask; columns: "
          "minute 0-4):")
    for b in range(len(BANDS) - 1):
        print("   %.2f-%.2f  " % (BANDS[b], min(BANDS[b + 1], 1.0)) + "  ".join(
            "%6.3f" % (scale * rate.get((b, m), 0)) for m in range(5)))

    def run(name, days):
        ws = sorted(s for s in res if s[-16:-6] in days)
        led = Ledger(ws, size_known=False)
        side = collections.Counter()
        for s in ws:
            rows = q[s]
            for i, row in states(s):
                na = asks(rows[i + 1])
                for oc, a in asks(row).items():
                    b = band(a)
                    usd = scale * rate.get((b, int(row[0] // 60)), 0) \
                        if b is not None else 0
                    px = na[oc]
                    if usd <= 0 or not (0.01 < px < 0.995):
                        continue
                    sh = usd / px
                    led.fill(s, sh, px, sh * K * px * (1 - px),
                             res[s] == oc)
                    side["fav" if px >= 0.5 else "dog"] += usd
        t = led.totals()
        print("\n" + led.report(name))
        print("   spend per window run $%.2f; on favourites %.0f%%; return "
              "on money spent %+.2f%%" % (
                  t["cost"] / t["windows_run"],
                  100 * side["fav"] / max(sum(side.values()), 1e-9),
                  100 * t["net"] / t["cost"]))

    run("FITTING DAYS 11-17, 11-18 (not evidence)",
        ("2026-11-17", "2026-11-18"))
    run("HELD OUT: 11-19, 11-20", ("2026-11-19", "2026-11-20"))
    run("HELD OUT: 11-11 .. 11-16", tuple("2026-11-%02d" % d
                                          for d in range(11, 17)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
