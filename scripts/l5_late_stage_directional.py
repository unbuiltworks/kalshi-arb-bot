"""Late-stage directional test: is L5 stacking the winner near settlement?

DATA. L5's BTC-15m BUY fills (both caches, deduped) joined to real
settlement from `markets.jsonl` `outcomePrices` (["1","0"] = Up won).
Window end is the slug epoch + 900 s.

SETTLEMENT IS USED ONLY AS AN OUTCOME LABEL, never as a feature or a
decision input. The counterfactual below decides purely from the state at
the moment of L5's fill and is scored afterwards.

COUNTERFACTUAL ASSUMPTIONS, stated because they decide the answer:
  * we are a TAKER lifting the favored side, so we pay L5's observed fill
    price PLUS one tick (0.01). We do NOT assume a touch fill.
  * fees use the frozen taker model, THETA_TAKER * px * (1-px) per share.
  * ONE q10 clip per observed opportunity, and at most one per market per
    bucket, so a market with 40 late fills cannot be counted 40 times.
  * a clip that would price above 1.00 all-in is skipped as unexecutable.
"""
import collections
import json
import statistics
from decimal import Decimal as D

CLIP = D(10)
TICK = D("0.01")
THETA_TAKER = D("0.0695")
WINDOW_S = 900.0

BUCKETS = [(120, 60, "120-60s"), (60, 30, "60-30s"), (30, 15, "30-15s"),
           (15, 5, "15-5s"), (5, 0, "<5s")]
PX_BUCKETS = [("0.60-0.70", D("0.60"), D("0.70")),
              ("0.70-0.80", D("0.70"), D("0.80")),
              ("0.80-0.90", D("0.80"), D("0.90")),
              ("0.90-0.95", D("0.90"), D("0.95")),
              ("0.95+", D("0.95"), D("1.01"))]


def settlement():
    """slug -> winning side, from the venue's own resolved outcomePrices."""
    out = {}
    for ln in open("data/l5/raw/markets.jsonl"):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        for m in (d.get("payload") or []):
            slug = str(m.get("slug") or "")
            if not slug.startswith("btc-updown-15m"):
                continue
            try:
                oc = json.loads(m.get("outcomes") or "[]")
                px = json.loads(m.get("outcomePrices") or "[]")
            except Exception:
                continue
            if len(oc) != 2 or len(px) != 2:
                continue
            if str(m.get("umaResolutionStatus")) != "resolved":
                continue
            win = None
            for name, p in zip(oc, px):
                if str(p) in ("1", "1.0", "1.00"):
                    win = str(name).strip().lower()
            if win in ("up", "down"):
                out[slug] = win
    return out


def fills():
    seen, per = set(), collections.defaultdict(list)
    for fn in ("data/l5/raw/trades.jsonl",
               "data/l5/raw/trades_takeronly.jsonl"):
        try:
            fh = open(fn)
        except OSError:
            continue
        for ln in fh:
            try:
                d = json.loads(ln)
            except Exception:
                continue
            for it in (d.get("payload") or []):
                slug = str(it.get("eventSlug") or "")
                if not slug.startswith("btc-updown-15m"):
                    continue
                f = (it.get("transactionHash"), it.get("asset"),
                     it.get("size"), it.get("price"), it.get("timestamp"))
                if f in seen:
                    continue
                seen.add(f)
                per[slug].append(it)
    for k in per:
        per[k].sort(key=lambda r: r.get("timestamp") or 0)
    return per


def rows():
    win = settlement()
    out = []
    for slug, fs in fills().items():
        tail = slug.rsplit("-", 1)[-1]
        try:
            end = float(tail) + WINDOW_S
        except ValueError:
            continue
        w = win.get(slug)
        q = {"up": D(0), "down": D(0)}
        for it in fs:
            side = str(it.get("outcome") or "").strip().lower()
            if side not in ("up", "down"):
                continue
            try:
                px = D(str(it.get("price")))
                sz = D(str(it.get("size")))
            except Exception:
                continue
            if not (D(0) < px < D(1)) or sz <= 0:
                continue
            ts = float(it.get("timestamp") or 0)
            resid = q["up"] - q["down"]
            held = "up" if resid > 0 else ("down" if resid < 0 else None)
            out.append({
                "slug": slug, "t": ts, "s_left": end - ts,
                "side": side, "px": px, "size": sz,
                "favored": px > D("0.5"),          # own-side price > 0.5
                "same_as_residual": (held is not None and side == held),
                "reduces_residual": (held is not None and side != held),
                "winner": w,
                "won": (None if w is None else (side == w)),
            })
            q[side] += sz
    return out


def fmt(rs, label, width=26):
    if not rs:
        return "%-*s  n=0" % (width, label)
    n = len(rs)
    mk = len({r["slug"] for r in rs})
    sh = sum(r["size"] for r in rs)
    usd = sum(r["size"] * r["px"] for r in rs)
    scored = [r for r in rs if r["won"] is not None]
    wr = (sum(1 for r in scored if r["won"]) / len(scored)) if scored else None
    ap = usd / sh if sh else D(0)
    # raw settlement expectancy per SHARE = payout(1 if won else 0) - price
    exp_sh = (sum((D(1) if r["won"] else D(0)) - r["px"]
                  for r in scored) / len(scored)) if scored else None
    # per DOLLAR staked
    stake = sum(r["size"] * r["px"] for r in scored)
    ret = sum(r["size"] * (D(1) if r["won"] else D(0)) for r in scored)
    exp_d = ((ret - stake) / stake) if stake else None
    return ("%-*s  n=%-5d mkts=%-4d shares=%-9s $%-10s avgpx=%-6s "
            "win=%-6s E/sh=%-8s E/$=%-7s"
            % (width, label, n, mk, round(sh, 1), round(usd, 1),
               round(ap, 4),
               "n/a" if wr is None else "%.1f%%" % (100 * wr),
               "n/a" if exp_sh is None else str(round(exp_sh, 4)),
               "n/a" if exp_d is None else "%+.3f" % exp_d))


def counterfactual(rs):
    """One q10 taker clip on the favored side, one per market per bucket."""
    used = set()
    pnl = D(0)
    fees = D(0)
    cost = D(0)
    taken = skipped = 0
    for r in sorted(rs, key=lambda x: x["t"]):
        if not r["favored"] or r["won"] is None:
            continue
        key = r["slug"]
        if key in used:
            continue
        px = r["px"] + TICK                    # taker, not a touch fill
        if px >= D(1):
            skipped += 1
            continue
        fee = THETA_TAKER * px * (D(1) - px) * CLIP
        payout = CLIP if r["won"] else D(0)
        pnl += payout - px * CLIP - fee
        cost += px * CLIP
        fees += fee
        used.add(key)
        taken += 1
    return {"clips": taken, "skipped_unexecutable": skipped,
            "cost": cost, "fees": fees, "net": pnl,
            "per_clip": (pnl / taken) if taken else None,
            "return_on_cost": (pnl / cost) if cost else None}


if __name__ == "__main__":
    R = rows()
    scored = [r for r in R if r["won"] is not None]
    print("L5 BTC-15m BUY fills: %d, markets %d | with settlement: %d, "
          "markets %d" % (len(R), len({r["slug"] for r in R}),
                          len(scored), len({r["slug"] for r in scored})))
    print()
    for lo, hi, name in BUCKETS:
        b = [r for r in R if hi <= r["s_left"] < lo]
        print("== %s to settlement ==" % name)
        print("  " + fmt(b, "all buys"))
        print("  " + fmt([r for r in b if r["favored"]], "favored outcome"))
        print("  " + fmt([r for r in b if not r["favored"]], "underdog outcome"))
        print("  " + fmt([r for r in b if r["same_as_residual"]], "same side as residual"))
        print("  " + fmt([r for r in b if r["reduces_residual"]], "reduces residual"))
        print()
    print("== FAVORED-side buys by own-side price, LATE (<120s) ==")
    late = [r for r in R if r["s_left"] < 120 and r["favored"]]
    for nm, lo, hi in PX_BUCKETS:
        print("  " + fmt([r for r in late if lo <= r["px"] < hi], nm))
    print()
    print("== earlier in the window, for comparison ==")
    for lo, hi, nm in ((900, 600, "900-600s"), (600, 300, "600-300s"),
                       (300, 120, "300-120s")):
        b = [r for r in R if hi <= r["s_left"] < lo]
        print("  " + fmt(b, nm + " all"))
        print("  " + fmt([r for r in b if r["favored"]], nm + " favored"))
    print()
    print("== buying INTENSITY by time bucket (per-second rate) ==")
    for lo, hi, nm in [(900, 600, "900-600s"), (600, 300, "600-300s"),
                       (300, 120, "300-120s")] + BUCKETS:
        b = [r for r in R if hi <= r["s_left"] < lo]
        span = lo - hi
        nm2 = nm if isinstance(nm, str) else nm
        print("  %-10s fills/s=%-7.3f shares/s=%-9.1f favored share=%s"
              % (nm2, len(b) / span, float(sum(r["size"] for r in b)) / span,
                 "n/a" if not b else "%.1f%%"
                 % (100 * sum(1 for r in b if r["favored"]) / len(b))))
    print()
    print("== COUNTERFACTUAL: +1 q10 TAKER clip on the favored side ==")
    print("   taker price = L5's observed fill + 0.01 tick (no touch fill)")
    print("   fees = 0.0695 * px * (1-px) per share; one clip per market per bucket")
    for lo, hi, name in BUCKETS:
        b = [r for r in R if hi <= r["s_left"] < lo]
        c = counterfactual(b)
        print("  %-9s clips=%-4d cost=$%-9s fees=$%-7s net=$%-10s "
              "per_clip=%-9s roc=%s"
              % (name, c["clips"], round(c["cost"], 2), round(c["fees"], 2),
                 round(c["net"], 2),
                 "n/a" if c["per_clip"] is None else str(round(c["per_clip"], 4)),
                 "n/a" if c["return_on_cost"] is None
                 else "%+.2f%%" % (100 * c["return_on_cost"])))
    print()
    allc = counterfactual([r for r in R if r["s_left"] < 120])
    print("  ALL <120s combined: clips=%d cost=$%s fees=$%s net=$%s "
          "per_clip=%s roc=%s"
          % (allc["clips"], round(allc["cost"], 2), round(allc["fees"], 2),
             round(allc["net"], 2),
             None if allc["per_clip"] is None else round(allc["per_clip"], 4),
             None if allc["return_on_cost"] is None
             else "%+.2f%%" % (100 * allc["return_on_cost"])))
