"""ADAPTED prospective arms: bounded residual control. cap5 / cap8 / cap13.

FROZEN BEFORE COLLECTION. The three caps are the only members of the
preregistered family that were positive in BOTH historical regimes; cap_2 and
cap_3 win August and lose spring, cap_21 goes negative in August. The cap value
is NOT robustly identified, so three are run rather than one being chosen.

THE POLICY, and it is deliberately small:

    quote BOTH sides as a maker, BUY only, at the touch
    count consecutive EVIDENCED same-side fills
    once that run reaches N, WITHHOLD that side's quote
    the other side keeps quoting; a fill there resets the run

That is the whole rule. There is no pair-cost gate on entry -- 0.96 gates
nothing here and is carried only as a reference -- no side selection, no
sizing model, and no signal. It exists to test one proposition: that capping
same-side continuation bounds the residual without giving up the matched edge.

WHAT IT DOES NOT DO. It never sells, never crosses, never takes. Withholding a
side is not a directional view; it is a refusal to keep adding to the leg that
historically consumed the matched edge.

SEPARATE FROM R/S/D, which stays frozen and untouched. This module is not
imported by it and does not alter it.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Optional, Sequence

LABEL = "ADAPTED/bounded_residual_control"
CAPS = (5, 8, 13)
ARMS = tuple(f"cap{c}" for c in CAPS)
# reference only; gates nothing in this module
REFERENCE_PAIR_CEILING = D("0.96")
OPEN_FROM_TTE_S = 900.0
CANCEL_UNDER_TTE_S = 60.0
DESIRED_PER_SIDE = D("10")


def arm_cap(arm: str) -> int:
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; frozen set is {ARMS}")
    return int(arm[3:])


def new_state() -> Dict[str, Any]:
    return {"run_side": None, "run_len": 0,
            "up_qty": D("0"), "dn_qty": D("0"),
            "up_cost": D("0"), "dn_cost": D("0")}


def on_evidenced_fill(state: Dict[str, Any], *, side: str, qty: D,
                      cost: D) -> Dict[str, Any]:
    """Update from an EVIDENCED fill only. A theoretical fill never lands here."""
    s = dict(state)
    if side == "Up":
        s["up_qty"] = s["up_qty"] + qty
        s["up_cost"] = s["up_cost"] + cost
    else:
        s["dn_qty"] = s["dn_qty"] + qty
        s["dn_cost"] = s["dn_cost"] + cost
    s["run_len"] = (s["run_len"] + 1) if s["run_side"] == side else 1
    s["run_side"] = side
    return s


def withheld(state: Dict[str, Any], *, arm: str) -> Optional[str]:
    """Which side, if any, is currently withheld under this arm."""
    cap = arm_cap(arm)
    if state.get("run_side") and int(state.get("run_len") or 0) >= cap:
        return state["run_side"]
    return None


def plan(state: Dict[str, Any], *, arm: str, tte_s: float,
         best_bid_up: Optional[D], best_bid_dn: Optional[D],
         qty: D = DESIRED_PER_SIDE) -> Dict[str, Any]:
    """The quotes this arm would rest right now. Shadow only.

    Both sides at the touch unless one is withheld by the run cap, and nothing
    at all outside the window's quoting band.
    """
    out: Dict[str, Any] = {"arm": arm, "label": LABEL, "quotes": [],
                           "withheld_side": None, "shadow_only": True,
                           "would_submit": False}
    if tte_s > OPEN_FROM_TTE_S or tte_s < CANCEL_UNDER_TTE_S:
        out["reason"] = (f"outside the quoting band "
                         f"[{CANCEL_UNDER_TTE_S:.0f}s, {OPEN_FROM_TTE_S:.0f}s]")
        return out
    hold = withheld(state, arm=arm)
    out["withheld_side"] = hold
    for side, bid in (("Up", best_bid_up), ("Down", best_bid_dn)):
        if bid is None:
            continue
        if side == hold:
            continue
        out["quotes"].append({"side": side, "price": str(bid), "qty": str(qty),
                              "type": "maker_bid", "crosses": False})
    if hold:
        out["reason"] = (f"run of {state['run_len']} on {hold} reached cap "
                         f"{arm_cap(arm)}; that side is withheld until the "
                         f"other fills")
    return out


def economics(state: Dict[str, Any]) -> Dict[str, Any]:
    """Inventory economics. Pair cost is a REFERENCE here, never a gate."""
    up_q, dn_q = state["up_qty"], state["dn_qty"]
    up_v = (state["up_cost"] / up_q) if up_q > 0 else None
    dn_v = (state["dn_cost"] / dn_q) if dn_q > 0 else None
    pair = (up_v + dn_v) if (up_v is not None and dn_v is not None) else None
    matched = min(up_q, dn_q)
    resid = up_q - dn_q
    tot = up_q + dn_q
    return {
        "up_qty": str(up_q), "dn_qty": str(dn_q),
        "up_vwap": (str(up_v) if up_v is not None else None),
        "dn_vwap": (str(dn_v) if dn_v is not None else None),
        "matched_qty": str(matched),
        "pair_cost": (str(pair) if pair is not None else None),
        "pair_cost_vs_reference": (
            None if pair is None
            else ("at_or_under_reference" if pair <= REFERENCE_PAIR_CEILING
                  else "over_reference")),
        "residual_qty": str(abs(resid)),
        "residual_side": (None if resid == 0 else
                          ("Up" if resid > 0 else "Down")),
        "normalized_imbalance": (str(abs(resid) / tot) if tot > 0 else None),
        "labels": ("shadow inventory from EVIDENCED fills only. Never realized "
                   "profit. The reference ceiling gates nothing here."),
    }
