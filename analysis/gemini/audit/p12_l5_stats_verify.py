"""P12: independently recompute the L5 trajectory statistics now being cited.

    python3 analysis/gemini/audit/p12_l5_stats_verify.py

Reads the SAME raw artifacts and implements the inventory walk, the episode
segmentation and every statistic ITSELF. It imports `iter_activity` and
`window_of` -- the raw reader and the slug parser -- because re-implementing
the multiset dedupe would risk a different bug in I/O rather than verify the
analysis. It imports NOTHING from `residual_exit.forensics`, which is the code
under audit.

The question it exists to answer: two flat-window rates are in circulation,
23 of 2,216 (1.04%) and 1 of 5,546 (0.018%), and they are 58x apart. Are they
different measurements or is one wrong?
"""
from __future__ import annotations

import collections
import csv
import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from analysis.l5.redemption import audit as A                  # noqa: E402

TRADES_CSV = os.path.join(ROOT, "analysis", "l5", "l5_all_trades.csv")
CANON_GZ = os.path.join(ROOT, "analysis", "l5",
                        "l5_canonical_sequence.csv.gz")


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------- POPULATION A: the CSV tape

def population_a():
    """l5_all_trades.csv: windows, fills, terminal residual, zero-residual."""
    per = collections.defaultdict(lambda: {"up": 0.0, "dn": 0.0, "n": 0,
                                           "asset": None, "mins": None})
    sides = collections.Counter()
    with open(TRADES_CSV, newline="") as fh:
        for row in csv.DictReader(fh):
            wid = row["window_id"]
            d = per[wid]
            d["n"] += 1
            d["asset"] = row["asset"]
            d["mins"] = row["window_minutes"]
            sh = _f(row["shares"])
            side = (row["side"] or "").upper()
            sides[side] += 1
            sgn = 1.0 if side == "BUY" else -1.0
            if str(row["outcome"]).strip().lower() in ("up", "yes"):
                d["up"] += sgn * sh
            else:
                d["dn"] += sgn * sh
    res = {w: (d["up"] - d["dn"]) for w, d in per.items()}
    zero = [w for w, r in res.items() if r == 0.0]
    #: the published definition is "zero residual"; a tolerance variant is
    #: reported beside it so the claim cannot rest on exact float equality
    near = [w for w, r in res.items() if abs(r) < 1e-9]
    tol1 = [w for w, r in res.items() if abs(r) <= 1.0]
    return {
        "windows": len(per),
        "fills": sum(d["n"] for d in per.values()),
        "sides": dict(sides),
        "zero_residual_exact": len(zero),
        "zero_residual_1e-9": len(near),
        "abs_residual_le_1_share": len(tol1),
        "by_asset": dict(collections.Counter(d["asset"]
                                             for d in per.values())),
        "by_minutes": dict(collections.Counter(str(d["mins"])
                                               for d in per.values())),
    }


# ------------------------------------- POPULATION B: the activity cache walk

def population_b(cohort="btc15m"):
    """My own BTC15m walk: windows, fills, episodes, exits, carried share."""
    win = collections.defaultdict(lambda: {"fills": [], "end": 0})
    nonbuy = 0
    for r in A.iter_activity():
        w = A.window_of(r)
        if not w:
            continue
        if ("%s%s" % (w[0], w[1])).lower() != cohort:
            continue
        if str(r.get("type") or "").upper() != "TRADE":
            continue
        cid = str(r.get("conditionId") or "")
        if not cid:
            continue
        side = str(r.get("side") or "").upper()
        px, sz = _f(r.get("price")), _f(r.get("size"))
        if side != "BUY" or px <= 0 or sz <= 0:
            nonbuy += 1
            continue
        d = win[cid]
        d["end"] = max(d["end"], w[2])
        d["fills"].append((int(_f(r.get("timestamp"))), px,
                           str(r.get("outcome")), sz))

    exits = collections.Counter()
    monthly = collections.defaultdict(lambda: collections.Counter())
    n_eps = 0
    flat_windows = 0
    carried_share = []
    runs = []
    same_side_actions = 0
    extend_holding_ge2 = 0
    peak_imb_units = []
    import datetime as dt

    for cid, d in win.items():
        fills = sorted(d["fills"], key=lambda x: x[0])
        if not fills:
            continue
        up = dn = 0.0
        vol = sum(f[3] for f in fills)
        open_res = None           # residual at the moment the episode opened
        cur_run_side, cur_run_len = None, 0
        peak = 0.0
        clips = sorted(f[3] for f in fills)
        unit = clips[len(clips) // 2] if clips else 1.0
        for t, px, out, sz in fills:
            before = up - dn
            if str(out) == "Up":
                up += sz
            else:
                dn += sz
            after = up - dn
            peak = max(peak, abs(after))
            # run bookkeeping
            side = "Up" if str(out) == "Up" else "Down"
            if side == cur_run_side:
                cur_run_len += 1
            else:
                if cur_run_len:
                    runs.append(cur_run_len)
                cur_run_side, cur_run_len = side, 1
            widening = (before > 0 and side == "Up") or (before < 0
                                                         and side == "Down")
            if before != 0 and widening:
                same_side_actions += 1
                if abs(before) >= 2 * unit:
                    extend_holding_ge2 += 1
            # episode bookkeeping
            if before == 0 and after != 0:
                n_eps += 1
                open_res = after
            elif before != 0 and after == 0:
                exits["COMPLETED_FLAT"] += 1
                mon = dt.datetime.utcfromtimestamp(t).strftime("%Y-%m")
                monthly[mon]["COMPLETED_FLAT"] += 1
                monthly[mon]["_total"] += 1
                open_res = None
            elif before != 0 and after != 0 and (before > 0) != (after > 0):
                exits["FLIPPED"] += 1
                mon = dt.datetime.utcfromtimestamp(t).strftime("%Y-%m")
                monthly[mon]["FLIPPED"] += 1
                monthly[mon]["_total"] += 1
                n_eps += 1        # a switch ends one episode and opens the next
        if cur_run_len:
            runs.append(cur_run_len)
        final = up - dn
        if final == 0:
            flat_windows += 1
        else:
            exits["CARRIED_TO_SETTLEMENT"] += 1
            mon = dt.datetime.utcfromtimestamp(fills[-1][0]).strftime("%Y-%m")
            monthly[mon]["CARRIED_TO_SETTLEMENT"] += 1
            monthly[mon]["_total"] += 1
            if vol > 0:
                carried_share.append(abs(final) / vol)
        if unit > 0:
            peak_imb_units.append(peak / unit)

    def q(xs, p):
        if not xs:
            return None
        s = sorted(xs)
        return s[min(len(s) - 1, int(p * len(s)))]

    tot_exits = sum(exits.values())
    return {
        "windows": len(win),
        "fills": sum(len(d["fills"]) for d in win.values()),
        "nonbuy_rows_skipped": nonbuy,
        "episodes_opened": n_eps,
        "exits": dict(exits),
        "exit_shares": {k: v / tot_exits for k, v in exits.items()}
        if tot_exits else {},
        "windows_ending_flat": flat_windows,
        "carried_residual_over_window_volume_p50": q(carried_share, 0.5),
        "carried_residual_over_window_volume_p90": q(carried_share, 0.9),
        "same_side_actions": same_side_actions,
        "extend_while_holding_ge2_units": extend_holding_ge2,
        "extend_while_holding_ge2_share": (extend_holding_ge2 / same_side_actions
                                           if same_side_actions else None),
        "peak_imbalance_units_p50": q(peak_imb_units, 0.5),
        "peak_imbalance_units_p75": q(peak_imb_units, 0.75),
        "peak_imbalance_units_share_gt_4": (
            sum(1 for x in peak_imb_units if x > 4) / len(peak_imb_units)
            if peak_imb_units else None),
        "same_side_run_mean": (sum(runs) / len(runs)) if runs else None,
        "same_side_run_p90": q(runs, 0.9),
        "monthly_flat_share": {
            m: (c["COMPLETED_FLAT"] / c["_total"]) if c["_total"] else None
            for m, c in sorted(monthly.items())},
    }


def main():
    print("POPULATION A -- analysis/l5/l5_all_trades.csv")
    a = population_a()
    print(json.dumps(a, indent=2, default=str))
    print("\nPOPULATION B -- data/l5/raw/activity*.jsonl, cohort btc15m")
    b = population_b()
    print(json.dumps(b, indent=2, default=str))


if __name__ == "__main__":
    main()


# ------------- POPULATION A, q-normalized: what live_envelope actually cites

def population_a_envelope():
    """The CAP4 justification, recomputed on the population it NAMES.

    `live_envelope.LIVE_Q1_BTC15M_CAP4` cites "2,216 windows, 35,576 same-side
    actions, q-normalized to each window's own action unit" -- that is the
    canonical CSV tape (Population A), NOT the BTC15m activity cache. Measuring
    it on the cache gives numbers about ten times larger, because the cache
    holds ten times the fills.
    """
    per = collections.defaultdict(list)
    with open(TRADES_CSV, newline="") as fh:
        for row in csv.DictReader(fh):
            per[row["window_id"]].append(
                (_f(row["timestamp"]), str(row["outcome"]).strip().lower(),
                 _f(row["shares"]), (row["side"] or "").upper()))
    same_side = 0
    ext_ge2 = 0
    peaks = []
    runs = []
    for wid, fills in per.items():
        fills.sort(key=lambda x: x[0])
        clips = sorted(f[2] for f in fills if f[2] > 0)
        if not clips:
            continue
        unit = clips[len(clips) // 2]          # the window's own action unit
        up = dn = 0.0
        peak = 0.0
        run_side, run_len = None, 0
        for _t, out, sh, side in fills:
            sgn = 1.0 if side == "BUY" else -1.0
            before = up - dn
            is_up = out in ("up", "yes")
            if is_up:
                up += sgn * sh
            else:
                dn += sgn * sh
            after = up - dn
            peak = max(peak, abs(after))
            s = "Up" if is_up else "Down"
            if s == run_side:
                run_len += 1
            else:
                if run_len:
                    runs.append(run_len)
                run_side, run_len = s, 1
            widening = (before > 0 and is_up) or (before < 0 and not is_up)
            if before != 0 and widening:
                same_side += 1
                if abs(before) >= 2 * unit:
                    ext_ge2 += 1
        if run_len:
            runs.append(run_len)
        peaks.append(peak / unit)

    def q(xs, p):
        s = sorted(xs)
        return s[min(len(s) - 1, int(p * len(s)))] if s else None

    return {
        "windows": len(per),
        "same_side_actions": same_side,
        "extend_while_holding_ge2_units": ext_ge2,
        "extend_while_holding_ge2_share": (ext_ge2 / same_side
                                           if same_side else None),
        "peak_imbalance_units_p50": q(peaks, 0.5),
        "peak_imbalance_units_p75": q(peaks, 0.75),
        "cap_coverage_k2": sum(1 for x in peaks if x <= 2) / len(peaks),
        "cap_coverage_k4": sum(1 for x in peaks if x <= 4) / len(peaks),
        "same_side_run_mean": (sum(runs) / len(runs)) if runs else None,
        "same_side_run_p90": q(runs, 0.9),
    }
