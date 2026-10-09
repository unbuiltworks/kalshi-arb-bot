"""Items 2, 5, 6 -- inventory path, entry-side controls, volume-matched modes.

ITEM 2. When does profitable matched inventory form, when does losing
residual accumulate, and what does L5 do next? Measured per window on the
actual fill sequence.

ITEM 5. Inventory-control policies, replayed with NO LOOKAHEAD: each policy
sees only state up to the current fill and may only REFUSE a buy. Full
inventory is carried, every window is settled, and fees are handled as
below.

  WHAT IS TESTABLE AND WHAT IS NOT. An ENTRY-side control (decline to buy)
  is faithfully simulable from fills alone: refusing a fill we know happened
  removes it, and nothing counterfactual has to be invented. An EXIT-side
  control (sell the residual, cancel-and-replace) is NOT: it requires a book
  to know whether the exit order would have filled, at what price, and
  whether it would have filled at all. The corpus has a book for 4 days and
  65 of 17,046 markets. So time-based EXIT and selective-completion-by-
  selling are reported as UNTESTABLE here rather than simulated on
  assumptions, and only entry-side analogues are evaluated.

  Policies (all entry-side, all causal):
    BASELINE          what L5 actually did
    RESID_CAP(k)      refuse any buy that would push |residual| above k
    ONE_SIDED_STOP(k) once |residual| > k, refuse further buys on the
                      residual side; the complement is still allowed
                      (the entry-side analogue of selective completion)
    TIME_STOP(t)      refuse any buy with less than t seconds to settlement
    ENTRY_BAND(lo,hi) refuse any buy priced outside [lo,hi]
    COMBINED          ONE_SIDED_STOP(k) + TIME_STOP(t)

ITEM 6. Two-mode comparison with VOLUME AND EXPOSURE MATCHED, so a higher
fill count cannot mechanically favour BOTH. Each single-mode arm is topped
up by resampling its own mode's fills until it matches the BOTH arm's
contract volume in that window, and spend is reported so exposure can be
compared directly.

FEES. Polymarket per-fill fees are absent from the corpus, so all figures
are GROSS and are upper bounds. A fee sensitivity column applies a flat
per-contract charge so the sign of each policy delta can be checked against
a plausible fee.

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
TRAIN_MONTHS = {"2026-03", "2026-04", "2026-05", "2026-06"}
TEST_MONTHS = {"2026-07", "2026-08"}
FEE_PER_CONTRACT = [0.0, 0.001, 0.002]


def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def ci(v, rng, reps=1200, cap=20000):
    v = [x for x in v if x is not None]
    if len(v) < 2:
        return None, None
    k, m = len(v), min(len(v), cap)
    o = sorted(statistics.fmean([v[rng.randrange(k)] for _ in range(m)])
               for _ in range(reps))
    return o[int(reps * .025)], o[int(reps * .975)]


def run_policy(fills, winner, decide, fee=0.0):
    """fills: (leg, px, sz, tts). decide(state, fill) -> bool (accept).
    Full inventory carried; every window settled."""
    up, dn = deque(), deque()
    spend = mq = ms = 0.0
    taken = 0
    qty = 0.0
    for leg, px, sz, tts in fills:
        uq = sum(l[0] for l in up)
        dq = sum(l[0] for l in dn)
        matched = min(uq, dq)
        rq = abs(uq - dq)
        rside = "UP" if uq > dq else ("DOWN" if dq > uq else "")
        st = {"resid_qty": rq, "resid_side": rside, "matched_held": matched,
              "tts": tts, "price": px, "size": sz, "leg": leg}
        if not decide(st):
            continue
        taken += 1
        qty += sz
        (up if leg == "UP" else dn).append([sz, px])
        spend += sz * px + fee * sz
        pair = min(sum(l[0] for l in up), sum(l[0] for l in dn))
        if pair > EPS:
            for q in (up, dn):
                need = pair
                while need > EPS:
                    lot = q[0]
                    use = min(lot[0], need)
                    ms += use * lot[1]
                    lot[0] -= use
                    need -= use
                    if lot[0] <= EPS:
                        q.popleft()
            mq += pair
    uq = sum(l[0] for l in up)
    dq = sum(l[0] for l in dn)
    rq = uq if uq > EPS else dq
    rside = "up" if uq > EPS else ("down" if dq > EPS else "")
    won = bool(rside and winner and rside == winner.lower())
    return {"pnl": mq + (rq if won else 0.0) - spend, "fills": taken,
            "qty": qty, "spend": spend, "matched": mq, "resid": rq}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rng = random.Random(SEED)

    wins = collections.defaultdict(list)
    for p in sorted(glob.glob(os.path.join(
            a.dataset, "fills", "l5_fills_enriched_part*.csv.gz"))):
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                wins[r["event_slug"]].append(r)

    R = {"fees": {"gross": True,
                  "why": "Polymarket per-fill fees are absent from the "
                         "corpus; a flat per-contract sensitivity is "
                         "applied instead.",
                  "sensitivity_per_contract": FEE_PER_CONTRACT},
         "exit_side_controls": {
             "TESTABLE": False,
             "why": "Selling the residual, or cancel-and-replace, needs a "
                    "book to know whether the exit order fills, at what "
                    "price, and whether it fills at all. The corpus has a "
                    "book for 4 days and 65 of 17,046 markets. Simulating "
                    "an exit policy on the remaining 99.6% would require "
                    "inventing fills. Only ENTRY-side controls are "
                    "evaluated below."}}

    # ---------------- ITEM 2: inventory path timing ----------------
    frac_matched_peak, frac_resid_start, after = [], [], collections.Counter()
    for slug, rows in wins.items():
        rows.sort(key=lambda r: int(r["trade_seq"]))
        dur = f(rows[0]["duration_minutes"], 15.0) or 15.0
        W = dur * 60.0
        best_m, best_i = 0.0, None
        first_big_resid = None
        for i, r in enumerate(rows):
            m = f(r["matched_qty"], 0.0) or 0.0
            rq = f(r["residual_qty"], 0.0) or 0.0
            if m > best_m + EPS:
                best_m, best_i = m, i
            if first_big_resid is None and rq > 0 and m > EPS and rq > m:
                first_big_resid = i
        sfs = lambda i: f(rows[i]["seconds_from_window_start"], 0.0) or 0.0
        if best_i is not None:
            frac_matched_peak.append(min(sfs(best_i) / W, 2.0))
        if first_big_resid is not None:
            frac_resid_start.append(min(sfs(first_big_resid) / W, 2.0))
            # what does L5 do immediately after residual first exceeds matched?
            j = first_big_resid
            nxt = rows[j + 1] if j + 1 < len(rows) else None
            if nxt is None:
                after["STOPPED"] += 1
            else:
                rs = (rows[j]["residual_side"] or "").upper()
                after["ADDED_TO_RESIDUAL_SIDE"
                      if nxt["leg"] == rs else "BOUGHT_THE_COMPLEMENT"] += 1
    tot_after = sum(after.values())
    R["item2_inventory_path"] = {
        "median_fraction_of_window_at_matched_PEAK":
            round(statistics.median(frac_matched_peak), 4)
            if frac_matched_peak else None,
        "median_fraction_of_window_when_residual_FIRST_EXCEEDS_matched":
            round(statistics.median(frac_resid_start), 4)
            if frac_resid_start else None,
        "n_windows_matched": len(frac_matched_peak),
        "n_windows_resid_exceeds": len(frac_resid_start),
        "immediately_after_residual_exceeds_matched":
            {k: round(v / tot_after, 4) for k, v in after.items()}
            if tot_after else {},
        "_reading": "fraction of the nominal window elapsed; >1.0 means "
                    "after nominal expiry.",
    }

    # ---------------- ITEM 5: entry-side controls ----------------
    def mk_fills(rows):
        out = []
        for r in rows:
            px, sz = f(r["price"]), f(r["size"])
            if px is None or sz is None:
                continue
            out.append((r["leg"], px, sz,
                        f(r["seconds_to_settlement"], 1e9)))
        return out

    POLICIES = {
        "BASELINE": lambda st: True,
        "RESID_CAP_50": lambda st: not (
            st["resid_side"] == st["leg"] and st["resid_qty"] >= 50),
        "RESID_CAP_200": lambda st: not (
            st["resid_side"] == st["leg"] and st["resid_qty"] >= 200),
        "ONE_SIDED_STOP_50": lambda st: not (
            st["resid_side"] == st["leg"] and st["resid_qty"] > 50),
        "ONE_SIDED_STOP_200": lambda st: not (
            st["resid_side"] == st["leg"] and st["resid_qty"] > 200),
        "TIME_STOP_60": lambda st: st["tts"] >= 60,
        "TIME_STOP_120": lambda st: st["tts"] >= 120,
        "ENTRY_BAND_25_75": lambda st: 0.25 <= st["price"] <= 0.75,
        "COMBINED_STOP50_T60": lambda st: (
            st["tts"] >= 60 and not (
                st["resid_side"] == st["leg"] and st["resid_qty"] > 50)),
    }
    res = {k: {"train": [], "test": []} for k in POLICIES}
    res_fee = {k: {fee: {"test": []} for fee in FEE_PER_CONTRACT}
               for k in POLICIES}
    for slug, rows in wins.items():
        rows.sort(key=lambda r: int(r["trade_seq"]))
        winner = rows[-1]["settled_outcome"]
        if not winner:
            continue
        month = rows[0]["datetime_utc"][:7]
        split = "train" if month in TRAIN_MONTHS else (
            "test" if month in TEST_MONTHS else None)
        if split is None:
            continue
        fl = mk_fills(rows)
        for name, dec in POLICIES.items():
            res[name][split].append(run_policy(fl, winner, dec)["pnl"])
            if split == "test":
                for fee in FEE_PER_CONTRACT:
                    res_fee[name][fee]["test"].append(
                        run_policy(fl, winner, dec, fee)["pnl"])
    R["item5_entry_side_controls"] = {
        "design": "no lookahead; each policy sees only state up to the "
                  "current fill and may only REFUSE a buy. Full inventory "
                  "carried, every window settled.",
        "train_months": sorted(TRAIN_MONTHS),
        "test_months": sorted(TEST_MONTHS),
        "by_policy": {},
    }
    base_test = None
    for name in POLICIES:
        tr, te = res[name]["train"], res[name]["test"]
        tlo, thi = ci(te, rng)
        row = {"train_mean": round(statistics.fmean(tr), 4) if tr else None,
               "train_total": round(sum(tr), 2),
               "OOS_test_mean": round(statistics.fmean(te), 4) if te else None,
               "OOS_test_ci95": [None if tlo is None else round(tlo, 4),
                                 None if thi is None else round(thi, 4)],
               "OOS_test_total": round(sum(te), 2),
               "fee_sensitivity_OOS_total": {
                   str(fee): round(sum(res_fee[name][fee]["test"]), 2)
                   for fee in FEE_PER_CONTRACT}}
        if name == "BASELINE":
            base_test = te
        R["item5_entry_side_controls"]["by_policy"][name] = row
    for name in POLICIES:
        if name == "BASELINE" or base_test is None:
            continue
        d = [x - y for x, y in zip(res[name]["test"], base_test)]
        lo, hi = ci(d, rng)
        R["item5_entry_side_controls"]["by_policy"][name]["OOS_delta_vs_baseline"] = {
            "mean": round(statistics.fmean(d), 4),
            "ci95": [None if lo is None else round(lo, 4),
                     None if hi is None else round(hi, 4)],
            "verdict": ("IMPROVES" if lo and lo > 0 else
                        ("HURTS" if hi and hi < 0 else "INDETERMINATE"))}

    # ---------------- ITEM 6: volume-matched two-mode ----------------
    SZC, TTC = 15, 300
    arms = {"BOTH": [], "EARLY_PASSIVE_matched": [],
            "LATE_AGGRESSIVE_matched": []}
    expo = {k: [] for k in arms}
    for slug, rows in wins.items():
        rows.sort(key=lambda r: int(r["trade_seq"]))
        winner = rows[-1]["settled_outcome"]
        if not winner:
            continue
        fl = []
        for r in rows:
            px, sz = f(r["price"]), f(r["size"])
            if px is None or sz is None:
                continue
            tts = f(r["seconds_to_settlement"], 1e9)
            tag = "LATE" if (sz >= SZC and tts < TTC) else "EARLY"
            fl.append((r["leg"], px, sz, tts, tag))
        if not fl:
            continue
        target_qty = sum(x[2] for x in fl)
        both = run_policy([(l, p, s, t) for l, p, s, t, _ in fl], winner,
                          lambda st: True)
        arms["BOTH"].append(both["pnl"])
        expo["BOTH"].append(both["spend"])
        for tag, nm in (("EARLY", "EARLY_PASSIVE_matched"),
                        ("LATE", "LATE_AGGRESSIVE_matched")):
            sub = [(l, p, s, t) for l, p, s, t, g in fl if g == tag]
            if not sub:
                arms[nm].append(0.0)
                expo[nm].append(0.0)
                continue
            # VOLUME MATCH: repeat this mode's own fills, in order, until
            # the arm's contract volume reaches the BOTH arm's volume.
            grown, q, i = [], 0.0, 0
            while q < target_qty - EPS and len(grown) < 20000:
                leg, p, s, t = sub[i % len(sub)]
                take = min(s, target_qty - q)
                grown.append((leg, p, take, t))
                q += take
                i += 1
            out = run_policy(grown, winner, lambda st: True)
            arms[nm].append(out["pnl"])
            expo[nm].append(out["spend"])
    R["item6_volume_matched_modes"] = {
        "design": "each single-mode arm is grown by repeating its OWN fills "
                  "in order until its contract volume equals the BOTH arm's, "
                  "so a higher fill count cannot mechanically favour BOTH. "
                  "Spend is reported so exposure is comparable.",
        "mode_proxy": f"size >= {SZC} AND seconds_to_settlement < {TTC}",
        "by_arm": {},
    }
    for nm, v in arms.items():
        lo, hi = ci(v, rng)
        R["item6_volume_matched_modes"]["by_arm"][nm] = {
            "windows": len(v),
            "mean_pnl": round(statistics.fmean(v), 4),
            "ci95": [None if lo is None else round(lo, 4),
                     None if hi is None else round(hi, 4)],
            "total_pnl": round(sum(v), 2),
            "mean_spend_exposure": round(statistics.fmean(expo[nm]), 2),
        }
    b = R["item6_volume_matched_modes"]["by_arm"]["BOTH"]
    e = R["item6_volume_matched_modes"]["by_arm"]["EARLY_PASSIVE_matched"]
    la = R["item6_volume_matched_modes"]["by_arm"]["LATE_AGGRESSIVE_matched"]
    R["item6_volume_matched_modes"]["VERDICT"] = (
        "BOTH still beats each single mode at matched volume"
        if (b["ci95"][0] is not None and b["ci95"][0] > (e["ci95"][1] or 9e9)
            and b["ci95"][0] > (la["ci95"][1] or 9e9))
        else "BOTH does NOT beat both single modes once volume is matched -- "
             "the earlier result was the fill-count artefact")
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    print(json.dumps(R, indent=1)[:4500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
