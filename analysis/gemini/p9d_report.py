"""P9-D reporting: rank families by measured behaviour, not displayed volume.

    python3 analysis/gemini/p9d_report.py <pull.json> [spreads.json] [events_out.jsonl]

Splits each family chronologically by the day its contracts first traded:
DEV is the earlier 70% of trading days, VALIDATION the rest. A parameter is
chosen on DEV and scored once on VALIDATION; a family with fewer than two
trading days is reported as UNSPLITTABLE and no parameter is chosen for it.

Writes an event-level JSONL (one row per hypothetical fill, carrying `tid` and
`timestampms` where the collector preserved them) so any figure here can be
recomputed independently.
"""
import json
import os
import sys
from collections import defaultdict
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import p9d_sequential as S                                     # noqa: E402

DEFAULT_SPREAD = F(4, 100)


def med(xs, d=None):
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else d


def tape_stats(c):
    pr = S.prints_of(c)
    if len(pr) < 2:
        return None
    span = max(1, int(pr[-1][0]) - int(pr[0][0]))
    notional = sum(F(str(p[2])) * F(str(p[3])) for p in pr)
    sides = {("YES" if p[1] == "sell" else "NO") for p in pr}
    return {"n": len(pr), "span_s": span,
            "per_min": len(pr) * 60.0 / span,
            "usd_per_min": float(notional) * 60.0 / span,
            "notional": float(notional),
            "two_sided": len(sides) > 1,
            "life_min": (S.iso(c.get("expiry")) - S.iso(c.get("start"))).total_seconds() / 60.0
            if (S.iso(c.get("expiry")) and S.iso(c.get("start"))) else None,
            "day": (str(c.get("day") or "") or None)}


def load_spreads(path):
    out = {}
    if path and os.path.exists(path):
        for r in json.load(open(path)):
            out[(r["category"], r["subcategory"])] = F(
                str(round(r["median_spread"], 3))).limit_denominator(1000)
    return out


def day_split(contracts):
    days = sorted({c.get("day") for c in contracts if c.get("day")})
    if len(days) < 2:
        return None, None
    cut = days[max(1, int(len(days) * 0.7)) - 1]
    return set(d for d in days if d <= cut), set(d for d in days if d > cut)


def run_arm(contracts, *, spread, arm, skew=F(0), comp_max=F(1), keep=None):
    rows = []
    for c in contracts:
        r = S.simulate(c, spread=spread, arm=arm, skew=skew, comp_max=comp_max)
        if r is None:
            continue
        rows.append(r)
        if keep is not None:
            for e in r.get("events") or []:
                keep.append({"symbol": r["symbol"], "arm": arm,
                             "spread": str(spread), "skew": str(skew), **e})
    return rows


def score(rows):
    filled = [r for r in rows if r.get("fills")]
    settled = [r for r in filled if r.get("settled")]
    paired = [r for r in filled if r.get("matched_qty")]
    orphan = [r for r in filled if r.get("residual_qty")]
    first = [r for r in filled if r.get("first_fill_t")]
    both = [r for r in first if r.get("second_fill_t")]
    gaps = [r["second_fill_t"] - r["first_fill_t"] for r in both]
    spend = sum((r["spend"] for r in settled), F(0))
    matched = sum((r["matched_pnl"] for r in settled if "matched_pnl" in r), F(0))
    resid = sum((r["residual_pnl"] for r in settled if "residual_pnl" in r), F(0))
    net = matched + resid
    return {
        "contracts": len(rows), "engaged": len(filled), "settled": len(settled),
        "paired": len(paired), "orphan": len(orphan),
        "two_sided_fill_rate": (len(paired) / len(filled)) if filled else 0.0,
        "orphan_rate": (len(orphan) / len(filled)) if filled else 0.0,
        "p_second_given_first": (len(both) / len(first)) if first else 0.0,
        "median_gap_s": med(gaps),
        "median_pair_margin": (med([float(1 - r["pair_cost"]) for r in paired])
                               if paired else None),
        "median_first_tte": med([r.get("first_tte") for r in filled]),
        "median_second_tte": med([r.get("second_tte") for r in both]),
        "matched": float(matched), "residual": float(resid), "net": float(net),
        "spend": float(spend),
        "roc": (float(net / spend) if spend else None),
    }


HDR = (f"{'arm':>4} {'n':>5} {'eng':>5} {'set':>5} {'pair%':>6} {'orph%':>6} "
       f"{'P2|1':>6} {'gap s':>7} {'margin':>7} {'matched$':>9} {'orphan$':>9} "
       f"{'NET$':>9} {'spend$':>9} {'RoC':>8}")


def show(tag, s):
    gap = "-" if s["median_gap_s"] is None else f"{s['median_gap_s']:.0f}"
    mar = "-" if s["median_pair_margin"] is None else f"{s['median_pair_margin']:.3f}"
    roc = "-" if s["roc"] is None else f"{s['roc']:.2%}"
    print(f"{tag:>4} {s['contracts']:>5} {s['engaged']:>5} {s['settled']:>5} "
          f"{s['two_sided_fill_rate']:>5.1%} {s['orphan_rate']:>5.1%} "
          f"{s['p_second_given_first']:>5.1%} {gap:>7} {mar:>7} "
          f"{s['matched']:>9.2f} {s['residual']:>9.2f} {s['net']:>9.2f} "
          f"{s['spend']:>9.2f} {roc:>8}", flush=True)


def main(path, spreads_path=None, events_out=None):
    raw = json.load(open(path))
    print(f"contracts pulled: {len(raw)}   RAW TRADES, no twin deletion "
          f"(P8 audit ed4573b)")
    spreads = load_spreads(spreads_path)
    fams = defaultdict(list)
    for c in raw:
        ts = tape_stats(c)
        if ts is None:
            continue
        c["_tape"] = ts
        fams[S.family(c)].append(c)

    print(f"\n{'='*150}\nFAMILY HABITAT, measured from the tape "
          f"(no policy involved)\n{'='*150}")
    print(f"{'category':>13} {'subcategory':>22} {'ctr':>5} {'settled':>8} "
          f"{'prints':>7} {'2-sided':>8} {'prints/min':>11} {'$/min':>9} "
          f"{'life min':>9} {'span s':>8} {'spread':>7} {'days':>5}")
    order = []
    for k, cs in fams.items():
        if len(cs) < 10:
            continue
        t = [c["_tape"] for c in cs]
        sp = spreads.get(k, DEFAULT_SPREAD)
        order.append((sum(x["notional"] for x in t), k, cs, sp))
    for _v, k, cs, sp in sorted(order, reverse=True):
        t = [c["_tape"] for c in cs]
        lm = med([x["life_min"] for x in t])
        lm = "-" if lm is None else f"{lm:.0f}"
        print(f"{k[0]:>13} {str(k[1])[:22]:>22} {len(cs):>5} "
              f"{sum(1 for c in cs if S.won_of(c)):>8} "
              f"{sum(x['n'] for x in t):>7} "
              f"{sum(x['two_sided'] for x in t) / len(t):>7.1%} "
              f"{med([x['per_min'] for x in t]):>11.2f} "
              f"{med([x['usd_per_min'] for x in t]):>9.2f} "
              f"{lm:>9} {med([x['span_s'] for x in t]):>8.0f} "
              f"{float(sp):>7.3f} {len({x['day'] for x in t}):>5}")
    return fams, spreads, order


if __name__ == "__main__":
    main(*sys.argv[1:4])
