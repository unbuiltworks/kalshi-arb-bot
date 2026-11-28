"""The complete L5 sequence on PMUS, as a chronological trajectory.

WHY NOT PAIR COST. Judging a window by whether its matched pairs individually
cost under $1 rebuilds the pair ceiling this programme already falsified: on
L5's own BTC15m history a <=0.96 ceiling produced ZERO completed windows and
-206.18, against 1016 windows and +1.50 with no ceiling at all. L5's own
corpus says temporary pairs exceed $1, that opposite acquisition can initially
WORSEN effective pair cost, and that continued recycling repairs it -- 84.3% of
his imbalance episodes end by overshooting THROUGH zero, and exactly one of
5,546 windows ended flat.

So a window is not rejected for an expensive intermediate pair. What is
measured is whether the SEQUENCE happens:

    initial acquisition -> repeated same-side -> opposite acquisition ->
    continued recycling -> matched growth -> pair-cost trajectory ->
    residual trajectory -> terminal settlement economics

and the final economics are matched inventory PLUS directional residual PLUS
rebates minus fees, never the profitability of any single pair.

THE RESIDUAL IS NOW MARKED, because it can be. Earlier it was left unmarked on
the grounds that its value is a settlement outcome; for an EXPIRED window the
venue publishes that outcome, so the honest thing is to use it. A window still
OPEN is still unmarked, and `settlementPx` is only read when the venue says
MARKET_STATE_EXPIRED -- a default 0.0000 on a live market is not a result.

Everything is SHADOW. No order existed.
"""
from __future__ import annotations

import json
import urllib.request
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_fees as fees

GATEWAY = "https://gateway.polymarket.us"
UA = {"User-Agent": "Mozilla/5.0 (l5 sequence trajectory; read-only)",
      "Accept": "application/json"}
PAR = D("1")


def settlement(slug: str) -> Dict[str, Any]:
    """The window's outcome, ONLY when the venue says it has expired."""
    try:
        d = json.load(urllib.request.urlopen(
            urllib.request.Request("%s/v1/markets/%s/bbo" % (GATEWAY, slug),
                                   headers=UA), timeout=25))
        m = d.get("marketData") or {}
    except Exception as exc:                                   # noqa: BLE001
        return {"state": None, "yes_value": None,
                "why": "unreadable: %s" % type(exc).__name__}
    state = m.get("state")
    if state != "MARKET_STATE_EXPIRED":
        return {"state": state, "yes_value": None,
                "why": "not expired; a default 0.0000 is not a result"}
    try:
        v = D(str((m.get("settlementPx") or {}).get("value")))
    except Exception:                                          # noqa: BLE001
        return {"state": state, "yes_value": None,
                "why": "settlement price unreadable"}
    return {"state": state, "yes_value": str(v),
            "last_trade": (m.get("lastTradePx") or {}).get("value"),
            "why": "venue-published settlement for an expired window"}


def trajectory(acquisitions: Sequence[Mapping[str, Any]],
               slug: str,
               settle: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Walk one bound's acquisitions in order and keep the whole path."""
    up = dn = 0
    up_cost = dn_cost = D("0")
    rebate = D("0")
    path: List[dict] = []
    pair_costs: List[D] = []
    signs: List[int] = []
    first_side = None
    first_opposite_at = None
    acq_after_first_opposite = 0

    for i, a in enumerate(sorted(acquisitions, key=lambda x: x["t"])):
        px = D(str(a["price"]))
        side_up = a["side"] == "UP"
        if first_side is None:
            first_side = a["side"]
        elif first_opposite_at is None and a["side"] != first_side:
            first_opposite_at = i
        elif first_opposite_at is not None:
            acq_after_first_opposite += 1
        if side_up:
            up += 1
            up_cost += px
        else:
            dn += 1
            dn_cost += px
        rebate += fees.maker_rebate(px)
        matched = min(up, dn)
        resid = up - dn
        pc = None
        if matched:
            pc = ((up_cost / up if up else D(0))
                  + (dn_cost / dn if dn else D(0)))
            pair_costs.append(pc)
        signs.append(1 if resid > 0 else (-1 if resid < 0 else 0))
        path.append({
            "i": i, "t": a["t"], "side": a["side"], "price": str(px),
            "evidence": a.get("evidence"),
            "up": up, "dn": dn,
            "matched": matched, "residual": resid,
            "effective_pair_cost": (str(pc.quantize(D("0.0001")))
                                    if pc is not None else None),
        })

    nz = [s for s in signs if s != 0]
    crossed = any(x != y for x, y in zip(nz, nz[1:]))
    maxpc = max(pair_costs) if pair_costs else None
    minpc = min(pair_costs) if pair_costs else None
    finalpc = pair_costs[-1] if pair_costs else None
    exceeded = maxpc is not None and maxpc > PAR
    repaired = bool(exceeded and finalpc is not None and finalpc < maxpc)
    repaired_below_par = bool(exceeded and finalpc is not None
                              and finalpc < PAR)

    matched = min(up, dn)
    matched_cost = (finalpc * matched) if (finalpc is not None and matched) \
        else D("0")
    matched_pnl = D(matched) - matched_cost

    resid = up - dn
    resid_cost = D("0")
    if resid > 0 and up:
        resid_cost = (up_cost / up) * abs(resid)
    elif resid < 0 and dn:
        resid_cost = (dn_cost / dn) * abs(resid)
    resid_pnl = None
    resid_value = None
    yv = (settle or {}).get("yes_value")
    if yv is not None and resid != 0:
        y = D(str(yv))
        # a UP unit pays the YES settlement; a DOWN unit pays 1 - YES
        unit = y if resid > 0 else (PAR - y)
        resid_value = unit * abs(resid)
        resid_pnl = resid_value - resid_cost
    elif resid == 0:
        resid_value = D("0")
        resid_pnl = D("0")

    total = None
    if resid_pnl is not None:
        total = matched_pnl + resid_pnl + rebate

    return {
        "market": slug,
        "acquisitions": len(path),
        "first_side": first_side,
        "repeated_same_side_before_opposite": (
            first_opposite_at if first_opposite_at is not None else len(path)),
        "opposite_acquired": first_opposite_at is not None,
        "acquisitions_after_first_opposite": acq_after_first_opposite,
        "continued_recycling_after_opposite": acq_after_first_opposite > 0,
        "matched_final": matched,
        "residual_final": resid,
        "residual_direction": ("UP" if resid > 0 else
                               "DOWN" if resid < 0 else "FLAT"),
        "inventory_crossed_zero": crossed,
        "max_temporary_pair_cost": (str(maxpc.quantize(D("0.0001")))
                                    if maxpc is not None else None),
        "min_pair_cost": (str(minpc.quantize(D("0.0001")))
                          if minpc is not None else None),
        "final_pair_cost": (str(finalpc.quantize(D("0.0001")))
                            if finalpc is not None else None),
        "pair_cost_exceeded_par": exceeded,
        "pair_cost_repaired": repaired,
        "pair_cost_repaired_below_par": repaired_below_par,
        "matched_pair_pnl_usd": str(matched_pnl.quantize(D("0.0001"))),
        "residual_cost_basis_usd": str(resid_cost.quantize(D("0.0001"))),
        "residual_settlement_value_usd": (
            str(resid_value.quantize(D("0.0001")))
            if resid_value is not None else None),
        "residual_pnl_usd": (str(resid_pnl.quantize(D("0.0001")))
                             if resid_pnl is not None else None),
        "maker_rebate_accrual_usd": str(rebate.quantize(D("0.0001"))),
        "taker_fees_usd": "0.0000",
        "liquidity_rewards_usd": "0.00",
        "total_settlement_implied_shadow_usd": (
            str(total.quantize(D("0.0001"))) if total is not None else None),
        "total_note": ("matched + residual + rebate accrual. SHADOW: no order "
                       "existed, so none of it is realized." if total is not None
                       else "residual unmarked because the window has not "
                            "expired; no total is offered"),
        "settlement": settle,
        "path": path,
        "labels": "SHADOW. A window is NEVER rejected here for an expensive "
                  "intermediate pair -- the pair ceiling was falsified on "
                  "L5's own history.",
    }
