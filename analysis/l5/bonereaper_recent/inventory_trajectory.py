"""Bonereaper's inventory path: does he add to his heavier side because
the market supports it, or does his concentration only mirror the price?

DATA  his fills (activity feed) and every trader's prints in the same
      markets (the tape). Two sets:
        TODAY   24 consecutive windows, 2026-11-20 19:00-21:00 UTC
        4 DAYS  2026-11-17..20 (to 14:00), ~1,100 windows; 11-17/18 FIT,
                11-19/20 TEST
KNOWN BEFORE EACH FILL (prints at least 2 s older than the fill stamp):
  price    UP's last traded price (a DOWN print at q counts as 1 - q)
  trend    that price now minus 30 s earlier
  holdings his UP and DOWN shares from his own earlier fills
  heavy    the side he holds more shares of;  fav = side priced >= 0.50
No outcome is used to form these. Outcomes only score the dollars.

ACCOUNTING: every number is dollars summed over fills: payout of the
shares bought minus what he paid (his price is after the venue's fee).
"return" = that sum / dollars spent. Intervals resample windows.
"""
import bisect
import collections
import csv
import gzip
import json
import random
import re
import sys

import os
LAG = int(os.environ.get('LAG', '2'))


def load_activity(path):
    op = gzip.open if path.endswith(".gz") else open
    fills = collections.defaultdict(list)
    won = {}
    for line in op(path, "rt"):
        r = json.loads(line)
        m = re.match(r"btc-updown-5m-(\d+)$", r.get("slug") or "")
        if not m:
            continue
        s = r["slug"]
        if r["type"] == "REDEEM" and (r.get("usdc_size") or 0) > 0:
            won[s] = r["outcome"].upper()
        elif r["type"] == "TRADE" and r["side"] == "BUY":
            fills[s].append((r["timestamp"], r["outcome"].upper(),
                             r["price"], r["size"], r["usdc_size"]))
    return fills, won


def load_tape(path):
    tape = {}
    for line in gzip.open(path, "rt"):
        d = json.loads(line)
        ts, px = [], []
        for t, oc, _side, p, *_ in sorted(d["trades"]):
            ts.append(t)
            px.append(p if oc.upper() == "UP" else 1 - p)
        tape[d["slug"]] = (ts, px)
    return tape


def price_at(tp, t):
    i = bisect.bisect_right(tp[0], t) - 1
    return None if i < 0 else tp[1][i]


def boot_ratio(rows, n=3000, seed=4):
    """rows: per-window (numerator, denominator)."""
    if len(rows) < 8:
        return None
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


def events(s, fills, tape, won):
    """One row per fill with what was known just before it."""
    t0 = int(s.rsplit("-", 1)[1])
    cu = cd = 0.0
    pend = []
    out = []
    for ts, oc, px, sh, usd in sorted(fills[s]):
        while pend and pend[0][0] < ts:           # earlier seconds only
            _t, o, q = pend.pop(0)
            if o == "UP":
                cu += q
            else:
                cd += q
        p = price_at(tape[s], ts - LAG)
        p30 = price_at(tape[s], ts - LAG - 30)
        heavy = None if abs(cu - cd) < 1e-6 else ("UP" if cu > cd else
                                                  "DOWN")
        fav = None if p is None else ("UP" if p >= 0.5 else "DOWN")
        tr = None
        if p is not None and p30 is not None:
            d = p - p30
            tr = "UP" if d > 0.02 else "DOWN" if d < -0.02 else "FLAT"
        pnl = sh * (1.0 if won[s] == oc else 0.0) - usd
        out.append(dict(t=ts - t0, side=oc, px=px, sh=sh, usd=usd, p=p,
                        heavy=heavy, fav=fav, trend=tr, pnl=pnl,
                        cu=cu, cd=cd))
        pend.append((ts, oc, sh))
    return out


def table_a(name, ws, EV):
    print("\n== A. %s: where his dollars go, by what he held and what the "
          "market was doing ==" % name)
    print("   %-44s %7s  %9s  %s" % ("state just before the fill",
                                     "dollars", "to HEAVY", "return on "
                                     "the dollars sent to heavy | light"))
    cells = collections.OrderedDict()
    for key in (("heavy = favourite", "heavy side rising"),
                ("heavy = favourite", "flat"),
                ("heavy = favourite", "heavy side falling"),
                ("heavy = UNDERDOG", "heavy side rising"),
                ("heavy = UNDERDOG", "flat"),
                ("heavy = UNDERDOG", "heavy side falling")):
        cells[key] = collections.defaultdict(lambda: [0.0] * 4)
    for s in ws:
        for e in EV[s]:
            if e["heavy"] is None or e["fav"] is None or e["trend"] is None:
                continue
            a = "heavy = favourite" if e["heavy"] == e["fav"] \
                else "heavy = UNDERDOG"
            b = "flat" if e["trend"] == "FLAT" else (
                "heavy side rising" if e["trend"] == e["heavy"]
                else "heavy side falling")
            c = cells[(a, b)][s]
            k = 0 if e["side"] == e["heavy"] else 2
            c[k] += e["usd"]
            c[k + 1] += e["pnl"]
    for key, byw in cells.items():
        rows = list(byw.values())
        hu = sum(r[0] for r in rows)
        lu = sum(r[2] for r in rows)
        if hu + lu == 0:
            continue
        ch = boot_ratio([(r[1], r[0]) for r in rows])
        cl = boot_ratio([(r[3], r[2]) for r in rows])
        f = lambda c: "[%+.3f,%+.3f]" % c if c else "[few]"
        print("   %-18s %-25s $%8.0f  %7.1f%%   %+.3f %-17s | %+.3f %s" % (
            key[0], key[1], hu + lu, 100 * hu / (hu + lu),
            sum(r[1] for r in rows) / hu if hu else 0, f(ch),
            sum(r[3] for r in rows) / lu if lu else 0, f(cl)))


def fit_null(ws, EV):
    """His dollar share on UP as a function of UP's price and the minute:
    what price alone would make him do."""
    acc = collections.defaultdict(lambda: [0.0, 0.0])
    for s in ws:
        for e in EV[s]:
            if e["p"] is None:
                continue
            k = (min(int(e["p"] * 20), 19), max(min(int(e["t"] // 60), 4),
                                                0))
            acc[k][0] += e["usd"] if e["side"] == "UP" else 0
            acc[k][1] += e["usd"]
    return {k: v[0] / v[1] for k, v in acc.items() if v[1] > 0}


def table_b(name, ws, EV, null, won):
    """Same dollars, same moments; sides chosen by price alone vs his."""
    rows = []
    act = slip = 0.0
    for s in ws:
        a = n = d = 0.0
        for e in EV[s]:
            if e["p"] is None or not (0.02 < e["p"] < 0.98):
                continue
            k = (min(int(e["p"] * 20), 19), max(min(int(e["t"] // 60), 4),
                                                0))
            if k not in null:
                continue
            pu, pd = min(e["p"] + 0.005, 0.995), min(1 - e["p"] + 0.005,
                                                     0.995)
            ru = (1.0 if won[s] == "UP" else 0.0) / pu - 1
            rd = (1.0 if won[s] == "DOWN" else 0.0) / pd - 1
            a += e["usd"] * (ru if e["side"] == "UP" else rd)
            n += e["usd"] * (null[k] * ru + (1 - null[k]) * rd)
            d += e["usd"]
            act += e["pnl"]
            slip += e["usd"] * (e["px"] - (pu if e["side"] == "UP" else pd))
        if d:
            rows.append((a, n, d))
    D = sum(r[2] for r in rows)
    ca = boot_ratio([(r[0], r[2]) for r in rows])
    cn = boot_ratio([(r[1], r[2]) for r in rows])
    cd = boot_ratio([(r[0] - r[1], r[2]) for r in rows])
    f = lambda c: "[%+.4f,%+.4f]" % c if c else "[few]"
    print("   %-22s windows %4d  $%9.0f | his sides %+.4f %s | price-only "
          "sides %+.4f %s | HIS MINUS PRICE-ONLY %+.4f %s" % (
              name, len(rows), D, sum(r[0] for r in rows) / D, f(ca),
              sum(r[1] for r in rows) / D, f(cn),
              sum(r[0] - r[1] for r in rows) / D, f(cd)))
    print("   %-22s at the prices he ACTUALLY paid: %+.4f per dollar; he "
          "paid %+.4f a share more than the %d-s-old price" % (
              "", act / D, slip / D, LAG))


def table_c(name, ws, EV):
    print("\n== C. %s: his position at fixed moments, profitable vs losing "
          "windows (only what was observable then) ==" % name)
    print("   %-9s %-10s %5s  %-22s %-26s %-24s" % (
        "at", "windows", "n", "share of final spend in",
        "net shares on the favourite", "avg price paid so far"))
    for cp in (60, 120, 180, 240):
        for lab, sel in (("profit", lambda x: x > 0),
                         ("loss", lambda x: x <= 0)):
            sp = im = ap = 0.0
            n = 0
            for s in ws:
                ev = EV[s]
                tot = sum(e["usd"] for e in ev)
                if not sel(sum(e["pnl"] for e in ev)) or not tot:
                    continue
                pre = [e for e in ev if e["t"] < cp]
                if not pre or pre[-1]["fav"] is None:
                    continue
                u = sum(e["sh"] for e in pre if e["side"] == "UP")
                d = sum(e["sh"] for e in pre if e["side"] == "DOWN")
                fav = pre[-1]["fav"]
                usd = sum(e["usd"] for e in pre)
                sp += usd / tot
                im += ((u - d) if fav == "UP" else (d - u)) / (u + d)
                ap += usd / (u + d)
                n += 1
            if n:
                print("   %3d s    %-10s %5d  %18.0f%%   %+22.2f      %18.3f"
                      % (cp, lab, n, 100 * sp / n, im / n, ap / n))


def main():
    act4, tape4, act24, tape24, out_csv = sys.argv[1:6]
    f4, w4 = load_activity(act4)
    t4 = load_tape(tape4)
    f24, w24 = load_activity(act24)
    t24 = load_tape(tape24)
    S4 = sorted(s for s in f4 if s in w4 and s in t4)
    S24 = sorted(s for s in f24 if s in w24 and s in t24)
    EV = {s: events(s, f4, t4, w4) for s in S4}
    EV24 = {s: events(s, f24, t24, w24) for s in S24}
    import datetime as dt
    day = lambda s: dt.datetime.fromtimestamp(
        int(s.rsplit("-", 1)[1]), dt.timezone.utc).strftime("%m-%d")
    fit = [s for s in S4 if day(s) in ("11-17", "11-18")]
    test = [s for s in S4 if day(s) in ("11-19", "11-20")]
    print("windows: today %d; four days %d (fit %d, test %d)" % (
        len(S24), len(S4), len(fit), len(test)))

    # the 10-second trajectory of the 24 windows, as a file
    with open(out_csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["window_start_utc", "winner", "t_from", "up_price_before",
                    "usd_up", "usd_down", "cum_up_shares", "cum_down_shares",
                    "cum_up_cost", "cum_down_cost", "net_shares_up_minus_down",
                    "usd_to_heavier_side", "usd_to_lighter_side"])
        for s in S24:
            t0 = int(s.rsplit("-", 1)[1])
            cu = cd = ku = kd = 0.0
            for b in range(-60, 300, 10):
                ev = [e for e in EV24[s] if b <= e["t"] < b + 10]
                if not ev and b < 0:
                    continue
                uu = sum(e["usd"] for e in ev if e["side"] == "UP")
                ud = sum(e["usd"] for e in ev if e["side"] == "DOWN")
                hv = sum(e["usd"] for e in ev if e["heavy"] == e["side"])
                lt = sum(e["usd"] for e in ev if e["heavy"] not in
                         (None, e["side"]))
                cu += sum(e["sh"] for e in ev if e["side"] == "UP")
                cd += sum(e["sh"] for e in ev if e["side"] == "DOWN")
                ku += uu
                kd += ud
                p = price_at(t24[s], t0 + b - LAG)
                w.writerow([dt.datetime.fromtimestamp(
                    t0, dt.timezone.utc).strftime("%H:%M"), w24[s], b,
                    "" if p is None else "%.3f" % p, "%.2f" % uu,
                    "%.2f" % ud, "%.2f" % cu, "%.2f" % cd, "%.2f" % ku,
                    "%.2f" % kd, "%.2f" % (cu - cd), "%.2f" % hv,
                    "%.2f" % lt])

    table_a("TODAY, 24 windows", S24, EV24)
    table_a("FIT days 11-17/18", fit, EV)
    table_a("TEST days 11-19/20", test, EV)

    print("\n== B. same dollars at the same moments: his choice of side "
          "against a choice made from price and minute alone ==")
    print("   (price-only rule fitted on 11-17/18; both scored at the same "
          "proxy prices, return per dollar)")
    null = fit_null(fit, EV)
    table_b("fit 11-17/18", fit, EV, null, w4)
    table_b("TEST 11-19/20", test, EV, null, w4)
    table_b("TODAY 24 windows", S24, EV24, null, w24)

    table_c("TODAY, 24 windows", S24, EV24)
    table_c("TEST days 11-19/20", test, EV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
