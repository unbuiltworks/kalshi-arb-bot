"""A HARD RISK GOVERNOR THAT CAN ACTUALLY FIRE, kept apart from realized P&L.

THE DEFECT THIS REPLACES. `RiskBudget.daily_realized_loss_stop_usd` is keyed
to `realized_usd`, which moves only on a venue-confirmed cash movement. No
attributable cashflow has EVER been observed on this venue -- Gemini REALIZED
is $0.00 across every window. A stop keyed to a quantity that never moves
cannot fire, and presenting it as a loss control is worse than having none,
because it reads like protection.

The fix is NOT to start calling a mark realized. It is to govern on the three
quantities the venue actually gives us, and to label each for exactly what it
is:

  CAPITAL AT RISK      cash that has LEFT on fills whose contract has not yet
                       resolved. Observed: fill price x filled quantity, from
                       the canonical execution view. Certain, and it is not a
                       loss -- it is money that is out.

  DETERMINED LOSS      a position on a contract the venue HAS resolved against
                       it. The outcome is published (`metadata.resolution_for`
                       reads it; the settlement gate proved that), so the loss
                       is DETERMINED even though no cash confirmation exists.
                       DETERMINED IS NOT REALIZED and this module never says
                       otherwise -- but it is knowable, and a loss control has
                       to run on what is knowable.

  RESIDUAL EXPOSURE    unmatched directional units outstanding, run-level. The
                       adapted-policy decomposition says the residual's sign
                       is adversely selected (it wins 0.3421 of the time), so
                       this is the exposure most worth bounding.

FAIL-CLOSED ON AN UNREADABLE OUTCOME. A contract that has expired but whose
outcome cannot be read is charged at its FULL COST against the determined-loss
governor, because a governor that waits for a readable outcome is a governor
that does not fire. When the outcome later reads as a win, the charge is
released. Fail-closed costs a false stop; fail-open costs money.

NOTHING HERE IS REALIZED P&L, is added to it, or is netted against it. The
seven-line ledger in `rebates.realized_ledger` is untouched, and every number
this module produces carries the label that keeps it out.

BOUNDS TIGHTEN OR STOP. `RiskGovernor` is frozen and has no method that
widens anything. Tightening means constructing a narrower one, which is an
act outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "GEMINI_L5_RISK_GOVERNOR_V1"

# ------------------------------------------------------------ stop states
CAPITAL_AT_RISK_EXCEEDED = "CAPITAL_AT_RISK_EXCEEDED"
DETERMINED_LOSS_STOP = "DETERMINED_LOSS_STOP"
RESIDUAL_EXPOSURE_EXCEEDED = "RESIDUAL_EXPOSURE_EXCEEDED"
TOTAL_COMMITTED_EXCEEDED = "TOTAL_COMMITTED_EXCEEDED"
OUTCOME_UNREADABLE = "OUTCOME_UNREADABLE"

GOVERNOR_STATES = (CAPITAL_AT_RISK_EXCEEDED, DETERMINED_LOSS_STOP,
                   RESIDUAL_EXPOSURE_EXCEEDED, TOTAL_COMMITTED_EXCEEDED,
                   OUTCOME_UNREADABLE)

NOT_REALIZED = ("DETERMINED, NOT REALIZED. No venue cash confirmation exists "
                "on this account. This number governs risk; it is never "
                "added to, netted against, or reported as realized P&L.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class RiskGovernor:
    """Hard bounds that can fire from observable state alone.

    Every one of these is computable today. None of them needs a cash
    confirmation, and none of them can widen itself.
    """
    #: cash out the door on contracts that have not resolved
    max_capital_at_risk_usd: str = "0.40"
    #: cumulative loss on contracts the venue HAS resolved against us
    max_determined_loss_usd: str = "0.25"
    #: run-level unmatched directional units
    max_residual_units: str = "0.04"
    #: everything ever committed, the outermost bound
    max_total_committed_usd: str = "1.00"

    def labels(self) -> str:
        return ("bounds TIGHTEN or STOP; nothing here can self-increase. "
                "Every bound fires from OBSERVED state -- fills, published "
                "contract outcomes and inventory -- and none of them waits "
                "on a cash confirmation this venue has never produced.")


def capital_at_risk(positions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Cash committed to contracts that have not resolved. Certain, not a loss.

    A position is `{symbol, leg, qty, price, resolved (bool|None), won
    (bool|None)}`. `resolved is None` means the outcome has not been read.
    """
    total = D("0")
    rows: List[Dict[str, Any]] = []
    for p in positions or []:
        q, px = _dec(p.get("qty")), _dec(p.get("price"))
        if q is None or px is None:
            rows.append({"symbol": p.get("symbol"), "cost_usd": None,
                         "why": "qty or price unreadable"})
            continue
        cost = q * px
        open_ = not bool(p.get("resolved"))
        if open_:
            total += cost
        rows.append({"symbol": p.get("symbol"), "leg": p.get("leg"),
                     "cost_usd": str(cost), "counted": open_})
    return {"capital_at_risk_usd": str(total), "positions": rows,
            "labels": ("cash that has LEFT on unresolved contracts. It is "
                       "certain and it is NOT a loss -- it is money that is "
                       "out and may not come back.")}


def determined_loss(positions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Loss on contracts the venue has RESOLVED against us, plus the
    fail-closed charge for any expired contract whose outcome cannot be read.

    A binary prediction contract pays $1 per winning unit and $0 per losing
    one, so a resolved-against position's loss is exactly its cost. No mark,
    no model, no estimate.
    """
    lost = D("0")
    assumed = D("0")
    rows: List[Dict[str, Any]] = []
    for p in positions or []:
        q, px = _dec(p.get("qty")), _dec(p.get("price"))
        if q is None or px is None:
            rows.append({"symbol": p.get("symbol"),
                         "why": "qty or price unreadable", "charged": None})
            continue
        cost = q * px
        resolved, won, expired = (p.get("resolved"), p.get("won"),
                                  bool(p.get("expired")))
        if resolved and won is False:
            lost += cost
            rows.append({"symbol": p.get("symbol"), "leg": p.get("leg"),
                         "charged_usd": str(cost), "basis": "RESOLVED_AGAINST"})
        elif resolved and won is True:
            rows.append({"symbol": p.get("symbol"), "leg": p.get("leg"),
                         "charged_usd": "0", "basis": "RESOLVED_FOR"})
        elif expired:
            # FAIL CLOSED. An expired contract with an unreadable outcome is
            # charged in full until the outcome can be read.
            assumed += cost
            rows.append({"symbol": p.get("symbol"), "leg": p.get("leg"),
                         "charged_usd": str(cost),
                         "basis": "EXPIRED_OUTCOME_UNREADABLE_ASSUMED_LOSS"})
        else:
            rows.append({"symbol": p.get("symbol"), "leg": p.get("leg"),
                         "charged_usd": "0", "basis": "NOT_YET_RESOLVED"})
    return {
        "determined_loss_usd": str(lost + assumed),
        "resolved_against_usd": str(lost),
        "assumed_pending_outcome_usd": str(assumed),
        "positions": rows,
        "labels": NOT_REALIZED,
        "fail_closed": ("an expired contract whose outcome cannot be read is "
                        "charged at FULL COST until it can be; a governor "
                        "that waits for a readable outcome does not fire"),
    }


def residual_exposure(positions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Unmatched directional units outstanding, across the whole run.

    Matched YES and NO on the SAME contract offset; different contracts do
    not, because they are different bets.
    """
    by_symbol: Dict[str, Dict[str, D]] = {}
    for p in positions or []:
        q = _dec(p.get("qty"))
        if q is None:
            continue
        sym = str(p.get("symbol"))
        leg = str(p.get("leg") or "").upper()
        row = by_symbol.setdefault(sym, {"YES": D("0"), "NO": D("0")})
        if leg in row:
            row[leg] += q
    total = D("0")
    rows = []
    for sym, r in sorted(by_symbol.items()):
        resid = abs(r["YES"] - r["NO"])
        total += resid
        rows.append({"symbol": sym, "yes": str(r["YES"]), "no": str(r["NO"]),
                     "residual": str(resid),
                     "matched": str(min(r["YES"], r["NO"]))})
    return {"residual_units": str(total), "by_symbol": rows,
            "labels": ("unmatched directional units. The adapted-policy "
                       "decomposition says this exposure is adversely "
                       "selected -- it wins 0.3421 of the time -- so it is "
                       "the one most worth bounding.")}


def evaluate(gov: RiskGovernor, positions: Sequence[Mapping[str, Any]], *,
             total_committed_usd: Any = "0") -> Dict[str, Any]:
    """May the run continue? A refusal names the bound and the number.

    Checked in order of how directly each bound protects capital.
    """
    car = capital_at_risk(positions)
    dl = determined_loss(positions)
    re_ = residual_exposure(positions)
    committed = _dec(total_committed_usd)

    checks: List[Dict[str, Any]] = []

    def check(state, measured, bound, name):
        m, b = _dec(measured), _dec(bound)
        breached = (m is not None and b is not None and m > b)
        checks.append({"bound": name, "measured": str(m) if m is not None
                       else None, "limit": str(b) if b is not None else None,
                       "breached": breached, "state": state})
        return breached

    stopped = None
    if check(TOTAL_COMMITTED_EXCEEDED, committed,
             gov.max_total_committed_usd, "max_total_committed_usd"):
        stopped = TOTAL_COMMITTED_EXCEEDED
    if check(DETERMINED_LOSS_STOP, dl["determined_loss_usd"],
             gov.max_determined_loss_usd, "max_determined_loss_usd") \
            and stopped is None:
        stopped = DETERMINED_LOSS_STOP
    if check(CAPITAL_AT_RISK_EXCEEDED, car["capital_at_risk_usd"],
             gov.max_capital_at_risk_usd, "max_capital_at_risk_usd") \
            and stopped is None:
        stopped = CAPITAL_AT_RISK_EXCEEDED
    if check(RESIDUAL_EXPOSURE_EXCEEDED, re_["residual_units"],
             gov.max_residual_units, "max_residual_units") \
            and stopped is None:
        stopped = RESIDUAL_EXPOSURE_EXCEEDED

    return {
        "label": LABEL,
        "admits": stopped is None,
        "state": stopped,
        "why": (None if stopped is None else
                next(c for c in checks if c["breached"])["bound"]
                + " breached: %s > %s" % (
                    next(c for c in checks if c["breached"])["measured"],
                    next(c for c in checks if c["breached"])["limit"])),
        "checks": checks,
        "capital_at_risk": car,
        "determined_loss": dl,
        "residual_exposure": re_,
        "bounds": {k: getattr(gov, k) for k in
                   ("max_capital_at_risk_usd", "max_determined_loss_usd",
                    "max_residual_units", "max_total_committed_usd")},
        "bound_labels": gov.labels(),
        "separation": ("THIS IS NOT REALIZED ACCOUNTING. The seven-line "
                       "realized ledger is untouched by every number here, "
                       "and no governor figure is added to it, netted "
                       "against it, or reported as realized."),
    }


def positions_from_executions(view: Any, *,
                              resolutions: Optional[Mapping[str, Any]] = None
                              ) -> List[Dict[str, Any]]:
    """Build governor positions from the CANONICAL execution view.

    One source, the same one the controller and the coherence gate read, so a
    governor can never be looking at a different set of fills from the thing
    it is governing.
    """
    from src.gemini_execution import executions as EX
    fills = EX.as_fills(view) or []
    res = dict(resolutions or {})
    out: List[Dict[str, Any]] = []
    for f in fills:
        sym = str(f.get("symbol") or (view or {}).get("symbol") or "")
        r = res.get(sym) or {}
        leg = str(f.get("leg") or "").upper()
        side = str(r.get("resolution_side") or r.get("side") or "").upper()
        won = None
        if r.get("resolved") and side in ("YES", "NO") and leg in ("YES", "NO"):
            won = (side == leg)
        out.append({"symbol": sym, "leg": leg, "qty": f.get("qty"),
                    "price": f.get("price"),
                    "resolved": bool(r.get("resolved")),
                    "won": won, "expired": bool(r.get("expired"))})
    return out
