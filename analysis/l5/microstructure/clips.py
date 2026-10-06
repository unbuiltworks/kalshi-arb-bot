"""What resting CLIPS best explain the observed partial fills?

THE QUESTION HAS CHANGED. Not "what size did L5 choose for this fill" -- that
question is unanswerable, because an observed fill quantity is the size of the
counterparty's aggression against his resting interest, not the size he
exposed (LIMITS.md ss2). The answerable question is: what resting order
quantities and price levels are most consistent with the SEQUENCE of partial
fills we observe?

A CLIP is a maximal run of consecutive L5 fills on the same (window, outcome,
price), in deterministic (t, log_index) order, broken by any of:

  B1 PRICE CHANGE      the next fill on that outcome is at a different price
  B2 TIME GAP          more than `gap_s` seconds since the previous fill
  B3 REPLENISHMENT     observable size at that price INCREASED between the two
                       fills, which means something was added -- so a single
                       continuously-resting order cannot explain both fills

B3 IS THE ONE THAT MATTERS and it is the reason this is not just a groupby: a
run of fills at one price could be one clip being eaten, or several clips
posted in succession. Only the book can distinguish them, and only when the
increase is visible.

GAP IS A FREE PARAMETER, SO IT IS SWEPT, NOT CHOSEN. Clip totals are reported
for gap_s in {2, 5, 10, 30, inf} and the clustering verdict must survive the
sweep or it is not reported as a finding.

PLACEMENT-TIME BOUNDS. A first fill at T+7s is NOT evidence that an order was
submitted at T+7s. For every fill this module walks the archive backwards to
the earliest observation at which size existed CONTINUOUSLY at that price
through to the fill, and reports it relative to the window open -- a value that
can be NEGATIVE, because the archive covers roughly 60s before the nominal
open. That timestamp is an upper bound on when such an order could have been
placed. It is NOT proof the size was L5's.

NOTHING HERE IS ATTRIBUTED. Size at a price is depth at a price level. Absence
of size is not proof L5 had no order there.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book
from analysis.l5.microstructure.load import load

GAPS = (2, 5, 10, 30, None)          # None = no time break at all
TARGETS = (10, 20, 25, 40, 50, 60, 75, 80, 100)
EPS = 0.75                            # shares, for "near a target"


def size_at_price(row: Optional[dict], price: float) -> Optional[float]:
    if not row:
        return None
    tot = 0.0
    for p, s in zip(row.get("bid_prices") or [], row.get("bid_sizes") or []):
        if abs(float(p) - price) < 1e-9:
            tot += float(s)
    return tot


def earliest_continuous(bk: Book, price: float, until: float
                        ) -> Tuple[Optional[float], bool]:
    """Earliest archived observation with size at `price` running unbroken to
    `until`. Returns (timestamp, hit_start_of_data).

    Unbounded within the data we hold -- deliberately not capped at 300s like
    the earlier resting probe, because the whole point is to reach back before
    the market's nominal open where the archive allows.
    """
    import bisect
    if not bk.t:
        return None, False
    i = bisect.bisect_right(bk.t, until) - 1
    if i < 0:
        return None, False
    if (size_at_price(bk.rows[i], price) or 0) <= 0:
        return None, False
    last = bk.t[i]
    while i - 1 >= 0 and (size_at_price(bk.rows[i - 1], price) or 0) > 0:
        i -= 1
        last = bk.t[i]
    return last, i == 0


def build_clips(fills: List[dict], books: Dict[str, Book], *, gap_s,
                slug: str, clen: str, start: int) -> List[Dict[str, Any]]:
    by: Dict[str, List[dict]] = defaultdict(list)
    for f in fills:
        by[f["outcome"]].append(f)
    out: List[Dict[str, Any]] = []
    for oc, g in by.items():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
        tok = g[0]["token_id"]
        bk = books.get(tok)
        cur: List[dict] = []
        reason = "FIRST"
        for f in g:
            if not cur:
                cur = [f]
                continue
            prev = cur[-1]
            brk = None
            if abs(f["price"] - prev["price"]) > 1e-9:
                brk = "B1_PRICE_CHANGE"
            elif gap_s is not None and (f["t"] - prev["t"]) > gap_s:
                brk = "B2_TIME_GAP"
            else:
                # B3: did observable size at the price INCREASE between them?
                a = bk.at(float(prev["t"]), max_age_s=MAX_CARRY_S) if bk else None
                b = bk.at(float(f["t"] - 1), max_age_s=MAX_CARRY_S) if bk else None
                sa, sb = size_at_price(a, prev["price"]), size_at_price(b, prev["price"])
                if sa is not None and sb is not None and sb > sa + 1e-9:
                    brk = "B3_REPLENISHED"
            if brk:
                out.append(_close(cur, oc, slug, clen, start, bk, reason, brk))
                reason = brk
                cur = [f]
            else:
                cur.append(f)
        if cur:
            out.append(_close(cur, oc, slug, clen, start, bk, reason,
                              "END_OF_WINDOW"))
    out.sort(key=lambda c: (c["t_first"], c["outcome"]))
    return out


def _close(run: List[dict], oc: str, slug: str, clen: str, start: int,
           bk: Optional[Book], open_reason: str, close_reason: str
           ) -> Dict[str, Any]:
    px = run[0]["price"]
    tot = sum(r["quantity"] for r in run)
    t0, t1 = run[0]["t"], run[-1]["t"]
    first_seen, at_data_start = (earliest_continuous(bk, px, float(t0))
                                 if bk else (None, False))
    pre = bk.at(float(t0 - 1), max_age_s=MAX_CARRY_S) if bk else None
    mech = Counter(r["mechanism"] for r in run)
    return {
        "market_slug": slug, "contract_length": clen, "outcome": oc,
        "price": px,
        "n_fills": len(run),
        "clip_total_shares": round(tot, 6),
        "fill_quantities": [round(r["quantity"], 6) for r in run],
        "t_first": t0, "t_last": t1,
        "secs_after_open_first": t0 - start,
        "secs_after_open_last": t1 - start,
        "duration_s": t1 - t0,
        "tte_at_first_s": run[0]["tte_s"],
        "blocks": sorted({r.get("block") for r in run}),
        "spans_multiple_blocks": len({r.get("block") for r in run}) > 1,
        "opened_because": open_reason, "closed_because": close_reason,
        "mechanisms": dict(mech),
        "mechanism_pure": len(mech) == 1,
        "dominant_mechanism": mech.most_common(1)[0][0],
        # observable depth context
        "visible_size_at_price_before": size_at_price(pre, px),
        "clip_total_vs_visible_before": (
            None if not size_at_price(pre, px)
            else round(tot / size_at_price(pre, px), 4)),
        # PLACEMENT-TIME BOUND -- an upper bound on when such an order could
        # have been placed, never proof the size was L5's
        "price_first_continuously_present_s_after_open": (
            None if first_seen is None else round(first_seen - start, 1)),
        "continuity_reaches_start_of_archive": at_data_start,
        "apparent_presence_before_first_fill_s": (
            None if first_seen is None else round(t0 - first_seen, 1)),
        "ownership_caveat": ("depth at a price level; never attributed to L5. "
                             "Absence of size is not proof of no order."),
    }


def near(v: float, t: float, eps: float = EPS) -> bool:
    return abs(v - t) <= eps


def cluster_test(vals: Sequence[float], targets=TARGETS, eps: float = EPS
                 ) -> Dict[str, Any]:
    """Local-background test: is there excess mass AT a target?

    The comparison band is +/-[2*eps, 5*eps] around the same target, so the
    test asks whether the target is special RELATIVE TO ITS OWN
    NEIGHBOURHOOD rather than relative to the whole distribution, which would
    just rediscover that small clips are common.
    """
    v = sorted(float(x) for x in vals)
    n = len(v)
    out: Dict[str, Any] = {"n_clips": n, "eps_shares": eps, "targets": {}}
    for t in targets:
        at = sum(1 for x in v if near(x, t, eps))
        lo = sum(1 for x in v if 2 * eps < abs(x - t) <= 5 * eps)
        # background per unit width, scaled to the target band's width
        bg_width = 2 * (5 * eps - 2 * eps)
        at_width = 2 * eps
        exp = (lo / bg_width * at_width) if bg_width > 0 else 0.0
        out["targets"][str(t)] = {
            "at_target": at, "in_background_band": lo,
            "expected_from_background": round(exp, 2),
            "excess_ratio": (round(at / exp, 2) if exp > 0
                             else (None if at == 0 else "inf")),
            "share_of_all_clips": round(at / n, 4) if n else None,
        }
    out["max_clip_total"] = v[-1] if v else None
    out["clips_over_100"] = sum(1 for x in v if x > 100 + 1e-9)
    out["clips_at_exactly_100"] = sum(1 for x in v if abs(x - 100) < 1e-9)
    out["share_integer_totals"] = (
        round(sum(1 for x in v if abs(x - round(x)) < 1e-6) / n, 4)
        if n else None)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--contract-length", required=True)
    ap.add_argument("--window-s", type=int, required=True)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)

    ev = [json.loads(l) for l in open(a.events, encoding="utf-8")
          if l.strip()]
    by_slug: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        by_slug[r["market_slug"]].append(r)
    books, _ = load(a.parquet, contract_length=a.contract_length,
                    slugs=set(by_slug))

    rep: Dict[str, Any] = {
        "contract_length": a.contract_length,
        "question": ("what resting clips and levels are most consistent with "
                     "the observed partial-fill sequence?"),
        "clip_break_rules": {"B1": "price change on that outcome",
                             "B2": "time gap > gap_s",
                             "B3": "observable size at the price INCREASED "
                                   "between fills (replenishment)"},
        "gap_sweep": {},
        "no_pnl_optimized": True,
    }
    best: List[Dict[str, Any]] = []
    for gap in GAPS:
        clips: List[Dict[str, Any]] = []
        for slug, fl in sorted(by_slug.items()):
            clips.extend(build_clips(fl, books, gap_s=gap, slug=slug,
                                     clen=a.contract_length,
                                     start=int(slug.rsplit("-", 1)[1])))
        tot = [c["clip_total_shares"] for c in clips]
        key = "gap_%s" % ("inf" if gap is None else gap)
        rep["gap_sweep"][key] = {
            "gap_s": gap, "n_clips": len(clips),
            "fills_per_clip_p50": (sorted(c["n_fills"] for c in clips)
                                   [len(clips) // 2] if clips else None),
            "multi_fill_clip_share": (
                round(sum(1 for c in clips if c["n_fills"] > 1) / len(clips), 4)
                if clips else None),
            "close_reasons": dict(Counter(c["closed_because"] for c in clips)),
            "clip_total_quantiles": _q(tot),
            "clustering": cluster_test(tot),
        }
        if gap == 10:
            best = clips
    # the committed per-clip table uses gap_s = 10 (middle of the sweep)
    p = os.path.join(a.outdir, "CLIPS_%s.jsonl" % a.contract_length)
    with open(p, "w") as fh:
        for c in best:
            fh.write(json.dumps(c, default=str) + "\n")
    rep["per_clip_table"] = {"path": os.path.basename(p), "gap_s": 10,
                             "n": len(best),
                             "note": ("gap_s=10 is the middle of the swept "
                                      "range and is used only for the dumped "
                                      "table; every verdict is reported "
                                      "across the whole sweep")}
    # placement bounds, from the gap_s=10 clips
    pb = [c["price_first_continuously_present_s_after_open"] for c in best]
    rep["placement_time_bounds"] = {
        "what": ("earliest archived second at which size existed "
                 "CONTINUOUSLY at the clip's price through to its first fill, "
                 "relative to the market open. NEGATIVE means the price was "
                 "already quoted before the nominal open."),
        "upper_bound_on_placement_not_proof_of_ownership": True,
        "n_with_a_bound": sum(1 for x in pb if x is not None),
        "n_without": sum(1 for x in pb if x is None),
        "quantiles": _q([x for x in pb if x is not None]),
        "share_present_before_open": (
            round(sum(1 for x in pb if x is not None and x < 0)
                  / max(1, sum(1 for x in pb if x is not None)), 4)),
        "share_continuity_reaches_archive_start": (
            round(sum(1 for c in best
                      if c["continuity_reaches_start_of_archive"])
                  / max(1, len(best)), 4)),
        "apparent_presence_before_first_fill_s": _q(
            [c["apparent_presence_before_first_fill_s"] for c in best
             if c["apparent_presence_before_first_fill_s"] is not None]),
    }
    out = os.path.join(a.outdir, "CLIP_ANALYSIS_%s.json" % a.contract_length)
    with open(out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in rep.items()
                      if k not in ("gap_sweep",)}, indent=1, default=str))
    for k, v in rep["gap_sweep"].items():
        print("\n--", k, "n_clips", v["n_clips"],
              "multi-fill share", v["multi_fill_clip_share"])
        print("   totals:", json.dumps(v["clip_total_quantiles"]))
        print("   max", v["clustering"]["max_clip_total"],
              "| >100:", v["clustering"]["clips_over_100"],
              "| ==100:", v["clustering"]["clips_at_exactly_100"],
              "| integer share", v["clustering"]["share_integer_totals"])
        for t, d in v["clustering"]["targets"].items():
            if d["at_target"]:
                print("     target %-4s at=%-4d bg_exp=%-6s excess=%s"
                      % (t, d["at_target"], d["expected_from_background"],
                         d["excess_ratio"]))
    return 0


def _q(v: Sequence[Any]) -> Dict[str, Any]:
    c = sorted(float(x) for x in v if x is not None)
    if not c:
        return {"n": 0}
    def at(p):
        return round(c[int(p * (len(c) - 1))], 4)
    return {"n": len(c), "min": at(0), "p10": at(.1), "p25": at(.25),
            "p50": at(.5), "p75": at(.75), "p90": at(.9), "p99": at(.99),
            "max": at(1.0)}


if __name__ == "__main__":
    raise SystemExit(main())
