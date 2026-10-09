"""Items 1 and 2 -- Kalshi settlement validation, and equivalent markouts.

ITEM 1 -- SETTLEMENT VALIDATION, AND WHAT IS ACTUALLY POSSIBLE HERE.

There is NO authoritative resolution record available. The tapes carry no
settlement field, and the egress proxy refuses CONNECT to every Kalshi
host, so the authoritative endpoint cannot be reached from this container.
Saying so is the honest answer to "validate against authoritative records";
inventing a validation would be worse than admitting the gap.

What IS possible, and is done here:

  (a) a SECOND, INDEPENDENT proxy -- the final BBO mid before close --
      cross-checked against the last-print proxy, so the two can disagree
      and the disagreement rate is measured rather than assumed;
  (b) a SENSITIVITY SWEEP -- flip a random fraction of outcomes and
      re-settle, to find how wrong the proxy would have to be before the
      negative Kalshi verdict flips. That bounds the conclusion's exposure
      to proxy error without needing the truth.

ITEM 2 -- EQUIVALENT MARKOUT COMPARISON.

Same definition on both venues, so the numbers are comparable:

  markout(h) = (mid at fill_time + h) - fill_price, signed so POSITIVE is
               good for the buyer.

  Polymarket  L5's own fills, matched to the Zenodo 1s ladders.
  Kalshi      the MAKER side of every real print. A print carries
              taker_side, so the maker is the opposite side and the maker's
              fill price is known exactly. These are ACTUAL historical
              fills, not simulated ones.

Horizons 5s / 30s / 60s on both. Fees are reported separately rather than
folded in, because the two venues' schedules differ and mixing them into a
markout would make the comparison meaningless.

Read-only. No network. Nothing armed.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import csv
import glob
import gzip
import json
import math
import os
import random
import statistics
import sys

HORIZONS = [5, 30, 60]
SEED = 20261009
FLIP_FRACTIONS = [0.0, 0.02, 0.05, 0.10, 0.20, 0.30, 0.50]


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


def kalshi_fee(c, p, maker):
    r = 0.0175 if maker else 0.07
    return math.ceil(r * c * p * (1 - p) * 100) / 100.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes", nargs="+", required=True)
    ap.add_argument("--zenodo", nargs="+", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rng = random.Random(SEED)
    R = {}

    # ================= KALSHI TAPE =================
    paths = []
    for g in a.tapes:
        paths.extend(sorted(glob.glob(g)))
    mkt = collections.defaultdict(lambda: {"bbo": [], "prints": []})
    for p in paths:
        with open(p, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                if r.get("venue") != "kalshi":
                    continue
                m, k, t = r.get("market"), r.get("kind"), r.get("t")
                if not m or t is None:
                    continue
                if k == "bbo":
                    yb = r.get("yes_bids") or []
                    nb = r.get("no_bids") or []
                    byb = max((f(x) for x, _ in yb), default=None)
                    bnb = max((f(x) for x, _ in nb), default=None)
                    if byb is None or bnb is None:
                        continue
                    ya = 1 - bnb                      # yes ask
                    mkt[m]["bbo"].append((t, (byb + ya) / 2.0))
                elif k == "print":
                    yp = f(r.get("yes_price"))
                    cnt = f(r.get("count"), 0.0)
                    ts = r.get("taker_side")
                    if yp is None or ts not in ("yes", "no"):
                        continue
                    mkt[m]["prints"].append((t, yp, cnt, ts))
    for m in mkt:
        mkt[m]["bbo"].sort()
        mkt[m]["prints"].sort()

    # ---- item 1a: two independent settlement proxies -----------------
    agree = disagree = only_print = only_bbo = neither = 0
    rows = {}
    for m, d in mkt.items():
        lastp = d["prints"][-1][1] if d["prints"] else None
        lastb = d["bbo"][-1][1] if d["bbo"] else None
        wp = ("YES" if lastp > 0.5 else "NO") if lastp is not None else None
        wb = ("YES" if lastb > 0.5 else "NO") if lastb is not None else None
        rows[m] = (wp, wb, lastp, lastb)
        if wp and wb:
            if wp == wb:
                agree += 1
            else:
                disagree += 1
        elif wp:
            only_print += 1
        elif wb:
            only_bbo += 1
        else:
            neither += 1
    both = agree + disagree
    # how decisive is the final print? distance from 0.50
    marg = [abs(v[2] - 0.5) for v in rows.values() if v[2] is not None]
    R["item1_settlement_validation"] = {
        "AUTHORITATIVE_RECORD_AVAILABLE": False,
        "why": "The tapes carry no settlement field and the container's "
               "egress proxy refuses CONNECT to Kalshi hosts, so the "
               "authoritative resolution endpoint cannot be reached from "
               "here. No authoritative validation was performed, and none "
               "is claimed.",
        "two_independent_proxies": {
            "proxy_A": "last print in the window (yes_price > 0.5 -> YES)",
            "proxy_B": "final BBO mid before close (mid > 0.5 -> YES)",
            "markets_with_both": both,
            "agree": agree, "disagree": disagree,
            "agreement_rate": round(agree / both, 5) if both else None,
            "only_proxy_A": only_print, "only_proxy_B": only_bbo,
            "neither": neither,
        },
        "decisiveness_of_final_print": {
            "_what": "|last yes_price - 0.50|. Near 0.50 means the proxy is "
                     "a coin flip on that market.",
            "median": round(statistics.median(marg), 4) if marg else None,
            "share_within_0.05_of_0.50": round(
                sum(1 for x in marg if x < 0.05) / len(marg), 4)
            if marg else None,
            "share_beyond_0.40_of_0.50": round(
                sum(1 for x in marg if x > 0.40) / len(marg), 4)
            if marg else None,
        },
    }

    # ---- item 1b: sensitivity of the Track B verdict to proxy error ---
    # Re-settle the simplest strategy (AGG at theta=0.25, the best case)
    # under randomly flipped outcomes.
    def replay_agg(m, theta):
        d = mkt[m]
        ya_hit = na_hit = None
        for t, mid in d["bbo"]:
            pass
        # rebuild asks from the raw ladders is unnecessary: use the mid-based
        # proxy only for settlement. For fills we need asks, so re-scan.
        return None

    # Re-run the AGG(0.25) fills using the same logic as Track B, then
    # re-settle under flips. Fills are independent of the outcome, so they
    # only need computing once.
    fills_by_mkt = {}
    for p in paths:
        pass
    # (fills recomputed below from ladders)
    lad = collections.defaultdict(list)
    for p in paths:
        with open(p, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                if r.get("venue") != "kalshi" or r.get("kind") != "bbo":
                    continue
                m, t = r.get("market"), r.get("t")
                yb = r.get("yes_bids") or []
                nb = r.get("no_bids") or []
                byb = max((f(x) for x, _ in yb), default=None)
                bnb = max((f(x) for x, _ in nb), default=None)
                if m and t is not None and byb is not None and bnb is not None:
                    lad[m].append((t, 1 - bnb, 1 - byb))   # yes_ask, no_ask
    THETA = 0.25
    for m, seq in lad.items():
        seq.sort()
        fy = fn = None
        for t, ya, na in seq:
            if fy is None and ya <= THETA:
                fy = ya
            if fn is None and na <= THETA:
                fn = na
        fills_by_mkt[m] = (fy, fn)

    sens = {}
    for frac in FLIP_FRACTIONS:
        pnls = []
        r2 = random.Random(SEED + int(frac * 1000))
        for m, (fy, fn) in fills_by_mkt.items():
            wp = rows.get(m, (None,))[0]
            if wp is None:
                continue
            w = wp
            if r2.random() < frac:
                w = "NO" if w == "YES" else "YES"
            fills = {}
            if fy is not None:
                fills["YES"] = fy
            if fn is not None:
                fills["NO"] = fn
            if not fills:
                pnls.append(0.0)
                continue
            cost = sum(px + kalshi_fee(1, px, False) for px in fills.values())
            proc = 1.0 if len(fills) == 2 else (
                1.0 if next(iter(fills)) == w else 0.0)
            pnls.append(proc - cost)
        lo, hi = ci(pnls, rng)
        sens[f"{frac:.2f}"] = {
            "markets": len(pnls),
            "mean_pnl_per_market": round(statistics.fmean(pnls), 5),
            "ci95": [None if lo is None else round(lo, 5),
                     None if hi is None else round(hi, 5)],
            "verdict": ("NEGATIVE" if hi is not None and hi < 0 else
                        ("POSITIVE" if lo is not None and lo > 0 else
                         "INDETERMINATE")),
        }
    R["item1_sensitivity_to_proxy_error"] = {
        "_what": "AGG(theta=0.25) -- Track B's best configuration -- "
                 "re-settled with a random fraction of outcomes flipped. "
                 "Fills do not depend on the outcome, so only settlement "
                 "changes.",
        "by_flip_fraction": sens,
        "interpretation_rule":
            "The negative verdict is robust up to the largest flip "
            "fraction whose CI is still entirely below zero. A 0.50 flip "
            "fraction is pure noise -- a coin flip for every market -- so "
            "if the verdict survives to there, proxy error cannot be what "
            "is producing it.",
    }

    # ================= ITEM 2: MARKOUTS =================
    # ---- Kalshi: maker side of every real print ----------------------
    kal = {h: [] for h in HORIZONS}
    kal_n = 0
    for m, d in mkt.items():
        if not d["bbo"] or not d["prints"]:
            continue
        bt = [x[0] for x in d["bbo"]]
        bm = [x[1] for x in d["bbo"]]
        for t, yp, cnt, ts in d["prints"]:
            # maker is the opposite of the taker. If taker_side == 'yes'
            # the taker lifted the YES ask, so the MAKER SOLD YES at yp,
            # equivalently BOUGHT NO at 1-yp. If taker_side == 'no' the
            # maker BOUGHT YES at yp.
            if ts == "no":
                mk_px, in_yes = yp, True
            else:
                mk_px, in_yes = 1 - yp, False
            kal_n += 1
            for h in HORIZONS:
                j = bisect.bisect_left(bt, t + h)
                if j >= len(bt):
                    continue
                mid = bm[j]                       # yes-mid
                fut = mid if in_yes else (1 - mid)
                kal[h].append(fut - mk_px)
    R["item2_markout_kalshi_maker_actual_prints"] = {
        "_definition": "(mid at fill+h) - maker fill price, signed so "
                       "positive is good for the maker. Maker side inferred "
                       "from taker_side, which is carried on every print.",
        "prints_used": kal_n,
        "by_horizon_s": {},
    }
    for h in HORIZONS:
        v = kal[h]
        lo, hi = ci(v, rng)
        R["item2_markout_kalshi_maker_actual_prints"]["by_horizon_s"][
            str(h)] = {
            "n": len(v),
            "mean_markout": round(statistics.fmean(v), 6) if v else None,
            "ci95": [None if lo is None else round(lo, 6),
                     None if hi is None else round(hi, 6)],
            "median": round(statistics.median(v), 6) if v else None,
            "share_positive": round(sum(1 for x in v if x > 0) / len(v), 4)
            if v else None,
        }

    # ---- Polymarket: L5's own fills vs the Zenodo ladders -------------
    import pandas as pd
    bk = pd.concat([pd.read_parquet(p) for g in a.zenodo
                    for p in sorted(glob.glob(g))], ignore_index=True)
    bk = bk[bk["best_ask"].notna() & bk["best_bid"].notna() & (~bk["crossed"])]
    bk["ts"] = bk["captured_at"].astype("int64") // 10 ** 6
    idx = {}
    for (s, o), g in bk.groupby(["market_slug", "outcome"]):
        g = g.sort_values("ts")
        idx[(s, o)] = (g["ts"].tolist(),
                       ((g["best_bid"] + g["best_ask"]) / 2).tolist())
    lab = {}
    lp = os.path.join(os.path.dirname(a.out), "A1_fill_labels.csv.gz")
    if os.path.exists(lp):
        for r in csv.DictReader(gzip.open(lp, "rt")):
            lab[(r["event_slug"], r["trade_seq"])] = r["classification"]
    pm = {h: [] for h in HORIZONS}
    pm_by_cls = {h: collections.defaultdict(list) for h in HORIZONS}
    pm_n = 0
    for p in sorted(glob.glob(os.path.join(
            a.dataset, "fills", "l5_fills_enriched_part*.csv.gz"))):
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                k = (r["event_slug"], "Up" if r["leg"] == "UP" else "Down")
                if k not in idx:
                    continue
                ts, mids = idx[k]
                t = int(r["timestamp"])
                px = f(r["price"])
                if px is None:
                    continue
                j0 = bisect.bisect_right(ts, t) - 1
                if j0 < 0 or t - ts[j0] > 30:
                    continue
                pm_n += 1
                c = lab.get((r["event_slug"], r["trade_seq"]), "UNLABELLED")
                for h in HORIZONS:
                    j = bisect.bisect_left(ts, t + h)
                    if j >= len(ts):
                        continue
                    pm[h].append(mids[j] - px)
                    pm_by_cls[h][c].append(mids[j] - px)
    R["item2_markout_polymarket_L5_actual_fills"] = {
        "_definition": "same formula; L5 is always a BUYER, so positive "
                       "markout means the leg it bought richened.",
        "fills_matched": pm_n,
        "by_horizon_s": {},
    }
    for h in HORIZONS:
        v = pm[h]
        lo, hi = ci(v, rng)
        R["item2_markout_polymarket_L5_actual_fills"]["by_horizon_s"][
            str(h)] = {
            "n": len(v),
            "mean_markout": round(statistics.fmean(v), 6) if v else None,
            "ci95": [None if lo is None else round(lo, 6),
                     None if hi is None else round(hi, 6)],
            "median": round(statistics.median(v), 6) if v else None,
            "share_positive": round(sum(1 for x in v if x > 0) / len(v), 4)
            if v else None,
            "by_execution_class": {
                c: {"n": len(x),
                    "mean": round(statistics.fmean(x), 6)} for c, x in
                sorted(pm_by_cls[h].items()) if len(x) >= 50},
        }

    R["item2_fee_treatment"] = {
        "_note": "Fees are NOT folded into the markouts above, because the "
                 "two venues' schedules differ and mixing them would make "
                 "the comparison meaningless. Applied separately:",
        "kalshi_maker_fee_at_p_0.25_1_contract":
            kalshi_fee(1, 0.25, True),
        "kalshi_maker_fee_at_p_0.50_1_contract":
            kalshi_fee(1, 0.50, True),
        "polymarket_fee":
            "no explicit per-fill fee field in the corpus; confirmed only "
            "where a position snapshot carries entryFeesUsdc (a minority), "
            "so the Polymarket side is a fee LOWER bound.",
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    print(json.dumps(R, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
