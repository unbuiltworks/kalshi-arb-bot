"""WHY DOES L5 REACH CHEAP-COMPLEMENT STATES SO OFTEN?

Sequential, NO-LOOKAHEAD reconstruction over the canonical L5 fill sequence.
Read-only research. Nothing here is imported by an armed path, nothing here
touches the production model, and nothing here places, prices or cancels an
order.

THE EXACT DECOMPOSITION THIS FILE IS BUILT AROUND. At a side switch, the
canonical pair cost (the analogue of our COMPLETE gate) is

    pair      = avg_cost_held + first_opposite_price

and the cross-side sum actually observed at that moment is

    last_pair = last_same_side_price + first_opposite_price

Subtracting gives an IDENTITY, not an approximation:

    pair = last_pair - (last_same_side_price - avg_cost_held)
         = last_pair - gain_per_unit

where `gain_per_unit` is the held side's unrealised gain against its own
running average cost, and is KNOWN BEFORE the switch fill is observed. So

    pair <= 0.97   <=>   gain_per_unit >= last_pair - 0.97

The cheap complement is therefore not a separate phenomenon from being up on
the position: it is the same number viewed from the other side, offset by the
cross-side sum (a market-structure term, typically ~0.98-0.99). Quantifying
how much of the <=0.97 rate is that identity, and how much is anything else,
is the point of this module.

NO FUTURE INFORMATION ENTERS A FEATURE. `pre_features()` is computed strictly
from fills BEFORE the switch; the switch fill's own price is an OUTCOME. A
regression pins that the feature builder cannot see the switch row.
"""
from __future__ import annotations

import bisect
import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Optional

PAIR_ARM = 0.97
#: bands for the held side's price, the same cut points the frozen state key
#: uses, so results can be read beside the model's own buckets
PRICE_BANDS = ((0.10, "p<10c"), (0.25, "p10-25c"), (0.45, "p25-45c"),
               (0.55, "p45-55c"), (0.75, "p55-75c"), (0.90, "p75-90c"),
               (1.01, "p>90c"))
TTE_BUCKETS = ((30, "tte<30s"), (120, "tte30-120s"), (300, "tte2-5m"),
               (600, "tte5-10m"), (1e9, "tte>10m"))


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def bucket(value: Optional[float], bands) -> Optional[str]:
    if value is None:
        return None
    for edge, label in bands:
        if value < edge:
            return label
    return bands[-1][1]


def pre_features(state: Mapping[str, Any]) -> dict:
    """Features known STRICTLY BEFORE the switch fill.

    `state` is the walker's accumulated state as of the last same-side fill.
    Nothing in here may be derived from the switch fill itself -- not its
    price, not its size, not its timestamp.
    """
    last_px = state.get("last_same_side_price")
    avg = state.get("avg_cost_held")
    gain = (last_px - avg) if (last_px is not None and avg is not None) else None
    entry = state.get("entry_price")
    return {
        # position economics, all accumulated from past fills only
        "avg_cost_held": avg,
        "last_same_side_price": last_px,
        "gain_per_unit": gain,
        "held_qty": state.get("held_qty"),
        "held_notional": (avg * state["held_qty"]
                          if avg is not None and state.get("held_qty") else None),
        "matched_qty": state.get("matched_qty"),
        "residual_qty_signed": state.get("residual_qty_signed"),
        # sequence shape so far
        "run_length": state.get("run_length"),
        "run_seconds": state.get("run_seconds"),
        "held_side_drift_during_run": state.get("held_side_drift_during_run"),
        "fills_so_far": state.get("fills_so_far"),
        "switches_so_far": state.get("switches_so_far"),
        "side_streak": state.get("side_streak"),
        # where we are in the window
        "tte_s": state.get("tte_s"),
        "tte_bucket": bucket(state.get("tte_s"), TTE_BUCKETS),
        "price_band": bucket(last_px, PRICE_BANDS),
        "entry_price": entry,
        "move_since_entry": ((last_px - entry)
                             if last_px is not None and entry is not None else None),
        "seconds_since_entry": state.get("seconds_since_entry"),
        "held_side": state.get("held_side"),
        "asset": state.get("asset"),
        "duration_minutes": state.get("duration_minutes"),
    }


def walk_window(rows: Iterable[Mapping[str, Any]]) -> list:
    """One settlement window -> one record per SIDE SWITCH, in time order.

    The walk is strictly sequential: state is accumulated from fills already
    seen, the switch record is emitted using that state, and only then is the
    switch fill folded in. That ordering is what makes the features
    no-lookahead by construction rather than by promise.
    """
    buys = [r for r in rows if (r.get("side") or "").upper() == "BUY"]
    buys.sort(key=lambda r: (int(_f(r.get("timestamp")) or 0),
                             int(_f(r.get("trade_seq")) or 0)))
    qty: dict = defaultdict(float)
    cost: dict = defaultdict(float)
    out: list = []
    prev = None
    run_len = 0
    run_start_t = run_start_px = None
    entry_px = entry_t = None
    fills = switches = 0
    streak = 0

    for r in buys:
        o = r.get("outcome")
        px, q, t = _f(r.get("price")), _f(r.get("quantity")), _f(r.get("timestamp"))
        if px is None or q is None or t is None:
            continue
        if entry_px is None:
            entry_px, entry_t = px, t

        if prev is not None and o != prev["outcome"]:
            held = prev["outcome"]
            hq = qty[held]
            avg = (cost[held] / hq) if hq > 0 else None
            other = qty[o]
            state = {
                "held_side": held,
                "held_qty": hq,
                "avg_cost_held": avg,
                "last_same_side_price": _f(prev["price"]),
                "matched_qty": min(hq, other),
                "residual_qty_signed": hq - other,
                "run_length": run_len,
                "run_seconds": (t - run_start_t) if run_start_t is not None else None,
                "held_side_drift_during_run": (
                    (_f(prev["price"]) - run_start_px)
                    if run_start_px is not None else None),
                "fills_so_far": fills,
                "switches_so_far": switches,
                "side_streak": streak,
                "tte_s": _f(prev.get("seconds_to_settlement")),
                "entry_price": entry_px,
                "seconds_since_entry": (t - entry_t) if entry_t is not None else None,
                "asset": r.get("asset"),
                "duration_minutes": _f(r.get("duration_minutes")),
            }
            pre = pre_features(state)
            last_pair = (pre["last_same_side_price"] + px
                         if pre["last_same_side_price"] is not None else None)
            pair = (avg + px) if avg is not None else None
            out.append({
                "window_id": r.get("window_id"),
                "market_slug": r.get("market_slug"),
                "asset": r.get("asset"),
                "t": t,
                "switch_index": switches,
                "pre": pre,
                # OUTCOMES -- never features
                "first_opposite_price": px,
                "pair": pair,
                "last_pair": last_pair,
                "pair_le_arm": (pair is not None and pair <= PAIR_ARM),
                "identity_residual": (
                    None if (pair is None or last_pair is None
                             or pre["gain_per_unit"] is None)
                    else (pair - (last_pair - pre["gain_per_unit"]))),
            })
            switches += 1
            run_len = 0
            run_start_t = run_start_px = None
            streak = 0

        # fold the fill in only AFTER any switch record was emitted
        qty[o] += q
        cost[o] += px * q
        if prev is None or o != prev["outcome"]:
            run_start_t, run_start_px = t, px
        run_len += 1
        streak += 1
        fills += 1
        prev = r
    return out


# ------------------------------------------------------------------ analysis
def auc(labels: list, scores: list) -> Optional[float]:
    """Rank AUC, ties counted as half. No dependency, no sampling."""
    pairs = [(s, y) for s, y in zip(scores, labels) if s is not None]
    pos = [s for s, y in pairs if y]
    neg = [s for s, y in pairs if not y]
    if not pos or not neg:
        return None
    wins = ties = 0
    neg_sorted = sorted(neg)
    for p in pos:
        lo = bisect.bisect_left(neg_sorted, p)
        hi = bisect.bisect_right(neg_sorted, p)
        wins += lo
        ties += hi - lo
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def rate(rows: list) -> Optional[float]:
    return (sum(1 for r in rows if r["pair_le_arm"]) / len(rows)) if rows else None
