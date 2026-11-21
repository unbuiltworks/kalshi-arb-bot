#!/usr/bin/env python3
"""Favorite-accumulation hypothesis, tested against L5's own settled record.

ISSUE #194 research gate. Owner question: does L5 profitably accumulate the
higher-probability side and then opportunistically hedge with the underdog?

DATA, ALL EXISTING AND FROZEN -- no new pipeline, no new service.
  data/l5/raw/markets.jsonl   settlement truth: outcomes + outcomePrices,
                              umaResolutionStatus resolved, Chainlink source
  data/l5/raw/activity.jsonl  L5's own TRADE rows, deduplicated

THE CIRCULARITY THE ISSUE WARNS ABOUT, AND HOW THIS AVOIDS IT. Using the
market price as both the predictor and the measure of edge proves nothing.
Here the PREDICTOR is the price L5 paid and the TRUTH is the venue's own
settlement (outcomePrices), which is independent of any price. That is a
calibration test, not a tautology.

THE INFERENCE UNIT IS THE WINDOW, NOT THE TRADE. Every trade inside one
15-minute window shares ONE settlement outcome, so 63,346 trades are not
63,346 observations -- they are ~526 coin flips. Trade-level intervals are
far too narrow and make noise look like edge. Every interval here is
clustered by window.

WHAT THIS IS NOT. It is not a promotion, not a live change, and not a claim
that any edge measured on L5's EXECUTED prices is capturable by our own
resting orders. Our live residual lands on the losing side 81% of the time
against L5's 57%, which is precisely the adverse selection a maker meets and
which no replay over his fills can observe. That gap is stated with every
result rather than assumed away.
"""
from __future__ import annotations

import collections
import json
import math
import os
import statistics as st
from typing import Any, Dict, List, Optional, Tuple

MARKETS = "data/l5/raw/markets.jsonl"
ACTIVITY = "data/l5/raw/activity.jsonl"
PREFIX = "btc-updown-15m-"

#: PMUS maker execution charges no commission; every live fill this campaign
#: produced carried commission 0.0000. A taker cross is modelled at the
#: deployed worst-case theta and is used ONLY as a counterfactual.
MAKER_FEE = 0.0
TAKER_FEE_PER_CONTRACT = 0.01
#: the deployed opening pair ceiling and residual cap, unchanged here.
PAIR_CEILING = 0.96
RESIDUAL_CAP = 1


# ----------------------------------------------------------------- loading
def load_outcomes(path: str = MARKETS) -> Dict[str, str]:
    """{slug: winning outcome} for resolved BTC15m windows. Binary only."""
    out: Dict[str, str] = {}
    with open(path) as fh:
        for line in fh:
            try:
                env = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            rows = env.get("payload")
            if not isinstance(rows, list):
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                slug = str(r.get("slug") or "")
                if not slug.startswith(PREFIX):
                    continue
                if str(r.get("umaResolutionStatus") or "").lower() != "resolved":
                    continue
                try:
                    outs, px = r.get("outcomes"), r.get("outcomePrices")
                    if isinstance(outs, str):
                        outs = json.loads(outs)
                    if isinstance(px, str):
                        px = json.loads(px)
                    pair = {str(o): float(p) for o, p in zip(outs, px)}
                except Exception:                              # noqa: BLE001
                    continue
                if sorted(pair.values()) != [0.0, 1.0]:
                    continue
                w = [o for o, p in pair.items() if p == 1.0][0]
                if out.get(slug, w) != w:          # conflicting captures
                    out.pop(slug, None)
                    continue
                out[slug] = w
    return out


def load_trades(outcomes: Dict[str, str],
                path: str = ACTIVITY) -> List[Dict[str, Any]]:
    """L5's deduplicated TRADE rows on resolved windows, with the outcome."""
    seen = set()
    out: List[Dict[str, Any]] = []
    with open(path) as fh:
        for line in fh:
            try:
                env = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            rows = env.get("payload")
            if not isinstance(rows, list):
                continue
            for r in rows:
                if not isinstance(r, dict) or str(r.get("type")) != "TRADE":
                    continue
                slug = str(r.get("slug") or r.get("eventSlug") or "")
                if slug not in outcomes:
                    continue
                key = (str(r.get("transactionHash")), str(r.get("asset")),
                       str(r.get("side")), str(r.get("price")),
                       str(r.get("size")), str(r.get("timestamp")))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    ws = float(slug.rsplit("-", 1)[1])
                    out.append({
                        "slug": slug, "ws": ws,
                        "t_off": float(r.get("timestamp")) - ws,
                        "side": str(r.get("side")),
                        "outcome": str(r.get("outcome")),
                        "px": float(r.get("price")),
                        "size": float(r.get("size")),
                        "winner": outcomes[slug]})
                except Exception:                              # noqa: BLE001
                    continue
    out.sort(key=lambda t: (t["ws"], t["t_off"]))
    return out


# ------------------------------------------------------------- statistics
def _t_crit(n: int) -> float:
    return 1.9647 if n > 300 else 2.0 if n > 60 else 2.2


def window_clustered_edge(trades: List[Dict[str, Any]]) -> Optional[Dict]:
    """Edge per contract with a WINDOW-clustered 95% interval.

    One window is one observation, because one window is one outcome.
    """
    byw: Dict[str, List[Dict]] = collections.defaultdict(list)
    for t in trades:
        byw[t["slug"]].append(t)
    per: List[Tuple[float, float]] = []
    for ts in byw.values():
        c = sum(x["size"] for x in ts)
        if c <= 0:
            continue
        imp = sum(x["px"] * x["size"] for x in ts) / c
        rea = sum(x["size"] for x in ts if x["outcome"] == x["winner"]) / c
        per.append((rea - imp, c))
    n = len(per)
    if n < 2:
        return None
    W = sum(c for _e, c in per)
    es = [e for e, _c in per]
    m, sd = st.mean(es), st.stdev(es)
    se = sd / math.sqrt(n)
    tc = _t_crit(n)
    lo, hi = m - tc * se, m + tc * se
    return {"windows": n, "contracts": W,
            "contract_weighted_edge": sum(e * c for e, c in per) / W,
            "window_mean_edge": m, "ci95": [lo, hi],
            "significant": bool(lo * hi > 0)}


# ------------------------------------------------------- the three policies
#
# PREDECLARED, AND DELIBERATELY REWRITTEN AFTER A FAILED FIRST ATTEMPT.
#
# WHAT THE FIRST VERSION DID AND WHY IT WAS WORTHLESS. It sorted each side of
# the window by price and greedily matched cheapest favorite to cheapest
# underdog. Over 211 out-of-sample windows that "earned" +$2,257 on 9,514
# pairs -- 45 pairs per window, against the 6 pairs our live engine actually
# completed across 4 windows. It was hindsight cherry-picking: it bought a
# minute-2 price against a minute-13 price, ignored the clip size, ignored
# the cash and exposure caps, and assumed we were the counterparty to every
# trade L5 made. A replay that reports a number like that is not evidence,
# and CLAUDE.md names the failure exactly: "Positive aggregate replay P&L
# with materially different behaviour is not sufficient evidence."
#
# WHAT THIS VERSION ENFORCES, all from the DEPLOYED configuration:
#   * CHRONOLOGICAL -- a price is only tradable at the moment it occurred;
#   * CLIP 1 contract per action (TS.opening_leg_max_contracts in force);
#   * $12 deployed cash and $20 worst-case exposure;
#   * the residual cap;
#   * the entry cutoff (480s) for anything that INCREASES exposure and the
#     completion cutoff (600s) for anything that reduces it;
#   * a participation fraction, because we are a maker and are NOT the
#     counterparty to every print. It is a parameter and the headline runs
#     report a sweep, because the result's sensitivity to it is the whole
#     question for a maker.
CLIP = 1.0
CASH_CAP = 12.0
EXPOSURE_CAP = 20.0
ENTRY_CUTOFF_S = 480.0
COMPLETE_CUTOFF_S = 600.0


def _simulate(ts: List[Dict], *, want_favorite: bool, hedge: bool,
              resid_cap: int, ceiling: float, participation: float,
              max_actions: int) -> Dict[str, Any]:
    """One window, chronologically, under the deployed caps.

    `want_favorite` False gives the paired controller, which opens on
    whichever side is cheap enough to pair. True accumulates the favorite.
    `hedge` allows an opposite-side completion.
    """
    winner = ts[0]["winner"] if ts else None
    up = dn = 0.0
    spend = 0.0
    pairs = 0
    acts = 0
    # a deterministic participation stride: every k-th print is ours. A
    # pseudo-random draw would make the result unreproducible, and a stride
    # is the conservative reading of "we see some prints, not all".
    stride = max(1, int(round(1.0 / participation))) if participation > 0 else 10**9
    for idx, t in enumerate(ts):
        if acts >= max_actions:
            break
        if idx % stride:
            continue
        px, oc, off = t["px"], t["outcome"], t["t_off"]
        held_same = up if oc == "Up" else dn
        held_other = dn if oc == "Up" else up
        reduces = held_other > held_same           # this leg shrinks |resid|
        # the deployed cutoffs: increase only before entry, reduce before
        # completion. Neither is relaxed here.
        if reduces and off > COMPLETE_CUTOFF_S:
            continue
        if (not reduces) and off > ENTRY_CUTOFF_S:
            continue
        is_fav = px > 0.50
        if want_favorite and not reduces and not is_fav:
            continue
        if reduces and not hedge:
            continue
        # THE PAIR CEILING, on the leg that completes. The carried basis is
        # the average paid for the side we already hold, so the pair this
        # leg would lock is that basis plus this price. Refusing above the
        # ceiling is the deployed behaviour and is not relaxed here.
        if reduces and held_other > 0:
            carried = spend / (up + dn) if (up + dn) > 0 else px
            if carried + px > ceiling:
                continue
        if not reduces:
            resid_after = abs((held_same + CLIP) - held_other)
            if resid_after > resid_cap:
                continue
        if spend + px * CLIP > CASH_CAP:
            continue
        worst = max(up, dn) + CLIP          # worst-case contracts at risk
        if worst * 1.0 > EXPOSURE_CAP:
            continue
        if oc == "Up":
            up += CLIP
        else:
            dn += CLIP
        spend += px * CLIP
        acts += 1
        m = min(up, dn)
        pairs = int(m)
    resid_up, resid_dn = up - min(up, dn), dn - min(up, dn)
    redeem = min(up, dn) * 1.0
    res_win = (resid_up if winner == "Up" else resid_dn)
    res_other = (resid_dn if winner == "Up" else resid_up)
    realized = redeem + res_win - spend
    other = redeem + res_other - spend
    return {"pairs": int(min(up, dn)), "residual": resid_up + resid_dn,
            "spend": spend, "fees": 0.0, "actions": acts,
            "realized": realized, "if_other_outcome": other,
            "worst_case": min(realized, other)}


def policy_paired(ts, ceiling: float = PAIR_CEILING,
                  resid_cap: int = RESIDUAL_CAP, participation: float = 1.0,
                  max_actions: int = 8) -> Dict[str, Any]:
    """A: the current controller -- open either side, complete, cap residual."""
    return _simulate(ts, want_favorite=False, hedge=True,
                     resid_cap=resid_cap, ceiling=ceiling,
                     participation=participation, max_actions=max_actions)


def policy_favorite(ts, cap: int = 3, participation: float = 1.0,
                    max_actions: int = 8) -> Dict[str, Any]:
    """B: favorite accumulation only, capped directional residual, no hedge."""
    return _simulate(ts, want_favorite=True, hedge=False,
                     resid_cap=cap, ceiling=PAIR_CEILING,
                     participation=participation, max_actions=max_actions)


def policy_favorite_hedge(ts, cap: int = 3, ceiling: float = PAIR_CEILING,
                          participation: float = 1.0,
                          max_actions: int = 8) -> Dict[str, Any]:
    """C: accumulate the favorite, then hedge opportunistically."""
    return _simulate(ts, want_favorite=True, hedge=True,
                     resid_cap=cap, ceiling=ceiling,
                     participation=participation, max_actions=max_actions)


def run(trades: List[Dict], split: float = 0.6, cap: int = 3,
        part: float = 1.0, max_actions: int = 8) -> Dict[str, Any]:
    """Chronological out-of-sample: first `split` trains, the rest tests."""
    byw: Dict[str, List[Dict]] = collections.defaultdict(list)
    for t in trades:
        byw[t["slug"]].append(t)
    slugs = sorted(byw, key=lambda s: byw[s][0]["ws"])
    cut = int(len(slugs) * split)
    folds = {"IN_SAMPLE": slugs[:cut], "OUT_OF_SAMPLE": slugs[cut:]}
    pol = {
        "A_paired": lambda ts: policy_paired(
            ts, participation=part, max_actions=max_actions),
        "B_favorite": lambda ts: policy_favorite(
            ts, cap=cap, participation=part, max_actions=max_actions),
        "C_favorite_hedge": lambda ts: policy_favorite_hedge(
            ts, cap=cap, participation=part, max_actions=max_actions)}
    out: Dict[str, Any] = {"cap": cap, "split": split,
                           "participation": part,
                           "max_actions": max_actions,
                           "windows_total": len(slugs)}
    for fold, ss in folds.items():
        out[fold] = {"windows": len(ss)}
        for name, fn in pol.items():
            rs = [fn(byw[s]) for s in ss if byw[s]]
            if not rs:
                continue
            real = [r["realized"] for r in rs]
            n = len(real)
            m, sd = st.mean(real), (st.stdev(real) if n > 1 else 0.0)
            se = sd / math.sqrt(n) if n else 0.0
            tc = _t_crit(n)
            run_sum, peak, mdd = 0.0, 0.0, 0.0
            for v in real:
                run_sum += v
                peak = max(peak, run_sum)
                mdd = min(mdd, run_sum - peak)
            out[fold][name] = {
                "windows": n,
                "total_realized": sum(real),
                "mean_per_window": m,
                "ci95": [m - tc * se, m + tc * se],
                "significant": bool((m - tc * se) * (m + tc * se) > 0),
                "max_drawdown": mdd,
                "pairs": sum(r["pairs"] for r in rs),
                "residual_contracts": sum(r["residual"] for r in rs),
                "spend": sum(r["spend"] for r in rs),
                "fees": sum(r["fees"] for r in rs),
                "mean_if_other_outcome": st.mean(
                    [r["if_other_outcome"] for r in rs]),
                "mean_worst_case": st.mean([r["worst_case"] for r in rs]),
            }
    return out


if __name__ == "__main__":
    oc = load_outcomes()
    tr = load_trades(oc)
    rep = {
        "corpus": {"resolved_windows": len(oc), "trades": len(tr),
                   "windows_with_trades": len({t["slug"] for t in tr})},
        "calibration": {
            k: window_clustered_edge(v) for k, v in {
                "favorite_gt_050": [t for t in tr if t["px"] > 0.50],
                "underdog_lt_050": [t for t in tr if t["px"] < 0.50],
                "strong_fav_ge_070": [t for t in tr if t["px"] >= 0.70],
                "strong_fav_ge_080": [t for t in tr if t["px"] >= 0.80],
                "all": tr}.items()},
        "l5_behaviour": None,
        "replay": run(tr),
    }
    byw = collections.defaultdict(list)
    for t in tr:
        byw[t["slug"]].append(t)
    fh_, uh_, bal = 0, 0, 0
    fc = uc = 0.0
    for ts in byw.values():
        f = sum(x["size"] for x in ts if x["px"] > 0.50)
        u = sum(x["size"] for x in ts if x["px"] < 0.50)
        fc += f
        uc += u
        if f > u * 1.2:
            fh_ += 1
        elif u > f * 1.2:
            uh_ += 1
        else:
            bal += 1
    rep["l5_behaviour"] = {
        "windows": len(byw), "favorite_heavy": fh_, "underdog_heavy": uh_,
        "balanced": bal, "favorite_contracts": fc, "underdog_contracts": uc,
        "favorite_to_underdog_ratio": (fc / uc) if uc else None}
    print(json.dumps(rep, indent=2, default=str))
