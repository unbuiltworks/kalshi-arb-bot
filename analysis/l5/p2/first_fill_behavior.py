"""P2 -- what does L5 respond to when he takes a side that our clone cannot see?

THE QUESTION. Our frozen state key is only three things:

    price band | TTE bucket | abs(residual_qty) bucket

If L5 conditions his next action on something else -- how much matched
inventory he already holds, how much he has spent, what his held-side average
cost is, how much completion headroom is left, how long his current side streak
is -- then two situations that look IDENTICAL to our clone are situations where
L5 does different things. That is the missing rule, and it would explain how
action frequencies can look faithful while economics are not.

WHAT IS MEASURED. Every point in a reconstructed L5 window where he holds
directional inventory, the candidate variables at that instant, and THE THING
HE ACTUALLY DID NEXT: same-side fill (EXTEND-like) or opposite-side fill
(COMPLETE-like).

HONESTY ABOUT WHAT PUBLIC DATA CANNOT GIVE. The activity feed publishes his
fills, not the book. So:
  * the CONTEMPORANEOUS OPPOSITE PRICE is not published. Completion headroom in
    the strict sense (best complement ask vs ceiling) is therefore UNKNOWN for
    L5 and is NOT reconstructed here. What IS reconstructable is his own
    realised pair cost once both sides exist, and the headroom implied by his
    held-side average cost -- which is a bound on what he could pay, not
    evidence of what was offered.
  * maker/taker is not published. UNKNOWN.
  * his order intentions, cancels and rests are not published. Only fills.
Every one of those is labelled UNKNOWN rather than inferred.

OUT-OF-SAMPLE BY CONSTRUCTION. Windows are split by TIME, not at random:
earlier windows train, later windows test. A random split would leak, because
fills inside one window are highly correlated.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reconstruct as R                                    # noqa: E402

UNKNOWN = "unknown_not_published_by_the_activity_feed"


# ---------------------------------------------------------------------------
# STATE: the frozen key, and the candidates it omits
# ---------------------------------------------------------------------------

def price_band(p: float) -> str:
    for hi, name in ((0.10, "p<10c"), (0.25, "p10-25c"), (0.45, "p25-45c"),
                     (0.55, "p45-55c"), (0.75, "p55-75c"), (0.90, "p75-90c")):
        if p < hi:
            return name
    return "p>90c"


def tte_bucket(s: float) -> str:
    if s <= 30:
        return "tte<30s"
    if s <= 120:
        return "tte30-120s"
    if s <= 300:
        return "tte2-5m"
    if s <= 600:
        return "tte5-10m"
    return "tte>10m"


def resid_bucket(r: float) -> str:
    a = abs(r)
    if a < 1e-9:
        return "flat"
    if a <= 10:
        return "imb1-10"
    if a <= 50:
        return "imb10-50"
    return "imb>50"


def frozen_key(step: R.Step) -> str:
    """EXACTLY what production keys on today."""
    return "|".join((price_band(step.price), tte_bucket(step.tte),
                     resid_bucket(step.residual)))


def candidates(step: R.Step, hist: list[R.Step]) -> dict:
    """The variables the frozen key throws away."""
    up, dn = step.qty_up, step.qty_dn
    total = up + dn
    held_cost = (step.avg_up if step.residual > 0 else
                 step.avg_dn if step.residual < 0 else None)
    # side streak: consecutive fills on the current side, ending here
    streak = 0
    for s in reversed(hist):
        if s.outcome == step.outcome:
            streak += 1
        else:
            break
    # recent price movement over the last 5 own fills
    prev = hist[-5] if len(hist) >= 5 else (hist[0] if hist else None)
    move = (step.price - prev.price) if prev else None
    return {
        "matched_qty": round(step.matched, 4),
        "abs_inventory": round(total, 4),
        "abs_spend": round(step.cost_up + step.cost_dn, 4),
        "imbalance_ratio": round(abs(step.residual) / total, 4) if total else None,
        "held_avg_cost": round(held_cost, 4) if held_cost is not None else None,
        # BOUND, not headroom: the most he could pay and still pair <= 1.00.
        # It is NOT evidence the market offered that price -- see the docstring.
        "implied_headroom_bound": (round(1.0 - held_cost, 4)
                                   if held_cost is not None else None),
        "effective_pair_cost": (round(step.pair_cost, 4)
                                if step.pair_cost is not None else None),
        "side_streak": streak,
        "n_same_side_fills": sum(1 for s in hist if s.outcome == step.outcome),
        "recent_price_move": round(move, 4) if move is not None else None,
        "n_fills_so_far": len(hist),
        "maker_or_taker": UNKNOWN,
        "contemporaneous_opposite_price": UNKNOWN,
    }


def samples(windows: dict[str, R.Window], min_fills: int = 2) -> list[dict]:
    """One row per decision point that HAS a next fill, so the label is real."""
    out: list[dict] = []
    for w in windows.values():
        steps = R.trajectory(w)
        if len(steps) < min_fills:
            continue
        for i in range(len(steps) - 1):
            s, nxt = steps[i], steps[i + 1]
            if abs(s.residual) < 1e-9:
                continue                     # only points with real inventory
            out.append({
                "window": w.window_id, "slug": w.slug,
                "t": s.ts, "asset": w.asset, "window_minutes": w.window_minutes,
                "frozen_key": frozen_key(s),
                "label_same_side": nxt.outcome == s.outcome,
                **candidates(s, steps[:i]),
            })
    return out


# ---------------------------------------------------------------------------
# DOES AN OMITTED VARIABLE SPLIT A FROZEN KEY?
# ---------------------------------------------------------------------------

def _rate(rows: list[dict]) -> float:
    return sum(1 for r in rows if r["label_same_side"]) / len(rows)


def split_power(rows: list[dict], var: str, min_n: int = 200,
                min_side: int = 60) -> list[dict]:
    """Within each frozen key, split on the MEDIAN of `var` and measure how far
    apart L5's same-side rate is on the two halves.

    A large, well-populated gap means: two situations our clone cannot tell
    apart, where L5 demonstrably does different things."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get(var) is not None:
            by[r["frozen_key"]].append(r)
    out = []
    for key, rs in by.items():
        if len(rs) < min_n:
            continue
        vals = sorted(r[var] for r in rs)
        med = vals[len(vals) // 2]
        lo = [r for r in rs if r[var] <= med]
        hi = [r for r in rs if r[var] > med]
        if len(lo) < min_side or len(hi) < min_side:
            continue
        out.append({
            "frozen_key": key, "variable": var, "n": len(rs),
            "median": med,
            "same_side_rate_low": round(_rate(lo), 4), "n_low": len(lo),
            "same_side_rate_high": round(_rate(hi), 4), "n_high": len(hi),
            "gap": round(abs(_rate(hi) - _rate(lo)), 4),
        })
    return sorted(out, key=lambda d: -d["gap"])


# ---------------------------------------------------------------------------
# OUT-OF-SAMPLE: does the omitted variable PREDICT, or only describe?
# ---------------------------------------------------------------------------

def _logloss(p: float, y: bool, eps: float = 1e-6) -> float:
    p = min(1 - eps, max(eps, p))
    return -(math.log(p) if y else math.log(1 - p))


def time_split(rows: list[dict], frac: float = 0.7) -> tuple[list, list]:
    """Split by TIME, never at random: fills inside one window are heavily
    correlated, so a random split leaks the answer into the training set."""
    rows = sorted(rows, key=lambda r: r["t"])
    cut = int(len(rows) * frac)
    t_cut = rows[cut]["t"]
    return ([r for r in rows if r["t"] < t_cut],
            [r for r in rows if r["t"] >= t_cut])


def evaluate(train: list[dict], test: list[dict], var: Optional[str],
             min_leaf: int = 40) -> dict:
    """Predict P(same side next) from the frozen key, optionally split by the
    median of one omitted variable. Unseen keys fall back to the base rate --
    the same fallback for both models, so the comparison is like-for-like."""
    base = _rate(train) if train else 0.5
    med: dict[str, float] = {}
    if var:
        by: dict[str, list[float]] = defaultdict(list)
        for r in train:
            if r.get(var) is not None:
                by[r["frozen_key"]].append(r[var])
        med = {k: sorted(v)[len(v) // 2] for k, v in by.items() if v}

    def cell(r: dict) -> str:
        k = r["frozen_key"]
        if var and k in med and r.get(var) is not None:
            return f"{k}|{'hi' if r[var] > med[k] else 'lo'}"
        return k

    agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in train:
        a = agg[cell(r)]
        a[0] += 1
        a[1] += 1 if r["label_same_side"] else 0
    ll = acc = 0.0
    for r in test:
        n, k = agg.get(cell(r), [0, 0])
        p = (k / n) if n >= min_leaf else base
        ll += _logloss(p, r["label_same_side"])
        acc += 1.0 if ((p >= 0.5) == r["label_same_side"]) else 0.0
    n = len(test) or 1
    return {"variable": var or "FROZEN_KEY_ONLY", "test_n": len(test),
            "logloss": round(ll / n, 5), "accuracy": round(acc / n, 4),
            "cells": len(agg)}


def main(argv: list[str]) -> int:
    canon = R.load_canonical()
    fresh, incentives = R.load_fresh()
    out: dict[str, Any] = {
        "labels": {
            "maker_taker": UNKNOWN,
            "contemporaneous_opposite_price": UNKNOWN,
            "implied_headroom_bound": "NOT completion headroom. It is 1.00 minus "
                                      "his held-side average cost -- a bound on "
                                      "what he COULD pay, not evidence of what "
                                      "the market offered.",
            "split": "by TIME (70/30), never at random",
        },
        "incentives_account_level_usd": incentives,
    }
    for name, wins in (("canonical_mar_aug", canon), ("fresh_sep", fresh)):
        rows = samples(wins)
        tr, te = time_split(rows)
        base = evaluate(tr, te, None)
        res = [base]
        for v in ("matched_qty", "abs_inventory", "abs_spend",
                  "imbalance_ratio", "held_avg_cost", "implied_headroom_bound",
                  "effective_pair_cost", "side_streak", "n_same_side_fills",
                  "recent_price_move"):
            res.append(evaluate(tr, te, v))
        splits: dict[str, list] = {}
        for v in ("matched_qty", "abs_spend", "held_avg_cost", "side_streak",
                  "effective_pair_cost", "recent_price_move"):
            s = split_power(rows, v)
            if s:
                splits[v] = s[:3]
        out[name] = {
            "windows": len(wins), "decision_points": len(rows),
            "same_side_rate_overall": round(_rate(rows), 4) if rows else None,
            "train_n": len(tr), "test_n": len(te),
            "out_of_sample": sorted(res, key=lambda d: d["logloss"]),
            "top_frozen_key_splits": splits,
        }
    print(json.dumps(out, indent=1, default=str))
    if len(argv) > 1:
        Path(argv[1]).write_text(json.dumps(out, indent=1, default=str),
                                 encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
