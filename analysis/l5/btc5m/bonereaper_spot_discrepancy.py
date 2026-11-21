"""Does Bonereaper's entry advantage come from a short-lived gap between
BTC spot, the price-to-beat and the executable Polymarket book?

INPUTS (all public, read-only, 2026-11-17..20):
  spot   Gemini BTC-USD trade ticks (the only exchange feed reachable
         from this environment), forward-filled to one price per second.
  tape   every trade by every trader in each BTC 5-minute market he
         traded (Polymarket data API). Each row is the TAKER's side.
  his    his own fills (activity feed). A fill of his that is also in the
         tape under his wallet was a TAKER fill; the rest were MAKER.
  ours   our venue's recorded quotes for the same clock windows (~10 s).

WHAT IS KNOWN BEFORE A FILL. For a fill stamped second t we use only
spot and tape strictly before t - LAG (LAG 2 s unless stated; the stamp
is the block time, a couple of seconds after the order matched).
  move   log(spot now / spot at window start), same feed both ends, so
         the unknown offset between Gemini and the settlement index
         cancels to first order.
  fair   P(up) = Phi(move / (vol x sqrt(seconds left))), vol = per-second
         volatility from the 30 minutes before the window opened.
  ask    the book is not archived. Proxy: the most recent taker print
         that shows where that side could be bought (a BUY of it, or a
         SELL of the other side at 1 - price).
  gap    fair - ask for the side bought. Positive = spot says the side
         is worth more than it costs.
No outcome is used to form any of these. Outcomes are used only to score.

Returns are USD per share: 1 if the side won, minus price; fee is
0.07 x p x (1 - p) there and 0.095 x p x (1 - p) here. Intervals are 95%
bootstraps over windows. DISCOVERY 11-17/18, VALIDATION 11-19/20.
"""
import bisect
import collections
import datetime as dt
import gzip
import json
import math
import random
import re
import sys

ACT = "analysis/l5/bonereaper_recent/activity_20261117_20261120.jsonl.gz"
LAG = 2
P = print


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def boot(v, n=2000, seed=7):
    if len(v) < 8:
        return None
    rng = random.Random(seed)
    k = len(v)
    ms = sorted(sum(rng.choice(v) for _ in range(k)) / k for _ in range(n))
    return ms[int(.025 * n)], ms[int(.975 * n)]


def stat(byw):
    """{window: [(weight, value)]} -> mean of per-window weighted means."""
    v = []
    for rows in byw.values():
        w = sum(a for a, _ in rows)
        if w > 0:
            v.append(sum(a * b for a, b in rows) / w)
    if not v:
        return "n/a"
    c = boot(v)
    return "%+.4f %s w=%d" % (sum(v) / len(v),
                              "[%+.3f,%+.3f]" % c if c else "[few]", len(v))


class Spot:
    def __init__(self, path):
        ts, px = [], []
        try:
            for line in gzip.open(path, "rt"):
                r = json.loads(line)
                ts.append(r[0] / 1000.0)
                px.append(float(r[1]))
        except EOFError:
            pass
        o = sorted(range(len(ts)), key=ts.__getitem__)
        self.ts = [ts[i] for i in o]
        self.px = [px[i] for i in o]

    def at(self, t):
        """Last trade at or before t (None if none within 60 s)."""
        i = bisect.bisect_right(self.ts, t) - 1
        if i < 0 or t - self.ts[i] > 60:
            return None
        return self.px[i]

    def age(self, t):
        i = bisect.bisect_right(self.ts, t) - 1
        return None if i < 0 else t - self.ts[i]

    def vol(self, t0):
        """Per-second vol from 10 s log returns over the 30 min before."""
        r = []
        prev = self.at(t0 - 1800)
        for k in range(1, 181):
            cur = self.at(t0 - 1800 + 10 * k)
            if prev and cur:
                r.append(math.log(cur / prev))
            prev = cur
        if len(r) < 60:
            return None
        return max(math.sqrt(sum(x * x for x in r) / len(r) / 10.0), 1e-6)


def day(start):
    return dt.datetime.fromtimestamp(start, dt.timezone.utc).strftime(
        "%m-%d")


def band(p):
    for lo, hi in ((0, .3), (.3, .5), (.5, .7), (.7, .85), (.85, .95),
                   (.95, 1.01)):
        if lo <= p < hi:
            return "%.2f-%.2f" % (lo, min(hi, 1.0))


def main():
    spot = Spot(sys.argv[1])
    P("spot ticks %d; median gap %.1f s" % (
        len(spot.ts), sorted(b - a for a, b in zip(spot.ts, spot.ts[1:]))[
            len(spot.ts) // 2]))

    # ---- his fills and each market's winner
    his_hash = collections.defaultdict(set)
    won = {}
    his = collections.defaultdict(list)
    for line in gzip.open(ACT, "rt"):
        r = json.loads(line)
        m = re.match(r"btc-updown-5m-(\d+)$", r.get("slug") or "")
        if not m:
            continue
        s = r["slug"]
        if r["type"] == "REDEEM" and (r.get("usdc_size") or 0) > 0:
            won[s] = (r.get("outcome") or "").upper()
        elif r["type"] == "TRADE" and r["side"] == "BUY":
            his[s].append((r["timestamp"], r["outcome"].upper(),
                           r["price"], r["size"],
                           r["transaction_hash"][:14]))

    # ---- tape
    tape = {}
    for line in gzip.open(sys.argv[2], "rt"):
        d = json.loads(line)
        tape[d["slug"]] = sorted(d["trades"])
    for s, rows in tape.items():           # winner from the last prints
        if s in won:
            continue
        last = [r for r in rows if r[2] == "BUY" and r[3] >= 0.97]
        if last:
            won[s] = last[-1][1].upper()
    wins = sorted(s for s in tape if s in won and s in his)
    P("markets with tape, his fills and a winner: %d" % len(wins))

    # ---- per-window context
    ctx = {}
    for s in wins:
        t0 = int(s.rsplit("-", 1)[1])
        s0, v = spot.at(t0), spot.vol(t0)
        if s0 and v:
            ctx[s] = (t0, s0, v)
    P("of which spot and volatility are available: %d" % len(ctx))

    def fair_up(s, t):
        t0, s0, v = ctx[s]
        now = spot.at(t)
        left = t0 + 300 - t
        if now is None or left <= 0:
            return None
        return phi(math.log(now / s0) / (v * math.sqrt(max(left, 1))))

    class Book:
        """Ask proxies from prints strictly before a time."""
        def __init__(self, rows):
            self.rows = rows
            self.i = 0
            self.ask = {"UP": None, "DOWN": None}

        def upto(self, t):
            while self.i < len(self.rows) and self.rows[self.i][0] < t:
                ts, oc, side, px, _sz, _b, _h = self.rows[self.i]
                oc = oc.upper()
                other = "DOWN" if oc == "UP" else "UP"
                if side == "BUY":
                    self.ask[oc] = (px, ts)
                else:
                    self.ask[other] = (round(1 - px, 4), ts)
                self.i += 1

    # ---- 1. classify every buy: his taker, his maker, everyone else
    rows = []          # (slug, who, t, side, price, usd, gap, fair, drift)
    for s in sorted(ctx):
        t0 = ctx[s][0]
        bk = Book(tape[s])
        mine = {h for (_t, _o, _p, _z, h) in his[s]}
        taker_hashes = set()
        events = []
        for ts, oc, side, px, sz, isbr, h in tape[s]:
            if side != "BUY":
                continue
            if isbr:
                taker_hashes.add(h)
            events.append((ts, "HIS_TAKER" if isbr else "OTHERS", oc.upper(),
                           px, sz))
        for ts, oc, px, sz, h in his[s]:
            if h not in taker_hashes:
                events.append((ts, "HIS_MAKER", oc, px, sz))
        events.sort()
        for ts, who, oc, px, sz in events:
            if not (t0 + 5 <= ts < t0 + 300) or not (0.01 < px < 0.995):
                continue
            tk = ts - LAG
            bk.upto(tk)
            f = fair_up(s, tk)
            if f is None:
                continue
            fs = f if oc == "UP" else 1 - f
            a = bk.ask[oc]
            ask = a[0] if a and tk - a[1] <= 15 else None
            p3, p0 = spot.at(tk - 3), spot.at(tk)
            drift = 0
            if p3 and p0 and p0 != p3:
                up = p0 > p3
                drift = 1 if (up == (oc == "UP")) else -1
            rows.append((s, who, ts - t0, oc, px, sz * px,
                         None if ask is None else fs - ask, fs, drift,
                         1.0 if won[s] == oc else 0.0, ask))
    P("buys scored: his taker %d, his maker %d, others %d" % tuple(
        sum(1 for r in rows if r[1] == k)
        for k in ("HIS_TAKER", "HIS_MAKER", "OTHERS")))
    usd = collections.Counter()
    for r in rows:
        usd[r[1]] += r[5]
    P("dollars: his taker $%.0f, his maker $%.0f, others $%.0f" % (
        usd["HIS_TAKER"], usd["HIS_MAKER"], usd["OTHERS"]))

    disc = {s for s in ctx if day(ctx[s][0]) in ("11-17", "11-18")}
    halves = (("DISCOVERY", disc), ("VALIDATION", set(ctx) - disc))

    def ret(group, sel, fee=0.0):
        out = collections.defaultdict(list)
        for r in rows:
            if r[0] in group and sel(r):
                out[r[0]].append((r[5], r[9] - r[4]
                                  - fee * r[4] * (1 - r[4])))
        return out

    P("\n== 1. what each group earned per share on Polymarket, by price "
      "paid (dollar-weighted, before fees) ==")
    for name, g in halves:
        P("  %s" % name)
        for b in ("0.00-0.30", "0.30-0.50", "0.50-0.70", "0.70-0.85",
                  "0.85-0.95", "0.95-1.00", None):
            line = "   %-10s" % (b or "ALL")
            for who in ("HIS_TAKER", "HIS_MAKER", "OTHERS"):
                line += "  %s %-34s" % (who[:9], stat(ret(
                    g, lambda r: r[1] == who and (b is None
                                                  or band(r[4]) == b))))
            P(line)
        # paired: his taker minus others, same window and price band
        v = []
        for s in g:
            acc = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
            for r in rows:
                if r[0] != s or r[1] == "HIS_MAKER":
                    continue
                a = acc[band(r[4])]
                k = 0 if r[1] == "HIS_TAKER" else 2
                a[k] += r[5]
                a[k + 1] += r[5] * (r[9] - r[4])
            num = den = 0.0
            for a in acc.values():
                if a[0] > 0 and a[2] > 0:
                    num += a[0] * (a[1] / a[0] - a[3] / a[2])
                    den += a[0]
            if den > 0:
                v.append(num / den)
        c = boot(v)
        P("   his taker buys minus other takers, same window and price "
          "band: %+.4f %s w=%d" % (sum(v) / max(len(v), 1),
                                   "[%+.3f,%+.3f]" % c if c else "", len(v)))

    P("\n== 2. what spot said just before each buy (lag %d s) ==" % LAG)
    for who in ("HIS_TAKER", "HIS_MAKER", "OTHERS"):
        rr = [r for r in rows if r[1] == who]
        w = sum(r[5] for r in rr) or 1
        wg = [r for r in rr if r[6] is not None]
        wgw = sum(r[5] for r in wg) or 1
        P("   %-9s spot moved his way in prior 3 s: %4.1f%%  against: "
          "%4.1f%%  no tick: %4.1f%%   mean gap (fair - ask) %+.4f   "
          "share of dollars with gap > +0.03: %4.1f%%" % (
              who, 100 * sum(r[5] for r in rr if r[8] > 0) / w,
              100 * sum(r[5] for r in rr if r[8] < 0) / w,
              100 * sum(r[5] for r in rr if r[8] == 0) / w,
              sum(r[5] * r[6] for r in wg) / wgw,
              100 * sum(r[5] for r in wg if r[6] > 0.03) / wgw))

    P("\n== 3. does the gap predict the return? all taker buys, by gap "
      "before the buy (before fees) ==")
    cuts = ((-9, -.10), (-.10, -.03), (-.03, .03), (.03, .10), (.10, 9))
    for name, g in halves:
        P("  %s" % name)
        for lo, hi in cuts:
            sel = (lambda r, lo=lo, hi=hi: r[1] != "HIS_MAKER"
                   and r[6] is not None and lo <= r[6] < hi)
            P("   gap %+.2f..%+.2f   all takers %-36s his taker %s" % (
                lo, hi, stat(ret(g, sel)),
                stat(ret(g, lambda r, sel=sel: sel(r)
                         and r[1] == "HIS_TAKER"))))

    # ---- 4. the gap as a rule, on Polymarket's tape and on our venue
    P("\n== 4. the gap as a rule: when fair - ask >= threshold, buy that "
      "side ==")
    P("   Polymarket: priced at the NEXT taker print on that side within "
      "5 s (a price someone really paid); none = no trade.")

    def rule_poly(group, th):
        out = collections.defaultdict(list)
        for s in group:
            t0 = ctx[s][0]
            tr = tape[s]
            bk = Book(tr)
            buys = {"UP": [], "DOWN": []}
            for ts, oc, side, px, *_ in tr:
                if side == "BUY":
                    buys[oc.upper()].append((ts, px))
            for t in range(t0 + 10, t0 + 290, 5):
                bk.upto(t)
                f = fair_up(s, t - 1)
                if f is None:
                    continue
                for oc, fs in (("UP", f), ("DOWN", 1 - f)):
                    a = bk.ask[oc]
                    if not a or t - a[1] > 15 or fs - a[0] < th:
                        continue
                    i = bisect.bisect_left(buys[oc], (t + 1, 0))
                    if i < len(buys[oc]) and buys[oc][i][0] <= t + 5:
                        px = buys[oc][i][1]
                        if 0.01 < px < 0.995:
                            out[s].append((1.0, px,
                                           1.0 if won[s] == oc else 0.0))
        return out

    def show(data, k):
        n = sum(len(v) for v in data.values())
        pre = {s: [(w, y - p) for w, p, y in v] for s, v in data.items()}
        net = {s: [(w, y - p - k * p * (1 - p)) for w, p, y in v]
               for s, v in data.items()}
        return "n=%5d  before fee %-34s after fee %s" % (n, stat(pre),
                                                         stat(net))

    for name, g in halves:
        P("  %s" % name)
        for th in (0.03, 0.06, 0.10, 0.15):
            P("   gap >= %.2f  %s" % (th, show(rule_poly(g, th), 0.07)))

    if len(sys.argv) > 3:
        P("   OUR VENUE: our recorded ask (~10 s samples), bought at the "
          "NEXT sample's ask, settled on our outcome, fee 0.095.")
        q = collections.defaultdict(list)
        tb = {}
        for line in open(sys.argv[3]):
            r = json.loads(line)
            if r.get("closed") or r.get("bid") is None \
                    or r.get("ask") is None:
                if r.get("to_beat") is not None:
                    tb.setdefault(r["slug"], float(r["to_beat"]))
                continue
            if r.get("to_beat") is not None:
                tb.setdefault(r["slug"], float(r["to_beat"]))
            q[r["slug"]].append((float(r["t"]), float(r["bid"]),
                                 float(r["ask"])))

        def ours(group, th, signal="spot", lo=0.0, hi=1.0):
            out = collections.defaultdict(list)
            for s in group:
                t0 = ctx[s][0]
                d0 = dt.datetime.fromtimestamp(t0, dt.timezone.utc)
                us = "cpc-btc-updown-5m-" + d0.strftime("%Y-%m-%d-%H%Mz")
                nx = "cpc-btc-updown-5m-" + (
                    d0 + dt.timedelta(minutes=5)).strftime("%Y-%m-%d-%H%Mz")
                if us not in q or us not in tb or nx not in tb:
                    continue
                res = "UP" if tb[nx] >= tb[us] else "DOWN"
                rows_ = sorted(set(q[us]))
                bk = Book(tape[s])
                for i, (t, b, a) in enumerate(rows_[:-1]):
                    if not (10 <= t <= 280):
                        continue
                    if signal == "spot":
                        f = fair_up(s, t0 + t - 1)
                        if f is None:
                            continue
                        sig = {"UP": f, "DOWN": 1 - f}
                    else:
                        # the other venue's last print, stamped at least
                        # 2 s before our quote and at most 8 s old
                        bk.upto(t0 + t - 2)
                        sig = {}
                        for oc in ("UP", "DOWN"):
                            x = bk.ask[oc]
                            if x and t0 + t - x[1] <= 8:
                                sig[oc] = x[0]
                    for oc, ask_now in (("UP", a), ("DOWN", 1 - b)):
                        fs = sig.get(oc)
                        if fs is None or not (lo <= ask_now < hi):
                            continue
                        if signal == "none":
                            if ask_now < 0.5:
                                continue
                        elif fs - ask_now < th:
                            continue
                        _t, nb, na = rows_[i + 1]
                        px = na if oc == "UP" else 1 - nb
                        if 0.01 < px < 0.995:
                            out[s].append((1.0, px,
                                           1.0 if res == oc else 0.0))
            return out

        for name, g in halves:
            P("  %s" % name)
            for th in (0.03, 0.06, 0.10, 0.15):
                P("   gap >= %.2f  %s" % (th, show(ours(g, th), 0.095)))
        P("\n== 5. our venue: the favourite by our ask, no signal "
          "(contemporaneous alternative) ==")
        for name, g in halves:
            P("  %s" % name)
            for lo, hi in ((.5, .7), (.7, .85), (.85, .95), (.95, 1.0)):
                P("   ask %.2f-%.2f  %s" % (lo, hi, show(
                    ours(g, 0, "none", lo, hi), 0.095)))
        P("\n== 6. our venue: buy when the OTHER venue's last print "
          "(>= 2 s old, <= 8 s old) is above our ask by the threshold ==")
        P("   bought at our NEXT sample's ask (~10 s later).")
        for name, g in halves:
            P("  %s" % name)
            for th in (0.02, 0.04, 0.06, 0.10):
                P("   other venue - our ask >= %.2f  %s" % (
                    th, show(ours(g, th, "poly"), 0.095)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
