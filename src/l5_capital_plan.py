"""Capital accounting for a BTC15m window, with the six quantities apart.

WHY SEPARATELY. The engine already has gates for cash and for worst-case
exposure and they are both correct for what they measure, but nothing
reports them side by side -- so a $1 authorization meant as a RISK bound was
configured onto the CASH gate, where one matched pair at a 0.95 basis
consumes 95% of it while carrying no risk at all. That is not a defect in
either gate; it is two different questions answered by one number. This
module answers them separately and changes nothing.

THE SIX, and why none of them substitutes for another:

  cash_committed        fill cost that has LEFT the account and has not been
                        redeemed. Real, irrecoverable until settlement.
  outstanding_reserve   the worst cash a RESTING order can still take. Per
                        side via `max`, never the sum: if both sides of a
                        pair fill they match each other, so charging both
                        would double-charge a pair. Never the signed net
                        either -- that is the 1415z error, where UP 2.17
                        held against DOWN 2.17 resting netted to 0.00 and a
                        gate read the book as flat.
  unmatched_worst_case  what an unpaired leg loses if it settles worthless.
                        This is what a RISK bound should charge.
  fees_paid             separate from trading P&L, always. A maker rebate is
                        never netted here and a zero commission is not a
                        rebate.
  realized_settled      money the venue has actually paid. The only figure
                        that is profit.
  buying_power          envelope - cash_committed - outstanding_reserve.

THE RULE THIS MODULE EXISTS TO ENFORCE: a matched pair's $1.00 redemption is
NOT buying power. It is certain, it is risk-free, and it is not cash until
the market settles. `expected_redemption_usd` is reported so the distinction
is visible, and it is never added to `buying_power_usd`. Treating it as
available is how an account spends the same dollar twice.

NOTHING HERE IS A LIMIT, A GATE OR A RESERVATION RELEASE. It reads state and
reports. Every gate in `l5_live_validation_gates` is untouched and remains
the only thing that refuses an order.
"""
from decimal import Decimal as D
from typing import Any, Dict, Iterable, Mapping, Optional

from src.l5_two_sided_probe import _d

#: the envelopes the staged plan is designed against
STAGES = (D("10"), D("25"), D("50"), D("200"))


def _read(src: Any, key: str) -> Optional[D]:
    """Absent means zero; present-but-unreadable means UNKNOWN."""
    m = src or {}
    if key not in m:
        return D(0)
    v = m.get(key)
    if v is None:
        return None
    return _d(v)


def outstanding_reserve(live_orders: Iterable[Mapping[str, Any]],
                        *, up_key: str = "UP", down_key: str = "DOWN"
                        ) -> Optional[D]:
    """Worst cash resting orders can still take, per side via `max`.

    An order whose side or price cannot be read is charged on BOTH sides at
    a dollar a share, which is the most a binary can cost: a resting order
    read as free is how an envelope is spent without the gate seeing it.
    """
    per = {up_key: D(0), down_key: D(0)}
    unknown = D(0)
    for o in live_orders or ():
        q = _d((o or {}).get("qty"))
        if q is None or q < 0:
            return None
        px = _d((o or {}).get("price"))
        side = str((o or {}).get("side") or "").strip().upper()
        if px is None or px <= 0 or px > 1:
            unknown += q                      # a dollar a share
            continue
        if side in per:
            per[side] += q * px
        else:
            unknown += q
    return max(per[up_key], per[down_key]) + unknown


def account(*, positions: Any, leg_cost: Any,
            live_orders: Iterable[Mapping[str, Any]] = (),
            cash_envelope_usd: Any,
            fees_paid_usd: Any = 0,
            realized_settled_usd: Any = 0,
            up_key: str = "UP", down_key: str = "DOWN") -> Dict[str, Any]:
    """The six quantities, plus buying power. Read-only.

    Returns `readable: False` with a reason on any input it cannot read --
    never a guess, because an unreadable book reported as having headroom is
    the one answer that can lose money.
    """
    def no(reason: str) -> Dict[str, Any]:
        return {"readable": False, "reason": reason}

    env = _d(cash_envelope_usd)
    fees = _d(fees_paid_usd)
    settled = _d(realized_settled_usd)
    if env is None or env < 0:
        return no("ENVELOPE_UNREADABLE")
    if fees is None or settled is None:
        return no("FEES_OR_SETTLEMENT_UNREADABLE")

    up, dn = _read(positions, up_key), _read(positions, down_key)
    cu, cd = _read(leg_cost, up_key), _read(leg_cost, down_key)
    if up is None or dn is None or cu is None or cd is None:
        return no("POSITIONS_OR_COST_UNREADABLE")
    aup, adn = abs(up), abs(dn)

    reserve = outstanding_reserve(live_orders, up_key=up_key,
                                  down_key=down_key)
    if reserve is None:
        return no("OUTSTANDING_UNREADABLE")

    cash_committed = cu + cd
    matched = min(aup, adn)
    unmatched = abs(aup - adn)
    bu = (cu / aup) if aup > 0 else D(0)
    bd = (cd / adn) if adn > 0 else D(0)
    pair_basis = (bu + bd) if matched > 0 else D(0)
    matched_cash = matched * pair_basis
    unmatched_basis = bu if aup > adn else bd
    unmatched_worst = unmatched * unmatched_basis

    buying_power = env - cash_committed - reserve
    return {
        "readable": True,
        "cash_envelope_usd": str(env),
        # 1. cash that has left the account
        "cash_committed_usd": str(cash_committed.quantize(D("0.0001"))),
        # 2. what resting orders can still take
        "outstanding_reserve_usd": str(reserve.quantize(D("0.0001"))),
        # 3. what a RISK bound should charge
        "unmatched_qty": str(unmatched),
        "unmatched_worst_case_usd": str(unmatched_worst.quantize(
            D("0.0001"))),
        # 4. fees, never netted into P&L
        "fees_paid_usd": str(fees),
        # 5. the only figure that is profit
        "realized_settled_usd": str(settled),
        # 6. what may still be deployed
        "buying_power_usd": str(buying_power.quantize(D("0.0001"))),
        # context
        "matched_qty": str(matched),
        "matched_pair_basis": str(pair_basis.quantize(D("0.0001"))),
        "matched_cash_usd": str(matched_cash.quantize(D("0.0001"))),
        # CERTAIN, RISK-FREE, AND NOT CASH. Reported so the distinction is
        # visible; deliberately absent from buying_power_usd.
        "expected_redemption_usd": str(matched.quantize(D("0.0001"))),
        "expected_redemption_is_not_buying_power": True,
        "labels": ("Cash committed, outstanding reservations, unmatched "
                   "worst case, fees, realized settlement and buying power "
                   "are six different questions and are reported "
                   "separately. A matched pair's $1.00 redemption is "
                   "certain and risk-free and is NOT buying power until "
                   "the market settles. This module reads state and "
                   "reports; it is not a gate and releases nothing."),
    }


def rounds_affordable(*, cash_envelope_usd: Any, pair_cost_usd: Any,
                      already_committed_usd: Any = 0) -> Dict[str, Any]:
    """How many pairs one window's envelope can hold at a given pair cost.

    THE ANSWER IS FLOOR, NOT AN ESTIMATE, because cash does not recycle
    inside a window: each completed pair's outlay stays committed until
    settlement, so the envelope divides rather than revolves. That is the
    whole reason a $1 envelope buys exactly one pair.
    """
    env = _d(cash_envelope_usd)
    cost = _d(pair_cost_usd)
    used = _d(already_committed_usd)
    if env is None or cost is None or used is None or cost <= 0:
        return {"readable": False, "reason": "UNREADABLE"}
    room = env - used
    n = int(room / cost) if room > 0 else 0
    return {
        "readable": True,
        "cash_envelope_usd": str(env),
        "pair_cost_usd": str(cost),
        "already_committed_usd": str(used),
        "rounds_affordable": n,
        "cash_after_n_rounds_usd": str((used + (n * cost)).quantize(
            D("0.0001"))),
        "worst_case_if_every_round_strands_one_leg_usd": str(
            (n * cost / 2).quantize(D("0.0001"))),
        "note": ("floor, not an estimate: cash does not recycle inside a "
                 "window, so the envelope divides rather than revolves. "
                 "The worst case assumes HALF of each pair strands, which "
                 "is the shape 1230z produced."),
    }
