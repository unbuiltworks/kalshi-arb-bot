"""WHY does a filled bid lose about 3.7 cents? An anatomy, not a fix.

Same 187 windows and fill rule as one_sided_value.py: a bid at the side's
own bid x, placed every 10 s, FILLS when that side's ask reaches x within
10 s. For every fill it follows the side's BID (what the position could be
sold for) and the model's probability for that side, before and after.

It answers four separate questions:
  1. Is the loss there AT the fill, or does it build afterwards?
     (mark at fill, +10 s, +30 s, +60 s, settlement)
  2. Had the model ALREADY moved against the side before the quote was
     placed, while the book had not? (a stale-book entry: avoidable by
     reading the index)
  3. Did the model move against the side BETWEEN placing and filling?
     (a stale-quote fill: avoidable only by cancelling faster)
  4. How fast are fills, and are fast fills worse?

Per-share USD, mean of window means with a 95% bootstrap over windows.
ARITHMETIC ON PUBLIC QUOTES WITH AN ASSUMED FILL RULE.
"""
import random
import sys

sys.path.insert(0, ".")
from analysis.l5.btc5m.one_sided_value import load, quote, p_side  # noqa

LIFE_S, GRID_S = 10.0, 10.0


def at_or_after(rows, i, dt_s):
    t0 = rows[i]["t_in_window_s"]
    for k in range(i, len(rows)):
        if rows[k]["t_in_window_s"] >= t0 + dt_s:
            return k
    return None


def at_or_before(rows, i, dt_s):
    t0 = rows[i]["t_in_window_s"]
    for k in range(i, -1, -1):
        if rows[k]["t_in_window_s"] <= t0 - dt_s:
            return k
    return None


def fills(rows):
    out, nxt = [], 0.0
    for i, r in enumerate(rows):
        t = r["t_in_window_s"]
        if t < nxt or t > 270:
            continue
        nxt = t + GRID_S
        for side in ("UP", "DOWN"):
            bid, ask = quote(r, side)
            if bid is None or ask is None or not (0 < bid < ask < 1):
                continue
            f = None
            for j in range(i + 1, len(rows)):
                if rows[j]["t_in_window_s"] - t > LIFE_S:
                    break
                a = quote(rows[j], side)[1]
                if a is not None and a <= bid:
                    f = j
                    break
            if f is None:
                continue
            rec = {"x": bid, "wait": rows[f]["t_in_window_s"] - t,
                   "settle": (1.0 if r["settled_outcome"] == side
                              else 0.0) - bid}
            for name, dt_s in (("m0", 0), ("m10", 10), ("m30", 30),
                               ("m60", 60)):
                k = at_or_after(rows, f, dt_s)
                b = None if k is None else quote(rows[k], side)[0]
                rec[name] = None if b is None else b - bid
            p0, pf = p_side(r, side), p_side(rows[f], side)
            kb = at_or_before(rows, i, 10)
            pb = None if kb is None else p_side(rows[kb], side)
            bb = None if kb is None else quote(rows[kb], side)[0]
            rec["dp_before"] = None if None in (p0, pb) else p0 - pb
            rec["dbid_before"] = None if bb is None else bid - bb
            rec["dp_during"] = None if None in (p0, pf) else pf - p0
            rec["gap0"] = None if p0 is None else p0 - (bid + ask) / 2
            out.append(rec)
    return out


def stat(per, key, pred=lambda r: True):
    vals = []
    n = 0
    for recs in per.values():
        xs = [r[key] for r in recs if pred(r) and r.get(key) is not None]
        n += len(xs)
        if xs:
            vals.append(sum(xs) / len(xs))
    if len(vals) < 8:
        return "n=%d too few" % n
    rng = random.Random(9)
    k = len(vals)
    ms = sorted(sum(rng.choice(vals) for _ in range(k)) / k
                for _ in range(3000))
    return "%+.4f [%+.4f,%+.4f] n=%d w=%d" % (
        sum(vals) / k, ms[75], ms[2925], n, k)


def main():
    data = load()
    per = {m: fills(rows) for m, rows in data.items()}
    P = print
    P("windows %d   fills %d" % (len(per), sum(len(v) for v in per.values())))
    P("\n1. WHERE THE LOSS APPEARS (side's bid minus the price paid)")
    for k, lab in (("m0", "at the fill"), ("m10", "+10 s"), ("m30", "+30 s"),
                   ("m60", "+60 s"), ("settle", "settlement")):
        P("   %-12s %s" % (lab, stat(per, k)))
    P("\n2. BEFORE THE QUOTE: last 10 s, this side")
    P("   model p change      %s" % stat(per, "dp_before"))
    P("   book bid change     %s" % stat(per, "dbid_before"))
    P("   model minus mid     %s" % stat(per, "gap0"))
    P("\n3. BETWEEN QUOTE AND FILL")
    P("   model p change      %s" % stat(per, "dp_during"))
    P("   seconds to fill     %s" % stat(per, "wait"))
    P("\n4. SPLITS (settlement return | mark +30 s)")
    splits = [
        ("model already falling >=2c before quote",
         lambda r: r["dp_before"] is not None and r["dp_before"] <= -0.02),
        ("model flat (within 2c) before quote",
         lambda r: r["dp_before"] is not None
         and abs(r["dp_before"]) < 0.02),
        ("model rising >=2c before quote",
         lambda r: r["dp_before"] is not None and r["dp_before"] >= 0.02),
        ("model BELOW mid by >=3c at quote",
         lambda r: r["gap0"] is not None and r["gap0"] <= -0.03),
        ("model within 3c of mid at quote",
         lambda r: r["gap0"] is not None and abs(r["gap0"]) < 0.03),
        ("model ABOVE mid by >=3c at quote",
         lambda r: r["gap0"] is not None and r["gap0"] >= 0.03),
        ("filled within 4 s", lambda r: r["wait"] <= 4),
        ("filled after 4-10 s", lambda r: r["wait"] > 4),
        ("model fell >=2c between quote and fill",
         lambda r: r["dp_during"] is not None and r["dp_during"] <= -0.02),
        ("model did not fall between quote and fill",
         lambda r: r["dp_during"] is not None and r["dp_during"] > -0.02),
    ]
    names = list(per)
    for lab, pred in splits:
        P("   %s" % lab)
        for hn, ws in (("first half", names[:len(names) // 2]),
                       ("second half", names[len(names) // 2:])):
            sub = {m: per[m] for m in ws}
            P("      %-11s settle %s | +30s %s" % (
                hn, stat(sub, "settle", pred), stat(sub, "m30", pred)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
