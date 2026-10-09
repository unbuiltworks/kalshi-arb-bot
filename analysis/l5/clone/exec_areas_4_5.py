"""AREA 4 (how the matched volume arrives) and AREA 5 (late aggression).

Both from the 760,817-fill corpus alone, chronologically, no book join and
no lookahead: each fill is classified by what it did to the inventory that
existed BEFORE it, and by how long the opposite side then took to arrive.

AREA 4. "He has a 0.904 matched fraction" does not say how. Two mechanisms
are distinguishable without seeing his orders:
  * MATCHING fills reduce |residual| -- the opposite side arriving;
  * EXTENDING fills increase it.
The time from an extending fill to the next matching fill in the same
market is the waiting time his inventory actually experiences, and its
distribution says whether matching is something he waits briefly for
(flow comes to him) or works at over the window.

AREA 5. The earlier taker-removal test was invalid: deleting a subset of
fills and re-pricing the remainder changes the inventory path, so the
counterfactual is not the same strategy minus takers. This instead
measures what late fills DO, with volume- and exposure-matched controls:
within each time-to-expiry decile, and within each inventory-imbalance
bucket, what fraction of fills are matching rather than extending, and how
large are they. If late fills are inventory management they are
disproportionately MATCHING at high imbalance; if they are directional they
are disproportionately EXTENDING; if opportunistic they track price, not
inventory.
"""
from __future__ import annotations
import collections, datetime as dt, json, sys
import numpy as np

sys.path.insert(0, "/home/user/kalshi-arb-bot")
from analysis.l5.clone.extract import load_fills, window_of

BREAK = int(dt.datetime(2026, 6, 23, tzinfo=dt.timezone.utc).timestamp())


def q(xs, p):
    return round(float(np.percentile(xs, p)), 3) if len(xs) else None


def main() -> int:
    fills = load_fills()
    by_mkt = collections.defaultdict(list)
    for r in fills:
        by_mkt[r.get("conditionId")].append(r)

    # per-fill rows, early regime, with a resolvable window clock
    rows = []
    for cid, rs in by_mkt.items():
        rs.sort(key=lambda r: int(r["timestamp"]))
        if int(rs[0]["timestamp"]) >= BREAK:
            continue
        dur, ws = window_of(rs[0].get("slug") or rs[0].get("eventSlug"))
        if not dur or not ws:
            continue
        qty = [0.0, 0.0]
        prev_t = None
        for i, r in enumerate(rs):
            oi = int(r["outcomeIndex"])
            sz = float(r.get("size") or 0)
            px = float(r["price"])
            ts = int(r["timestamp"])
            resid_before = qty[0] - qty[1]
            same_before = qty[oi]
            opp_before = qty[1 - oi]
            # MATCHING iff this fill is on the side that was SHORT
            matching = (opp_before > same_before)
            imb_before = (abs(resid_before) / (qty[0] + qty[1])
                          if (qty[0] + qty[1]) > 0 else 0.0)
            tte = max(0.0, ws + dur - ts)
            rows.append({
                "cid": cid, "i": i, "ts": ts, "oi": oi, "px": px, "sz": sz,
                "matching": bool(matching),
                "abs_resid_before": abs(resid_before),
                "imb_before": imb_before,
                "tte": tte, "tte_frac": min(1.0, max(0.0, tte / dur)),
                "gap_prev": (None if prev_t is None else ts - prev_t),
            })
            qty[oi] += sz
            prev_t = ts

    n = len(rows)
    out = {"regime": "early (pre 2026-06-23)", "fills": n,
           "markets": len(set(r["cid"] for r in rows))}

    # ---- AREA 4: matching share and waiting time --------------------
    match = [r for r in rows if r["matching"]]
    out["AREA_4_matching"] = {
        "matching_fill_share": round(len(match) / n, 4),
        "note": ("a MATCHING fill is one on the side that was behind, so it "
                 "reduces |residual|; the complement arriving is what makes "
                 "the matched fraction high"),
    }
    # waiting time: from each extending fill to the next matching fill
    waits = []
    by_cid = collections.defaultdict(list)
    for r in rows:
        by_cid[r["cid"]].append(r)
    for cid, rs in by_cid.items():
        pending = None
        for r in rs:
            if not r["matching"]:
                if pending is None:
                    pending = r["ts"]
            else:
                if pending is not None:
                    waits.append(r["ts"] - pending)
                    pending = None
    out["AREA_4_matching"]["seconds_from_extend_to_next_match"] = {
        "n": len(waits), "p25": q(waits, 25), "median": q(waits, 50),
        "p75": q(waits, 75), "p90": q(waits, 90)}
    gaps = [r["gap_prev"] for r in rows if r["gap_prev"] is not None]
    out["AREA_4_matching"]["seconds_between_consecutive_fills"] = {
        "n": len(gaps), "p25": q(gaps, 25), "median": q(gaps, 50),
        "p75": q(gaps, 75), "p90": q(gaps, 90)}

    # ---- AREA 5: late aggression, controlled ------------------------
    # decile by time-to-expiry fraction, and imbalance bucket
    def bucket(v):
        if v < 0.05:
            return "0_balanced"
        if v < 0.2:
            return "1_mild"
        if v < 0.5:
            return "2_moderate"
        return "3_heavy"

    dec = {}
    for r in rows:
        d = min(9, int((1.0 - r["tte_frac"]) * 10))   # 0 = early, 9 = latest
        dec.setdefault(d, []).append(r)
    out["AREA_5_late_aggression"] = {
        "controls": ("within each time-to-expiry decile AND imbalance "
                     "bucket, so a late-vs-early difference is not a "
                     "difference in how imbalanced he happened to be"),
        "by_tte_decile": {},
    }
    for d in sorted(dec):
        rs = dec[d]
        sizes = [r["sz"] for r in rs]
        ent = {"n": len(rs),
               "matching_share": round(np.mean([r["matching"] for r in rs]), 4),
               "median_size": q(sizes, 50),
               "mean_abs_resid_before": round(float(np.mean(
                   [r["abs_resid_before"] for r in rs])), 2),
               "by_imbalance": {}}
        bb = {}
        for r in rs:
            bb.setdefault(bucket(r["imb_before"]), []).append(r)
        for k in sorted(bb):
            v = bb[k]
            ent["by_imbalance"][k] = {
                "n": len(v),
                "matching_share": round(np.mean([x["matching"] for x in v]), 4),
                "median_size": q([x["sz"] for x in v], 50)}
        out["AREA_5_late_aggression"]["by_tte_decile"][str(d)] = ent

    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
