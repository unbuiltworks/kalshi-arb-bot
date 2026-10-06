"""Q1: historical L5 accounting with rebates/rewards set to exactly $0.

Frozen protocol: PREREGISTRATION_NOREBATE.md @ 3fb8fccb.

This measures L5's OWN historical cash. It is never our realized profit.
Rebates and rewards are $0.00 by construction.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import statistics as st
from collections import defaultdict
from typing import Any, Dict, List, Optional

from analysis.l5.microstructure.stats import cliffs_delta, qs

FEE_K = 0.07
PREREG = "PREREGISTRATION_NOREBATE.md @ 3fb8fccb"
FIRST_REBATE = dt.datetime(2026, 4, 21)
INCENTIVE_TYPES = ("MAKER_REBATE", "TAKER_REBATE", "REWARD")


def load(path: str) -> List[dict]:
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        p = d.get("payload")
        if p is None:
            continue
        for r in (p if isinstance(p, list) else [p]):
            if isinstance(r, dict) and r.get("type"):
                out.append(r)
    return out


def day(ts) -> str:
    return dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")


def era(ts) -> str:
    d = dt.datetime.utcfromtimestamp(ts)
    if d < FIRST_REBATE:
        return "E1_GENUINELY_PRE_REBATE"
    if d.month == 4:
        return "E2_EARLY_REBATE"
    return "E3_INCENTIVE_ERA"


def boot(vals: List[float], n=10000, seed=7) -> Dict[str, Any]:
    if len(vals) < 5:
        return {"n": len(vals), "CI": None}
    rng = random.Random(seed)
    tot = []
    k = len(vals)
    for _ in range(n):
        tot.append(sum(vals[rng.randrange(k)] for _ in range(k)))
    tot.sort()
    return {"n": k, "resamples": n,
            "CI95_total": [round(tot[int(0.025 * n)], 2),
                           round(tot[int(0.975 * n)], 2)],
            "share_of_resamples_positive": round(
                sum(1 for x in tot if x > 0) / n, 4)}


def drawdown(vals: List[float]) -> float:
    peak = cum = 0.0
    dd = 0.0
    for v in vals:
        cum += v
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return round(dd, 2)


def stats(evs: List[dict], label: str) -> Dict[str, Any]:
    if not evs:
        return {"era": label, "N": 0}
    nets = [e["net"] for e in evs]
    days = sorted({e["day"] for e in evs})
    tot = sum(nets)
    srt = sorted(nets)
    top = sorted(nets, reverse=True)
    return {
        "era": label,
        "N_events": len(evs),
        "observed_days": len(days),
        "first_day": days[0], "last_day": days[-1],
        "total_usd": round(tot, 2),
        "per_event_usd": round(tot / len(evs), 4),
        "per_OBSERVED_day_usd": round(tot / len(days), 2),
        "median_event_usd": round(st.median(nets), 4),
        "win_rate": round(sum(1 for x in nets if x > 0) / len(nets), 4),
        "bootstrap": boot(nets),
        "max_drawdown_usd": drawdown(nets),
        "concentration": {
            "top1_share_of_total": (round(top[0] / tot, 4) if tot else None),
            "top5_share_of_total": (round(sum(top[:5]) / tot, 4)
                                    if tot else None),
            "top10_share_of_total": (round(sum(top[:10]) / tot, 4)
                                     if tot else None),
            "net_excluding_top1_usd": round(tot - top[0], 2),
            "net_excluding_top5_usd": round(tot - sum(top[:5]), 2),
            # on a NEGATIVE total a share-of-total ratio is uninterpretable,
            # so the loss side is reported in absolute dollars instead.
            "worst1_event_usd": round(srt[0], 2),
            "worst5_sum_usd": round(sum(srt[:5]), 2),
            "worst10_sum_usd": round(sum(srt[:10]), 2),
            "net_excluding_worst1_usd": round(tot - srt[0], 2),
            "net_excluding_worst5_usd": round(tot - sum(srt[:5]), 2),
        },
        "components_usd": {
            k: round(sum(e["components"][k] for e in evs), 2)
            for k in ("matched_pair_economics", "directional_residual",
                      "charged_fees", "rebates_rewards")},
        "ROBUSTNESS": (
            "CONCENTRATED_NOT_ROBUST"
            if (tot > 0 and (st.median(nets) <= 0
                             or (top[0] / tot if tot else 0) >= 0.5))
            else "LOSS_CONCENTRATED_many_small_wins_few_large_losses"
            if (tot < 0 and st.median(nets) > 0)
            else "BROAD"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity", default="data/l5/raw/activity.jsonl")
    ap.add_argument("--outdir", default="analysis/l5/norebate")
    a = ap.parse_args(argv)
    rows = load(a.activity)

    # ---- fee identity verification (never assumed) ----
    buys = [r for r in rows if r["type"] == "TRADE" and r.get("side") == "BUY"]
    resid, model = [], []
    for r in buys:
        px, sz = float(r["price"]), float(r["size"])
        u = float(r["usdcSize"])
        resid.append(u - px * sz)
        model.append(FEE_K * px * (1 - px) * sz)
    diff = [abs(x - y) for x, y in zip(resid, model)]
    # Pooling fee-charged and fee-FREE buys makes the identity look broken:
    # a fee-free buy has resid 0 while the model predicts >0, so the pooled
    # median measures the fee-free share, not model error. The identity is
    # therefore tested on the buys that were actually charged.
    charged = [(x, y) for x, y in zip(resid, model) if x > 1e-9]
    cdiff = [abs(x - y) for x, y in charged]
    cratio = [x / y for x, y in charged if y > 1e-12]
    confirmed_pooled = bool(diff and st.median(diff) < 1e-4)
    confirmed_charged = bool(cdiff and st.median(cdiff) < 1e-4)

    # ---- per-market aggregation ----
    mk: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"buy": 0.0, "sell": 0.0, "redeem": 0.0, "fees": 0.0,
                 "shares": defaultdict(float), "cost": defaultdict(float),
                 "ts": None, "slug": None})
    for r in rows:
        slug = r.get("eventSlug") or r.get("conditionId") or ""
        if r["type"] == "TRADE":
            m = mk[slug]
            u = float(r["usdcSize"])
            px, sz = float(r["price"]), float(r["size"])
            oc = str(r.get("outcome") or "").lower()
            if r.get("side") == "BUY":
                m["buy"] += u
                m["fees"] += u - px * sz
                m["shares"][oc] += sz
                m["cost"][oc] += px * sz
            else:
                m["sell"] += u
            m["ts"] = r["timestamp"] if m["ts"] is None else min(m["ts"],
                                                                 r["timestamp"])
            m["slug"] = slug
        elif r["type"] == "REDEEM":
            m = mk[slug]
            m["redeem"] += float(r.get("usdcSize") or 0)
            m["ts"] = r["timestamp"] if m["ts"] is None else min(m["ts"],
                                                                 r["timestamp"])
            m["slug"] = slug

    events = []
    for slug, m in mk.items():
        if m["ts"] is None or (m["buy"] == 0 and m["redeem"] == 0):
            continue
        net = m["redeem"] + m["sell"] - m["buy"]
        sh = m["shares"]
        ks = sorted(sh)
        matched = min(sh[ks[0]], sh[ks[1]]) if len(ks) >= 2 else 0.0
        # matched pair redeems exactly $1.00; pair cost from both VWAPs
        pc = 0.0
        for k in ks[:2]:
            if sh[k] > 0:
                pc += m["cost"][k] / sh[k]
        matched_econ = matched * (1.0 - pc) if matched > 0 else 0.0
        # residual: proceeds are whatever the cashflow leaves after matched
        residual_econ = net - matched_econ - 0.0
        events.append({
            "slug": slug, "ts": m["ts"], "day": day(m["ts"]),
            "era": era(m["ts"]),
            "net": round(net, 6),
            "components": {
                "matched_pair_economics": round(matched_econ, 6),
                "directional_residual": round(residual_econ, 6),
                "charged_fees": round(m["fees"], 6),
                "rebates_rewards": 0.0,
            },
            "matched_shares": round(matched, 4),
            "buy_debits": round(m["buy"], 4),
            "redeem_proceeds": round(m["redeem"], 4),
        })
    events.sort(key=lambda e: e["ts"])

    by_era = defaultdict(list)
    for e in events:
        by_era[e["era"]].append(e)

    # monthly / weekly stability
    def group(evs, fmt):
        g = defaultdict(float)
        for e in evs:
            g[dt.datetime.utcfromtimestamp(e["ts"]).strftime(fmt)] += e["net"]
        return {k: round(v, 2) for k, v in sorted(g.items())}

    # observed incentives, the 4 days only
    inc = defaultdict(float)
    inc_by_type = defaultdict(float)
    for r in rows:
        if r["type"] in INCENTIVE_TYPES:
            inc[day(r["timestamp"])] += float(r["usdcSize"])
            inc_by_type[r["type"]] += float(r["usdcSize"])

    out = {
        "protocol": PREREG,
        "NOT_OUR_REALIZED_PROFIT": (
            "this is L5's own historical cash under a rebates=$0 "
            "accounting. It is not our realized profit and is not a "
            "forecast."),
        "COVERAGE_HOLE": ("May, June and July 2026 are entirely absent "
                          "(activity feed hit the 5000-offset cap). A hole, "
                          "not a zero. No $/day divides by calendar span."),
        "fee_identity": {
            "model": "0.07*p*(1-p) per share, embedded in usdcSize",
            "n_buys": len(buys),
            "n_buys_actually_charged": len(charged),
            "share_of_buys_charged": (round(len(charged) / len(buys), 4)
                                      if buys else None),
            "pooled_median_abs_deviation": (round(st.median(diff), 8)
                                            if diff else None),
            "VERDICT_POOLED": ("CONFIRMED" if confirmed_pooled
                               else "UNCONFIRMED_artifact_of_pooling_"
                                    "fee_free_buys"),
            "charged_only_median_abs_deviation": (round(st.median(cdiff), 8)
                                                  if cdiff else None),
            "charged_only_median_ratio_resid_over_model": (
                round(st.median(cratio), 6) if cratio else None),
            "VERDICT_ON_CHARGED_BUYS": ("FEE_IDENTITY_CONFIRMED"
                                        if confirmed_charged
                                        else "FEE_IDENTITY_UNCONFIRMED"),
            "total_reconstructed_fees_usd": round(sum(resid), 2),
            "NOTE": ("fees appear ONLY in the incentive era; March and "
                     "April buys carry no fee at all"),
        },
        "observed_incentives_ONLY_4_DAYS": {
            "per_day_usd": {k: round(v, 2) for k, v in sorted(inc.items())},
            "by_type_usd": {k: round(v, 2) for k, v in inc_by_type.items()},
            "CORPUS_TOTAL_UNIDENTIFIABLE": True,
        },
        "ERAS": {k: stats(v, k) for k, v in sorted(by_era.items())},
        "FULL_CORPUS": stats(events, "FULL_CORPUS"),
        "monthly_net_usd": group(events, "%Y-%m"),
        "weekly_net_usd": group(events, "%Y-W%W"),
        "n_markets": len(events),
    }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "Q1_NOREBATE.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    with open(os.path.join(a.outdir, "Q1_EVENTS.jsonl"), "w") as fh:
        for e in events:
            fh.write(json.dumps(e, default=str) + "\n")
    print("written Q1_NOREBATE.json: %d markets" % len(events))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
