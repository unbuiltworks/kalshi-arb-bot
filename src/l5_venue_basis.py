"""A market's result from the VENUE's confirmed executions. PURE ARITHMETIC.

THE DEFECT (measured 2026-11-16, cpc-btc-updown-5m-2026-11-16-0335z). Another
executor on this account bought UP 2 @ 0.08 and, fifteen seconds later,
DOWN 2 @ 0.75: a pair costing 1.66 that pays 2.00. The account ledger learns
of positions it did not open from the venue's NET position (UP minus DOWN).
It read the net in the gap between the two fills, adopted "UP 2" at the $1
bound, read net 0 fifteen seconds later, kept UP 2 / DOWN 0 on its book and
booked -2.00 at settlement. Nine other pairs that day filled entirely
between two reads, showed net 0 both times and were never booked at all.

A net-position snapshot is evidence of EXPOSURE at one instant. It is not a
trading history, and a result cannot be derived from it. This module derives
the result from the executions themselves.

WHAT IS HERE. Functions over already-normalized rows. No I/O, no clock, no
store, no environment: stdlib and Decimal only.

WHAT IT REFUSES TO DO.
  * It never guesses. An unreadable row, an unknown action, a settlement
    value that is not 0 or 1, or a feed that was not read to its explicit
    end gives `complete` False and `pnl` None.
  * It never APPLIES anything. `compare` reports a difference; an UPWARD
    difference (the ledger is more pessimistic than the venue) is pending
    the owner's existing correction paths and is never netted against a
    downward one. `downward_adjustment` is what the ledger is MISSING in
    losses, and is the only figure a conservative reader should act on.
  * A result whose settlement cash is not CONFIRMED is labelled as settled
    ARITHMETIC on confirmed fills. It is not realized cash.

ROW FORMAT (one market's fills). Each row is a mapping:
    side    "UP" or "DOWN"            the outcome held
    action  "BUY" or "SELL"           absent means BUY; anything else is
                                      unreadable
    qty     > 0
    price   the OWN-OUTCOME price per share, 0 < price < 1 (a DOWN share
            bought while YES trades at 0.25 has price 0.75)
    fee     charged on the fill; negative is a credited rebate. ABSENT IS
            NOT ZERO: a row without a fee is unreadable
    id      the venue's execution id; required, and never repeated
    t       optional, carried through untouched
"""
from __future__ import annotations

from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional

UP = "UP"
DOWN = "DOWN"

#: |venue - booked| at or below this is agreement (half the ledger's 4dp).
AGREE_TOLERANCE = D("0.00005")
#: a confirmed settlement credit may differ from the arithmetic payout by
#: at most a cent of the venue's own rounding.
CREDIT_TOLERANCE = D("0.005")

CASH_CONFIRMED = "CONFIRMED"
CASH_NOT_CONFIRMED = "NOT_CONFIRMED"
CASH_NOT_CHECKED = "NOT_CHECKED"

AGREES = "AGREES"
VENUE_INCOMPLETE = "VENUE_INCOMPLETE"
VENUE_HAS_NO_FILLS = "VENUE_HAS_NO_FILLS"
UNBOOKED = "UNBOOKED"
BOOKED_MORE_PESSIMISTIC = "BOOKED_MORE_PESSIMISTIC"
BOOKED_TOO_OPTIMISTIC = "BOOKED_TOO_OPTIMISTIC"
SETTLEMENT_CREDIT_DISAGREES = "SETTLEMENT_CREDIT_DISAGREES"
LEDGER_UNREADABLE = "LEDGER_UNREADABLE"
CLASSES = (AGREES, VENUE_INCOMPLETE, VENUE_HAS_NO_FILLS, UNBOOKED,
           BOOKED_MORE_PESSIMISTIC, BOOKED_TOO_OPTIMISTIC,
           SETTLEMENT_CREDIT_DISAGREES, LEDGER_UNREADABLE)

PATH_ATTESTATION = "ATTESTATION (l5_venue_cost_attestation, exact quantities)"
PATH_SUPERSET = ("SUPERSET CORRECTION (l5_venue_superset_correction, "
                 "owner-listed manifest)")
PATH_UNBOOKED = "NO PATH: UNBOOKED MARKET"
PATH_NOT_AT_BOUND = "NO PATH: ROW NOT AT THE $1 BOUND"
PATH_NOT_CONTAINED = "NO PATH: LEDGER QUANTITY NOT CONTAINED IN VENUE FILLS"
PATH_SELLS = "NO PATH: VENUE FILLS INCLUDE SELLS"
PATH_CREDIT = "NO PATH: SETTLEMENT CREDIT DISAGREES WITH FILLS"

MAX_LISTED = 40

LABEL_ARITHMETIC = ("SETTLED ARITHMETIC ON CONFIRMED FILLS: payout computed "
                    "from the settlement value, NOT a confirmed cash credit. "
                    "Not realized cash.")
LABEL_CONFIRMED = ("venue-confirmed fills, fees and settlement credit agree")
LABEL_CREDIT_DISAGREES = ("the confirmed settlement credit DISAGREES with "
                          "the payout the fills imply; neither is trusted")
LABEL_INCOMPLETE = "INCOMPLETE: no result is stated"


def dec(value: Any) -> Optional[D]:
    """A finite Decimal, or None. None is UNKNOWN, never zero."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        out = value if isinstance(value, D) else D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


class Position(dict):
    """`position_from_fills`'s answer: a plain dict, plus one question."""

    def consistent_with_net(self, venue_net: Any) -> bool:
        """Is the venue's NET position (UP minus DOWN) what these fills
        add up to? False when either side is unreadable -- never a guess."""
        want = dec(venue_net)
        if want is None or not self.get("readable"):
            return False
        return self["net_up_minus_down"] == want


def position_from_fills(fills: Iterable[Any]) -> Position:
    """Per-side quantity and cost from executions, and their net.

    A BUY adds quantity and cost. A SELL removes quantity and credits its
    proceeds against that side's cost (so the cost can go negative: a
    position sold for more than it cost). Selling more than was bought is
    unreadable here -- the missing buy is somewhere this caller did not
    look.
    """
    qty = {UP: D(0), DOWN: D(0)}
    cost = {UP: D(0), DOWN: D(0)}
    fees = D(0)
    ids: List[str] = []
    problems: List[str] = []
    sells = 0
    n = 0
    for f in (fills or ()):
        n += 1
        if not isinstance(f, Mapping):
            problems.append("ROW_NOT_A_MAPPING")
            continue
        side = str(f.get("side") or "").strip().upper()
        action = str(f.get("action") or "BUY").strip().upper()
        q, px, fee = dec(f.get("qty")), dec(f.get("price")), dec(f.get("fee"))
        rid = str(f.get("id") or "").strip()
        if side not in (UP, DOWN):
            problems.append("SIDE_UNREADABLE")
            continue
        if action not in ("BUY", "SELL"):
            problems.append("ACTION_UNREADABLE")
            continue
        if q is None or q <= 0:
            problems.append("QTY_UNREADABLE")
            continue
        if px is None or not (D(0) < px < D(1)):
            problems.append("PRICE_UNREADABLE")
            continue
        if fee is None:
            problems.append("FEE_ABSENT")
            continue
        if not rid:
            problems.append("ID_ABSENT")
            continue
        if rid in ids:
            problems.append("ID_REPEATED")
            continue
        ids.append(rid)
        fees += fee
        if action == "BUY":
            qty[side] += q
            cost[side] += q * px
        else:
            sells += 1
            qty[side] -= q
            cost[side] -= q * px
    for side in (UP, DOWN):
        if qty[side] < 0:
            problems.append("SOLD_MORE_THAN_BOUGHT:%s" % side)
    return Position({
        "up_qty": qty[UP], "dn_qty": qty[DOWN],
        "up_cost": cost[UP], "dn_cost": cost[DOWN],
        "cost": cost[UP] + cost[DOWN], "fees": fees,
        "net_up_minus_down": qty[UP] - qty[DOWN],
        "fills": n, "sells": sells, "fill_ids": ids,
        "readable": not problems,
        "problems": sorted(set(problems))})


def market_result(fills: Iterable[Any], yes_value: Any, *,
                  feed_complete: bool,
                  settlement_credit: Any = None) -> Dict[str, Any]:
    """One market's result from its venue executions.

    `yes_value` is the settlement value of the UP/YES outcome: exactly 0 or
    1. None is accepted ONLY when the fills hold the same quantity on both
    sides, because a matched pair pays the same either way -- that is
    arithmetic, not a guess, and the result says so (`outcome_independent`).

    `settlement_credit`: None = the caller did not look (NOT_CHECKED);
    the string "NOT_CONFIRMED" = it looked and the venue has not credited
    it; a number = the venue's confirmed credit, in the same convention as
    `payout` (gross: UP x yes + DOWN x (1 - yes)).

    `pnl` is None unless `complete`. A confirmed credit that differs from
    the arithmetic payout by more than a cent is FLAGGED
    (`settlement_credit_agrees` False) and `pnl_on_credit` states the result
    the credit implies; `compare` then trusts neither.
    """
    pos = position_from_fills(fills)
    reasons: List[str] = list(pos["problems"])
    if feed_complete is not True:
        reasons.append("FEED_NOT_READ_TO_ITS_END")
    v = dec(yes_value)
    independent = False
    payout: Optional[D] = None
    if yes_value is None:
        if pos["readable"] and pos["up_qty"] == pos["dn_qty"]:
            independent = True
            payout = pos["up_qty"]
        else:
            reasons.append("SETTLEMENT_VALUE_UNKNOWN")
    elif v is None or v not in (D(0), D(1)):
        reasons.append("SETTLEMENT_VALUE_NOT_0_OR_1")
    elif pos["readable"]:
        payout = pos["up_qty"] * v + pos["dn_qty"] * (D(1) - v)

    credit: Optional[D] = None
    if settlement_credit is None:
        cash = CASH_NOT_CHECKED
    elif isinstance(settlement_credit, str) \
            and settlement_credit.strip() == CASH_NOT_CONFIRMED:
        cash = CASH_NOT_CONFIRMED
    else:
        credit = dec(settlement_credit)
        if credit is None:
            cash = CASH_NOT_CONFIRMED
            reasons.append("SETTLEMENT_CREDIT_UNREADABLE")
        else:
            cash = CASH_CONFIRMED

    complete = not reasons
    pnl = (payout - pos["cost"] - pos["fees"]) if complete else None
    agrees: Optional[bool] = None
    pnl_on_credit: Optional[D] = None
    if complete and credit is not None:
        agrees = abs(credit - payout) <= CREDIT_TOLERANCE
        pnl_on_credit = credit - pos["cost"] - pos["fees"]
    realized_cash = bool(complete and cash == CASH_CONFIRMED and agrees)
    if not complete:
        label = LABEL_INCOMPLETE
    elif realized_cash:
        label = LABEL_CONFIRMED
    elif agrees is False:
        label = LABEL_CREDIT_DISAGREES
    else:
        label = LABEL_ARITHMETIC
    return {
        "up_qty": pos["up_qty"], "dn_qty": pos["dn_qty"],
        "up_cost": pos["up_cost"], "dn_cost": pos["dn_cost"],
        "cost": pos["cost"], "fees": pos["fees"],
        "net_up_minus_down": pos["net_up_minus_down"],
        "fills": pos["fills"], "sells": pos["sells"],
        "fill_ids": list(pos["fill_ids"]),
        "yes_value": v if not independent else None,
        "outcome_independent": independent,
        "payout": payout if complete else None,
        "pnl": pnl,
        "complete": complete,
        "incomplete_reasons": sorted(set(reasons)),
        "feed_complete": feed_complete is True,
        "settlement_cash": cash,
        "settlement_credit": credit,
        "settlement_credit_agrees": agrees,
        "pnl_on_credit": pnl_on_credit,
        "realized_cash": realized_cash,
        "label": label,
    }


def compare(booked_pnl: Any, result: Mapping[str, Any]) -> Dict[str, Any]:
    """The ledger's booked figure beside the venue's. APPLIES NOTHING.

    `booked_pnl` None means the ledger has no row for the market. A value
    that is given but cannot be read is LEDGER_UNREADABLE, with no figures.

    `difference` = venue - booked (booked taken as 0 when unbooked).
    `downward_adjustment` = min(0, difference): the loss the ledger lacks.
    `upward_pending` = max(0, difference): reported, never applied here.
    All three are None unless the venue result is complete.
    """
    booked = dec(booked_pnl)
    out: Dict[str, Any] = {
        "booked_pnl": booked, "venue_pnl": None, "difference": None,
        "downward_adjustment": None, "upward_pending": None,
        "applied": False}
    if not result.get("complete") or result.get("pnl") is None:
        out["class"] = VENUE_INCOMPLETE
        return out
    if booked_pnl is not None and booked is None:
        out["class"] = LEDGER_UNREADABLE
        return out
    if not result.get("fills"):
        #: a ledger row with no venue execution behind it. Not a zero
        #: result: the position came from somewhere this feed does not show.
        out["class"] = VENUE_HAS_NO_FILLS
        return out
    venue = result["pnl"]
    if result.get("settlement_credit_agrees") is False:
        #: two venue readings disagree. The LOWER is used, so a loss the
        #: ledger lacks is still surfaced; nothing upward is claimed.
        venue = min(venue, result["pnl_on_credit"])
        diff = venue - (booked or D(0))
        out.update({"class": SETTLEMENT_CREDIT_DISAGREES, "venue_pnl": venue,
                    "difference": diff, "downward_adjustment": min(D(0), diff),
                    "upward_pending": D(0)})
        return out
    diff = venue - (booked or D(0))
    out.update({"venue_pnl": venue, "difference": diff,
                "downward_adjustment": min(D(0), diff),
                "upward_pending": max(D(0), diff)})
    if booked is None:
        out["class"] = UNBOOKED
    elif abs(diff) <= AGREE_TOLERANCE:
        out["class"] = AGREES
    elif booked < venue:
        out["class"] = BOOKED_MORE_PESSIMISTIC
    else:
        out["class"] = BOOKED_TOO_OPTIMISTIC
    return out


def phantom(ledger_up: Any, ledger_dn: Any, result: Mapping[str, Any]
            ) -> Optional[Dict[str, Any]]:
    """Name a position the ledger holds that the venue's fills do not net
    to: a snapshot adopted mid-sequence and never corrected. None when the
    nets agree or either cannot be read. Names it; changes nothing."""
    lu, ld = dec(ledger_up), dec(ledger_dn)
    vu, vd = dec(result.get("up_qty")), dec(result.get("dn_qty"))
    if None in (lu, ld, vu, vd) or result.get("incomplete_reasons"):
        return None
    excess = (lu - ld) - (vu - vd)
    if excess == 0:
        return None
    return {"side": UP if excess > 0 else DOWN, "qty": abs(excess),
            "ledger": {UP: lu, DOWN: ld}, "venue": {UP: vu, DOWN: vd},
            "ledger_net": lu - ld, "venue_net": vu - vd,
            "labels": ("the ledger's net position is not the net of the "
                       "venue's fills: a net-position snapshot was taken as "
                       "the position")}


def correction_path(cls: str, result: Mapping[str, Any], *,
                    ledger_up: Any = None, ledger_dn: Any = None,
                    at_bound: bool = False) -> Optional[str]:
    """Which EXISTING owner-gated path could apply this difference, by the
    quantity forms those paths accept. A statement of eligibility by
    quantity only -- each path has further conditions of its own and
    decides for itself. None where there is nothing to apply."""
    if cls in (AGREES, VENUE_INCOMPLETE, VENUE_HAS_NO_FILLS,
               LEDGER_UNREADABLE):
        return None
    if cls == UNBOOKED:
        return PATH_UNBOOKED
    if cls == SETTLEMENT_CREDIT_DISAGREES:
        return PATH_CREDIT
    if not at_bound:
        return PATH_NOT_AT_BOUND
    if result.get("sells"):
        return PATH_SELLS
    lu, ld = dec(ledger_up), dec(ledger_dn)
    vu, vd = dec(result.get("up_qty")), dec(result.get("dn_qty"))
    if None in (lu, ld, vu, vd):
        return PATH_NOT_CONTAINED
    if (lu == vu and ld == vd) or (min(lu, ld) == 0
                                   and lu - ld == vu - vd):
        return PATH_ATTESTATION
    if lu <= vu and ld <= vd:
        return PATH_SUPERSET
    return PATH_NOT_CONTAINED


def _s(x: Any) -> Optional[str]:
    return None if x is None else str(x)


def summarise(per_market: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Totals for a report. Each entry: {"market", "result", "comparison",
    optional "path", "phantom"}. Strings out, so it can go on a log line.

    Money totals cover COMPLETE venue results only; an incomplete market
    contributes to the counts and to nothing else. Upward and downward
    differences are totalled SEPARATELY and never netted.
    """
    by: Dict[str, int] = {}
    cash = {CASH_CONFIRMED: 0, CASH_NOT_CONFIRMED: 0, CASH_NOT_CHECKED: 0}
    n = fills = phantoms = 0
    cost = fees = payout = venue = booked = up = down = D(0)
    diffs: List[Dict[str, Any]] = []
    for e in (per_market or ()):
        if not isinstance(e, Mapping):
            continue
        res = e.get("result") if isinstance(e.get("result"), Mapping) else {}
        cmp_ = e.get("comparison") \
            if isinstance(e.get("comparison"), Mapping) else {}
        n += 1
        cls = str(cmp_.get("class") or VENUE_INCOMPLETE)
        by[cls] = by.get(cls, 0) + 1
        if e.get("phantom"):
            phantoms += 1
        if not res.get("complete"):
            continue
        fills += int(res.get("fills") or 0)
        cost += res["cost"]
        fees += res["fees"]
        payout += res["payout"]
        key = str(res.get("settlement_cash") or CASH_NOT_CHECKED)
        cash[key] = cash.get(key, 0) + 1
        if cmp_.get("difference") is None:
            continue
        venue += cmp_["venue_pnl"]
        booked += cmp_.get("booked_pnl") or D(0)
        up += cmp_.get("upward_pending") or D(0)
        down += cmp_.get("downward_adjustment") or D(0)
        if cls != AGREES:
            diffs.append({"market": _s(e.get("market")), "class": cls,
                          "booked_pnl": _s(cmp_.get("booked_pnl")),
                          "venue_pnl": _s(cmp_.get("venue_pnl")),
                          "difference": _s(cmp_.get("difference")),
                          "path": e.get("path"),
                          "phantom": (None if not e.get("phantom") else {
                              "side": e["phantom"].get("side"),
                              "qty": _s(e["phantom"].get("qty"))}),
                          "applied": False})
    diffs.sort(key=lambda r: (-abs(D(r["difference"])), str(r["market"])))
    return {
        "markets": n, "by_class": by,
        "venue_confirmed_fills": fills,
        "total_cost_usd": str(cost), "total_fees_usd": str(fees),
        "total_payout_usd": str(payout),
        "venue_pnl_usd": str(venue), "booked_pnl_usd": str(booked),
        "upward_difference_pending_usd": str(up),
        "downward_adjustment_usd": str(down),
        "settlement_cash": cash,
        "phantom_positions": phantoms,
        "differences_total": len(diffs),
        "largest_differences": diffs[:MAX_LISTED],
        "applied": False,
        "labels": ("REPORT ONLY: nothing here was applied to any booked "
                   "figure, position or limit. Money totals cover markets "
                   "whose venue result is COMPLETE. venue_pnl_usd is settled "
                   "arithmetic on confirmed fills unless settlement_cash says "
                   "CONFIRMED. upward_difference_pending_usd is pending the "
                   "owner's existing correction paths and adds no capacity; "
                   "downward_adjustment_usd is losses the ledger lacks."),
    }
