"""UNDERWATER INVENTORY BEHAVIOUR -- what L5 does when the held side is down.

Read-only research. Sequential, no-lookahead for every FEATURE; outcomes are
labelled as outcomes. Nothing here is imported by an armed path and nothing
here changes the production model.

THE UNIT IS AN INVENTORY EPISODE, aligned at its first fill. At every point in
the episode we know, from fills already seen:

    gain_per_unit = price of the held side now - our average cost on it

and we record what L5 DID NEXT: EXTEND the same side, SWITCH to the opposite
side (his analogue of our COMPLETE), or never trade again in that window
(HOLD_TO_SETTLEMENT). Bucketing those next-actions by gain_per_unit is the
whole experiment.

WHY gain_per_unit IS AN EXPLANATORY VARIABLE AND NOT A SIGNAL. `pair =
cross_side_sum - gain_per_unit` is an identity, so "complete only when gain > 0"
is close to "complete only when the pair is cheap" -- which is already the
existing gate, not a new rule. L5 switches ABOVE $1.00 on 47.16% of his
switches, so a positive-gain precondition would refuse roughly half of what he
actually does. This module measures; it does not propose.

`price of the held side now` is the most recent FILL price on that side. The
canonical sequence carries no book, so this is a traded-price proxy and is
labelled as one wherever it is used.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Optional

#: gain buckets the owner asked for, in cents of price
GAIN_BUCKETS = ((-0.20, "<-20c"), (-0.10, "-20..-10c"), (-0.05, "-10..-5c"),
                (0.0, "-5..0c"), (0.05, "0..+5c"), (1e9, ">+5c"))
TTE_BUCKETS = ((30, "tte<30s"), (120, "tte30-120s"), (300, "tte2-5m"),
               (600, "tte5-10m"), (1e9, "tte>10m"))

EXTEND, SWITCH, HOLD = "EXTEND", "SWITCH", "HOLD_TO_SETTLEMENT"


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


def episode(rows: Iterable[Mapping[str, Any]]) -> list:
    """One window -> one decision point per fill, each carrying the state
    BEFORE the next fill and the action that followed it.

    State is accumulated strictly from fills already seen. The next fill
    supplies the ACTION label only; none of its numbers enter the features.
    """
    buys = [r for r in rows if (r.get("side") or "").upper() == "BUY"]
    buys.sort(key=lambda r: (int(_f(r.get("timestamp")) or 0),
                             int(_f(r.get("trade_seq")) or 0)))
    if not buys:
        return []
    qty: dict = defaultdict(float)
    cost: dict = defaultdict(float)
    last_px: dict = {}
    out: list = []
    t0 = _f(buys[0].get("timestamp"))
    run_side, run_len = None, 0

    for i, r in enumerate(buys):
        o = r.get("outcome")
        px, q, t = _f(r.get("price")), _f(r.get("quantity")), _f(r.get("timestamp"))
        if px is None or q is None or t is None:
            continue
        qty[o] += q
        cost[o] += px * q
        last_px[o] = px
        run_len = run_len + 1 if o == run_side else 1
        run_side = o

        # ---- state AFTER this fill, BEFORE whatever comes next
        up, dn = qty.get("Up", 0.0), qty.get("Down", 0.0)
        held = "Up" if up > dn else ("Down" if dn > up else None)
        gain = residual = avg = held_now = None
        if held is not None and qty[held] > 0:
            avg = cost[held] / qty[held]
            held_now = last_px.get(held)
            if held_now is not None:
                gain = held_now - avg
            residual = abs(up - dn)
        matched = min(up, dn)

        nxt = buys[i + 1] if i + 1 < len(buys) else None
        if nxt is None:
            action = HOLD
            dwell = None
        else:
            action = EXTEND if nxt.get("outcome") == o else SWITCH
            dwell = (_f(nxt.get("timestamp")) or t) - t

        out.append({
            "window_id": r.get("window_id"), "asset": r.get("asset"),
            "duration_minutes": _f(r.get("duration_minutes")),
            "fill_index": i, "t": t, "seconds_since_first_fill": t - t0,
            "tte_s": _f(r.get("seconds_to_settlement")),
            # --- pre-action features
            "held_side": held, "held_qty": (qty[held] if held else 0.0),
            "avg_cost_held": avg, "held_price_now": held_now,
            "gain_per_unit": gain, "gain_bucket": bucket(gain, GAIN_BUCKETS),
            "tte_bucket": bucket(_f(r.get("seconds_to_settlement")), TTE_BUCKETS),
            "residual_qty": residual,
            "residual_usd": (residual * avg) if (residual and avg) else None,
            "matched_qty": matched, "run_length": run_len,
            "last_fill_side": o, "last_fill_price": px,
            # --- outcome labels (never features)
            "next_action": action, "dwell_s": dwell,
            "settlement_outcome": r.get("settlement_outcome"),
            "resolution_source": r.get("resolution_source"),
        })
    return out


def episode_summary(points: list) -> Optional[dict]:
    """Terminal economics of one episode, for Q5: did an underwater position
    recover, get matched, or settle as a directional residual?"""
    if not points:
        return None
    last = points[-1]
    under = [p for p in points if p["gain_per_unit"] is not None
             and p["gain_per_unit"] < 0]
    recovered = any(p["gain_per_unit"] is not None and p["gain_per_unit"] > 0
                    for p in points[points.index(under[0]):]) if under else None
    deepest = min((p["gain_per_unit"] for p in points
                   if p["gain_per_unit"] is not None), default=None)
    return {
        "window_id": last["window_id"], "asset": last["asset"],
        "duration_minutes": last["duration_minutes"],
        "n_fills": len(points),
        "n_switches": sum(1 for p in points if p["next_action"] == SWITCH),
        "n_extends": sum(1 for p in points if p["next_action"] == EXTEND),
        "seconds_span": last["seconds_since_first_fill"],
        "ever_underwater": bool(under),
        "deepest_gain": deepest,
        "recovered_to_positive": recovered,
        "terminal_gain": last["gain_per_unit"],
        "terminal_residual_qty": last["residual_qty"],
        "terminal_matched_qty": last["matched_qty"],
        "matched_fraction": (last["matched_qty"] /
                             (last["matched_qty"] + (last["residual_qty"] or 0))
                             if (last["matched_qty"] or last["residual_qty"])
                             else None),
        "settlement_outcome": last["settlement_outcome"],
    }
