"""GEMINI_EV_REPLAY_V1 -- run a policy over a contract's real tape and settle it.

PRE-REBATE BY CONSTRUCTION. The objective is

    matched P&L + directional residual P&L - confirmed fees

and no rebate term exists anywhere in this module. Rebates are incremental
income; a candidate that needs them to cross zero has not crossed zero.

WHAT A PRINT MEANS, calibrated earlier against 56 confirmed maker fills:
a SELL-aggressor print at p means a YES contract was buyable at p; a BUY
print at b means a NO contract was buyable at 1-b. So the tape is a list of
units we COULD have bought, at prices we COULD have paid, in the order they
were available.

THE CAPTURE ASSUMPTION IS THE WEAK POINT AND IT IS A PARAMETER. `capture`
is the share of qualifying prints we would actually have won. At 1.0 every
figure is an UPPER BOUND on what a participant taking the whole tape could
have extracted; P3 measured us at 33-100% of the BTC15m tape, so that lane is
closest to achievable and the others are not. Results are reported across
capture levels rather than at one flattering value.

SETTLEMENT IS THE VENUE'S OWN. `resolutionSide` from the events endpoint, not
inferred from the last trade. A matched pair pays exactly $1; a residual leg
pays $1 only if its side won.
"""
from __future__ import annotations

from fractions import Fraction as F
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution import ev_policy as EV

LABEL = "GEMINI_EV_REPLAY_V1"
YES, NO = EV.YES, EV.NO


def _f(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, F):
        from decimal import Decimal as D
        v = D(v.numerator) / D(v.denominator)
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def _future_complement(prints, idx, need_side):
    """Complement prices AFTER a decision. DIAGNOSTIC ONLY.

    This function exists to answer, after the fact, whether a third action --
    WAIT -- would have had value. It is called only when assembling the
    diagnostic record and its output never reaches a decision. Passing it into
    the policy would be exactly the future leakage the whole design forbids.
    """
    out = []
    for t, side, px_s, _q in prints[idx + 1:]:
        offered = YES if side == "sell" else (NO if side == "buy" else None)
        if offered != need_side:
            continue
        px = F(px_s)
        out.append(px if offered == YES else (F(1) - px))
    if not out:
        return None
    return {"n": len(out), "min": _f(min(out)), "max": _f(max(out)),
            "last": _f(out[-1]), "first": _f(out[0])}


def replay(contract: Mapping[str, Any], *,
           policy: str = "ev",
           diagnostics: bool = False,
           max_pair_cost: Optional[F] = None,
           max_legs: int = 8,
           max_residual: int = 4,
           capture: F = F(1),
           fee_per_fill: F = F(0),
           min_ev: F = F(0)) -> Dict[str, Any]:
    """Walk one contract's tape under one policy and settle it.

    `policy`:
      "ev"      the expected-value comparison in `ev_policy`
      "cap"     a flat pair-cost cap, the earlier arm, for contrast
      "always"  take every unit, the frozen-controller-like baseline
    """
    prints = sorted(contract.get("prints") or [],
                    key=lambda r: (int(r[0]), str(r[1])))
    st = EV.MarketState()
    y = n = 0
    ys: List[F] = []
    ns: List[F] = []
    fills: List[Dict[str, Any]] = []
    skipped = 0
    take_every = None
    if capture < 1:
        take_every = int(1 / float(capture)) if float(capture) > 0 else None
    seen = 0
    diag: List[Dict[str, Any]] = []

    for _i, (t, side, px_s, qty_s) in enumerate(prints):
        px = F(px_s)
        st.observe(int(t), side, px)
        offered = YES if side == "sell" else (NO if side == "buy" else None)
        if offered is None:
            continue
        # the unit's price on the leg it makes buyable
        unit = px if offered == YES else (F(1) - px)
        if unit <= 0 or unit >= 1:
            continue
        seen += 1
        if take_every and (seen % take_every) != 0:
            continue
        if len(fills) >= max_legs:
            continue
        avg_y = (sum(ys, F(0)) / len(ys)) if ys else None
        avg_n = (sum(ns, F(0)) / len(ns)) if ns else None
        reduces = (offered == NO and y > n) or (offered == YES and n > y)

        take = False
        why = ""
        if policy == "always":
            take = True
            why = "baseline: take every unit"
        elif policy == "cap":
            if reduces:
                leg = avg_y if offered == NO else avg_n
                take = (max_pair_cost is None
                        or (leg is not None and leg + unit <= max_pair_cost))
                why = "flat pair cap"
            else:
                take = abs((y + (1 if offered == YES else 0))
                           - (n + (1 if offered == NO else 0))) <= max_residual
                why = "flat residual cap"
        else:  # ev
            inv = {"yes_qty": y, "no_qty": n,
                   "avg_yes": _f(avg_y), "avg_no": _f(avg_n)}
            d = EV.decide(inventory=inv, offered_side=offered, price=unit,
                          state=st)
            if d["kind"] == EV.COMPLETE:
                take = d["action"] == EV.COMPLETE
                why = "EV: complete beats hold" if take else "EV: hold beats complete"
                if diagnostics:
                    leg = avg_y if offered == NO else avg_n
                    diag.append({
                        "t": int(t), "held_leg": (YES if offered == NO else NO),
                        "leg_cost": _f(leg), "complement_offered": _f(unit),
                        "complete_now": _f(d["complete_value"]),
                        "hold_expected": _f(d["hold_value"]),
                        "decision": d["action"],
                        "future_complement": _future_complement(
                            prints, _i, offered),
                    })
            else:
                take = d.get("ev", F(0)) > min_ev
                why = "EV: acquisition positive" if take else "EV: acquisition negative"
                nyy = y + (1 if offered == YES else 0)
                nnn = n + (1 if offered == NO else 0)
                if take and abs(nyy - nnn) > max_residual:
                    take = False
                    why = "EV positive but residual cap binds"
        if not take:
            skipped += 1
            continue
        if offered == YES:
            y += 1
            ys.append(unit)
        else:
            n += 1
            ns.append(unit)
        fills.append({"t": int(t), "leg": offered, "price": _f(unit),
                      "reduces": reduces, "why": why})

    m = min(y, n)
    r = abs(y - n)
    dom = None if y == n else (YES if y > n else NO)
    spend = sum(ys, F(0)) + sum(ns, F(0))
    avg_y = (sum(ys, F(0)) / len(ys)) if ys else F(0)
    avg_n = (sum(ns, F(0)) / len(ns)) if ns else F(0)
    avg_d = avg_y if dom == YES else (avg_n if dom == NO else F(0))

    res = str(contract.get("resolution") or "").strip().lower()
    won = YES if res in ("yes", "up", "true") else (
        NO if res in ("no", "down", "false") else None)
    fees = fee_per_fill * len(fills)

    out = {
        "symbol": contract.get("symbol"), "duration": contract.get("duration"),
        "day": contract.get("day"), "hour": contract.get("hour"),
        "event_ticker": contract.get("event_ticker"),
        "fills": len(fills), "skipped": skipped,
        "yes_qty": y, "no_qty": n, "matched": m, "residual": r,
        "dominant": dom, "spend": _f(spend), "fees": _f(fees),
        "pair_cost": _f(avg_y + avg_n) if m else None,
        "residual_unit": _f(avg_d) if r else None,
        "resolution": res or None, "settled": won is not None,
    }
    if diagnostics:
        # POST-HOC ONLY. `hold_actual` needs the outcome and therefore could
        # never have been known at the decision; it is recorded so that a WAIT
        # action can be evaluated LATER, never to choose one now.
        for d in diag:
            d["hold_actual"] = (None if won is None
                                else ("1" if d["held_leg"] == won else "0"))
        out["completion_diagnostics"] = diag
    if won is None:
        out["verdict"] = "UNSETTLED"
        return out
    matched_pnl = F(m) * (F(1) - avg_y - avg_n)
    residual_pnl = F(r) * ((F(1) if dom == won else F(0)) - avg_d)
    payout = F(m) + (F(r) if (dom is not None and dom == won) else F(0))
    net = payout - spend - fees
    out.update({
        "verdict": "SETTLED", "winning_leg": won,
        "matched_pnl": _f(matched_pnl), "residual_pnl": _f(residual_pnl),
        "payout": _f(payout), "net": _f(net),
        "identity_holds": bool(matched_pnl + residual_pnl - fees == net),
    })
    return out
