"""Where a settled window's result came from: matched pairs or unmatched
inventory.

`settlement_arithmetic` gives one number. One number cannot say whether a
window lost because its pairs cost too much or because it was left holding
one side, and those call for opposite changes.

PAIRING IS FIRST-IN-FIRST-OUT, in the order the legs were opened: the
earliest UP share is paired with the earliest DOWN share. It is the
convention the venue's own position record uses -- checked on window
2026-11-20 17:05Z, where this arithmetic gives matched +0.0296 and an
unmatched cost of 0.0447 and the venue row reads `realized 0.0296`,
`cost 0.0447`. Average-cost pairing was rejected: it lets cheap late
contracts lower the apparent cost of pairs that were locked before they
were bought.

A matched pair pays 1.00 whichever side wins, so `matched_pnl` does not
depend on the outcome. Everything that does depend on it is in the
unmatched line.

FEES ARE WHAT THE VENUE REPORTED, summed, or None when any order's fee was
not read. REBATES ARE NEVER ESTIMATED: the caller passes what was credited
and source-accounted, and absent that the line is None and is not netted.
Pure arithmetic; it reads nothing and decides nothing.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

D = Decimal
ZERO = D("0")
UP, DOWN = "UP", "DOWN"


def _dec(x: Any) -> Optional[Decimal]:
    if isinstance(x, bool) or x is None:
        return None
    try:
        d = x if isinstance(x, Decimal) else D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def attribute(fills: Iterable[Dict[str, Any]], up_won: Any, *,
              rebates_credited_usd: Any = None) -> Dict[str, Any]:
    """Split one market's settlement into matched and unmatched lines.

    `fills`: rows with side, qty, cost and optionally fee, IN THE ORDER THE
    LEGS WERE OPENED. `up_won`: True, False, or None when not yet settled,
    in which case the outcome-dependent lines are None.
    """
    def no(reason: str) -> Dict[str, Any]:
        return {"ok": False, "reason": reason}

    if up_won is not None and not isinstance(up_won, bool):
        return no("OUTCOME_UNREADABLE")
    lots: Dict[str, List[List[Decimal]]] = {UP: [], DOWN: []}
    fee_total: Optional[Decimal] = ZERO
    total_q = {UP: ZERO, DOWN: ZERO}
    total_c = {UP: ZERO, DOWN: ZERO}
    for f in fills:
        side = str((f or {}).get("side") or "").upper()
        q, c = _dec((f or {}).get("qty")), _dec((f or {}).get("cost"))
        if side not in lots or q is None or c is None or q < 0 or c < 0:
            return no("FILL_UNREADABLE")
        if q == 0:
            continue
        lots[side].append([q, c / q])
        total_q[side] += q
        total_c[side] += c
        fee = _dec((f or {}).get("fee"))
        if fee is None or fee < 0:
            fee_total = None
        elif fee_total is not None:
            fee_total += fee
    matched_q = min(total_q[UP], total_q[DOWN])
    matched_c = ZERO
    for side in (UP, DOWN):
        need = matched_q
        for lot in lots[side]:
            if need <= 0:
                break
            take = min(need, lot[0])
            matched_c += take * lot[1]
            lot[0] -= take
            need -= take
    if total_q[UP] > total_q[DOWN]:
        heavy: Optional[str] = UP
    elif total_q[DOWN] > total_q[UP]:
        heavy = DOWN
    else:
        heavy = None
    un_q = abs(total_q[UP] - total_q[DOWN])
    un_c = (total_c[UP] + total_c[DOWN]) - matched_c
    if up_won is None or heavy is None:
        un_redeem = ZERO if heavy is None else None
    else:
        won = (heavy == UP) == up_won
        un_redeem = un_q if won else ZERO
    un_pnl = None if un_redeem is None else un_redeem - un_c
    matched_pnl = matched_q - matched_c
    rebates = _dec(rebates_credited_usd)
    gross = None if un_pnl is None else matched_pnl + un_pnl
    net = (None if gross is None or fee_total is None
           else gross - fee_total + (rebates or ZERO))
    return {
        "ok": True, "reason": None, "pairing": "FIFO_BY_LEG_OPEN_ORDER",
        "matched": {"qty": matched_q, "cost": matched_c,
                    "pair_cost": (matched_c / matched_q
                                  if matched_q > 0 else None),
                    "redeem": matched_q, "pnl": matched_pnl},
        "unmatched": {"side": heavy, "qty": un_q, "cost": un_c,
                      "redeem": un_redeem, "pnl": un_pnl},
        "fees_usd": fee_total,
        "rebates_credited_usd": rebates,
        "settlement_pnl": gross, "net_usd": net,
        "labels": ("matched pays 1.00 a pair whichever side wins; the "
                   "unmatched line carries all of the outcome. fees are the "
                   "venue's own, None if any was unread. rebates are only "
                   "what was credited: None is not zero and is not netted."),
    }
