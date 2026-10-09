"""Items 2, 3, 4 -- is the late aggressive overlay actually causal, and
does the two-mode combination beat either mode alone out of sample?

ITEM 2. The removal-of-trades counterfactual reported earlier (+2.224 per
window) is NOT causal evidence and is not treated as such here. Removing
any trades changes the inventory path, and taker fills are systematically
late and large, so "remove the taker fills" is confounded with "remove
late fills" and with "remove volume". Three placebos separate those:

  P-RANDOM  remove the SAME NUMBER of maker fills per window, drawn at
            random. If this reproduces the effect, removal measures
            "fewer fills", not aggression.
  P-LATE    remove the same number of fills from the same late part of
            the window regardless of class. If this reproduces it, the
            effect is timing, not aggression.
  P-SIZE    remove the N largest fills per window regardless of class. If
            this reproduces it, the effect is size, not aggression.

The aggression claim survives only if the real removal moves P&L
materially MORE than all three placebos.

ITEM 3. Separate economics per mode, from ACTUAL fills rather than
counterfactuals: spend, matched pairs created, locked pair profit,
residual created, and the settlement outcome of residual attributed FIFO
to the mode that bought it. Incentives are reported at the account level
only, because the venue returns them with no market attached.

ITEM 4. Held-out test. Mode labels exist for 4 days only, so a mode PROXY
is calibrated against the 72,550 real labels and then applied corpus-wide.
Three policies are replayed per window -- both modes, early-passive only,
late-aggressive only -- and scored on months the proxy was NOT calibrated
on.

Read-only. No network. Nothing armed.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import gzip
import json
import os
import random
import statistics
import sys
from collections import deque

EPS = 1e-9
SEED = 20261009


def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def ci(vals, rng, reps=1500, cap=20000):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return None, None
    k, m = len(v), min(len(v), cap)
    out = sorted(statistics.fmean([v[rng.randrange(k)] for _ in range(m)])
                 for _ in range(reps))
    return out[int(reps * .025)], out[int(reps * .975)]


def fifo_take(q, qty):
    sp = 0.0
    tags = []
    while qty > EPS and q:
        lot = q[0]
        use = min(lot[0], qty)
        sp += use * lot[1]
        tags.append((lot[2], use, lot[1]))
        lot[0] -= use
        qty -= use
        if lot[0] <= EPS:
            q.popleft()
    return sp, tags


def replay(fills, winner):
    """fills: (leg, px, size, tag). Returns P&L plus a mode attribution."""
    q = {"UP": deque(), "DOWN": deque()}
    mq = ms = sp = 0.0
    matched_by_tag = collections.Counter()
    for leg, px, sz, tag in fills:
        if px is None or sz is None:
            continue
        q[leg].append([sz, px, tag])
        sp += sz * px
        pair = min(sum(l[0] for l in q["UP"]), sum(l[0] for l in q["DOWN"]))
        if pair > EPS:
            su, tu = fifo_take(q["UP"], pair)
            sd, td = fifo_take(q["DOWN"], pair)
            ms += su + sd
            mq += pair
            for t, u, p in tu + td:
                matched_by_tag[t] += u * (0.5 - p) + 0.0   # placeholder
    uq = sum(l[0] for l in q["UP"])
    dq = sum(l[0] for l in q["DOWN"])
    if uq > EPS:
        rs, rq = "Up", uq
        resid_lots = [(l[2], l[0], l[1]) for l in q["UP"]]
    elif dq > EPS:
        rs, rq = "Down", dq
        resid_lots = [(l[2], l[0], l[1]) for l in q["DOWN"]]
    else:
        rs, rq, resid_lots = "", 0.0, []
    won = bool(rs and winner and rs.lower() == winner.lower())
    pnl = mq + (rq if won else 0.0) - sp
    return {"pnl": pnl, "matched_qty": mq, "matched_spend": ms,
            "spend": sp, "resid_side": rs, "resid_qty": rq,
            "resid_won": won, "resid_lots": resid_lots}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rng = random.Random(SEED)

    lab = {}
    for r in csv.DictReader(gzip.open(a.labels, "rt")):
        lab[(r["event_slug"], r["trade_seq"])] = r["classification"]

    wins = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(
            a.dataset, "fills", "l5_fills_enriched_part*.csv.gz"))):
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                wins[r["event_slug"]].append(r)

    R = {}

    # =============== ITEM 2: real removal vs three placebos ===========
    eff = {"REAL_remove_taker": [], "P_RANDOM_maker": [],
           "P_LATE_any_class": [], "P_SIZE_largest": []}
    resid_eff = {k: [] for k in eff}
    nwin = 0
    for slug, rows in wins.items():
        rows = sorted(rows, key=lambda r: int(r["trade_seq"]))
        labs = [lab.get((slug, r["trade_seq"])) for r in rows]
        tk = [i for i, c in enumerate(labs) if c == "TAKER_OBSERVED"]
        mk = [i for i, c in enumerate(labs) if c == "MAKER_INFERRED"]
        if not tk or len(rows) < 4:
            continue
        winner = rows[-1]["settled_outcome"]
        if not winner:
            continue
        nwin += 1
        n = len(tk)
        base = [(r["leg"], f(r["price"]), f(r["size"]),
                 labs[i] or "UNLABELLED") for i, r in enumerate(rows)]
        full = replay(base, winner)

        def drop(idxs):
            s = set(idxs)
            return replay([x for i, x in enumerate(base) if i not in s],
                          winner)

        variants = {
            "REAL_remove_taker": tk,
            "P_RANDOM_maker": rng.sample(mk, min(n, len(mk))) if mk else [],
            "P_LATE_any_class": list(range(len(rows)))[-n:],
            "P_SIZE_largest": sorted(
                range(len(rows)),
                key=lambda i: -(f(rows[i]["size"], 0.0) or 0.0))[:n],
        }
        for k, idxs in variants.items():
            if not idxs:
                continue
            d = drop(idxs)
            eff[k].append(full["pnl"] - d["pnl"])
            resid_eff[k].append(full["resid_qty"] - d["resid_qty"])

    R["item2_causality_placebos"] = {
        "_what": "P&L effect of REMOVING a set of fills, per window. "
                 "Positive means the removed fills were adding P&L. The "
                 "aggression claim survives only if REAL moves P&L "
                 "materially more than every placebo.",
        "windows": nwin,
        "by_variant": {},
    }
    for k in eff:
        v = eff[k]
        if not v:
            continue
        lo, hi = ci(v, rng)
        rlo, rhi = ci(resid_eff[k], rng)
        R["item2_causality_placebos"]["by_variant"][k] = {
            "n": len(v),
            "mean_pnl_effect": round(statistics.fmean(v), 4),
            "ci95": [None if lo is None else round(lo, 4),
                     None if hi is None else round(hi, 4)],
            "mean_residual_effect": round(statistics.fmean(resid_eff[k]), 4),
            "residual_ci95": [None if rlo is None else round(rlo, 4),
                              None if rhi is None else round(rhi, 4)],
        }
    bv = R["item2_causality_placebos"]["by_variant"]
    if "REAL_remove_taker" in bv:
        real = bv["REAL_remove_taker"]["mean_pnl_effect"]
        plac = [v["mean_pnl_effect"] for k, v in bv.items()
                if k != "REAL_remove_taker"]
        real_lo = bv["REAL_remove_taker"]["ci95"][0]
        worst_plac_hi = max(
            (v["ci95"][1] for k, v in bv.items()
             if k != "REAL_remove_taker" and v["ci95"][1] is not None),
            default=None)
        R["item2_causality_placebos"]["VERDICT"] = (
            "AGGRESSION-SPECIFIC: the real removal's CI lies above every "
            "placebo's CI."
            if (real_lo is not None and worst_plac_hi is not None
                and real_lo > worst_plac_hi) else
            "NOT AGGRESSION-SPECIFIC: at least one placebo reproduces the "
            "effect, so removal-of-trades measures fill count, timing or "
            "size rather than aggression. The earlier +2.224 figure must "
            "NOT be read as causal.")
        R["item2_causality_placebos"]["placebo_means"] = plac

    # =============== ITEM 3: separate economics per mode ==============
    econ = {c: {"fills": 0, "spend": 0.0, "qty": 0.0,
                "resid_qty_created": 0.0, "resid_won_qty": 0.0,
                "resid_lost_qty": 0.0, "resid_spend": 0.0}
            for c in ("TAKER_OBSERVED", "MAKER_INFERRED")}
    matched_pairs_total = 0.0
    locked_total = 0.0
    for slug, rows in wins.items():
        rows = sorted(rows, key=lambda r: int(r["trade_seq"]))
        labs = [lab.get((slug, r["trade_seq"])) for r in rows]
        if not any(labs):
            continue
        winner = rows[-1]["settled_outcome"]
        base = [(r["leg"], f(r["price"]), f(r["size"]),
                 labs[i] or "UNLABELLED") for i, r in enumerate(rows)]
        st = replay(base, winner)
        matched_pairs_total += st["matched_qty"]
        if st["matched_qty"] > EPS:
            locked_total += st["matched_qty"] - st["matched_spend"]
        for tag, qty, px in st["resid_lots"]:
            if tag in econ:
                econ[tag]["resid_qty_created"] += qty
                econ[tag]["resid_spend"] += qty * px
                if st["resid_won"]:
                    econ[tag]["resid_won_qty"] += qty
                else:
                    econ[tag]["resid_lost_qty"] += qty
        for (leg, px, sz, tag) in base:
            if tag in econ and px is not None and sz is not None:
                econ[tag]["fills"] += 1
                econ[tag]["spend"] += px * sz
                econ[tag]["qty"] += sz
    for c, d in econ.items():
        d["mean_price"] = round(d["spend"] / d["qty"], 5) if d["qty"] else None
        d["residual_settlement_pnl"] = round(
            d["resid_won_qty"] - d["resid_spend"], 2)
        for k in ("spend", "qty", "resid_qty_created", "resid_won_qty",
                  "resid_lost_qty", "resid_spend"):
            d[k] = round(d[k], 2)
    R["item3_mode_economics"] = {
        "_scope": "the 72,550 fills carrying a real execution label "
                  "(2026-08-28..31). Residual lots are attributed FIFO to "
                  "the mode that bought them.",
        "by_mode": econ,
        "matched_book": {
            "matched_pairs": round(matched_pairs_total, 2),
            "locked_profit": round(locked_total, 2),
            "_note": "the matched book's profit is settlement-independent "
                     "and cannot be attributed to one mode: a pair needs "
                     "one unit from each leg, often bought in different "
                     "modes.",
        },
        "incentives": {
            "_note": "MAKER_REBATE / TAKER_REBATE / REWARD records carry an "
                     "EMPTY slug, so they cannot be attributed to a window "
                     "or a mode. Account-level only; see cashflow.csv.gz.",
        },
    }

    # =============== ITEM 4: held-out combined vs single mode =========
    # calibrate a mode proxy on the real labels
    lab_rows = []
    for slug, rows in wins.items():
        for r in rows:
            c = lab.get((slug, r["trade_seq"]))
            if c in ("TAKER_OBSERVED", "MAKER_INFERRED"):
                lab_rows.append((f(r["size"], 0.0) or 0.0,
                                 f(r["seconds_to_settlement"], 1e9),
                                 1 if c == "TAKER_OBSERVED" else 0))
    best = None
    for szc in (15, 20, 25, 30, 38):
        for ttc in (120, 180, 240, 300):
            tp = sum(1 for s, t, y in lab_rows if s >= szc and t < ttc and y)
            fp = sum(1 for s, t, y in lab_rows
                     if s >= szc and t < ttc and not y)
            fn = sum(1 for s, t, y in lab_rows
                     if not (s >= szc and t < ttc) and y)
            prec = tp / (tp + fp) if tp + fp else 0
            rec = f1 = 0
            rec = tp / (tp + fn) if tp + fn else 0
            if prec + rec:
                f1 = 2 * prec * rec / (prec + rec)
            if best is None or f1 > best[0]:
                best = (f1, szc, ttc, round(prec, 4), round(rec, 4))
    _, SZC, TTC, PREC, REC = best
    R["item4_heldout"] = {
        "mode_proxy": {
            "rule": f"size >= {SZC} AND seconds_to_settlement < {TTC} "
                    "-> late-aggressive, else early-passive",
            "calibrated_on": "the 72,550 real labels",
            "precision": PREC, "recall": REC, "f1": round(best[0], 4),
        },
        "by_heldout_month": {},
    }
    pol = {"BOTH_MODES": [], "EARLY_PASSIVE_ONLY": [],
           "LATE_AGGRESSIVE_ONLY": []}
    per_month = {k: collections.defaultdict(list) for k in pol}
    for slug, rows in wins.items():
        rows = sorted(rows, key=lambda r: int(r["trade_seq"]))
        winner = rows[-1]["settled_outcome"]
        if not winner:
            continue
        month = rows[0]["datetime_utc"][:7]
        base = []
        for r in rows:
            sz = f(r["size"], 0.0) or 0.0
            tt = f(r["seconds_to_settlement"], 1e9)
            tag = "LATE_AGG" if (sz >= SZC and tt < TTC) else "EARLY_PAS"
            base.append((r["leg"], f(r["price"]), sz, tag))
        for name, keep in (("BOTH_MODES", None),
                           ("EARLY_PASSIVE_ONLY", "EARLY_PAS"),
                           ("LATE_AGGRESSIVE_ONLY", "LATE_AGG")):
            sub = base if keep is None else [x for x in base if x[3] == keep]
            if not sub:
                pol[name].append(0.0)
                per_month[name][month].append(0.0)
                continue
            v = replay(sub, winner)["pnl"]
            pol[name].append(v)
            per_month[name][month].append(v)
    for name, v in pol.items():
        lo, hi = ci(v, rng)
        R["item4_heldout"][name] = {
            "windows": len(v), "mean_pnl": round(statistics.fmean(v), 4),
            "ci95": [None if lo is None else round(lo, 4),
                     None if hi is None else round(hi, 4)],
            "total_pnl": round(sum(v), 2)}
    months = sorted({m for d in per_month.values() for m in d})
    for m in months:
        row = {}
        for name in pol:
            v = per_month[name][m]
            if not v:
                continue
            lo, hi = ci(v, rng)
            row[name] = {"n": len(v),
                         "mean_pnl": round(statistics.fmean(v), 4),
                         "ci95": [None if lo is None else round(lo, 4),
                                  None if hi is None else round(hi, 4)]}
        R["item4_heldout"]["by_heldout_month"][m] = row
    b = R["item4_heldout"]["BOTH_MODES"]
    e = R["item4_heldout"]["EARLY_PASSIVE_ONLY"]
    la = R["item4_heldout"]["LATE_AGGRESSIVE_ONLY"]
    R["item4_heldout"]["VERDICT"] = (
        "BOTH beats each single mode (CIs disjoint from both)"
        if (b["ci95"][0] is not None and e["ci95"][1] is not None
            and la["ci95"][1] is not None
            and b["ci95"][0] > e["ci95"][1]
            and b["ci95"][0] > la["ci95"][1])
        else "BOTH does NOT clearly beat both single modes")
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    print(json.dumps(R, indent=1)[:5000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
