"""LADDER V1 -- infer the latent resting order set, then replay it.

The unit of analysis changes: not "fill -> predict next side" but
"latent two-sided resting set -> market evolution -> observed fills".

ORDER OWNERSHIP IS UNOBSERVED. Nothing here claims L5's actual orders. It
reports the MINIMUM resting set each burst implies, and a candidate
placement policy whose replayed fill stream is compared to his.

THE CIRCULARITY, AND HOW IT IS AVOIDED. Inferring a ladder from L5's
fills and then "replaying" it against those same fills would fill by
construction. So the replay uses execution evidence INDEPENDENT of L5:

    a resting bid at price p on outcome X is deemed filled only when the
    observed best bid for X falls STRICTLY BELOW p

i.e. the book walked through the level, so anything resting there was
consumed. No touch=fill (a bid merely equal to p is not a fill), no
invented cancellations, and L5's own fills are never used as execution
evidence for the candidate.

POWER: 27 markets. The independent unit is the market. Grouped
leave-one-market-out validation; no out-of-sample claim is made.
"""
from __future__ import annotations

import bisect
import collections
import hashlib
import json
import statistics
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "scripts")
import l5_candidate_v2 as V  # noqa: E402  (book + fill loaders, verified)

BURST_GAP_S = 5.0
#: Ladder V1 specification -- FROZEN before any evaluation below.
SPEC = {
    "name": "LADDER_V1_SYMMETRIC_TWO_SIDED_AT_TOUCH",
    "structure": "resting BID on BOTH outcomes simultaneously",
    "levels": 1,
    "level_offsets_ticks": [0],
    "placement": "at the prevailing best bid of each outcome",
    "sizing": "fixed shares, the observed per-leg median",
    "replenish": "re-rest at the new best bid after a fill",
    "residual_control": ("suppress the side whose inventory already "
                         "exceeds the other by RESIDUAL_CAP shares"),
    "residual_cap_shares": 80,
    "fill_rule": ("resting bid at p is filled only when the observed best "
                  "bid falls STRICTLY below p; no touch=fill; L5's own "
                  "fills are never execution evidence"),
    "tte_shutdown": "stop placing in the final SHUTDOWN_S seconds",
    "shutdown_s": 60,
    "validation": "grouped leave-one-market-out; no OOS claim",
}
FINGERPRINT = hashlib.sha256(
    json.dumps(SPEC, sort_keys=True).encode()).hexdigest()


def bursts(legs) -> List[List[dict]]:
    out, cur = [], ([legs[0]] if legs else [])
    for a, b in zip(legs, legs[1:]):
        if b["t"] - a["t"] <= BURST_GAP_S:
            cur.append(b)
        else:
            out.append(cur)
            cur = [b]
    if cur:
        out.append(cur)
    return out


def classify(px, bid, ask):
    if bid is not None and px <= bid + 1e-9:
        return "RESTING_BID_CONSISTENT"
    if ask is not None and px >= ask - 1e-9:
        return "MINT_OR_AT_ABOVE_ASK"
    return "INSIDE_SPREAD"


def observed_process(per, ser):
    """Item 1: the observed fill process, and the MINIMUM implied ladder."""
    rows, implied = [], []
    for slug, tr in per.items():
        legs = V.legs_for(tr)
        inv = collections.defaultdict(float)
        for bz in bursts(legs):
            first = bz[0]
            bk0 = {oc: V.before(ser.get((slug, oc), []), first["t"])
                   for oc in {x["oc"] for x in bz}}
            cls = collections.Counter()
            for x in bz:
                b = V.before(ser.get((slug, x["oc"]), []), x["t"])
                cls[classify(x["px"], b[1] if b else None,
                             b[2] if b else None)] += 1
                inv[x["oc"]] += x["q"]
            ocs = sorted({x["oc"] for x in bz})
            # the MINIMUM resting set this burst implies: one order per
            # distinct (outcome, price) actually filled
            lad = sorted({(x["oc"], round(x["px"], 4)) for x in bz})
            implied.append({"slug": slug, "t": first["t"],
                            "orders": len(lad),
                            "outcomes": len(ocs),
                            "levels_per_outcome": {
                                oc: len({round(x["px"], 4) for x in bz
                                         if x["oc"] == oc}) for oc in ocs}})
            q0 = inv[ocs[0]]
            q1 = inv[ocs[1]] if len(ocs) > 1 else 0.0
            rows.append({
                "slug": slug, "t": first["t"], "fills": len(bz),
                "both_outcomes": len(ocs) > 1,
                "gap_s": round(bz[-1]["t"] - first["t"], 2),
                "qty": sum(x["q"] for x in bz),
                "px_min": min(x["px"] for x in bz),
                "px_max": max(x["px"] for x in bz),
                "bid_before": {oc: (bk0[oc][1] if bk0.get(oc) else None)
                               for oc in ocs},
                "classes": dict(cls),
                "matched": min(q0, q1), "residual": abs(q0 - q1)})
    return rows, implied


def replay(slug, ser, outcomes, size, residual_cap, shutdown_s, t_end):
    """Item 3: place the candidate ladder and fill it from BOOK EVOLUTION
    only. Never consults L5's fills."""
    srs = {oc: ser.get((slug, oc), []) for oc in outcomes}
    times = sorted({r[0] for oc in outcomes for r in srs[oc]})
    if not times:
        return []
    inv = collections.defaultdict(float)
    resting: Dict[str, Optional[float]] = {oc: None for oc in outcomes}
    fills = []
    for t in times:
        if t_end is not None and t > t_end - shutdown_s:
            break
        state = {}
        for oc in outcomes:
            b = V.before(srs[oc], t + 1e-9)
            state[oc] = b
        # 1) FILL CHECK on what is already resting: the book must have
        #    walked strictly through our price.
        for oc in outcomes:
            p = resting[oc]
            b = state[oc]
            if p is None or not b or b[1] is None:
                continue
            if b[1] < p - 1e-9:
                inv[oc] += size
                fills.append({"t": t, "oc": oc, "px": p, "q": size})
                resting[oc] = None
        # 2) PLACE / REPLENISH, with residual suppression
        for oc in outcomes:
            if resting[oc] is not None:
                continue
            other = next((o for o in outcomes if o != oc), None)
            if other is not None and inv[oc] - inv[other] >= residual_cap:
                continue                      # suppress the heavy side
            b = state[oc]
            if b and b[1] is not None:
                resting[oc] = b[1]
    return fills


def stats(fills, key_t="t"):
    if not fills:
        return {"n": 0}
    px = [f["px"] for f in fills]
    gaps = [b[key_t] - a[key_t] for a, b in zip(fills, fills[1:])]
    return {"n": len(fills),
            "px_p10": round(sorted(px)[int(.1 * len(px))], 4),
            "px_median": round(statistics.median(px), 4),
            "px_p90": round(sorted(px)[int(.9 * len(px))], 4),
            "gap_median_s": (round(statistics.median(gaps), 2)
                             if gaps else None),
            "qty_total": round(sum(f["q"] for f in fills), 2)}


def main() -> int:
    ser = V.load_books()
    slugs = {k[0] for k in ser}
    per = V.load_fills(slugs)
    rows, implied = observed_process(per, ser)

    legqty = [x["q"] for slug, tr in per.items()
              for x in V.legs_for(tr)]
    size = round(statistics.median(legqty), 2)

    # observed aggregates
    obs_fills = []
    for slug, tr in per.items():
        for x in V.legs_for(tr):
            obs_fills.append({"t": x["t"], "oc": x["oc"], "px": x["px"],
                              "q": x["q"], "slug": slug})
    obs_fills.sort(key=lambda f: f["t"])
    cls_tot = collections.Counter()
    for r in rows:
        cls_tot.update(r["classes"])
    nc = sum(cls_tot.values())

    # grouped market-level replay
    per_market = {}
    for slug in sorted(slugs):
        ocs = sorted({oc for (s, oc) in ser if s == slug})
        if len(ocs) < 2:
            continue
        tend = max(r[0] for oc in ocs for r in ser[(slug, oc)])
        rf = replay(slug, ser, ocs, size, SPEC["residual_cap_shares"],
                    SPEC["shutdown_s"], tend)
        of = [f for f in obs_fills if f["slug"] == slug]
        inv = collections.defaultdict(float)
        for f in rf:
            inv[f["oc"]] += f["q"]
        # pad FIRST, then sort -- appending after the sort put the
        # zeros at the tail and made matched/residual structurally 0.
        q = sorted(list(inv.values()) + [0.0, 0.0])
        per_market[slug] = {
            "candidate": stats(rf), "observed": stats(of),
            "candidate_matched": round(min(q[-1], q[-2]), 2),
            "candidate_residual": round(abs(q[-1] - q[-2]), 2),
            "candidate_both_sides": len({f["oc"] for f in rf}) > 1}

    traded = {k: v for k, v in per_market.items()
              if v["observed"]["n"] > 0}
    cand_n = [v["candidate"]["n"] for v in traded.values()]
    obs_n = [v["observed"]["n"] for v in traded.values()]
    res = {
        "label": "L5_LADDER_V1",
        "spec": SPEC, "spec_fingerprint_sha256": FINGERPRINT,
        "frozen_before_evaluation": True,
        "book_source": {"zenodo_record": 22084977, "sha256": V.SHA,
                        "licence": "CC-BY-4.0"},
        "POWER": ("27 markets; the independent unit is the MARKET. Grouped "
                  "leave-one-market-out only -- NO out-of-sample claim."),
        "1_observed_fill_process": {
            "bursts": len(rows),
            "bursts_both_outcomes": sum(1 for r in rows
                                        if r["both_outcomes"]),
            "bursts_both_outcomes_pct": round(
                100.0 * sum(1 for r in rows if r["both_outcomes"])
                / len(rows), 1),
            "fill_classification_pct": {
                k: round(100.0 * v / nc, 1) for k, v in cls_tot.items()},
            "leg_qty_median": size,
            "leg_price": {"p10": round(sorted(
                [f["px"] for f in obs_fills])[
                    int(.1 * len(obs_fills))], 3),
                "median": round(statistics.median(
                    [f["px"] for f in obs_fills]), 3),
                "p90": round(sorted([f["px"] for f in obs_fills])[
                    int(.9 * len(obs_fills))], 3)},
        },
        "1b_MINIMUM_implied_ladder": {
            "orders_per_burst": {
                "median": statistics.median([i["orders"] for i in implied]),
                "p90": sorted([i["orders"] for i in implied])[
                    int(.9 * len(implied))],
                "max": max(i["orders"] for i in implied)},
            "levels_per_outcome_distribution": dict(collections.Counter(
                n for i in implied for n in
                i["levels_per_outcome"].values()).most_common()),
            "caveat": ("a LOWER BOUND. Orders that never filled are "
                       "unobservable, so the true resting set is at least "
                       "this and possibly much larger."),
        },
        "3_conservative_replay": {
            "markets_with_book": len(per_market),
            "markets_L5_TRADED": sum(1 for v in per_market.values()
                                     if v["observed"]["n"] > 0),
            "markets_L5_NEVER_TRADED": sum(1 for v in per_market.values()
                                           if v["observed"]["n"] == 0),
            "comparison_scope": ("the headline ratio uses ONLY the markets "
                                 "where L5 actually traded; comparing 64 "
                                 "book markets against 27 traded ones "
                                 "mixes populations"),
            "candidate_fills_total": sum(cand_n),
            "observed_fills_total": sum(obs_n),
            "ratio_candidate_to_observed": (
                round(sum(cand_n) / sum(obs_n), 3) if sum(obs_n) else None),
            "markets_where_candidate_filled_both_sides": sum(
                1 for v in per_market.values() if v["candidate_both_sides"]),
            "candidate_fills_per_market": {
                "median": statistics.median(cand_n) if cand_n else None,
                "max": max(cand_n) if cand_n else None},
            "fill_rule": SPEC["fill_rule"],
        },
        "per_market": per_market,
        "accounting": ("historical replay on third-party public data; no "
                       "order sent; not realized profit for our account."),
    }
    with open("analysis/l5/LADDER_V1.json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, default=str)
    print(json.dumps({k: v for k, v in res.items() if k != "per_market"},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
