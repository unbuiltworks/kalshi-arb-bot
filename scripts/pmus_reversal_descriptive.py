"""Previous-window reversal: IN-SAMPLE DESCRIPTIVE ECONOMICS. NOT VALIDATION.

READ THIS FIRST. These 178 windows GENERATED the reversal hypothesis
(P(UP|prev DOWN) 0.5926 against P(UP|prev UP) 0.4896, p = 0.171). They are
permanently retired from validating it. Nothing in this file is evidence that the
effect is real. It measures only how large the effect LOOKS on the sample that
suggested it, which is the quantity needed to decide whether a fresh preregistered
test is worth running at all -- and which is biased upward by construction, because
the sample was chosen by noticing this very pattern in it.

THE RULE, fully fixed, nothing optimised:
    previous window settled UP   -> buy 1 DOWN in the next window
    previous window settled DOWN -> buy 1 UP   in the next window
Entry at the already-fixed first-eligible second (885 s remaining in every window
of this sample). Hold to settlement. No skip, no resize, no complement, no SELL,
no EXTEND, no threshold, no progressive or Martingale sizing. The first
chronological window has no prior outcome and is not traded.

The two transition directions are reported SEPARATELY, because a 10.3 pp aggregate
difference can hide all of its economic value in one direction and none in the
other, and the aggregate alone would not reveal that.

The capacity figure is an UPPER BOUND and is labelled as one. It scales by the
depth actually displayed AT THE TOUCH and by the $200 per-window ceiling, with the
venue fee recomputed for the whole clip rather than multiplied -- the fee is
convex and cent-rounded, so per-unit fee times quantity is wrong. The tape carries
no ladder, so nothing beyond the displayed touch is assumed, and no market impact,
queue position or partial-fill behaviour is modelled. It is not executable and not
realized.
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
from decimal import Decimal as D, ROUND_FLOOR

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_fees as FEES                                 # noqa: E402
from scripts.pmus_path_economics import eligible                  # noqa: E402
from scripts.pmus_favorite_dca import window_start                # noqa: E402
from scripts.pmus_fixed_side_baseline import economics            # noqa: E402

ONE, ZERO = D(1), D(0)
#: hard per-window capital ceiling the programme is working to
CEILING = D(200)


def _open(p):
    if p.endswith(".gz"):
        return gzip.open(p, "rt", encoding="utf-8", errors="replace")
    return io.open(p, "rt", encoding="utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--label", default="OOS")
    ap.add_argument("--out", default="analysis/l5/path_economics")
    a = ap.parse_args()

    by = collections.defaultdict(list)
    with _open(a.state) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                   # noqa: BLE001
                continue
            if r.get("slug"):
                by[r["slug"]].append(r)
    for v in by.values():
        v.sort(key=lambda r: r["t"])

    rows = []
    for slug in sorted(by, key=window_start):
        entry = None
        for r in by[slug]:
            e = eligible(r)
            if e is not None:
                entry = (r, e)
                break
        oc = by[slug][0].get("outcome_up")
        if entry is None or oc is None:
            continue
        r, (up_px, dn_px, up_q, dn_q) = entry
        rows.append({
            "slug": slug, "outcome_up": oc,
            "entry_seconds_remaining": r["seconds_remaining"],
            "UP": {"px": up_px, "qty": up_q,
                   "fee": FEES.taker_fee(up_px, ONE, cents=True)},
            "DOWN": {"px": dn_px, "qty": dn_q,
                     "fee": FEES.taker_fee(dn_px, ONE, cents=True)}})

    trades = []
    for prev, cur in zip(rows, rows[1:]):
        side = "DOWN" if prev["outcome_up"] == 1 else "UP"
        leg = cur[side]
        won = (cur["outcome_up"] == 1) if side == "UP" else (cur["outcome_up"] == 0)
        trades.append({
            "slug": cur["slug"], "side": side,
            "prev_outcome": "UP" if prev["outcome_up"] == 1 else "DOWN",
            "px": leg["px"], "fee": leg["fee"], "qty_touch": leg["qty"],
            "won": won})

    def breakeven(g):
        if not g:
            return None
        return str(round(sum((t["px"] + t["fee"] for t in g), ZERO) / len(g), 5))

    def capacity(g):
        """UPPER BOUND. min(displayed touch, $200 ceiling), fee on the clip."""
        if not g:
            return None
        nets, units, bind = [], [], collections.Counter()
        for t in g:
            per = t["px"] + t["fee"]
            by_ceiling = (CEILING / per).to_integral_value(rounding=ROUND_FLOOR)
            by_depth = t["qty_touch"].to_integral_value(rounding=ROUND_FLOOR)
            q = min(by_ceiling, by_depth)
            bind["ceiling" if by_ceiling <= by_depth else "touch_depth"] += 1
            if q <= 0:
                continue
            fee = FEES.taker_fee(t["px"], q, cents=True)
            payout = q if t["won"] else ZERO
            nets.append(float(payout - t["px"] * q - fee))
            units.append(float(q))
        return {
            "UPPER_BOUND_not_executable_not_realized": True,
            "n": len(nets),
            "mean_units_per_window": round(statistics.mean(units), 1) if units else None,
            "median_units_per_window": round(statistics.median(units), 1) if units else None,
            "binding_constraint": dict(bind),
            "net_total": round(sum(nets), 2),
            "net_per_window": round(statistics.mean(nets), 3) if nets else None,
            "median_per_window": round(statistics.median(nets), 3) if nets else None,
            "worst_window": round(min(nets), 2) if nets else None,
            "caveats": ("single displayed touch level only -- the tape carries no "
                        "ladder; no market impact, no queue, no partial fills, no "
                        "adverse selection from being the only buyer at size"),
        }

    up_prev = [t for t in trades if t["prev_outcome"] == "UP"]      # buy DOWN
    dn_prev = [t for t in trades if t["prev_outcome"] == "DOWN"]    # buy UP

    cum, series = 0.0, []
    for t in trades:
        payout = ONE if t["won"] else ZERO
        cum += float(payout - t["px"] - t["fee"])
        series.append(round(cum, 4))

    ent = [r["entry_seconds_remaining"] for r in rows]
    out = {
        "LABEL": "HYPOTHESIS-GENERATION / IN-SAMPLE DESCRIPTIVE ECONOMICS",
        "NOT_VALIDATION": ("these 178 windows generated the reversal hypothesis "
                           "and are permanently retired from validating it; the "
                           "figures below are biased upward by construction"),
        "sample": a.label,
        "windows_available": len(rows),
        "first_window_untraded_no_prior_outcome": rows[0]["slug"] if rows else None,
        "entry_seconds_remaining": {"min": min(ent), "max": max(ent),
                                    "distinct_values": len(set(ent))},
        "rule": ("prev UP -> buy 1 DOWN; prev DOWN -> buy 1 UP; "
                 "entry at the fixed first-eligible second; hold to settlement"),
        "ALL_trades": {
            "trades": len(trades),
            "UP_purchases": sum(1 for t in trades if t["side"] == "UP"),
            "DOWN_purchases": sum(1 for t in trades if t["side"] == "DOWN"),
            "mean_breakeven_probability_incl_fees": breakeven(trades),
            **(economics(trades) or {}),
            "capacity_UPPER_BOUND_at_200_ceiling": capacity(trades),
        },
        "prev_UP_buy_DOWN": {
            "trades": len(up_prev),
            "mean_breakeven_probability_incl_fees": breakeven(up_prev),
            **(economics(up_prev) or {}),
            "capacity_UPPER_BOUND_at_200_ceiling": capacity(up_prev),
        },
        "prev_DOWN_buy_UP": {
            "trades": len(dn_prev),
            "mean_breakeven_probability_incl_fees": breakeven(dn_prev),
            **(economics(dn_prev) or {}),
            "capacity_UPPER_BOUND_at_200_ceiling": capacity(dn_prev),
        },
        "chronological_cumulative_pnl_unit_size": series,
    }
    # significance of the unit-size mean, so the magnitude is read with its noise
    fn = [float((ONE if t["won"] else ZERO) - t["px"] - t["fee"]) for t in trades]
    if len(fn) > 2:
        sd = statistics.stdev(fn)
        se = sd / math.sqrt(len(fn))
        out["ALL_trades"]["sd_per_window"] = round(sd, 5)
        out["ALL_trades"]["se_of_mean"] = round(se, 5)
        out["ALL_trades"]["t_stat_vs_zero"] = round(statistics.mean(fn) / se, 3)
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "reversal_descriptive_%s.json" % a.label.lower())
    with open(p, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    slim = json.loads(json.dumps(out, default=str))
    slim["chronological_cumulative_pnl_unit_size"] = (
        "%d points, final %s" % (len(series), series[-1] if series else None))
    print(json.dumps(slim, indent=1))
    print("\nwrote %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
