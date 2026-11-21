"""First-leg entry quality: adverse selection on fills, whether a vulnerable
bid could have been pulled in time, and ONE targeted first-leg filter.

DATA
  live     analysis/l5/btc5m/evidence/live_windows_20261121.json -- the two
           live windows of 2026-11-21 (29 venue-confirmed maker fills), with
           the lane's own book reading at every ~5 s step.
  replay   the recorded public quotes used by inventory_controller_replay:
           ~2 s quotes 11-17..20, ~10 s quotes 11-11..16.

REPLAY  `src.l5_btc5m_controller.decide`, unmodified, live configuration
(clip $0.05, side cap $0.30, unmatched cap $0.10, window-loss ceiling $0.20,
pair-cost cap OFF, heavy-underdog stop OFF). Orders rest between decisions.
DECISIONS ARE TAKEN EVERY ~6 s on the 2 s data, as the live lane does
(measured: 45 steps in 260 s), while FILLS are checked at every quote; on
the 10 s data both happen at every quote. A rule can therefore act only as
fast as the lane could.

FILLS are inferred (the recorder has no trades):
  STRICT  the side's ask reaches our price        -- certain, worst selection
  TOUCH   STRICT, or the side's bid drops below our price
Both are reported. NEITHER reproduces what the live windows show most
often: a bid filled 1-3 s after it was placed while the quoted bid did not
move at all. The replay therefore under-counts fills and cannot be used to
say what the live fill rate would be.

A FILL IS
  INCREASING  on the side that held at least as much as the other before
              the fill (a first leg, or more of the heavier side);
  REDUCING    on the lighter side (it reduces the unmatched quantity).
MARKOUT = the side's own BID (what the share could be sold at) N seconds
after the fill, minus the price paid. Maker fee 0.

PART A  live fills: markouts at +5/+15/+30 s by kind; time from the venue
        accepting the order to the fill (window 14:20Z, venue timestamps).
PART B  replay fills: the same markouts by kind, and settlement result.
PART C  could the bid have been pulled? For each INCREASING replay fill, how
        long before the fill the side's bid first stood below its level at
        placement, and what a pull rule does when the lane decides every
        6 s versus every 2 s.
PART D  ONE FILTER -- "quiet first leg": no INCREASING bid (and its resting
        bids cancelled) on a side whose own bid moved by at least X in
        either direction over the last N s. REDUCING bids are untouched.
        Six (X, N) pairs; the best total net on 11-17/18 is frozen and
        scored on 11-19/20 and on 11-11..16.

ACCOUNTING  settlement proceeds minus cost, summed over every fill and every
window run (no per-window averaging). PAIRED = rule minus no-rule in the
same window, 95% interval by bootstrap over windows.
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
from src import l5_btc5m_controller as C            # noqa: E402
import inventory_controller_replay as R             # noqa: E402

PAR = R.params(False)
LIVE = "analysis/l5/btc5m/evidence/live_windows_20261121.json"
HORIZONS = (5, 15, 30)
QUIET_GRID = [(x, n) for n in (6, 10, 30) for x in (0.02, 0.03)]


# ------------------------------------------------------------------ replay

def run_window(job):
    slug, rows, cfg = job
    touch, step_s, rule = cfg
    ts = [r[0] for r in rows]
    inv = {s: [D(0), D(0)] for s in C.SIDES}
    rest, fills, nid = [], [], 0
    last_dec = None

    def side_bid(i, side):
        return rows[i][1] if side == C.UP else 1 - rows[i][2]

    def move(i, side, n):
        j = bisect.bisect_right(ts, ts[i] - n) - 1
        return None if j < 0 else side_bid(i, side) - side_bid(j, side)

    def increasing(side):
        o = C.DOWN if side == C.UP else C.UP
        return inv[side][0] >= inv[o][0]

    for i, (t, b, a) in enumerate(rows):
        sa = {C.UP: D(str(a)), C.DOWN: D(1) - D(str(b))}
        sb = {C.UP: D(str(b)), C.DOWN: D(1) - D(str(a))}
        keep = []
        for o in rest:
            hit = sa[o["side"]] <= o["price"] or (
                touch and sb[o["side"]] < o["price"])
            if hit:
                kind = "INCREASING" if increasing(o["side"]) else "REDUCING"
                inv[o["side"]][0] += o["qty"]
                inv[o["side"]][1] += o["qty"] * o["price"]
                #: first quote after placement at which the side's bid
                #: stood below its level at placement
                warn = None
                for j in range(o["i"] + 1, i + 1):
                    if side_bid(j, o["side"]) < o["bid0"] - 1e-9:
                        warn = ts[j]
                        break
                mk = []
                for h in HORIZONS:
                    j = bisect.bisect_left(ts, t + h)
                    mk.append(None if j >= len(ts) else
                              side_bid(j, o["side"]) - float(o["price"]))
                fills.append(dict(side=o["side"], qty=float(o["qty"]),
                                  price=float(o["price"]), kind=kind,
                                  rested=t - o["t"],
                                  lead=None if warn is None else t - warn,
                                  mk=mk))
            else:
                keep.append(o)
        rest = keep
        if last_dec is not None and t - last_dec < step_s - 0.5:
            continue
        last_dec = t
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
        if rule is not None and rule[0] == "QUIET":
            for s in C.SIDES:
                m = move(i, s, rule[2])
                if increasing(s) and m is not None \
                        and abs(m) >= rule[1] - 1e-9:
                    blocked.add(s)
            rest = [o for o in rest if not (
                o["side"] in blocked and o["reason"] == "STRADDLE_LEG")]
        if rule is not None and rule[0] == "COMPLETE":
            #: WHICH COMPLETING BIDS ARE ALLOWED (the lighter side only).
            #:   NEVER    none: unmatched inventory is held to settlement
            #:   CHEAP    only when the completing side's bid <= rule[2]
            #:   DEAR     only when the completing side's bid >= rule[2]
            for s_ in C.SIDES:
                o_ = C.DOWN if s_ == C.UP else C.UP
                if inv[s_][0] >= inv[o_][0]:
                    continue                      # not the lighter side
                px_ = side_bid(i, s_)
                ok_ = (rule[1] == "CHEAP" and px_ <= rule[2] + 1e-9) or (
                    rule[1] == "DEAR" and px_ >= rule[2] - 1e-9)
                if not ok_:
                    blocked.add(s_)
            rest = [o for o in rest if not (
                o["side"] in blocked and o["reason"] == "STRADDLE_LEG")]
        if rule is not None and rule[0] == "PULL":
            #: the side's bid now stands below where it stood when this
            #: bid was placed: pull it, and do not re-bid that side now
            gone = [o for o in rest if increasing(o["side"])
                    and side_bid(i, o["side"]) < o["bid0"] - 1e-9]
            for o in gone:
                blocked.add(o["side"])
            rest = [o for o in rest if o not in gone]
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
            rest.append({"id": "o%d" % nid, "side": o["side"], "price": px,
                         "qty": q, "t": t, "i": i,
                         "bid0": side_bid(i, o["side"]),
                         "reason": o.get("reason")})
    return slug, cfg, fills


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
    if not v:
        return (0.0, 0.0)
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
    if not out:
        return (float("nan"), float("nan"))
    return out[int(.025 * len(out))], out[int(.975 * len(out)) - 1]


# -------------------------------------------------------------------- live

def _ts(s):
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        frac = rest[:-6] if rest.endswith("+00:00") else rest
        s = "%s.%s+00:00" % (head, (frac + "000000")[:6])
    return dt.datetime.fromisoformat(s).timestamp()


def part_a(P):
    doc = json.load(open(LIVE))
    P("== PART A. the two live windows of 2026-11-21 (venue-confirmed "
      "maker fills, fee 0) ==")
    agg = collections.defaultdict(lambda: [[0.0, 0.0] for _ in HORIZONS])
    setl = collections.defaultdict(lambda: [0.0, 0.0])
    delays = []
    for mk, steps in sorted(doc["steps"].items()):
        won = doc["settled"][mk]
        st = [(_ts(s["utc"]), float(s["bid"]) if s["bid"] else None,
               float(s["ask"]) if s["ask"] else None) for s in steps]
        times = {}
        for o in doc["orders"].get(mk, []):
            v = o.get("venue") or {}
            if float(o["filled_qty"]) > 0 and v.get("lastTransactTime"):
                times[o["venue_order_id"]] = _ts(v["lastTransactTime"])
                delays.append((_ts(v["lastTransactTime"])
                               - _ts(v["insertTime"]), o["side"],
                               o["price"]))
        exact = bool(times)
        q = {"UP": 0.0, "DOWN": 0.0}
        rows = []
        for s in steps:
            for side, qty, px, vid in s["fills"]:
                rows.append((times.get(vid, _ts(s["utc"])), side,
                             float(qty), float(px)))
        rows.sort()
        P("  %s (settled %s; fill time = %s)" % (
            mk[-16:], won,
            "venue lastTransactTime" if exact else
            "the step that REPORTED the fill, up to ~6 s late"))
        for t, side, qty, px in rows:
            o = "DOWN" if side == "UP" else "UP"
            kind = "INCREASING" if q[side] >= q[o] else "REDUCING"
            q[side] += qty
            cells = []
            for k, h in enumerate(HORIZONS):
                nxt = [x for x in st if x[0] >= t + h]
                if not nxt:
                    cells.append("   n/a")
                    continue
                _, b, a = nxt[0]
                sbid = (b if side == "UP" else
                        (None if a is None else 1 - a))
                if sbid is None:
                    #: no ask quoted: the other side is at 0.99+, this
                    #: side's bid is worth nothing
                    sbid = 0.0 if side == "DOWN" else b
                if sbid is None:
                    cells.append("   n/a")
                    continue
                cells.append("%+.2f" % (sbid - px))
                agg[kind][k][0] += qty * (sbid - px)
                agg[kind][k][1] += qty
            res = (1.0 if side == won else 0.0) - px
            setl[kind][0] += qty * res
            setl[kind][1] += qty
            P("    %s %-4s %.2f @ %.2f  %-10s  +5s %s  +15s %s  +30s %s  "
              "settle %+.2f" % (
                  dt.datetime.fromtimestamp(t, dt.timezone.utc)
                  .strftime("%H:%M:%S"), side, qty, px, kind,
                  cells[0], cells[1], cells[2], res))
    P("  per share, both windows (share-weighted):")
    for kind in ("INCREASING", "REDUCING"):
        a = agg[kind]
        P("    %-10s shares %.2f  +5s %+.4f  +15s %+.4f  +30s %+.4f  "
          "settlement %+.4f ($%+.4f)" % (
              kind, setl[kind][1],
              *[x[0] / x[1] if x[1] else float("nan") for x in a],
              setl[kind][0] / setl[kind][1], setl[kind][0]))
    delays.sort()
    P("  14:20Z, venue accept -> fill, the 12 filled orders (s): %s" % (
        ", ".join("%.1f" % d[0] for d in delays)))
    P("    median %.1f s; %d of %d filled within 3 s; the lane re-reads "
      "about every 5.5 s." % (
          delays[len(delays) // 2][0],
          sum(1 for d in delays if d[0] <= 3.0), len(delays)))
    P("  29 fills in 2 windows: an observation, not an estimate.")


# -------------------------------------------------------------------- main

def main():
    out_lines = []

    def P(s=""):
        print(s, flush=True)
        out_lines.append(s)

    part_a(P)
    rows, res = load()
    slugs = sorted(rows)
    sets = (("11-17/18 (FIT)", [s for s in slugs if s[-16:-6] in
                                ("2026-11-17", "2026-11-18")], 6.0),
            ("11-19/20 (held out)", [s for s in slugs if s[-16:-6] in
                                     ("2026-11-19", "2026-11-20")], 6.0),
            ("11-11..16 (held out, 10 s quotes)",
             [s for s in slugs if s[-16:-6] < "2026-11-17"], 6.0))
    cfgs = []
    for touch in (False, True):
        cfgs.append((touch, 6.0, None))
        for g in QUIET_GRID:
            cfgs.append((touch, 6.0, ("QUIET",) + g))
        cfgs.append((touch, 6.0, ("PULL",)))
        cfgs.append((touch, 2.0, None))
        cfgs.append((touch, 2.0, ("PULL",)))
    jobs = [(s, rows[s], c) for s in slugs for c in cfgs]
    out = collections.defaultdict(dict)
    with Pool(os.cpu_count() or 2) as pool:
        for slug, cfg, fills in pool.imap_unordered(run_window, jobs,
                                                    chunksize=8):
            out[cfg][slug] = fills

    def pnl(f, s):
        return f["qty"] * ((1.0 if res[s] == f["side"] else 0.0)
                           - f["price"])

    def tot(cfg, ws):
        net = sum(pnl(f, s) for s in ws for f in out[cfg][s])
        sh = sum(f["qty"] for s in ws for f in out[cfg][s])
        return net, sh

    P("\n== PART B. replay fills by kind (no rule, lane decides every 6 s): "
      "per share [95% over windows], shares ==")
    for touch in (False, True):
        P("  fills: %s" % ("TOUCH" if touch else "STRICT"))
        base = (touch, 6.0, None)
        for name, ws, _ in sets:
            P("   %s  windows %d" % (name, len(ws)))
            for kind in ("INCREASING", "REDUCING"):
                line = "      %-10s" % kind
                sh = sum(f["qty"] for s in ws for f in out[base][s]
                         if f["kind"] == kind)
                line += " shares %7.1f" % sh
                for k, h in enumerate(HORIZONS):
                    r = []
                    for s in ws:
                        a = d = 0.0
                        for f in out[base][s]:
                            if f["kind"] == kind and f["mk"][k] is not None:
                                a += f["qty"] * f["mk"][k]
                                d += f["qty"]
                        if d:
                            r.append((a, d))
                    if not r:
                        continue
                    c = boot_ratio(r)
                    line += "  +%ds %+.4f [%+.4f,%+.4f]" % (
                        h, sum(x[0] for x in r) / sum(x[1] for x in r),
                        c[0], c[1])
                r = []
                for s in ws:
                    a = sum(pnl(f, s) for f in out[base][s]
                            if f["kind"] == kind)
                    d = sum(f["qty"] for f in out[base][s]
                            if f["kind"] == kind)
                    if d:
                        r.append((a, d))
                if r:
                    c = boot_ratio(r)
                    line += "  settle %+.4f [%+.4f,%+.4f] ($%+.2f)" % (
                        sum(x[0] for x in r) / sum(x[1] for x in r),
                        c[0], c[1], sum(x[0] for x in r))
                P(line)

    P("\n== PART C. could an INCREASING bid have been pulled before it "
      "filled? ==")
    P("  (2 s quotes only: 11-17..20. 'warning' = the side's bid stood "
      "below its level at placement at a quote BEFORE the fill.)")
    two = sets[0][1] + sets[1][1]
    for touch in (False, True):
        base = (touch, 6.0, None)
        fs = [(f, s) for s in two for f in out[base][s]
              if f["kind"] == "INCREASING"]
        n = len(fs)
        sh = sum(f["qty"] for f, _ in fs)
        loss = sum(pnl(f, s) for f, s in fs)
        P("  fills %s: %d INCREASING fills, %.1f shares, settlement $%+.2f"
          % ("TOUCH" if touch else "STRICT", n, sh, loss))
        rested = sorted(f["rested"] for f, _ in fs)
        P("    seconds resting before the fill: median %.0f, 25%% within "
          "%.0f, 75%% within %.0f" % (
              rested[n // 2], rested[n // 4], rested[3 * n // 4]))
        for lead in (2, 4, 6, 8):
            sel = [(f, s) for f, s in fs
                   if f["lead"] is not None and f["lead"] >= lead - 0.5]
            P("    warning at least %d s before the fill: %4.1f%% of "
              "shares, carrying $%+.2f of the $%+.2f" % (
                  lead, 100 * sum(f["qty"] for f, _ in sel) / sh,
                  sum(pnl(f, s) for f, s in sel), loss))
        none = [(f, s) for f, s in fs if f["lead"] is None]
        P("    no warning at any earlier quote:        %4.1f%% of shares, "
          "carrying $%+.2f" % (
              100 * sum(f["qty"] for f, _ in none) / sh,
              sum(pnl(f, s) for f, s in none)))
    P("  PULL RULE (cancel an INCREASING bid once the side's bid stands "
      "below its level at placement; do not re-bid that side at that "
      "decision). Total window economics:")
    for touch in (False, True):
        for step in (6.0, 2.0):
            b_ = (touch, step, None)
            r_ = (touch, step, ("PULL",))
            for name, ws, _ in sets[:2]:
                bn, bs = tot(b_, ws)
                rn, rs = tot(r_, ws)
                diff = [sum(pnl(f, s) for f in out[r_][s])
                        - sum(pnl(f, s) for f in out[b_][s]) for s in ws]
                c = boot_sum(diff)
                P("    %-6s decide every %d s  %-20s no rule $%+7.2f "
                  "(%.0f sh, %+.4f/sh) | pull $%+7.2f (%.0f sh, %+.4f/sh) "
                  "| PAIRED $%+.2f [%+.2f,%+.2f]" % (
                      "TOUCH" if touch else "STRICT", step, name, bn, bs,
                      bn / bs if bs else 0, rn, rs, rn / rs if rs else 0,
                      sum(diff), c[0], c[1]))

    P("\n== PART D. ONE FILTER: quiet first leg (no INCREASING bid while "
      "the side's own bid moved >= X over the last N s) ==")
    for touch in (False, True):
        P("  fills: %s" % ("TOUCH" if touch else "STRICT"))
        base = (touch, 6.0, None)
        fit = sets[0][1]
        bn, bs = tot(base, fit)
        P("   FIT 11-17/18  no rule: net $%+.2f, shares %.0f, per share "
          "%+.4f" % (bn, bs, bn / bs))
        best = None
        for g in QUIET_GRID:
            cfg = (touch, 6.0, ("QUIET",) + g)
            n_, s_ = tot(cfg, fit)
            P("   FIT  X %.2f N %2d: net $%+.2f, shares %.0f, per share "
              "%+.4f" % (g[0], g[1], n_, s_, n_ / s_ if s_ else 0))
            if best is None or n_ > best[1]:
                best = (cfg, n_, g)
        cfg = best[0]
        P("   chosen on FIT: X %.2f, N %d" % best[2])
        for name, ws, _ in sets[1:]:
            b_, bsh = tot(base, ws)
            n_, nsh = tot(cfg, ws)
            diff = [sum(pnl(f, s) for f in out[cfg][s])
                    - sum(pnl(f, s) for f in out[base][s]) for s in ws]
            c = boot_sum(diff)
            rb = boot_ratio([(sum(pnl(f, s) for f in out[cfg][s]),
                              sum(f["qty"] for f in out[cfg][s]))
                             for s in ws if out[cfg][s]])
            per = [sum(pnl(f, s) for f in out[cfg][s]) for s in ws]
            ci = boot_sum(per)
            P("   %-34s no rule: net $%+.2f (%.0f sh, %+.4f/sh) | rule: "
              "net $%+.2f [%+.2f,%+.2f] (%.0f sh, %+.4f/sh [%+.4f,%+.4f]) "
              "| PAIRED $%+.2f [%+.2f,%+.2f]" % (
                  name, b_, bsh, b_ / bsh, n_, ci[0], ci[1], nsh,
                  n_ / nsh if nsh else 0, rb[0], rb[1],
                  sum(diff), c[0], c[1]))
            for kind in ("INCREASING", "REDUCING"):
                kb = sum(pnl(f, s) for s in ws for f in out[base][s]
                         if f["kind"] == kind)
                kr = sum(pnl(f, s) for s in ws for f in out[cfg][s]
                         if f["kind"] == kind)
                sb_ = sum(f["qty"] for s in ws for f in out[base][s]
                          if f["kind"] == kind)
                sr_ = sum(f["qty"] for s in ws for f in out[cfg][s]
                          if f["kind"] == kind)
                P("        %-10s no rule $%+.2f on %.0f sh | rule $%+.2f "
                  "on %.0f sh" % (kind, kb, sb_, kr, sr_))
    open("analysis/l5/btc5m/FIRST_LEG_ENTRY_QUALITY_RESULT.txt", "w").write(
        "\n".join(out_lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
