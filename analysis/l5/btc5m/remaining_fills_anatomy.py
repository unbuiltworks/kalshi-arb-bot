"""The fills that remain with the heavy-underdog stop ON: where the loss
is, and whether the book's own movement is a reason to withhold a bid.

Same replay as inventory_controller_replay.py (real controller, live
configuration + heavy-underdog stop, orders resting between decisions,
STRICT fills: the ask reaches our price). All figures are dollars summed
over fills; maker fee 0.

PART 1  the loss split into MATCHED PAIRS (first-in-first-out, pays 1.00
        whichever side wins) and UNMATCHED shares (win or lose).
PART 2  each fill by what the book was doing when the bid was PLACED:
        the side's own bid now minus its bid N seconds earlier. Known at
        placement; nothing later is used.
PART 3  a withholding rule: do not bid a side (and cancel its resting
        two-sided bids) while its bid has FALLEN by at least X over the
        last N seconds. Nine (X, N) pairs; the best net on 11-17/18 is
        frozen and scored on 11-19/20 and on 11-11..16. "Per share" is
        net / shares filled: if the rule only removes orders, total loss
        falls but per-share does not improve.
"""
import bisect
import collections
import datetime as dt
import gzip
import json
import os
import random
import sys
from decimal import Decimal as D
from multiprocessing import Pool

sys.path.insert(0, ".")
sys.path.insert(0, "analysis/l5/btc5m")
os.environ["L5_BTC5M_CONTROLLER"] = "ON"
from src import l5_btc5m_attribution as A           # noqa: E402
from src import l5_btc5m_controller as C            # noqa: E402
import inventory_controller_replay as R             # noqa: E402

PAR = R.params(True)
GRID = [(x, n) for n in (6, 10, 30) for x in (0.01, 0.02, 0.03)]


def run_window(job):
    slug, rows, rule = job                 # rule: None or (X, N)
    ts = [r[0] for r in rows]
    inv = {s: [D(0), D(0)] for s in C.SIDES}
    rest, fills, nid = [], [], 0

    def side_bid(i, side):
        return rows[i][1] if side == C.UP else 1 - rows[i][2]

    def move(i, side, n):
        j = bisect.bisect_right(ts, ts[i] - n) - 1
        return None if j < 0 else side_bid(i, side) - side_bid(j, side)

    for i, (t, b, a) in enumerate(rows):
        sa = {C.UP: D(str(a)), C.DOWN: D(1) - D(str(b))}
        keep = []
        for o in rest:
            if sa[o["side"]] <= o["price"]:
                inv[o["side"]][0] += o["qty"]
                inv[o["side"]][1] += o["qty"] * o["price"]
                fills.append(dict(side=o["side"], qty=o["qty"],
                                  price=o["price"], t_placed=o["t"],
                                  t_fill=t, feat=o["feat"]))
            else:
                keep.append(o)
        rest = keep
        d = C.decide(
            book={"bid": str(b), "ask": str(a), "age_s": 0.1},
            t_in_window_s=t,
            inventory={s: {"qty": str(inv[s][0]), "cost": str(inv[s][1])}
                       for s in C.SIDES},
            resting=[{"id": o["id"], "side": o["side"],
                      "price": str(o["price"]), "qty": str(o["qty"]),
                      "age_s": max(t - o["t"], 0.0), "reason": o["reason"]}
                     for o in rest],
            params=PAR)
        blocked = set()
        if rule is not None:
            for s in C.SIDES:
                m = move(i, s, rule[1])
                if m is not None and m <= -rule[0] + 1e-9:
                    blocked.add(s)
            rest = [o for o in rest if not (
                o["side"] in blocked and o["reason"] == "STRADDLE_LEG")]
        for o in d["orders"]:
            if o.get("op") == "CANCEL":
                rest = [x for x in rest if x["id"] != o["id"]]
        for o in d["orders"]:
            if o.get("op", "PLACE") != "PLACE" or o["side"] in blocked:
                continue
            px, q = D(str(o["price"])), D(str(o["qty"]))
            if px >= sa[o["side"]]:
                continue
            nid += 1
            feat = (move(i, o["side"], 6), move(i, o["side"], 10),
                    move(i, o["side"], 30), float(a - b), t)
            rest.append({"id": "o%d" % nid, "side": o["side"], "price": px,
                         "qty": q, "t": t, "reason": o.get("reason"),
                         "feat": feat})
    return slug, rule, fills


def load():
    q = collections.defaultdict(dict)
    tb = {}
    for path in R.FILES:
        for line in gzip.open(path, "rt"):
            r = json.loads(line)
            if r.get("to_beat") is not None:
                tb.setdefault(r["slug"], float(r["to_beat"]))
            if r.get("closed") or r.get("bid") is None \
                    or r.get("ask") is None:
                continue
            b, a = float(r["bid"]), float(r["ask"])
            if 0 < b < a < 1:
                q[r["slug"]][r["utc"]] = (float(r["t"]), b, a)

    def nxt(s):
        d = dt.datetime.strptime(s[-16:], "%Y-%m-%d-%H%Mz") \
            + dt.timedelta(minutes=5)
        return "cpc-btc-updown-5m-" + d.strftime("%Y-%m-%d-%H%Mz")
    res = {s: ("UP" if tb[nxt(s)] >= tb[s] else "DOWN")
           for s in q if s in tb and nxt(s) in tb}
    return {s: sorted(q[s].values()) for s in res}, res


def boot_sum(v, n=3000, seed=6):
    rng = random.Random(seed)
    k = len(v)
    ms = sorted(sum(rng.choice(v) for _ in range(k)) for _ in range(n))
    return ms[int(.025 * n)], ms[int(.975 * n)]


def boot_ratio(rows, n=3000, seed=6):
    rng = random.Random(seed)
    k = len(rows)
    out = []
    for _ in range(n):
        smp = [rng.choice(rows) for _ in range(k)]
        d = sum(x[1] for x in smp)
        if d:
            out.append(sum(x[0] for x in smp) / d)
    out.sort()
    return out[int(.025 * len(out))], out[int(.975 * len(out)) - 1]


def main():
    rows, res = load()
    slugs = sorted(rows)
    sets = (("11-17/18", [s for s in slugs if s[-16:-6] in
                          ("2026-11-17", "2026-11-18")]),
            ("11-19/20", [s for s in slugs if s[-16:-6] in
                          ("2026-11-19", "2026-11-20")]),
            ("11-11..16 (10 s quotes)", [s for s in slugs
                                         if s[-16:-6] < "2026-11-17"]))
    jobs = [(s, rows[s], rule) for s in slugs for rule in [None] + GRID]
    out = collections.defaultdict(dict)
    with Pool(os.cpu_count() or 2) as pool:
        for slug, rule, fills in pool.imap_unordered(run_window, jobs,
                                                     chunksize=8):
            out[rule][slug] = fills
    P = print

    def pnl(f, s):
        return float(f["qty"]) * ((1.0 if res[s] == f["side"] else 0.0)
                                  - float(f["price"]))

    P("== 1. where the loss is (no withholding rule) ==")
    for name, ws in sets:
        m = u = sh_m = sh_u = cost_m = 0.0
        per = []
        for s in ws:
            f = out[None][s]
            att = A.attribute(
                [{"side": x["side"], "qty": x["qty"],
                  "cost": x["qty"] * x["price"], "fee": "0"} for x in f],
                res[s] == "UP")
            if not att.get("ok", True) and "matched" not in att:
                continue
            mm = float(att["matched"]["pnl"])
            uu = float(att["unmatched"]["pnl"])
            m += mm
            u += uu
            per.append((mm, uu))
            sh_m += float(att["matched"].get("qty", 0) or 0)
            cost_m += float(att["matched"].get("cost", 0) or 0)
        cm, cu = boot_sum([x[0] for x in per]), boot_sum([x[1] for x in per])
        P("   %-24s windows %4d  matched pairs $%+7.2f [%+.2f,%+.2f]%s   "
          "unmatched $%+7.2f [%+.2f,%+.2f]   net $%+.2f" % (
              name, len(ws), m, cm[0], cm[1],
              "  (avg pair cost %.4f)" % (cost_m / sh_m) if sh_m else "",
              u, cu[0], cu[1], m + u))

    P("\n== 2. fills by how the side's own bid had moved when the bid was "
      "placed (no rule): net per share [95%], shares ==")
    for fi, lab in ((0, "over the prior 6 s"), (1, "over the prior 10 s"),
                    (2, "over the prior 30 s")):
        P("  %s" % lab)
        for name, ws in sets[:2]:
            P("   %s" % name)
            for lo, hi, nm in ((-9, -.025, "fell 3c or more"),
                               (-.025, -.005, "fell 1-2c"),
                               (-.005, .005, "unchanged"),
                               (.005, .025, "rose 1-2c"),
                               (.025, 9, "rose 3c or more")):
                r = []
                for s in ws:
                    a = d = 0.0
                    for f in out[None][s]:
                        mv = f["feat"][fi]
                        if mv is not None and lo <= mv < hi:
                            a += pnl(f, s)
                            d += float(f["qty"])
                    if d:
                        r.append((a, d))
                if len(r) < 20:
                    continue
                c = boot_ratio(r)
                P("      %-16s %+.4f [%+.4f,%+.4f]  shares %7.1f" % (
                    nm, sum(x[0] for x in r) / sum(x[1] for x in r),
                    c[0], c[1], sum(x[1] for x in r)))

    def tot(rule, ws):
        net = sum(pnl(f, s) for s in ws for f in out[rule][s])
        sh = sum(float(f["qty"]) for s in ws for f in out[rule][s])
        return net, sh

    P("\n== 3. withholding rule: no bid on a side whose bid fell by X over "
      "the last N s ==")
    fit = sets[0][1]
    base = tot(None, fit)
    P("   FIT 11-17/18  no rule: net $%+.2f, shares %.0f, per share %+.4f"
      % (base[0], base[1], base[0] / base[1]))
    best = None
    for rule in GRID:
        n_, s_ = tot(rule, fit)
        P("   FIT  X %.2f N %2d: net $%+.2f, shares %.0f, per share %+.4f"
          % (rule[0], rule[1], n_, s_, n_ / s_))
        if best is None or n_ > best[1]:
            best = (rule, n_)
    rule = best[0]
    P("   chosen on FIT: X %.2f, N %d" % rule)
    for name, ws in sets[1:]:
        b_, bs = tot(None, ws)
        n_, ns = tot(rule, ws)
        diff = [sum(pnl(f, s) for f in out[rule][s])
                - sum(pnl(f, s) for f in out[None][s]) for s in ws]
        c = boot_sum(diff)
        rb = boot_ratio([(sum(pnl(f, s) for f in out[rule][s]),
                          sum(float(f["qty"]) for f in out[rule][s]))
                         for s in ws if out[rule][s]])
        P("   HELD OUT %-24s no rule: net $%+.2f (per share %+.4f, shares "
          "%.0f) | with rule: net $%+.2f (per share %+.4f [%+.4f,%+.4f], "
          "shares %.0f) | PAIRED $%+.2f [%+.2f,%+.2f]" % (
              name, b_, b_ / bs, bs, n_, n_ / ns, rb[0], rb[1], ns,
              sum(diff), c[0], c[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
