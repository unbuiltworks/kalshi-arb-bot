"""Is there a selection signal in what Bonereaper buys, or is he buying
favourites?

SAME CLOCK WINDOWS ON TWO VENUES. His real BTC 5-minute buys on Polymarket
(2026-11-17..20: side, price, second) and our venue's recorded quotes for
the same five-minute windows (the public recorder, ~10 s cadence, UP
bid/ask; DOWN's ask is 1 - UP's bid). Everything is priced at OUR venue's
ask and settled on OUR venue's outcome.

WHAT IS AND IS NOT BEING REPLAYED.
  B  "his side at his times": for each 10-second bucket in which he
     bought, buy the same side here. This copies his DECISIONS, which were
     made on Polymarket's book; it is a transfer test, not a rule.
  A  "plain favourite at his times": in the same buckets buy whichever
     side is the favourite HERE. B minus A is what his choice of side adds
     over buying the favourite at the same moments.
  A* "plain favourite, his habits": at every 10-second point of every
     window, buy the favourite here, weighted to the price bands and
     minutes he spends his dollars in. No knowledge of when he traded.
     A minus A* is what his choice of MOMENT adds.
  R  "reconstructed rule": the venue-observable rule that best describes
     his side on the DISCOVERY days, applied at every 10-second point on
     the VALIDATION days. This is the only line that is a usable signal.
  C  "our controller": the existing two-sided maker controller on the same
     windows, fills assumed when the ask reaches the bid within 10 s.

Returns are USD per share: 1 if the side won here else 0, minus the ask
(pre-fee), and minus 0.095 x p x (1 - p) (our taker fee; C pays none).
Delay 0 prices at the first quote at or after the bucket; delay 1 at the
next quote (~10 s later). His replays are weighted by his dollars. Outcome is the venue's own rule
(next window's price-to-beat at or above this one's = UP), which needs no
closing quote and so drops no close-run window. Mean of per-window means, 95% bootstrap over
windows. DISCOVERY = 2026-11-17 and 11-18; VALIDATION = 11-19 and 11-20.
"""
import collections
import datetime as dt
import gzip
import json
import os
import random
import re
import sys

sys.path.insert(0, ".")
K = 0.095
BR = "analysis/l5/bonereaper_recent/activity_20261117_20261120.jsonl.gz"
BANDS = [(0, .30), (.30, .50), (.50, .70), (.70, .85), (.85, .95),
         (.95, 1.01)]


def fee(p):
    return K * p * (1 - p)


def boot(v, n=3000, seed=31):
    if len(v) < 8:
        return None
    rng = random.Random(seed)
    k = len(v)
    ms = sorted(sum(rng.choice(v) for _ in range(k)) / k for _ in range(n))
    return ms[int(.025 * n)], ms[int(.975 * n)]


def fmt(by_window):
    """{window: [(weight, value)]} -> weighted mean of window means."""
    v = []
    for rows in by_window.values():
        w = sum(a for a, _ in rows)
        if w > 0:
            v.append(sum(a * b for a, b in rows) / w)
    if not v:
        return "      n/a"
    c = boot(v)
    return "%+.4f %-17s w=%4d" % (sum(v) / len(v),
                                  "[%+.3f,%+.3f]" % c if c else "[few]",
                                  len(v))


def num(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def load_quotes(path):
    by = collections.defaultdict(list)
    seen = set()
    TB.clear()
    for line in open(path):
        r = json.loads(line)
        k = (r["slug"], r["utc"])
        if k in seen or r.get("closed"):
            continue
        seen.add(k)
        if num(r.get("to_beat")) is not None:
            TB.setdefault(r["slug"], num(r["to_beat"]))
        by[r["slug"]].append((float(r["t"]), num(r.get("bid")),
                              num(r.get("ask"))))
    for s in by:
        by[s].sort()
    return by


def ask(q, side):
    _t, b, a = q
    if side == "UP":
        return a
    return None if b is None else round(1 - b, 4)


def bid(q, side):
    _t, b, a = q
    if side == "UP":
        return b
    return None if a is None else round(1 - a, 4)


TB = {}


def next_slug(s):
    d = dt.datetime.strptime(s[-16:], "%Y-%m-%d-%H%Mz").replace(
        tzinfo=dt.timezone.utc) + dt.timedelta(minutes=5)
    return "cpc-btc-updown-5m-" + d.strftime("%Y-%m-%d-%H%Mz")


def outcome_tb(s):
    """The venue's own rule: UP iff the next window's price-to-beat is at
    or above this window's. Uses no quote, so no window is dropped for
    being close at the end."""
    a, b = TB.get(s), TB.get(next_slug(s))
    if a is None or b is None:
        return None
    return "UP" if b >= a else "DOWN"


def outcome(rows):
    t, b, a = rows[-1]
    if t < 290:
        return None
    if b is not None and b >= 0.90:
        return "UP"
    if a is not None and a <= 0.10:
        return "DOWN"
    return None


def slug_of(epoch):
    d = dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
    return "cpc-btc-updown-5m-" + d.strftime("%Y-%m-%d-%H%Mz")


def load_br():
    ev = collections.defaultdict(lambda: collections.defaultdict(
        lambda: [0.0, 0.0]))           # slug -> (bucket, side) -> [sh, usd]
    won = {}
    cond = {}
    for line in gzip.open(BR, "rt"):
        r = json.loads(line)
        m = re.match(r"btc-updown-5m-(\d+)$", r.get("slug") or "")
        if not m:
            continue
        start = int(m.group(1))
        s = slug_of(start)
        cond[r["condition_id"]] = s
        if r["type"] == "REDEEM" and (r.get("usdc_size") or 0) > 0:
            won[s] = (r.get("outcome") or "").upper()
        elif r["type"] == "TRADE":
            t = r["timestamp"] - start
            if 0 <= t < 300:
                a = ev[s][(int(t // 10) * 10, (r["outcome"] or "").upper())]
                a[0] += r["size"]
                a[1] += r["usdc_size"]
    return ev, won


def main():
    quotes = load_quotes(sys.argv[1])
    ev, br_won = load_br()
    P = print
    res_q = {s: outcome(rows) for s, rows in quotes.items()}
    res = {s: outcome_tb(s) for s in quotes}
    chk = [s for s in quotes if res[s] and res_q[s]]
    P("outcome = next window's price-to-beat vs this one's: known for %d of "
      "%d windows; agrees with the final quote on %d of %d" % (
          sum(1 for s in quotes if res[s]), len(quotes),
          sum(1 for s in chk if res[s] == res_q[s]), len(chk)))
    wins = [s for s in quotes if res[s] and s in ev
            and len(quotes[s]) >= 20]
    both = [s for s in wins if s in br_won]
    agree = sum(1 for s in both if br_won[s] == res[s])
    P("windows recorded here: %d   with his buys and a known outcome here: "
      "%d" % (len(quotes), len(wins)))
    P("his venue's winner known for %d of them; it matches ours on %d "
      "(%.1f%%)" % (len(both), agree, 100.0 * agree / max(len(both), 1)))
    disc = [s for s in wins if s[-16:-6] in ("2026-11-17", "2026-11-18")]
    val = [s for s in wins if s[-16:-6] in ("2026-11-19", "2026-11-20")]
    P("DISCOVERY windows %d   VALIDATION windows %d" % (len(disc), len(val)))

    def at(s, t, delay=0):
        rows = quotes[s]
        for i, q in enumerate(rows):
            if q[0] >= t:
                j = i + delay
                return (rows[j], rows[max(0, j - 3)]) if j < len(rows) \
                    else (None, None)
        return None, None

    def fav_here(q):
        au, ad = ask(q, "UP"), ask(q, "DOWN")
        bu, bd = bid(q, "UP"), bid(q, "DOWN")
        mu = None if None in (au, bu) else (au + bu) / 2
        if mu is None:
            if bu is not None and bu >= 0.5:
                return "UP"
            if bd is not None and bd >= 0.5:
                return "DOWN"
            return None
        return "UP" if mu >= 0.5 else "DOWN"

    # ---------------- 1. what he does, in OUR venue's terms
    P("\n== 1. his buys described with OUR venue's quotes at that moment "
      "(share of his dollars) ==")
    tab = collections.Counter()
    tot = 0.0
    for s in wins:
        for (t, side), (sh, usd) in ev[s].items():
            q, prev = at(s, t)
            if q is None:
                continue
            f = fav_here(q)
            if f is None:
                continue
            b_now, b_prev = bid(q, side), bid(prev, side)
            mv = ("rising" if None not in (b_now, b_prev)
                  and b_now > b_prev + 0.005 else
                  "falling" if None not in (b_now, b_prev)
                  and b_now < b_prev - 0.005 else "flat")
            tab[("favourite" if side == f else "underdog", mv)] += usd
            tot += usd
    for k, v in sorted(tab.items(), key=lambda kv: -kv[1]):
        P("   %-10s %-8s %5.1f%%" % (k[0], k[1], 100 * v / tot))

    # ---------------- 2. the replays
    def replay(ws, delay, mode, only=None):
        out = collections.defaultdict(list)
        for s in ws:
            for (t, side), (sh, usd) in ev[s].items():
                q, _p = at(s, t, delay)
                if q is None:
                    continue
                f = fav_here(q)
                pick = side if mode == "B" else f
                if pick is None or f is None:
                    continue
                if only == "fav" and side != f:
                    continue
                if only == "dog" and side == f:
                    continue
                a = ask(q, pick)
                if a is None or not (0 < a < 1):
                    continue
                w = 1.0 if res[s] == pick else 0.0
                out[s].append((usd, a, w))
        return out

    def lines(label, data):
        pre = {s: [(wt, w - a) for wt, a, w in r] for s, r in data.items()}
        net = {s: [(wt, w - a - fee(a)) for wt, a, w in r]
               for s, r in data.items()}
        n = sum(len(r) for r in data.values())
        tw = sum(w for r in data.values() for w, _a, _x in r)
        px = (sum(w * a for r in data.values() for w, a, _x in r) / tw) \
            if tw else 0
        return "   %-34s n=%6d px %.3f  pre-fee %s   after our fee %s" % (
            label, n, px, fmt(pre), fmt(net))

    def wmean(rows):
        w = sum(x for x, _a, _b in rows)
        return sum(x * (b - a) for x, a, b in rows) / w if w > 0 else None

    def paired(x, y):
        v = [wmean(x[s]) - wmean(y[s]) for s in x
             if s in y and wmean(x[s]) is not None
             and wmean(y[s]) is not None]
        c = boot(v)
        return "%+.4f %s w=%d" % (sum(v) / len(v),
                                  "[%+.3f,%+.3f]" % c if c else "", len(v))

    def grid(ws, rule):
        out = collections.defaultdict(list)
        for s in ws:
            rows = quotes[s]
            for i, q in enumerate(rows):
                if q[0] > 285:
                    break
                prev = rows[max(0, i - 3)]
                pick, wt = rule(q, prev)
                if pick is None or wt <= 0:
                    continue
                j = i + 1                       # always one quote of delay
                if j >= len(rows):
                    continue
                a = ask(rows[j], pick)
                if a is None or not (0 < a < 1):
                    continue
                out[s].append((wt, a, 1.0 if res[s] == pick else 0.0))
        return out

    # his habits: dollar share by (price band, minute) on DISCOVERY days,
    # measured with OUR quotes at his moments
    habit = collections.Counter()
    for s in disc:
        for (t, side), (sh, usd) in ev[s].items():
            q, _p = at(s, t)
            if q is None or fav_here(q) is None:
                continue
            a = ask(q, fav_here(q))
            if a is None:
                continue
            bi = next((i for i, (lo, hi) in enumerate(BANDS)
                       if lo <= a < hi), None)
            habit[(bi, int(t // 60))] += usd
    hsum = sum(habit.values()) or 1.0

    def rule_habit(q, _prev):
        f = fav_here(q)
        if f is None:
            return None, 0
        a = ask(q, f)
        if a is None:
            return None, 0
        bi = next((i for i, (lo, hi) in enumerate(BANDS) if lo <= a < hi),
                  None)
        return f, habit.get((bi, int(q[0] // 60)), 0.0) / hsum

    def rule_fav_rising(q, prev):
        f = fav_here(q)
        if f is None:
            return None, 0
        b_now, b_prev = bid(q, f), bid(prev, f)
        if None in (b_now, b_prev) or b_now <= b_prev + 0.005:
            return None, 0
        return f, 1.0

    def rule_fav_falling(q, prev):
        f = fav_here(q)
        if f is None:
            return None, 0
        b_now, b_prev = bid(q, f), bid(prev, f)
        if None in (b_now, b_prev) or b_now >= b_prev - 0.005:
            return None, 0
        return f, 1.0

    def rule_fav_all(q, _prev):
        f = fav_here(q)
        return (f, 1.0) if f else (None, 0)

    for name, ws in (("DISCOVERY (11-17, 11-18)", disc),
                     ("VALIDATION (11-19, 11-20)", val)):
        P("\n== 2. %s: %d windows ==" % (name, len(ws)))
        for d in (0, 1):
            P("  delay %d quote(s):" % d)
            b_, a_ = replay(ws, d, "B"), replay(ws, d, "A")
            P(lines("B  his side at his times", b_))
            P(lines("   of which his favourite buys", replay(ws, d, "B", "fav")))
            P(lines("   of which his underdog buys", replay(ws, d, "B", "dog")))
            P(lines("A  favourite here at his times", a_))
            P("   paired, per window:  B - A (his choice of side)   %s" %
              paired(b_, a_))
            if d == 1:
                P("   paired, per window:  A - favourite every point "
                  "(his choice of moment)   %s" %
                  paired(a_, grid(ws, rule_fav_all)))
        P("  every 10 s point, one quote of delay:")
        P(lines("A* favourite, his price/time habits", grid(ws, rule_habit)))
        P(lines("   favourite, every point", grid(ws, rule_fav_all)))
        P(lines("R1 favourite that just ROSE", grid(ws, rule_fav_rising)))
        P(lines("R2 favourite that just FELL", grid(ws, rule_fav_falling)))

    # ---------------- 3. our controller on the same windows
    os.environ["L5_BTC5M_CONTROLLER"] = "ON"
    from decimal import Decimal as D
    from src import l5_btc5m_controller as C
    par = C.Params(
        entry_mode=C.STRADDLE, straddle_price=D("0.08"), clip_usd=D("0.05"),
        hedge_enabled=False, reserve_mode="NONE", max_exposure_usd=D("0.60"),
        straddle_book_relative=True, straddle_fill_model_measured=True,
        straddle_pair_ceiling=D("0.99"), straddle_repeat_enabled=True,
        straddle_max_side_usd=D("0.30"), straddle_max_unmatched_usd=D("0.10"),
        straddle_requote_ticks=2, straddle_max_window_loss_usd=D("0.20"),
        straddle_leg_max_age_s=240.0, straddle_ev_waiver=C.EV_WAIVER_TOKEN)

    def controller(ws):
        out = collections.defaultdict(list)
        for s in ws:
            rows = quotes[s]
            inv = {x: [D(0), D(0)] for x in C.SIDES}
            for i, q in enumerate(rows):
                t, b, a = q
                if b is None or a is None or i + 1 >= len(rows):
                    continue
                d = C.decide(
                    book={"bid": str(b), "ask": str(a), "age_s": 0.1},
                    t_in_window_s=t,
                    inventory={x: {"qty": str(inv[x][0]),
                                   "cost": str(inv[x][1])}
                               for x in C.SIDES},
                    resting=[], params=par)
                for o in d["orders"]:
                    if o.get("op", "PLACE") != "PLACE":
                        continue
                    side, px, qy = (o["side"], float(o["price"]),
                                    float(o["qty"]))
                    na = ask(rows[i + 1], side)
                    if na is not None and na <= px + 1e-9:
                        inv[side][0] += D(str(qy))
                        inv[side][1] += D(str(qy)) * D(str(px))
                        out[s].append((qy, px,
                                       1.0 if res[s] == side else 0.0))
        return out

    P("\n== 3. our controller (maker, both sides) on the same windows: "
      "per share bought, no fee ==")
    P("   CRUDE FILL MODEL: a bid counts as filled only if the ask is at or "
      "below it 10 s later, which\n   keeps only fills the price ran "
      "through. Sign is informative; the size overstates the loss.")
    for name, ws in (("DISCOVERY", disc), ("VALIDATION", val)):
        data = controller(ws)
        pre = {s: [(wt, w - a) for wt, a, w in r] for s, r in data.items()}
        n = sum(len(r) for r in data.values())
        P("   %-11s fills %5d   per share %s" % (name, n, fmt(pre)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
