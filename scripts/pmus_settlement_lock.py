"""Does the final-minute settlement lock give a CAUSAL residual-management rule?

DIAGNOSIS, NOT RULE FITTING. Nothing here is swept, chosen or optimised. No exit
time, stop, probability cutoff or hurdle threshold is selected, and the entry and
completion mechanics of the sequential path study are imported unchanged. Bands
are fixed in this module before any number is read.

THE STRUCTURE. The venue settles on the simple average of the 60 one-second BRTI
closes over [T-60, T-1], rounded half-even to the cent (`pmus_brti.settlement_stat`).
So across the final minute the outcome locks progressively: with n of those 60
observations already published and summing to S_fixed, UP needs the remaining
60-n observations to average at least

    R_required = (60*K - S_fixed) / (60 - n)

where K is priceToBeat. The signed displacement from the current index level is
gap = BRTI - R_required, and dividing by the standard deviation of the mean of
those remaining observations gives a scale-free hurdle.

WHAT WE ALREADY HAD, AND IT MATTERS FOR THE locked_frac QUESTION. That hurdle is
NOT a new quantity. `pmus_state_table` already computes
`required_remaining_avg`, `gap_to_required`, `sigma_remaining_avg`, and
`z = gap_to_required / sigma_remaining_avg` -- and **z is one of the three frozen
features of the rejected model**, alongside locked_frac and mom_60s. So the
premise that locked_frac "discarded" the settlement information is only half
right, and this module measures which half.

Part 0 verifies the algebra and the settlement semantics against the established
code rather than trusting either.
Part A asks whether PMUS executable pricing incorporates the already-fixed
observations.
Part B asks whether the residual losses of the completed sequential study are
concentrated in states where the hurdle had already become adverse.
Part C compares locked_frac against z directly.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import io
import json
import math
import os
import statistics
import sys
from decimal import Decimal as D, ROUND_HALF_EVEN

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_brti as BR                                   # noqa: E402
from src import pmus_fees as FEES                                 # noqa: E402
from scripts.pmus_path_economics import phase2, load_windows      # noqa: E402

ONE, ZERO = D(1), D(0)
CENT = D("0.01")
STAT_N = BR.STAT_N

#: fixed before any result is read
Z_BANDS = ((0.0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 5.0),
           (5.0, float("inf")))
LOCKED_BANDS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01))
#: causal checkpoints inside the final minute, by observations already fixed
CHECKPOINTS = (15, 30, 45)


def band_of(v, bands):
    for lo, hi in bands:
        if lo <= v < hi:
            return "[%s,%s)" % (lo, hi)
    return "out"


def _open(p):
    if p.endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8", errors="replace")
    return io.open(p, "rt", encoding="utf-8", errors="replace")


def sd_final_stat(n_rem, d=0):
    """SD of the FINAL statistic given n_rem observations still to come.

    Derived from the repository's own sigma_remaining_avg: the remaining
    observations contribute n_rem/60 of the average.
    """
    if n_rem <= 0:
        return 0.0
    return (n_rem / float(STAT_N)) * math.sqrt(
        d + (n_rem + 1) * (2 * n_rem + 1) / (6.0 * n_rem))


def verify(rows_by_slug):
    """Part 0. Re-derive the hurdle and the settlement from first principles."""
    worst_req = worst_z = 0.0
    n_checked = 0
    npub_mismatch = 0
    recon_ok = recon_tot = 0
    tie_rows = 0
    for slug, rows in rows_by_slug.items():
        we = None
        closes = {}
        for r in rows:
            if r.get("brti_now") is not None:
                closes[int(r["t"])] = D(str(r["brti_now"]))
            if r.get("seconds_remaining") is not None:
                we = int(r["t"]) + int(r["seconds_remaining"])
        for r in rows:
            try:
                n = int(r["n_published"])
                cum = D(str(r["settle_cum_sum"]))
                K = D(str(r["price_to_beat"]))
                brti = D(str(r["brti_now"]))
                sra = float(r["sigma_remaining_avg"])
            except Exception:                                   # noqa: BLE001
                continue
            n_rem = STAT_N - n
            if n_rem <= 0 or sra <= 0:
                continue
            req = (D(STAT_N) * K - cum) / n_rem
            worst_req = max(worst_req,
                            abs(float(req - D(str(r["required_remaining_avg"])))))
            z = float(brti - req) / sra
            worst_z = max(worst_z, abs(z - float(r["z"])))
            n_checked += 1
            if we is not None:
                exp = BR.settlement_minute_published(we, int(r["t"]))
                if exp != n:
                    npub_mismatch += 1
        # reconstruct the venue statistic from the per-second closes we have
        if we is not None:
            sel = [closes[s] for s in range(we + BR.STAT_LO, we + BR.STAT_HI + 1)
                   if s in closes]
            if len(sel) == STAT_N:
                stat = (sum(sel, ZERO) / STAT_N).quantize(
                    CENT, rounding=ROUND_HALF_EVEN)
                K = D(str(rows[0]["price_to_beat"]))
                oc = rows[0].get("outcome_up")
                if oc is not None:
                    recon_tot += 1
                    # venue convention under test: UP iff stat > K
                    if int(stat > K) == int(oc):
                        recon_ok += 1
                    if stat == K:
                        tie_rows += 1
    return {
        "rows_checked": n_checked,
        "max_abs_error_required_remaining_avg": round(worst_req, 10),
        "max_abs_error_z": round(worst_z, 10),
        "n_published_mismatches_vs_pmus_brti": npub_mismatch,
        "settlement_reconstruction": {
            "windows_with_all_60_closes": recon_tot,
            "agree_with_venue_outcome_under_stat_gt_K": recon_ok,
            "agreement_pct": (round(100.0 * recon_ok / recon_tot, 4)
                              if recon_tot else None),
            "exact_ties_stat_equals_K": tie_rows},
        "final_statistic_sd_in_sigma_units": {
            "%ds_remaining" % n: round(sd_final_stat(n), 4)
            for n in (60, 45, 30, 15, 10, 5, 1)},
        "external_claim_check": {
            "claim_1_3_at_30s_remaining": "1/%.2f" % (
                sd_final_stat(60) / sd_final_stat(30)),
            "claim_1_7_at_15s_remaining": "1/%.2f" % (
                sd_final_stat(60) / sd_final_stat(15)),
            "reading": ("both reproduce from the repository's own "
                        "sigma_remaining_avg, so they restate established "
                        "algebra rather than adding information; tau^3 and the "
                        "60x activity claim are not used anywhere here")},
    }


def part_a(rows_by_slug):
    """Does executable pricing incorporate the already-fixed observations?"""
    agg = {}
    for slug, rows in rows_by_slug.items():
        for r in rows:
            try:
                n = int(r["n_published"])
                z = float(r["z"])
                up = D(str(r["up_ask"]))
                dn = D(str(r["down_px"]))
                uq = D(str(r["up_ask_qty"]))
                dq = D(str(r["down_qty"]))
                oc = r.get("outcome_up")
            except Exception:                                   # noqa: BLE001
                continue
            if n < 1 or oc is None:
                continue        # final minute only
            if up <= 0 or dn <= 0:
                continue
            # the hurdle disfavours DOWN when z > 0 (index already above the
            # required remaining average) and UP when z < 0
            dis_side = "DOWN" if z > 0 else "UP"
            dis_px = dn if dis_side == "DOWN" else up
            dis_dep = dq if dis_side == "DOWN" else uq
            dis_won = (oc == 0) if dis_side == "DOWN" else (oc == 1)
            b = band_of(abs(z), Z_BANDS)
            fav_side = "UP" if z > 0 else "DOWN"
            fav_px = up if fav_side == "UP" else dn
            fav_dep = uq if fav_side == "UP" else dq
            fav_won = (oc == 1) if fav_side == "UP" else (oc == 0)
            d = agg.setdefault(b, {"n": 0, "px": [], "won": 0, "dep": [],
                                   "npub": [], "fpx": [], "fwon": 0,
                                   "fdep": [], "ftake": []})
            d["n"] += 1
            d["px"].append(float(dis_px))
            d["dep"].append(float(dis_dep))
            d["npub"].append(n)
            if dis_won:
                d["won"] += 1
            d["fpx"].append(float(fav_px))
            d["fdep"].append(float(fav_dep))
            if fav_won:
                d["fwon"] += 1
            # IS THE MISPRICING TAKEABLE? buy the hurdle-favoured side as a
            # taker at its displayed ask and hold to settlement.
            fee = FEES.taker_fee(fav_px, ONE, cents=True)
            d["ftake"].append(float((ONE if fav_won else ZERO) - fav_px - fee))
    out = {}
    for b, d in agg.items():
        implied = statistics.mean(d["px"])
        realized = d["won"] / d["n"]
        out[b] = {
            "n_rows": d["n"],
            "median_n_fixed": statistics.median(d["npub"]),
            "mean_price_of_hurdle_disfavoured_side": round(implied, 5),
            "median_price_of_hurdle_disfavoured_side": round(
                statistics.median(d["px"]), 5),
            "realized_win_rate_of_that_side": round(realized, 5),
            "price_minus_realized": round(implied - realized, 5),
            "median_touch_depth_on_that_side": round(
                statistics.median(d["dep"]), 1),
            "hurdle_FAVOURED_side": {
                "median_price": round(statistics.median(d["fpx"]), 5),
                "realized_win_rate": round(d["fwon"] / d["n"], 5),
                "median_touch_depth": round(statistics.median(d["fdep"]), 1),
                "TAKEABLE_mean_pnl_buy_and_hold_to_settlement": round(
                    statistics.mean(d["ftake"]), 5),
                "pct_profitable": round(
                    100.0 * sum(1 for v in d["ftake"] if v > 0)
                    / len(d["ftake"]), 2)},
        }
    return {"bands_fixed_in_advance": [band_of(lo, Z_BANDS)
                                       for lo, _ in Z_BANDS],
            "by_abs_z": dict(sorted(out.items())),
            "reading": ("price_minus_realized > 0 means the disfavoured side "
                        "was priced above the frequency with which it actually "
                        "won, i.e. the market still implied uncertainty the "
                        "already-fixed observations had removed")}


def part_b(rows_by_slug, ph2):
    """How does residual P&L vary with the causally known hurdle?"""
    state = {}
    for slug, rows in rows_by_slug.items():
        per_n = {}
        for r in rows:
            try:
                n = int(r["n_published"])
            except Exception:                                   # noqa: BLE001
                continue
            if n in CHECKPOINTS and n not in per_n:
                per_n[n] = r
        state[slug] = per_n

    out = {}
    for cp in CHECKPOINTS:
        rows_cp = []
        for w in ph2["per_window"]:
            lots = w.get("residual_lots") or []
            if not lots:
                continue
            r = state.get(w["slug"], {}).get(cp)
            if r is None:
                continue
            try:
                z = float(r["z"])
                lf = float(r["locked_frac"])
            except Exception:                                   # noqa: BLE001
                continue
            oc = w["outcome_up"]
            for lot in lots:
                q = D(lot["qty"])
                cost = D(lot["cost"])
                won = ((oc == 1) if lot["side"] == "UP" else (oc == 0))
                pnl = float((q if won else ZERO) - cost)
                # was the hurdle already AGAINST this lot at the checkpoint?
                favoured = ("UP" if z > 0 else "DOWN")
                rows_cp.append({
                    "pnl": pnl, "z": z, "absz": abs(z), "lf": lf,
                    "adverse": lot["side"] != favoured,
                    "qty": float(q), "won": won})
        def summar(g):
            if not g:
                return None
            p = [x["pnl"] for x in g]
            return {"n_lots": len(g),
                    "total_pnl": round(sum(p), 4),
                    "mean_pnl": round(statistics.mean(p), 5),
                    "median_pnl": round(statistics.median(p), 5),
                    "pct_losing": round(100.0 * sum(1 for v in p if v < 0)
                                        / len(g), 2),
                    "win_rate_of_lot": round(100.0 * sum(1 for x in g
                                                         if x["won"]) / len(g), 2)}
        byband = {}
        for lo, hi in Z_BANDS:
            b = band_of(lo, Z_BANDS)
            g = [x for x in rows_cp if band_of(x["absz"], Z_BANDS) == b]
            if g:
                byband[b] = {"all": summar(g),
                             "hurdle_ADVERSE_to_the_lot": summar(
                                 [x for x in g if x["adverse"]]),
                             "hurdle_favourable_to_the_lot": summar(
                                 [x for x in g if not x["adverse"]])}
        out["n_fixed_%d" % cp] = {
            "seconds_remaining": STAT_N - cp,
            "all_residual_lots": summar(rows_cp),
            "adverse_vs_favourable": {
                "hurdle_ADVERSE_to_the_lot": summar(
                    [x for x in rows_cp if x["adverse"]]),
                "hurdle_favourable_to_the_lot": summar(
                    [x for x in rows_cp if not x["adverse"]])},
            "by_abs_z": byband,
        }
    return out


def part_c(rows_by_slug, ph2):
    """locked_frac against z, on the same residual lots."""
    state = collections.defaultdict(dict)
    for slug, rows in rows_by_slug.items():
        for r in rows:
            try:
                n = int(r["n_published"])
            except Exception:                                   # noqa: BLE001
                continue
            if n == 30 and 30 not in state[slug]:
                state[slug][30] = r
    lots = []
    for w in ph2["per_window"]:
        r = state.get(w["slug"], {}).get(30)
        if r is None:
            continue
        for lot in (w.get("residual_lots") or []):
            oc = w["outcome_up"]
            won = ((oc == 1) if lot["side"] == "UP" else (oc == 0))
            lots.append({
                "pnl": float((D(lot["qty"]) if won else ZERO) - D(lot["cost"])),
                "absz": abs(float(r["z"])), "lf": float(r["locked_frac"])})

    def spread(key, bands):
        g = collections.defaultdict(list)
        for x in lots:
            g[band_of(x[key], bands)].append(x["pnl"])
        t = {k: {"n": len(v), "mean_pnl": round(statistics.mean(v), 5)}
             for k, v in sorted(g.items())}
        means = [v["mean_pnl"] for v in t.values()]
        return {"table": t,
                "separation_range_of_mean_pnl": (round(max(means) - min(means), 5)
                                                 if means else None)}
    return {
        "checkpoint": "n_fixed = 30 (30 seconds remaining)",
        "by_locked_frac": spread("lf", LOCKED_BANDS),
        "by_abs_z": spread("absz", Z_BANDS),
        "note_locked_frac_is_degenerate_at_a_checkpoint": (
            "locked_frac is n_fixed/60 by definition, so at a FIXED checkpoint "
            "it is a constant and cannot separate anything. That is the point: "
            "it encodes only how much of the minute is fixed, never what was "
            "fixed. z encodes what the remaining observations must achieve."),
        "note_z_was_already_frozen_in": (
            "z is one of the three features of the rejected model "
            "(pmus_state_table.FEATURES = z, locked_frac, mom_60s), so this "
            "information was NOT absent from it."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--label", default="OOS")
    ap.add_argument("--out", default="analysis/l5/path_economics")
    a = ap.parse_args()

    rows_by_slug = collections.defaultdict(list)
    with _open(a.state) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                   # noqa: BLE001
                continue
            if r.get("slug"):
                rows_by_slug[r["slug"]].append(r)
    for v in rows_by_slug.values():
        v.sort(key=lambda r: r["t"])

    # the FIXED mechanics of the completed study, imported unchanged
    by = load_windows(a.state)
    ph2 = phase2(by, "UP")

    out = {
        "sample": a.label,
        "windows": len(rows_by_slug),
        "mechanics": ("entry and completion imported unchanged from "
                      "pmus_path_economics.phase2(first_side=UP)"),
        "nothing_swept": ("no exit time, stop, probability cutoff or hurdle "
                          "threshold is selected; all bands are fixed in the "
                          "module before any result is read"),
        "part0_verification": verify(rows_by_slug),
        "partA_market_pricing": part_a(rows_by_slug),
        "partB_residual_vs_hurdle": part_b(rows_by_slug, ph2),
        "partC_locked_frac_vs_z": part_c(rows_by_slug, ph2),
        "residual_totals_from_fixed_study": {
            "residual_pnl_total": ph2["residual_pnl_total"],
            "locked_net_total": ph2["locked_net_total"],
            "TOTAL_NET": ph2["TOTAL_NET"]},
    }
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "settlement_lock_%s.json" % a.label.lower())
    with open(p, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps(out, indent=1, default=str))
    print("\nwrote %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
