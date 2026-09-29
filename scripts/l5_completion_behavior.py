"""What does L5 do differently in the windows that end well?

THE QUESTION, and it is not the one we were asking. Two attempts to predict
which PMUS window to trade failed out of sample (the q25 knee, the
early-window selectors). So this stops asking "which window?" and asks
"which BEHAVIOUR?": across 527 BTC-15m windows of L5's own sequential
actions, how does L5 act in windows that end matched and cheap versus
windows that end with an expensive residual?

That matters because the expensive residual is OUR failure mode. Our clone's
losing windows are residual-drag windows. If L5 survives the same kind of
window, the difference is in the action sequence, and it is observable here.

DATA. analysis/l5/l5_window_sequences.csv -- 63,569 L5 BUY fills across 527
BTC-15m windows, with running inventory per trade. Every cum_* column is the
state AFTER that trade, so the state a decision SAW is the previous row's.
This distinction is the whole analysis: read it wrong and every feature
silently contains its own outcome.

MAKER/TAKER IS NOT IN THIS DATASET. It is reported absent, not proxied.

No threshold is fitted. This is a behavioural comparison, reported as
distributions.
"""
from __future__ import annotations

import csv
import json
import statistics
from typing import Any, Dict, Iterable, List, Optional

SRC = "analysis/l5/l5_window_sequences.csv"


def _f(v) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def windows(path: str = SRC) -> Dict[str, List[dict]]:
    by: Dict[str, List[dict]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("asset", "").upper() != "BTC":
                continue
            if r.get("window_minutes") != "15":
                continue
            by.setdefault(r["window_id"], []).append(r)
    for rows in by.values():
        rows.sort(key=lambda r: int(r["trade_seq"]))
    return by


def outcome_of(rows: List[dict]) -> Optional[Dict[str, Any]]:
    """How the window ENDED. Labels only -- never a feature."""
    last = rows[-1]
    up, dn = _f(last["cum_up_qty"]), _f(last["cum_down_qty"])
    matched, excess = _f(last["matched_qty"]), _f(last["excess_abs_qty"])
    if None in (up, dn, matched, excess) or (up + dn) <= 0:
        return None
    spend = (_f(last["cum_up_spend"]) or 0.0) + (_f(last["cum_down_spend"]) or 0.0)
    return {
        "completion": 2.0 * matched / (up + dn),
        "residual_qty": excess,
        "residual_frac": excess / (up + dn),
        "excess_cost": _f(last["excess_cost"]),
        "pair_unit_cost": _f(last["matched_pair_unit_cost"]),
        "spend": spend,
        "n_trades": len(rows),
    }


def actions(rows: List[dict]) -> List[Dict[str, Any]]:
    """One record per L5 action, describing the state it was taken FROM."""
    out: List[Dict[str, Any]] = []
    streak = 0
    prev_side: Optional[str] = None
    prev_price_by_side: Dict[str, float] = {}
    prev_price: Optional[float] = None

    for i, r in enumerate(rows):
        side = r["outcome"]                     # Up / Down
        px = _f(r["price"])
        if px is None:
            continue
        before = rows[i - 1] if i else None

        # state as the decision saw it
        b_up = _f(before["cum_up_qty"]) if before else 0.0
        b_dn = _f(before["cum_down_qty"]) if before else 0.0
        b_matched = _f(before["matched_qty"]) if before else 0.0
        b_excess = _f(before["excess_qty"]) if before else 0.0     # signed
        b_dom = before["dominant_side"] if before else None

        # 1. mode: extending the heavy side, or rebalancing toward the light one
        if b_excess in (None, 0) or not b_dom or b_dom == "":
            mode = "flat_open"
        elif side == b_dom:
            mode = "extend"
        else:
            mode = "rebalance"

        # 2. same-side streak, counted before this action is added
        streak = streak + 1 if (prev_side is not None and side == prev_side) else 1

        # 6. price move on THIS side since L5 last took it
        last_same = prev_price_by_side.get(side)
        move_same = None if last_same is None else px - last_same
        move_any = None if prev_price is None else px - prev_price

        # 9. adds to weakness vs chases: this side got CHEAPER since L5 last
        #    bought it -> adding to weakness; dearer -> chasing
        if move_same is None or move_same == 0:
            weak_chase = "flat_or_first"
        else:
            weak_chase = "adds_to_weakness" if move_same < 0 else "chases"

        # 10. replenish at the same price, or reprice
        if last_same is None:
            repl = "first_on_side"
        elif abs(px - last_same) < 1e-9:
            repl = "same_price"
        else:
            repl = "reprice"

        out.append({
            "mode": mode,
            "streak": streak,
            "imbalance": b_excess,
            "abs_imbalance": abs(b_excess) if b_excess is not None else None,
            "matched": b_matched,
            "residual": abs(b_excess) if b_excess is not None else None,
            "move_same_side": move_same,
            "move_any": move_any,
            "dt_prev": _f(r["seconds_since_prev_trade"]),
            "s_to_expiry": _f(r["seconds_to_expiry"]),
            "weak_chase": weak_chase,
            "replenish": repl,
            "price": px,
            "shares": _f(r["shares"]),
            "maker_taker": None,          # NOT IN THIS DATASET
        })
        prev_side = side
        prev_price_by_side[side] = px
        prev_price = px
    return out


def profile(acts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Behavioural summary of one window's action sequence."""
    n = len(acts) or 1
    def frac(key, val):
        return sum(1 for a in acts if a[key] == val) / n
    def med(key):
        v = [a[key] for a in acts if a.get(key) is not None]
        return statistics.median(v) if v else None
    return {
        "n_actions": len(acts),
        "extend_frac": frac("mode", "extend"),
        "rebalance_frac": frac("mode", "rebalance"),
        "flat_open_frac": frac("mode", "flat_open"),
        "max_streak": max((a["streak"] for a in acts), default=0),
        "median_streak": med("streak"),
        "median_abs_imbalance": med("abs_imbalance"),
        "max_abs_imbalance": max((a["abs_imbalance"] or 0) for a in acts) if acts else None,
        "median_matched": med("matched"),
        "median_dt_prev": med("dt_prev"),
        "median_s_to_expiry": med("s_to_expiry"),
        "adds_to_weakness_frac": frac("weak_chase", "adds_to_weakness"),
        "chases_frac": frac("weak_chase", "chases"),
        "same_price_frac": frac("replenish", "same_price"),
        "reprice_frac": frac("replenish", "reprice"),
        "median_move_same_side": med("move_same_side"),
        "median_price": med("price"),
    }


BEHAVIOURS = ("extend_frac", "rebalance_frac", "flat_open_frac", "max_streak",
              "median_streak", "median_abs_imbalance", "max_abs_imbalance",
              "median_matched", "median_dt_prev", "median_s_to_expiry",
              "adds_to_weakness_frac", "chases_frac", "same_price_frac",
              "reprice_frac", "median_move_same_side", "median_price",
              "n_actions")


def auc(good: List[float], bad: List[float]) -> Optional[float]:
    if len(good) < 5 or len(bad) < 5:
        return None
    w = t = 0
    for x in good:
        for y in bad:
            if x > y:
                w += 1
            elif x == y:
                t += 1
    return (w + 0.5 * t) / (len(good) * len(bad))


def build() -> Dict[str, Any]:
    by = windows()
    recs = []
    for wid, rows in by.items():
        o = outcome_of(rows)
        if o is None or len(rows) < 5:
            continue
        p = profile(actions(rows))
        if p["n_actions"] < 5:
            continue
        recs.append({"window_id": wid, "first_t": rows[0]["datetime_utc"],
                     **{"out_" + k: v for k, v in o.items()}, **p})
    return {"windows": recs}


if __name__ == "__main__":
    print(json.dumps(build()["windows"][:3], indent=2, default=str))
