"""STATE_CONDITIONAL_PAIR_V1 on DEVELOPMENT. Frozen by its preregistration.

Pure Python by necessity (no numpy here), which for three features is no
hardship and makes the fit reproducible without a pinned BLAS.

EVERY economic figure uses OUT-OF-FOLD probabilities. A window is never traded
on a model that saw its own outcome -- with 28 windows and one binary draw
each, in-sample probabilities would be worth nothing.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.pmus_state_table import FEATURES, LABEL  # noqa
from src import pmus_fees as FEES  # noqa

ONE, ZERO = D(1), D(0)
CADENCE_S = 60
UNIT = D(1)


# ----------------------------------------------------------------- logistic
def _solve(A, b):
    """Gauss-Jordan with partial pivoting; n is 4, so this is honest enough."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-14:
            return None
        M[c], M[p] = M[p], M[c]
        pv = M[c][c]
        for j in range(c, n + 1):
            M[c][j] /= pv
        for r in range(n):
            if r != c and M[r][c] != 0:
                f = M[r][c]
                for j in range(c, n + 1):
                    M[r][j] -= f * M[c][j]
    return [M[i][n] for i in range(n)]


def fit_logistic(X, y, ridge=1e-6, iters=60):
    """Newton-Raphson. X rows already carry the leading 1."""
    k = len(X[0])
    w = [0.0] * k
    for _ in range(iters):
        g = [0.0] * k
        H = [[0.0] * k for _ in range(k)]
        for xi, yi in zip(X, y):
            z = sum(w[j] * xi[j] for j in range(k))
            z = max(-30.0, min(30.0, z))
            p = 1.0 / (1.0 + math.exp(-z))
            r = p - yi
            s = p * (1.0 - p)
            for a in range(k):
                g[a] += r * xi[a]
                for b2 in range(a, k):
                    H[a][b2] += s * xi[a] * xi[b2]
        for a in range(k):
            g[a] += ridge * w[a]
            for b2 in range(a, k):
                H[b2][a] = H[a][b2]
            H[a][a] += ridge
        d = _solve(H, g)
        if d is None:
            break
        w = [w[j] - d[j] for j in range(k)]
        if max(abs(x) for x in d) < 1e-10:
            break
    return w


def predict(w, xi):
    z = sum(w[j] * xi[j] for j in range(len(w)))
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def design(rows, mu, sd):
    """Standardized design matrix. mu/sd come from the TRAIN fold only."""
    X = []
    for r in rows:
        x = [1.0]
        for i, f in enumerate(FEATURES):
            x.append((float(r[f]) - mu[i]) / (sd[i] if sd[i] > 0 else 1.0))
        X.append(x)
    return X


def moments(rows):
    mu, sd = [], []
    for f in FEATURES:
        v = [float(r[f]) for r in rows]
        m = sum(v) / len(v)
        mu.append(m)
        sd.append(math.sqrt(sum((x - m) ** 2 for x in v) / max(1, len(v) - 1)))
    return mu, sd


# ------------------------------------------------------------- calibration
def brier(y, p):
    return sum((pi - yi) ** 2 for yi, pi in zip(y, p)) / len(y)


def logloss(y, p):
    e = 1e-12
    return -sum(yi * math.log(max(e, pi)) + (1 - yi) * math.log(max(e, 1 - pi))
                for yi, pi in zip(y, p)) / len(y)


def reliability(y, p, bins=10):
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, pi in enumerate(p)
               if (pi >= lo and (pi < hi or (b == bins - 1 and pi <= hi)))]
        if not idx:
            out.append({"bin": "%.1f-%.1f" % (lo, hi), "n": 0})
            continue
        out.append({"bin": "%.1f-%.1f" % (lo, hi), "n": len(idx),
                    "mean_pred": round(sum(p[i] for i in idx) / len(idx), 4),
                    "observed": round(sum(y[i] for i in idx) / len(idx), 4)})
    return out


# ------------------------------------------------------------------ fee/EV
def fee_per_share(px: D, size: D) -> D:
    """All-in per-share cost: the venue-cent-rounded fee at this size."""
    return FEES.taker_fee(px, size, cents=True) / size


def evs(q: float, r: dict, size: D = UNIT):
    up_px, dn_px = D(r["up_ask"]), D(r["down_px"])
    ev_up = D(str(q)) - up_px - fee_per_share(up_px, size)
    ev_dn = (ONE - D(str(q))) - dn_px - fee_per_share(dn_px, size)
    return ev_up, ev_dn


# -------------------------------------------------------------- controller
def run_window(rows, q_of, outcome_up: int) -> dict:
    """BUY UP / BUY DOWN / WAIT / SETTLE. There is no SELL."""
    ws0 = rows[0]["t"] - (rows[0]["t"] % 1)   # t is already integer seconds
    base = rows[0]["t"] - rows[0]["seconds_remaining"] - 0  # = we - 900 ~ ws
    up_sh = dn_sh = ZERO
    up_cash = dn_cash = ZERO
    cash_out = fees_cent = fees_4dp = ZERO
    purchases = []
    last_period = {"UP": None, "DOWN": None}
    for r in rows:
        q = q_of(r)
        ev_up, ev_dn = evs(q, r)
        period = int((r["t"] - base) // CADENCE_S)
        cands = []
        if ev_up > 0:
            cands.append(("UP", ev_up, D(r["up_ask"]), D(r["up_ask_qty"])))
        if ev_dn > 0:
            cands.append(("DOWN", ev_dn, D(r["down_px"]), D(r["down_qty"])))
        # strictly positive, independently, with no complement credit
        cands = [c for c in cands if last_period[c[0]] != period and c[3] > 0]
        if not cands:
            continue
        side, ev, px, dep = max(cands, key=lambda c: c[1])
        size = min(UNIT, dep)
        if size <= 0 or not (D("0.01") <= px <= D("0.99")):
            continue
        fc = FEES.taker_fee(px, size, cents=True)
        f4 = FEES.taker_fee(px, size)
        cash_out += px * size
        fees_cent += fc
        fees_4dp += f4
        if side == "UP":
            up_sh += size
            up_cash += px * size
        else:
            dn_sh += size
            dn_cash += px * size
        last_period[side] = period
        purchases.append({
            "t": r["t"], "side": side, "px": str(px), "sh": str(size),
            "fee_cent": str(fc), "fee_4dp": str(f4),
            "q": round(q, 6), "ev": str(round(ev, 6)),
            "seconds_remaining": r["seconds_remaining"],
            "locked_frac": r["locked_frac"], "z": r["z"],
            "up_ask": r["up_ask"], "down_px": r["down_px"],
            "up_sh_after": str(up_sh), "dn_sh_after": str(dn_sh),
        })
    settle = (up_sh if outcome_up else dn_sh)
    return {
        "slug": rows[0]["slug"], "outcome_up": outcome_up,
        "purchases": purchases,
        "up_sh": up_sh, "dn_sh": dn_sh,
        "up_cash": up_cash, "dn_cash": dn_cash,
        "matched": min(up_sh, dn_sh),
        "residual": up_sh - dn_sh,
        "cash_out": cash_out, "settlement": settle,
        "fees_cent": fees_cent, "fees_4dp": fees_4dp,
        "net_cent": settle - cash_out - fees_cent,
        "net_4dp": settle - cash_out - fees_4dp,
        "gross": settle - cash_out,
    }


# --------------------------------------------------------- sub-$1 pair study
def pair_study(rows_by_slug, first_leg_t) -> dict:
    """When is UP + DOWN + both fees < $1, at what size, and for how long?"""
    tot = under = 0
    after = after_under = 0
    sizes, costs, runs = [], [], []
    for sl, rows in rows_by_slug.items():
        flt = first_leg_t.get(sl)
        cur = 0
        for r in rows:
            up, dn = D(r["up_ask"]), D(r["down_px"])
            allin = (up + dn + FEES.taker_fee(up, ONE, cents=True)
                     + FEES.taker_fee(dn, ONE, cents=True))
            tot += 1
            hit = allin < ONE
            if flt is not None and r["t"] > flt:
                after += 1
                after_under += 1 if hit else 0
            if hit:
                under += 1
                sizes.append(float(min(D(r["up_ask_qty"]), D(r["down_qty"]))))
                costs.append(float(allin))
                cur += 1
            else:
                if cur:
                    runs.append(cur)
                cur = 0
        if cur:
            runs.append(cur)
    q = lambda v, f: (round(sorted(v)[int(f * (len(v) - 1))], 4)  # noqa
                      if v else None)
    return {
        "rows_evaluated": tot,
        "rows_with_allin_pair_under_1": under,
        "pct_of_all_rows": round(100.0 * under / tot, 4) if tot else None,
        "rows_after_a_qualifying_first_leg": after,
        "of_those_under_1": after_under,
        "pct_after_first_leg": (round(100.0 * after_under / after, 4)
                                if after else None),
        "min_side_size_median": q(sizes, 0.5),
        "min_side_size_p10": q(sizes, 0.10),
        "min_side_size_p90": q(sizes, 0.90),
        "allin_pair_cost_min": (round(min(costs), 4) if costs else None),
        "allin_pair_cost_median": q(costs, 0.5),
        "persistence_runs": len(runs),
        "persistence_seconds_median": q([float(x) for x in runs], 0.5),
        "persistence_seconds_max": (max(runs) if runs else None),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table",
                    default="analysis/l5/state_conditional/state_table_development.jsonl")
    ap.add_argument("--out", default="analysis/l5/state_conditional")
    ap.add_argument("--label", default="DEVELOPMENT")
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.table)]
    by = {}
    for r in rows:
        by.setdefault(r["slug"], []).append(r)
    for sl in by:
        by[sl].sort(key=lambda r: r["t"])
    slugs = sorted(by)

    # ---------- leave-one-window-out, grouped by window
    oof = {}
    coefs = []
    for ho in slugs:
        tr = [r for s in slugs if s != ho for r in by[s]]
        mu, sd = moments(tr)
        w = fit_logistic(design(tr, mu, sd), [r[LABEL] for r in tr])
        coefs.append(w)
        for r in by[ho]:
            oof[(ho, r["t"])] = predict(w, design([r], mu, sd)[0])
    y = [r[LABEL] for r in rows]
    p = [oof[(r["slug"], r["t"])] for r in rows]

    # ---------- chronological split
    tr = [r for r in rows if "2026-09-30" in r["slug"]]
    te = [r for r in rows if "2026-10-02" in r["slug"]]
    mu, sd = moments(tr)
    wc = fit_logistic(design(tr, mu, sd), [r[LABEL] for r in tr])
    pc = [predict(wc, x) for x in design(te, mu, sd)]
    yc = [r[LABEL] for r in te]

    # ---------- full-sample coefficients, for the frozen specification
    muf, sdf = moments(rows)
    wf = fit_logistic(design(rows, muf, sdf), y)

    cal = {
        "base_rate_rows": round(sum(y) / len(y), 4),
        "base_rate_windows": round(sum(by[s][0][LABEL] for s in slugs)
                                   / len(slugs), 4),
        "LOWO": {"n": len(y), "brier": round(brier(y, p), 6),
                 "logloss": round(logloss(y, p), 6),
                 "brier_vs_baserate": round(
                     brier(y, [sum(y) / len(y)] * len(y)), 6),
                 "reliability": reliability(y, p)},
        "CHRONOLOGICAL_fit0930_test1002": {
            "n_train": len(tr), "n_test": len(te),
            "brier": round(brier(yc, pc), 6),
            "logloss": round(logloss(yc, pc), 6),
            "brier_vs_baserate": round(
                brier(yc, [sum(yc) / len(yc)] * len(yc)), 6),
            "reliability": reliability(yc, pc)},
        "frozen_full_sample_coefficients": {
            "intercept": round(wf[0], 6),
            **{f: round(wf[i + 1], 6) for i, f in enumerate(FEATURES)},
            "standardization_mu": [round(x, 6) for x in muf],
            "standardization_sd": [round(x, 6) for x in sdf],
        },
        "LOWO_coefficient_stability": {
            "intercept": [round(min(c[0] for c in coefs), 4),
                          round(max(c[0] for c in coefs), 4)],
            **{f: [round(min(c[i + 1] for c in coefs), 4),
                   round(max(c[i + 1] for c in coefs), 4)]
               for i, f in enumerate(FEATURES)},
        },
    }

    # ---------- controller on OUT-OF-FOLD probabilities
    res, first_leg_t = [], {}
    for sl in slugs:
        r0 = run_window(by[sl], lambda r: oof[(r["slug"], r["t"])],
                        by[sl][0][LABEL])
        res.append(r0)
        if r0["purchases"]:
            first_leg_t[sl] = r0["purchases"][0]["t"]

    # ---------- the twelve-part decomposition
    traded = [r for r in res if r["purchases"]]
    firsts, comps, unpaired, extends = [], [], [], []
    for r in traded:
        ps = r["purchases"]
        f = ps[0]
        side0 = f["side"]
        won = (outcome := r["outcome_up"]) == (side0 == "UP")
        firsts.append({
            "slug": r["slug"], "side": side0, "px": D(f["px"]),
            "fee": D(f["fee_cent"]), "q": f["q"], "ev": D(f["ev"]),
            "sh": D(f["sh"]), "won": won,
            "pnl": (D(f["sh"]) if won else ZERO) - D(f["px"]) * D(f["sh"])
                   - D(f["fee_cent"]),
            "seconds_remaining": f["seconds_remaining"],
        })
        comp = next((x for x in ps[1:] if x["side"] != side0), None)
        if comp:
            comps.append({
                "slug": r["slug"], "dt_s": comp["t"] - f["t"],
                "leg1_px": D(f["px"]), "leg1_fee": D(f["fee_cent"]),
                "comp_px": D(comp["px"]), "comp_fee": D(comp["fee_cent"]),
                "matched_sh": min(D(f["sh"]), D(comp["sh"])),
                "allin_pair_cost": (D(f["px"]) + D(f["fee_cent"])
                                    + D(comp["px"]) + D(comp["fee_cent"])),
            })
        else:
            unpaired.append(firsts[-1])
        for x in ps[1:]:
            if x["side"] == side0 or (comp and x["t"] > comp["t"]):
                extends.append({"slug": r["slug"], "side": x["side"],
                                "px": D(x["px"]), "sh": D(x["sh"]),
                                "fee": D(x["fee_cent"]), "ev": D(x["ev"])})
    s = lambda v: sum(v, ZERO)  # noqa
    locked = [c["matched_sh"] * (ONE - c["allin_pair_cost"]) for c in comps]
    dec = {
        "1_first_leg_standalone": {
            "n": len(firsts),
            "mean_predicted_EV_per_share": (
                str(round(s(f["ev"] for f in firsts) / len(firsts), 6))
                if firsts else None),
            "total_predicted_EV": str(round(s(f["ev"] * f["sh"]
                                              for f in firsts), 4)),
            "total_realized_pnl": str(round(s(f["pnl"] for f in firsts), 4)),
            "win_rate": (round(sum(1 for f in firsts if f["won"])
                               / len(firsts), 4) if firsts else None),
            "mean_q": (round(sum(f["q"] for f in firsts) / len(firsts), 4)
                       if firsts else None),
            "mean_seconds_remaining_at_entry": (
                round(sum(f["seconds_remaining"] for f in firsts)
                      / len(firsts), 1) if firsts else None),
        },
        "2_first_leg_calibration_by_edge_bucket": [],
        "3_complement_completion_rate": (
            round(len(comps) / len(firsts), 4) if firsts else None),
        "3_complement_counts": {"completed": len(comps),
                                "never": len(unpaired),
                                "first_legs": len(firsts)},
        "4_time_to_complement_s": {
            "n": len(comps),
            "median": (statistics.median([c["dt_s"] for c in comps])
                       if comps else None),
            "min": (min(c["dt_s"] for c in comps) if comps else None),
            "max": (max(c["dt_s"] for c in comps) if comps else None),
        },
        "5_allin_matched_pair_cost": {
            "n": len(comps),
            "mean": (str(round(s(c["allin_pair_cost"] for c in comps)
                               / len(comps), 4)) if comps else None),
            "min": (str(min(c["allin_pair_cost"] for c in comps))
                    if comps else None),
            "under_1_00": sum(1 for c in comps
                              if c["allin_pair_cost"] < ONE),
        },
        "6_locked_pair_gain": str(round(s(locked), 4)),
        "7_first_legs_never_complemented": {
            "n": len(unpaired),
            "share": (round(len(unpaired) / len(firsts), 4)
                      if firsts else None)},
        "8_unpaired_leg_pnl": str(round(s(f["pnl"] for f in unpaired), 4)),
        "9_extend_purchases": {
            "n": len(extends),
            "cash": str(round(s(e["px"] * e["sh"] for e in extends), 4)),
            "fees": str(round(s(e["fee"] for e in extends), 4)),
            "mean_predicted_EV": (
                str(round(s(e["ev"] for e in extends) / len(extends), 6))
                if extends else None)},
        "10_residual_directional_pnl": None,   # filled below
        "11_fees": {"venue_cent": str(round(s(r["fees_cent"]
                                              for r in traded), 4)),
                    "modeled_4dp": str(round(s(r["fees_4dp"]
                                               for r in traded), 4))},
        "12_total_portfolio_cashflow": {
            "purchase_cash": str(round(s(r["cash_out"] for r in traded), 4)),
            "settlement_cash": str(round(s(r["settlement"]
                                           for r in traded), 4)),
            "gross_before_fees": str(round(s(r["gross"] for r in traded), 4)),
            "net_venue_cent": str(round(s(r["net_cent"] for r in traded), 4)),
            "net_modeled_4dp": str(round(s(r["net_4dp"] for r in traded), 4)),
        },
    }
    # ---- corrected residual and whole-book matched economics.
    # #6 above is the FIRST-LEG/COMPLEMENT pair only. The matched BOOK is
    # bigger than that pair once EXTENDs land on both sides, and its economics
    # must be stated at average cost over everything actually held -- the
    # average-cost-vs-FIFO ambiguity that produced the withdrawn +$62.66 figure
    # is why the whole-book term is reported separately rather than inferred
    # from the pair.
    resid_pnl = ZERO
    matched_book = ZERO
    for r in traded:
        cu = (r["up_cash"] / r["up_sh"]) if r["up_sh"] > 0 else ZERO
        cd = (r["dn_cash"] / r["dn_sh"]) if r["dn_sh"] > 0 else ZERO
        m = min(r["up_sh"], r["dn_sh"])
        matched_book += m * (ONE - (cu + cd))
        rr = r["residual"]
        if rr != 0:
            basis = abs(rr) * (cu if rr > 0 else cd)
            val = (abs(rr) if ((rr > 0 and r["outcome_up"])
                               or (rr < 0 and not r["outcome_up"]))
                   else ZERO)
            resid_pnl += val - basis
    dec["10_residual_directional_pnl"] = str(round(resid_pnl, 4))
    dec["10b_matched_book_economics_average_cost"] = str(round(matched_book,
                                                               4))
    gross_chk = s(r["gross"] for r in traded)
    dec["10c_reconciliation"] = {
        "matched_book_plus_residual_pnl": str(round(matched_book
                                                    + resid_pnl, 4)),
        "gross_before_fees": str(round(gross_chk, 4)),
        "residual_abs": str(round(abs(matched_book + resid_pnl
                                      - gross_chk), 6)),
    }

    # calibration of the first leg by predicted-edge bucket
    for lo, hi in ((0, 0.01), (0.01, 0.02), (0.02, 0.05), (0.05, 1)):
        g = [f for f in firsts if lo <= float(f["ev"]) < hi]
        if not g:
            continue
        dec["2_first_leg_calibration_by_edge_bucket"].append({
            "edge": "[%.2f,%.2f)" % (lo, hi), "n": len(g),
            "mean_predicted_EV": str(round(s(x["ev"] for x in g) / len(g), 5)),
            "mean_realized_pnl": str(round(s(x["pnl"] for x in g) / len(g), 5)),
            "mean_q": round(sum(x["q"] for x in g) / len(g), 4),
            "win_rate": round(sum(1 for x in g if x["won"]) / len(g), 4)})

    nets = [float(r["net_cent"]) for r in traded]
    econ = {
        "eligible_windows": len(slugs), "traded_windows": len(traded),
        "purchases_total": sum(len(r["purchases"]) for r in traded),
        "purchases_per_traded_window": (
            round(sum(len(r["purchases"]) for r in traded) / len(traded), 2)
            if traded else None),
        "net_per_traded_window_venue_cent": (
            round(sum(nets) / len(nets), 4) if nets else None),
        "median_net": round(statistics.median(nets), 4) if nets else None,
        "positive_pct": (round(100.0 * sum(1 for v in nets if v > 0)
                               / len(nets), 1) if nets else None),
        "worst_window": (min(((round(v, 4), r["slug"])
                              for v, r in zip(nets, traded)),
                             key=lambda x: x[0]) if nets else None),
        "peak_shares_any_side": str(max((max(r["up_sh"], r["dn_sh"])
                                         for r in traded), default=ZERO)),
    }
    out = {"sample": a.label, "calibration": cal, "decomposition": dec,
           "economics_unit_size": econ,
           "sub_dollar_pair_state": pair_study(by, first_leg_t),
           "per_window": [{k: str(v) for k, v in r.items()
                           if k != "purchases"} for r in res]}
    os.makedirs(a.out, exist_ok=True)
    p2 = os.path.join(a.out, "state_conditional_v1_%s.json" % a.label.lower())
    with open(p2, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    with open(os.path.join(a.out, "purchases_%s.json" % a.label.lower()),
              "w") as fh:
        json.dump({r["slug"]: r["purchases"] for r in res}, fh, indent=1)
    print(json.dumps({k: out[k] for k in
                      ("sample", "calibration", "decomposition",
                       "economics_unit_size", "sub_dollar_pair_state")},
                     indent=1, default=str))
    print("\nwrote %s" % p2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
