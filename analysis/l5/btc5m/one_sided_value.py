"""Does the one-sided accumulation rule have economic value?

INPUT (in the repository): analysis/l5/btc5m_accumulation/
btc5m_aligned_20261112.jsonl.gz -- 27,425 rows, 189 windows, 187 resolved,
2026-11-11 21:00Z to 2026-11-12 13:25Z, ~2 s cadence. Each row carries the
causal model p_up, the public UP and DOWN bid/ask, and the final outcome.
It has NO sizes and NO fills, so a fill is a MODEL here, stated below.

THE RULE UNDER TEST (src/l5_btc5m_controller.one_sided_verdict), frozen
before this script was written: a lone maker buy on side s at its own bid
x is ALLOWED iff  p_s - x - 0.06 >= 0.01.

WHAT IS MEASURED, per candidate lone bid (one per side per 10 s per window):
  fill model   THROUGH: the side's ASK reaches x within the next 10 s
               (the quote's life before a requote). Conservative: the
               market traded through our level. A fill is detected at a
               LATER row, so it is at least one poll (~2 s) after the
               decision -- that is the latency allowance.
  settle       1{side won} - x                       (hold to settlement)
  mark@fill    side bid at the fill row - x          (what it could be
  mark@+10s    side bid 10 s after the fill - x       sold for, then)
  All per share, maker fee 0, no rebate (none is credited to this account).

Windows are the unit of independence: every statistic is a mean of
per-window means with a bootstrap over windows. The first and second half
of windows are reported separately. Nothing here is fitted.

THIS IS A REPLAY ON PUBLIC QUOTES. It measures the SIGN and size of a
conditional return under an assumed fill rule; it is not an executable
profit and says nothing about queue position.
"""
import collections
import gzip
import json
import random
import sys

SRC = "analysis/l5/btc5m_accumulation/btc5m_aligned_20261112.jsonl.gz"
ADVERSE, MIN_EDGE, LIFE_S, GRID_S = 0.06, 0.01, 10.0, 10.0


def load():
    by = collections.defaultdict(list)
    for line in gzip.open(SRC, "rt"):
        r = json.loads(line)
        if r.get("settled_outcome") not in ("UP", "DOWN"):
            continue
        by[r["market"]].append(r)
    for m in by:
        by[m].sort(key=lambda r: r["t_in_window_s"])
    return dict(sorted(by.items()))


def quote(r, side):
    q = r["up"] if side == "UP" else r["down"]
    return q.get("bid"), q.get("ask")


def p_side(r, side):
    p = r["p_up"]
    if not p.get("ok") or p.get("value") is None:
        return None
    return p["value"] if side == "UP" else 1.0 - p["value"]


def candidates(rows):
    """One lone-bid candidate per side per GRID_S, with its fate."""
    out, nxt = [], 0.0
    for i, r in enumerate(rows):
        t = r["t_in_window_s"]
        if t < nxt or t > 270:
            continue
        nxt = t + GRID_S
        for side in ("UP", "DOWN"):
            bid, ask = quote(r, side)
            ps = p_side(r, side)
            if bid is None or ask is None or not (0 < bid < ask < 1):
                continue
            fill = None
            for j in range(i + 1, len(rows)):
                if rows[j]["t_in_window_s"] - t > LIFE_S:
                    break
                _b, a = quote(rows[j], side)
                if a is not None and a <= bid:
                    fill = j
                    break
            rec = {"t": t, "side": side, "x": bid, "p": ps,
                   "mid": (bid + ask) / 2,
                   "won": 1.0 if r["settled_outcome"] == side else 0.0,
                   "edge": None if ps is None else ps - bid - ADVERSE,
                   "filled": fill is not None}
            if fill is not None:
                fb, _ = quote(rows[fill], side)
                rec["mark_fill"] = None if fb is None else fb - bid
                later = None
                for k in range(fill, len(rows)):
                    if rows[k]["t_in_window_s"] >= \
                            rows[fill]["t_in_window_s"] + 10:
                        later = quote(rows[k], side)[0]
                        break
                rec["mark_10s"] = None if later is None else later - bid
                rec["settle"] = rec["won"] - bid
            out.append(rec)
    return out


def win_means(per_window, key, pred):
    vals = []
    for recs in per_window.values():
        xs = [r[key] for r in recs if pred(r) and r.get(key) is not None]
        if xs:
            vals.append(sum(xs) / len(xs))
    return vals


def ci(vals, n=4000, seed=1):
    if len(vals) < 5:
        return None
    rng = random.Random(seed)
    k = len(vals)
    ms = sorted(sum(rng.choice(vals) for _ in range(k)) / k
                for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def line(label, per_window, pred):
    n = sum(1 for recs in per_window.values() for r in recs if pred(r))
    out = "%-34s n=%5d" % (label, n)
    for key in ("settle", "mark_fill", "mark_10s"):
        v = win_means(per_window, key, pred)
        if not v:
            out += "  %s        n/a" % key
            continue
        m = sum(v) / len(v)
        c = ci(v)
        out += "  %s %+.4f" % (key, m)
        out += " [%+.3f,%+.3f] w=%d" % (c[0], c[1], len(v)) if c \
            else " [too few] w=%d" % len(v)
    return out


def calibration(per_window, pred, label):
    """Brier of model p vs market mid, per-window mean, on the same rows."""
    bm, bk = [], []
    for recs in per_window.values():
        xs = [r for r in recs if pred(r) and r["p"] is not None]
        if not xs:
            continue
        bm.append(sum((r["p"] - r["won"]) ** 2 for r in xs) / len(xs))
        bk.append(sum((r["mid"] - r["won"]) ** 2 for r in xs) / len(xs))
    if not bm:
        return "%-34s n/a" % label
    d = [a - b for a, b in zip(bm, bk)]
    c = ci(d)
    return "%-34s w=%3d  Brier model %.4f  market %.4f  diff %+.4f %s" % (
        label, len(bm), sum(bm) / len(bm), sum(bk) / len(bk),
        sum(d) / len(d), "[%+.4f,%+.4f]" % c if c else "")


def main():
    data = load()
    names = list(data)
    per = {m: candidates(data[m]) for m in names}
    halves = {"ALL": names, "FIRST HALF": names[:len(names) // 2],
              "SECOND HALF": names[len(names) // 2:]}
    P = print
    P("windows resolved: %d   candidates: %d   filled (THROUGH): %d" % (
        len(names), sum(len(v) for v in per.values()),
        sum(1 for v in per.values() for r in v if r["filled"])))
    P("\n== 1. CALIBRATION at the decision (model p vs market mid) ==")
    cheap = lambda r: r["x"] <= 0.40                         # noqa: E731
    for name, ws in halves.items():
        sub = {m: per[m] for m in ws}
        P(calibration(sub, lambda r: True, name + " all candidates"))
        P(calibration(sub, cheap, name + " side bid<=0.40"))
    P("\n   reliability on the unfavoured side (bid<=0.40), all windows:")
    for lo, hi in ((0, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .6)):
        xs = [r for v in per.values() for r in v
              if r["p"] is not None and r["x"] <= 0.40 and lo <= r["p"] < hi]
        if xs:
            P("   model p %.1f-%.1f  n=%5d  mean p %.3f  mean mid %.3f  "
              "won %.3f" % (lo, hi, len(xs),
                            sum(r["p"] for r in xs) / len(xs),
                            sum(r["mid"] for r in xs) / len(xs),
                            sum(r["won"] for r in xs) / len(xs)))
    P("\n== 2. FILLED LONE BIDS: per-share return, mean of window means, "
      "95% bootstrap over windows ==")
    allow = lambda r: r["filled"] and r["edge"] is not None \
        and r["edge"] >= MIN_EDGE                            # noqa: E731
    refuse = lambda r: r["filled"] and (r["edge"] is None
                                        or r["edge"] < MIN_EDGE)  # noqa
    anyf = lambda r: r["filled"]                             # noqa: E731
    for name, ws in halves.items():
        sub = {m: per[m] for m in ws}
        P("-- %s (%d windows)" % (name, len(ws)))
        for lab, base in (("all sides", lambda r: True),
                          ("unfavoured side bid<=0.40", cheap),
                          ("unfavoured side bid<=0.25",
                           lambda r: r["x"] <= 0.25)):
            P(line("  %s | every fill" % lab, sub,
                   lambda r, b=base: anyf(r) and b(r)))
            P(line("  %s | rule ALLOWS" % lab, sub,
                   lambda r, b=base: allow(r) and b(r)))
            P(line("  %s | rule REFUSES" % lab, sub,
                   lambda r, b=base: refuse(r) and b(r)))
    P("\n== 3. UNCONDITIONAL (not filled-only): what the same bids are "
      "worth if they always filled ==")
    for lab, pred in (("rule ALLOWS, bid<=0.40",
                       lambda r: cheap(r) and r["edge"] is not None
                       and r["edge"] >= MIN_EDGE),
                      ("rule REFUSES, bid<=0.40",
                       lambda r: cheap(r) and (r["edge"] is None
                                               or r["edge"] < MIN_EDGE))):
        v = []
        for recs in per.values():
            xs = [r["won"] - r["x"] for r in recs if pred(r)]
            if xs:
                v.append(sum(xs) / len(xs))
        c = ci(v)
        P("%-28s w=%3d  won-x %+.4f %s" % (
            lab, len(v), sum(v) / len(v),
            "[%+.3f,%+.3f]" % c if c else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
