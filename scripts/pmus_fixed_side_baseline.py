"""Fixed-side directional baselines on the OOS tape. Diagnostic only.

THE SIMPLEST POSSIBLE THING. At ONE predeclared causal entry point per window,
buy exactly 1 unit and hold to settlement. No EXTEND, no complement, no SELL, no
hedge, no resize, no skipped window, no model, no threshold.

THE ENTRY POINT IS INHERITED, NOT CHOSEN. It is the first second of the window at
which `pmus_path_economics.eligible` passes -- both sides quoted with displayed
size -- which is exactly the entry the completed sequential study already used.
No entry time was searched here and none could be: the rule is a single
pre-existing predicate evaluated forward in time. The realized distribution of
entry `seconds_remaining` is reported so that claim is checkable rather than
asserted.

THREE BASELINES, all mechanical:
  ALWAYS_UP     buy UP every window
  ALWAYS_DOWN   buy DOWN every window
  ALTERNATE     UP, DOWN, UP, DOWN ... in chronological window order, with the
                FIRST observed OOS window anchored to UP. The opposite
                orientation is deliberately NOT evaluated, because picking
                between two orientations by result is exactly the selection this
                baseline exists to avoid.

Fees are the venue cent taker fee from `pmus_fees`, the treatment already
established in this repository. Settlement pays 1 on the winning side, 0 on the
other.

The transition matrix is DESCRIPTIVE. No continuation or reversal strategy is
constructed from it.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import io
import json
import os
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_fees as FEES                                 # noqa: E402
from scripts.pmus_path_economics import eligible                  # noqa: E402
from scripts.pmus_favorite_dca import window_start                # noqa: E402

ONE, ZERO = D(1), D(0)


def _open(p):
    if p.endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8", errors="replace")
    return io.open(p, "rt", encoding="utf-8", errors="replace")


def load(path):
    by = collections.defaultdict(list)
    with _open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                   # noqa: BLE001
                continue
            if r.get("slug"):
                by[r["slug"]].append(r)
    for v in by.values():
        v.sort(key=lambda r: r["t"])
    return by


def entry_of(rows):
    """First causally eligible second. The predicate is pre-existing."""
    for r in rows:
        e = eligible(r)
        if e is not None:
            return r, e
    return None, None


def economics(trades):
    """trades: list of dicts with px, fee, won, slug (chronological)."""
    n = len(trades)
    if not n:
        return None
    nets, grosses, fees = [], [], []
    for t in trades:
        payout = ONE if t["won"] else ZERO
        gross = payout - t["px"]
        fee = t["fee"]
        grosses.append(gross)
        fees.append(fee)
        nets.append(gross - fee)
    fnets = [float(x) for x in nets]
    wins = sum(1 for t in trades if t["won"])
    # longest losing streak and max drawdown, in chronological order
    streak = best_streak = 0
    cum = peak = 0.0
    maxdd = 0.0
    for v in fnets:
        if v < 0:
            streak += 1
            best_streak = max(best_streak, streak)
        else:
            streak = 0
        cum += v
        peak = max(peak, cum)
        maxdd = max(maxdd, peak - cum)
    worst = min(range(n), key=lambda i: fnets[i])
    return {
        "windows": n,
        "wins": wins, "losses": n - wins,
        "settlement_win_rate": round(wins / n, 4),
        "mean_executable_purchase_price": str(
            round(sum((t["px"] for t in trades), ZERO) / n, 4)),
        "gross_pnl": str(round(sum(grosses, ZERO), 4)),
        "fees_total": str(round(sum(fees, ZERO), 4)),
        "net_pnl": str(round(sum(nets, ZERO), 4)),
        "net_per_window": round(statistics.mean(fnets), 5),
        "median_per_window": round(statistics.median(fnets), 5),
        "positive_window_pct": round(100.0 * sum(1 for v in fnets if v > 0) / n, 2),
        "worst_window": {"net": round(fnets[worst], 4),
                         "slug": trades[worst]["slug"]},
        "longest_losing_streak": best_streak,
        "max_cumulative_drawdown_unit_size": round(maxdd, 4),
        "final_cumulative": round(sum(fnets), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--label", default="OOS")
    ap.add_argument("--out", default="analysis/l5/path_economics")
    a = ap.parse_args()

    by = load(a.state)
    # CHRONOLOGICAL order, from the slug's own window start
    slugs = sorted(by, key=window_start)

    rows = []
    skipped = []
    for slug in slugs:
        r, e = entry_of(by[slug])
        if r is None:
            skipped.append(slug)
            continue
        up_px, dn_px, _uq, _dq = e
        oc = by[slug][0].get("outcome_up")
        if oc is None:
            skipped.append(slug)
            continue
        rows.append({
            "slug": slug, "outcome_up": oc,
            "entry_seconds_remaining": r["seconds_remaining"],
            "up_px": up_px, "dn_px": dn_px,
            "up_fee": FEES.taker_fee(up_px, ONE, cents=True),
            "dn_fee": FEES.taker_fee(dn_px, ONE, cents=True)})

    def trades_for(side_of):
        out = []
        for i, x in enumerate(rows):
            side = side_of(i)
            px = x["up_px"] if side == "UP" else x["dn_px"]
            fee = x["up_fee"] if side == "UP" else x["dn_fee"]
            won = (x["outcome_up"] == 1) if side == "UP" else (x["outcome_up"] == 0)
            out.append({"slug": x["slug"], "side": side, "px": px,
                        "fee": fee, "won": won})
        return out

    base = {
        "ALWAYS_UP": economics(trades_for(lambda i: "UP")),
        "ALWAYS_DOWN": economics(trades_for(lambda i: "DOWN")),
        "ALTERNATE_anchored_UP": economics(
            trades_for(lambda i: "UP" if i % 2 == 0 else "DOWN")),
    }

    # descriptive transition matrix on chronological settlement outcomes
    tm = collections.Counter()
    for a_, b_ in zip(rows, rows[1:]):
        tm[("UP" if a_["outcome_up"] == 1 else "DOWN",
            "UP" if b_["outcome_up"] == 1 else "DOWN")] += 1
    up_prev = tm[("UP", "UP")] + tm[("UP", "DOWN")]
    dn_prev = tm[("DOWN", "UP")] + tm[("DOWN", "DOWN")]
    ent = [x["entry_seconds_remaining"] for x in rows]
    out = {
        "sample": a.label,
        "windows_used": len(rows),
        "windows_skipped_no_eligible_second_or_no_outcome": len(skipped),
        "entry_rule": ("first second where pmus_path_economics.eligible passes "
                       "-- the predicate the completed sequential study used; "
                       "no entry time was searched"),
        "entry_seconds_remaining": {
            "min": min(ent) if ent else None, "max": max(ent) if ent else None,
            "median": statistics.median(ent) if ent else None,
            "distinct_values": len(set(ent))},
        "fee_treatment": "pmus_fees.taker_fee, venue cent, 1 unit",
        "baselines": base,
        "settlement_base_rate_up": round(
            sum(1 for x in rows if x["outcome_up"] == 1) / len(rows), 4)
        if rows else None,
        "transition_matrix_DESCRIPTIVE_ONLY": {
            "UP_to_UP": tm[("UP", "UP")], "UP_to_DOWN": tm[("UP", "DOWN")],
            "DOWN_to_UP": tm[("DOWN", "UP")],
            "DOWN_to_DOWN": tm[("DOWN", "DOWN")],
            "transitions": sum(tm.values()),
            "P_next_UP_given_prev_UP": (round(tm[("UP", "UP")] / up_prev, 4)
                                        if up_prev else None),
            "P_next_UP_given_prev_DOWN": (round(tm[("DOWN", "UP")] / dn_prev, 4)
                                          if dn_prev else None),
            "n_prev_UP": up_prev, "n_prev_DOWN": dn_prev,
            "note": ("descriptive; no continuation or reversal strategy is "
                     "constructed or optimised from this")},
    }
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "fixed_side_baseline_%s.json" % a.label.lower())
    with open(p, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps(out, indent=1, default=str))
    print("\nwrote %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
