"""CANDIDATE V1 -- the smallest causal sequential L5 policy, full-corpus.

NOT a profitability optimization and NOT a threshold sweep. Every number
is an EMPIRICAL CONDITIONAL from the pre-incentive corpus; no parameter is
searched, no cutoff is chosen by its P&L.

WHAT IS DELIBERATELY NOT HERE, per the brief:
  * no 5c EXTEND improvement gate -- higher-price EXTEND is ALLOWED,
    because it is measured to precede compression;
  * no requirement that pair cost be < 1 at any intermediate point;
  * no reuse of the 13-hour [0.20, 0.82] band or the 0.991 trigger.

STATE, all contemporaneously observable at the decision instant:
  inventory by outcome, weighted acquisition cost by outcome, matched
  inventory, directional residual, effective matched pair cost, last
  purchased outcome, that outcome's price change since acquisition,
  consecutive same-side acquisitions, TTE.

ACTION SPACE: OPEN / EXTEND_SAME_SIDE / SWITCH_OUTCOME / STOP.

SPLIT: chronological. Discovery = the earlier 70% of pre-incentive events
by first-fill time; the remaining 30% is SEALED and is NOT read when the
model is estimated or frozen. Cash economics on OOS run only after the
fingerprint is written.

TIMESTAMP TIES: fills sharing a timestamp are not ordered in the tape, so
same-timestamp legs on the OPPOSITE outcome are genuinely unordered. They
are marked AMBIGUOUS and excluded from both estimation and scoring rather
than having an order invented for them.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import statistics
from typing import Any, Dict, List, Optional

SRC = (("data/l5/raw/activity.jsonl", False),
       ("data/l5/raw/activity_gap.jsonl.gz", True))
L5 = "0xb945945d5bca"
PRE = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc).timestamp()
DISCOVERY_FRAC = 0.70
ACTIONS = ("EXTEND_SAME_SIDE", "SWITCH_OUTCOME", "STOP")
KEY = lambda x: (x.get("transactionHash"), x.get("conditionId"),
                 x.get("outcomeIndex"), x.get("timestamp"),
                 str(x.get("price")), str(x.get("size")), x.get("type"))
#: predeclared bins -- these are REPORTING bins, not fitted thresholds
MOVE_BINS = (("down_gt_2c", -9e9, -0.02), ("down_0_2c", -0.02, -1e-9),
             ("flat", -1e-9, 1e-9), ("up_0_2c", 1e-9, 0.02),
             ("up_gt_2c", 0.02, 9e9))
RUN_BINS = ((1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 10 ** 9))
TTE_BINS = (("expired", -9e9, 0.0), ("0_30s", 0.0, 30.0),
            ("30_120s", 30.0, 120.0), ("120_300s", 120.0, 300.0),
            ("ge_300s", 300.0, 9e9))


def fl(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def band(v, bins):
    for name, lo, hi in bins:
        if lo <= v < hi:
            return name
    return bins[-1][0]


def runband(n):
    for lo, hi in RUN_BINS:
        if lo <= n <= hi:
            return "run_%d" % lo if lo == hi else "run_%d_plus" % lo
    return "run_6_plus"


def load():
    seen, rows = set(), []
    for path, gz in SRC:
        op = gzip.open if gz else open
        with op(path, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    env = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                if L5 not in str((env.get("params") or {})
                                 .get("user", "")).lower():
                    continue
                pl = env.get("payload")
                if not isinstance(pl, list):
                    continue
                for r in pl:
                    if not isinstance(r, dict):
                        continue
                    nm = r.get("name")
                    if nm is not None and nm != "l5Zn1bWoM8eTsK":
                        continue
                    k = KEY(r)
                    if k in seen:
                        continue
                    seen.add(k)
                    rows.append(r)
    return rows


def legs_for(trades):
    """Collapse same (timestamp, outcome) fills into size-weighted legs."""
    g = collections.OrderedDict()
    for r in trades:
        oi = r.get("outcomeIndex")
        if oi not in (0, 1):
            continue
        t = fl(r.get("timestamp"))
        k = (t, oi)
        a = g.setdefault(k, {"t": t, "oi": oi, "q": 0.0, "notional": 0.0,
                             "usdc": 0.0})
        q = fl(r.get("size"))
        a["q"] += q
        a["notional"] += fl(r.get("price")) * q
        a["usdc"] += fl(r.get("usdcSize"))
    legs = sorted(g.values(), key=lambda a: (a["t"], a["oi"]))
    for a in legs:
        a["px"] = a["notional"] / a["q"] if a["q"] > 0 else 0.0
    # mark legs that share a timestamp with the OTHER outcome
    bytime = collections.Counter(a["t"] for a in legs)
    for a in legs:
        a["ambiguous"] = bytime[a["t"]] > 1
    return legs


def events(rows):
    by = collections.defaultdict(list)
    for r in rows:
        c = r.get("conditionId")
        if c:
            by[c].append(r)
    out = []
    for cond, rs in by.items():
        trades = [r for r in rs if r.get("type") == "TRADE"]
        if not trades:
            continue
        t0 = min(fl(r.get("timestamp")) for r in trades)
        if t0 <= 0 or t0 >= PRE:
            continue
        slug = next((str(r["eventSlug"]) for r in rs if r.get("eventSlug")),
                    "")
        dur = 900 if "-15m-" in slug else (300 if "-5m-" in slug else None)
        try:
            w0 = int(slug.rsplit("-", 1)[-1])
        except Exception:                                      # noqa: BLE001
            w0 = None
        legs = legs_for(trades)
        if not legs:
            continue
        reds = [r for r in rs if r.get("type") == "REDEEM"]
        out.append({"cond": cond, "t0": t0, "slug": slug, "dur": dur,
                    "w0": w0, "legs": legs,
                    "red_cash": sum(fl(r.get("usdcSize")) for r in reds),
                    "red_legs": {r.get("outcomeIndex") for r in reds
                                 if r.get("outcomeIndex") is not None},
                    "buy_usdc": sum(a["usdc"] for a in legs)})
    out.sort(key=lambda e: e["t0"])
    return out


def decisions(ev):
    """Causal decision points: state BEFORE the next leg, label = next leg."""
    qty = {0: 0.0, 1: 0.0}
    cost = {0: 0.0, 1: 0.0}
    last_oi = None
    last_px = None
    run = 0
    pts = []
    for i, a in enumerate(ev["legs"]):
        # state after applying leg i
        qty[a["oi"]] += a["q"]
        cost[a["oi"]] += a["notional"]
        if a["oi"] == last_oi:
            run += 1
        else:
            run = 1
        last_oi, last_px = a["oi"], a["px"]
        nxt = ev["legs"][i + 1] if i + 1 < len(ev["legs"]) else None
        if nxt is not None and (a["ambiguous"] or nxt["ambiguous"]):
            continue                      # unordered: never guessed
        label = ("STOP" if nxt is None else
                 ("EXTEND_SAME_SIDE" if nxt["oi"] == last_oi
                  else "SWITCH_OUTCOME"))
        vw = {o: (cost[o] / qty[o] if qty[o] > 0 else None) for o in (0, 1)}
        matched = min(qty[0], qty[1])
        resid = abs(qty[0] - qty[1])
        pair = (vw[0] + vw[1]) if (vw[0] is not None and vw[1] is not None) \
            else None
        tte = ((ev["w0"] + ev["dur"] - a["t"])
               if (ev["w0"] and ev["dur"]) else None)
        # price change of the last-purchased outcome since its acquisition:
        # causal, uses only this event's own prior legs on that outcome
        prior = [z for z in ev["legs"][:i] if z["oi"] == last_oi]
        move = (last_px - prior[-1]["px"]) if prior else 0.0
        pts.append({
            "cond": ev["cond"], "t": a["t"], "i": i, "label": label,
            "last_oi": last_oi, "run": run, "matched": matched,
            "residual": resid, "pair_cost": pair,
            "vw0": vw[0], "vw1": vw[1], "qty0": qty[0], "qty1": qty[1],
            "tte": tte, "move": move,
            "move_bin": band(move, MOVE_BINS), "run_bin": runband(run),
            "tte_bin": (band(tte, TTE_BINS) if tte is not None
                        else "tte_unknown"),
        })
    return pts


def estimate(pts):
    """Empirical conditionals. No search, no argmax, no fitted cutoff."""
    tab: Dict[str, Dict[str, collections.Counter]] = {}
    for feat in ("move_bin", "run_bin", "tte_bin"):
        tab[feat] = collections.defaultdict(collections.Counter)
        for p in pts:
            tab[feat][p[feat]][p["label"]] += 1
    # the joint cell the model actually uses
    joint = collections.defaultdict(collections.Counter)
    for p in pts:
        joint[(p["tte_bin"], p["move_bin"], p["run_bin"])][p["label"]] += 1
    return tab, joint


def to_rules(joint, min_n=30):
    """argmax of the empirical cell -- a lookup, not a tuned threshold."""
    rules = {}
    for cell, c in joint.items():
        n = sum(c.values())
        if n < min_n:
            continue
        best = max(ACTIONS, key=lambda a: c.get(a, 0))
        rules["|".join(cell)] = {
            "action": best, "n": n,
            "p": {a: round(c.get(a, 0) / n, 4) for a in ACTIONS}}
    return rules


def predict(p, rules, fallback):
    k = "|".join((p["tte_bin"], p["move_bin"], p["run_bin"]))
    r = rules.get(k)
    return (r["action"] if r else fallback), bool(r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="analysis/l5/CANDIDATE_V1.json")
    ap.add_argument("--eval-oos", action="store_true",
                    help="read the SEALED OOS tail; only after freezing")
    a = ap.parse_args(argv)

    ev = events(load())
    cut = int(round(DISCOVERY_FRAC * len(ev)))
    disc, oos = ev[:cut], ev[cut:]
    dpts = [p for e in disc for p in decisions(e)]
    tab, joint = estimate(dpts)
    rules = to_rules(joint)
    fallback = max(ACTIONS,
                   key=lambda x: sum(1 for p in dpts if p["label"] == x))

    model = {"rules": rules, "fallback": fallback,
             "bins": {"move": [b[0] for b in MOVE_BINS],
                      "run": [runband(b[0]) for b in RUN_BINS],
                      "tte": [b[0] for b in TTE_BINS]},
             "min_cell_n": 30}
    fp = hashlib.sha256(json.dumps(model, sort_keys=True)
                        .encode()).hexdigest()

    # ---- behavioural fidelity on DISCOVERY only
    def score(pts):
        per = collections.defaultdict(lambda: [0, 0])
        hit = cov = 0
        for p in pts:
            pred, covered = predict(p, rules, fallback)
            per[p["label"]][1] += 1
            if pred == p["label"]:
                per[p["label"]][0] += 1
                hit += 1
            cov += 1 if covered else 0
        return {"n": len(pts),
                "overall_accuracy": (round(hit / len(pts), 4)
                                     if pts else None),
                "cell_coverage": (round(cov / len(pts), 4) if pts else None),
                "per_action": {k: {"correct": v[0], "n": v[1],
                                   "accuracy": round(v[0] / v[1], 4)
                                   if v[1] else None}
                               for k, v in sorted(per.items())}}

    res = {
        "label": "L5_CANDIDATE_V1_FULL_CORPUS",
        "candidate_fingerprint_sha256": fp,
        "frozen_before_any_oos_read": True,
        "removed_assumptions": [
            "no 5c EXTEND improvement gate -- higher-price EXTEND allowed",
            "no requirement that pair cost be < 1 at any point",
            "no 13-hour [0.20,0.82] band, no 0.991 compression trigger",
            "no fixed 900s duration -- TTE uses each event's own window"],
        "split": {"pre_incentive_events": len(ev),
                  "discovery_events": len(disc),
                  "OOS_events_SEALED": len(oos),
                  "rule": "chronological by first fill, earliest 70%"},
        "decision_points_discovery": len(dpts),
        "ambiguous_excluded_note": (
            "legs sharing a timestamp with the opposite outcome are "
            "unordered in the tape and are excluded, never ordered by "
            "assumption"),
        "action_base_rates": {k: round(sum(1 for p in dpts
                                           if p["label"] == k) / len(dpts), 4)
                              for k in ACTIONS},
        "conditionals": {f: {b: {k: v for k, v in sorted(c.items())}
                             for b, c in sorted(t.items())}
                         for f, t in tab.items()},
        "model_cells": len(rules),
        "fidelity_discovery": score(dpts),
        "accounting": ("behavioural fidelity only; no cash number here. "
                       "Historical reconstruction of another trader's "
                       "public activity -- not realized profit for our "
                       "account."),
    }
    if a.eval_oos:
        opts = [p for e in oos for p in decisions(e)]
        res["fidelity_OOS"] = score(opts)
        res["OOS_UNSEALED"] = True
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, default=str)
    print(json.dumps({k: v for k, v in res.items()
                      if k != "conditionals"}, indent=2, default=str)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
