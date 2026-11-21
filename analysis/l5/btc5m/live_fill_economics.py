"""Where the money went on the 55 live maker fills (four windows).

Each fill's settlement result is split, per share, into three parts measured
against the side's MID (average of that side's bid and ask):

  SPREAD   mid when the order was resting, minus the price paid.
           The most a passive bid can earn: about half the spread.
  MOVE     mid 30 s after the fill, minus mid when the order was resting.
           What the market did to the share across the fill.
  HOLD     settlement value (1 or 0), minus mid 30 s after the fill.
           What carrying the share to the end added or lost.

SPREAD + MOVE + HOLD = settlement value - price paid, exactly, for every
fill, so the three columns sum to the settled result. Fees were 0 on every
fill and no rebate or reward was credited.

"When the order was resting" is the lane's last book reading BEFORE the step
that reported the fill (for 2026-11-21 14:20Z: before the venue's fill
time). Readings are ~5.5 s apart, so SPREAD and MOVE are each known to about
one reading; their SUM does not depend on that choice.

INCREASING = on the side that held at least as much as the other before the
fill; REDUCING = on the lighter side. Dollars are summed over fills.
55 fills in 4 windows: a description of what happened, not an estimate.
"""
import collections
import datetime as dt
import json
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "analysis/l5/btc5m")
E = "analysis/l5/btc5m/evidence/"


def ts(s):
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        frac = rest[:-6] if rest.endswith("+00:00") else rest
        s = "%s.%s+00:00" % (head, (frac + "000000")[:6])
    return dt.datetime.fromisoformat(s).timestamp()


def windows():
    out = []
    import first_leg_entry_quality as F
    _rows, res = F.load()
    for w in ("1420z", "1705z"):
        slug = "cpc-btc-updown-5m-2026-11-20-%s" % w
        steps = [json.loads(x) for x in open(E + "window_%s_steps.jsonl" % w)]
        out.append((slug, res.get(slug), steps, {}))
    doc = json.load(open(E + "live_windows_20261121.json"))
    for slug, steps in sorted(doc["steps"].items()):
        times = {}
        for o in doc["orders"].get(slug, []):
            v = o.get("venue") or {}
            if float(o["filled_qty"]) > 0 and v.get("lastTransactTime"):
                times[o["venue_order_id"]] = ts(v["lastTransactTime"])
        out.append((slug, doc["settled"][slug], steps, times))
    return out


def side_mid(step, side):
    b = float(step["bid"]) if step.get("bid") else None
    a = float(step["ask"]) if step.get("ask") else None
    if b is None and a is None:
        return None
    if a is None:
        a = 1.0
    if b is None:
        b = 0.0
    m = (a + b) / 2
    return m if side == "UP" else 1 - m


def main():
    P = print
    tot = collections.defaultdict(lambda: [0.0] * 5)
    for slug, won, steps, times in windows():
        if won is None:
            P("%s: settlement side not in the recorder data -- skipped" % slug)
            continue
        st = [(ts(s["utc"]), s) for s in steps]
        q = {"UP": 0.0, "DOWN": 0.0}
        fills = []
        for i, (t, s) in enumerate(st):
            for side, qty, px, vid in s["fills"]:
                fills.append((times.get(vid, t), vid in times, i, side,
                              float(qty), float(px)))
        fills.sort()
        wt = collections.defaultdict(lambda: [0.0] * 5)
        for t, exact, i, side, qty, px in fills:
            o = "DOWN" if side == "UP" else "UP"
            kind = "INCREASING" if q[side] >= q[o] else "REDUCING"
            q[side] += qty
            before = ([x for x in st if x[0] <= t] if exact
                      else st[:i]) or st[:1]
            m0 = side_mid(before[-1][1], side)
            after = [x for x in st if x[0] >= t + 30] or st[-1:]
            m30 = side_mid(after[0][1], side)
            if m0 is None or m30 is None:
                continue
            v = 1.0 if side == won else 0.0
            parts = (m0 - px, m30 - m0, v - m30)
            assert abs(sum(parts) - (v - px)) < 1e-9
            for k in (kind, "ALL"):
                for d in (wt, tot):
                    d[k][0] += qty
                    d[k][1] += qty * parts[0]
                    d[k][2] += qty * parts[1]
                    d[k][3] += qty * parts[2]
                    d[k][4] += qty * (v - px)
        P("%s (settled %s)" % (slug[-16:], won))
        show(P, wt)
    P("ALL FOUR WINDOWS")
    show(P, tot)
    a = tot["ALL"]
    P("  per share: spread %+.4f, move %+.4f, hold %+.4f, settled %+.4f" % (
        a[1] / a[0], a[2] / a[0], a[3] / a[0], a[4] / a[0]))


def show(P, d):
    for k in ("INCREASING", "REDUCING", "ALL"):
        x = d[k]
        if not x[0]:
            continue
        P("   %-10s shares %5.2f | spread $%+.4f | move $%+.4f | "
          "hold $%+.4f | settled $%+.4f" % (k, x[0], x[1], x[2], x[3], x[4]))


if __name__ == "__main__":
    main()
