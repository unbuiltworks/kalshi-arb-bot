"""Given a state like ours, what did L5 CHOOSE -- complete, extend, or stop?

The companion script conditions on L5 HAVING completed, which answers "how
expensive were its completes" but not "did it complete at all". This asks the
choice question directly: at every fill, reconstruct the state BEFORE it, and
where that state resembles ours -- a cheap surplus leg and a deficit -- record
which action L5 actually took next.

THE SELECTION LIMIT, STATED UP FRONT. This is still a trade feed. "L5 stopped"
is only ever observed as "the window ended", and a window that ends is
indistinguishable from a window where L5 quoted and went unfilled. So the
complete-vs-extend split below is a ratio of ACTIONS TAKEN, and the residual
category is the window ending, not an observed decision to wait.
"""
from __future__ import annotations

import csv
import gzip
import json
import statistics
from collections import defaultdict
from typing import Any, Dict, List, Optional

SRC = "analysis/l5/l5_canonical_sequence.csv.gz"
OUT = "analysis/l5/complete_boundary_choice.json"

HELD_BAND = (0.08, 0.22)          # our NO leg at average cost 0.14
OUR_PAIR = 1.05


def pct(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 4)


def main() -> int:
    with gzip.open(SRC, "rt", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_window: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_window[r["window_id"]].append(r)

    # choice, conditioned on the state BEFORE the fill
    choice: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    complete_pairs_in_band: List[float] = []
    extend_prices_in_band: List[float] = []
    window_last_seq: Dict[str, int] = {}
    ends_in_band = 0

    # realized economics of windows containing an expensive complete
    win_has_expensive: Dict[str, bool] = {}
    win_final_pair: Dict[str, float] = {}
    win_residual_qty: Dict[str, float] = {}
    win_settle: Dict[str, str] = {}
    win_lane: Dict[str, str] = {}

    for wid, rs in by_window.items():
        rs.sort(key=lambda r: int(r["trade_seq"]))
        window_last_seq[wid] = int(rs[-1]["trade_seq"])
        last = rs[-1]
        win_lane[wid] = "%s-%sm" % (last["asset"], last["duration_minutes"])
        try:
            win_final_pair[wid] = float(last["matched_pair_unit_cost"] or 0) or 0.0
        except ValueError:
            win_final_pair[wid] = 0.0
        try:
            win_residual_qty[wid] = float(last["residual_qty_signed"] or 0)
        except ValueError:
            win_residual_qty[wid] = 0.0
        win_settle[wid] = (last["settlement_outcome"] or "").strip().lower()

        for i, r in enumerate(rs):
            if r["side"].upper() != "BUY":
                continue
            prev = rs[i - 1] if i else None
            up_q = float(prev["cum_up_qty"]) if prev else 0.0
            up_s = float(prev["cum_up_spend"]) if prev else 0.0
            dn_q = float(prev["cum_down_qty"]) if prev else 0.0
            dn_s = float(prev["cum_down_spend"]) if prev else 0.0
            res = up_q - dn_q
            if abs(res) < 1.0:
                continue                     # flat: not our state
            sq, ss = (up_q, up_s) if res > 0 else (dn_q, dn_s)
            if sq <= 0:
                continue
            held_avg = ss / sq
            if not (HELD_BAND[0] <= held_avg <= HELD_BAND[1]):
                continue                     # not a cheap held leg
            out = r["outcome"].strip().lower()
            is_up = out in ("up", "yes")
            rel = "extend" if (res > 0) == is_up else "complete"
            lane = "%s-%sm" % (r["asset"], r["duration_minutes"])
            choice["ALL"][rel] += 1
            choice[lane][rel] += 1
            price = float(r["price"])
            if rel == "complete":
                complete_pairs_in_band.append(price + held_avg)
            else:
                extend_prices_in_band.append(price)
            if int(r["trade_seq"]) == window_last_seq[wid]:
                ends_in_band += 1

            if rel == "complete" and price + held_avg > 1.05:
                win_has_expensive[wid] = True

    rep: Dict[str, Any] = {
        "label": "L5_COMPLETE_CHOICE_V1",
        "source": SRC,
        "held_avg_band": list(HELD_BAND),
        "limit": ("trade feed only: 'L5 waited' cannot be distinguished from "
                  "'L5 quoted and was not filled'. The split below is over "
                  "ACTIONS TAKEN."),
        "choice_given_cheap_held_leg": {
            k: {**dict(v),
                "complete_pct": round(100.0 * v["complete"]
                                      / max(1, v["complete"] + v["extend"]), 2)}
            for k, v in choice.items()},
        "complete_pair_in_band": {
            "n": len(complete_pairs_in_band),
            "p10": pct(complete_pairs_in_band, 10),
            "p25": pct(complete_pairs_in_band, 25),
            "p50": pct(complete_pairs_in_band, 50),
            "p75": pct(complete_pairs_in_band, 75),
            "p90": pct(complete_pairs_in_band, 90),
            "min": round(min(complete_pairs_in_band), 4)
            if complete_pairs_in_band else None,
            "pct_at_or_below_1_00": round(
                100.0 * sum(1 for x in complete_pairs_in_band if x <= 1.0)
                / max(1, len(complete_pairs_in_band)), 2),
            "pct_at_or_below_our_1_05": round(
                100.0 * sum(1 for x in complete_pairs_in_band if x <= OUR_PAIR)
                / max(1, len(complete_pairs_in_band)), 2),
        },
        "extend_price_in_band": {
            "n": len(extend_prices_in_band),
            "p50": pct(extend_prices_in_band, 50),
            "p90": pct(extend_prices_in_band, 90),
        },
        "fills_in_band_that_ended_the_window": ends_in_band,
    }

    # ---- realized economics, matched-pair kept apart from residual
    exp_w = [w for w in win_has_expensive]
    other_w = [w for w in by_window if w not in win_has_expensive
               and win_final_pair.get(w)]
    def econ(ws: List[str]) -> Dict[str, Any]:
        fp = [win_final_pair[w] for w in ws if win_final_pair.get(w)]
        rq = [abs(win_residual_qty.get(w, 0.0)) for w in ws]
        return {
            "windows": len(ws),
            "final_matched_pair_unit_cost": {
                "n": len(fp), "p25": pct(fp, 25), "p50": pct(fp, 50),
                "p75": pct(fp, 75), "p90": pct(fp, 90),
                "mean": round(statistics.fmean(fp), 4) if fp else None,
                "pct_at_or_below_1_00": round(
                    100.0 * sum(1 for x in fp if x <= 1.0) / max(1, len(fp)), 2),
            },
            "abs_residual_qty_at_settlement": {
                "p50": pct(rq, 50), "p90": pct(rq, 90)},
        }
    rep["realized_matched_pair_by_window"] = {
        "windows_with_a_complete_above_1_05": econ(exp_w),
        "windows_without_one": econ(other_w),
        "labels": ("matched-pair economics ONLY. Directional residual is "
                   "reported as quantity and is NEVER summed with the pair "
                   "cost. Neither is REALIZED: settlement cashflow is a "
                   "separate fact."),
    }

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    print(json.dumps(rep, indent=2, default=str)[:2600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
