"""P2: what the evidenced fills are WORTH. The economic ledger.

THE POINT OF THIS MODULE IS TO STOP OPTIMIZING REACHABILITY. Every number this
program has produced so far says how often we could have been there. None of
them says whether being there makes money. This walks each evidenced shadow
fill through to settlement and puts a figure on it.

WHAT MAY AND MAY NOT BE COUNTED, and these are not stylistic:

  * A THEORETICAL FILL IS NOT A FILL. Only `evidenced_fill is True` enters the
    ledger. `None` is "we could not tell" -- a quote resting across a restart,
    or prints we could not read -- and it is carried as UNKNOWN, never as a
    no-fill and never as a fill.
  * AN UNRESOLVED MARKET IS NOT REALIZED. A position whose window has not
    settled has NO realized line at all. It is reported as open, with its cost
    basis, and nothing else.
  * AN ESTIMATED REBATE IS NOT INCOME. Maker rebates and liquidity rewards are
    reported as zero with a reason, because a shadow account is credited
    nothing. They are kept in their own fields so that if a venue ever does
    credit us, the number arrives beside the trading P&L rather than inside it.

AND THE LABEL IS `realized_shadow`, NEVER `realized`. This is a settled
counterfactual: the fill is evidenced by public prints, but no order was ever
submitted and no cash ever moved. The distinction is the difference between
this program's evidence and its claims.

DECOMPOSITION. A settled position is split into matched-pair economics and
directional residual, because they are different bets with different variances
and conflating them is how a losing residual hides inside a winning pair:

    matched  = min(up_qty, dn_qty) pays exactly $1 per pair, whatever settles
    residual = the unmatched side, which pays $1 or $0 on the outcome

Fees are charged where the venue charges them (PM maker pays nothing under
`takerOnly: true`; Gemini maker pays a ceil-cent fee) and reported separately
from both.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Optional, Sequence

SCHEMA = "l5_shadow_economics/v1"
CENT = D("0.01")


def positions(fills: Sequence[dict]) -> Dict[tuple, Dict[str, Any]]:
    """Group evidenced fills into one position per (venue, cohort, market).

    Inventory is per market, not per fill: matched-pair economics only exists
    once both sides of the SAME window are held.
    """
    out: Dict[tuple, Dict[str, Any]] = {}
    for f in fills:
        key = (f["venue"], f.get("cohort"), f["market_key"])
        p = out.setdefault(key, {
            "venue": f["venue"], "cohort": f.get("cohort"),
            "market_key": f["market_key"],
            "clock": f.get("clock"), "up_qty": D("0"), "up_cost": D("0"),
            "dn_qty": D("0"), "dn_cost": D("0"), "fees": D("0"),
            "n_fills": 0, "intent_ids": [], "first_t": f.get("t"),
            "last_t": f.get("t")})
        q, px = D(str(f["qty"])), D(str(f["price"]))
        side = "up" if str(f["outcome"]) == "Up" else "dn"
        p[f"{side}_qty"] += q
        p[f"{side}_cost"] += q * px
        p["fees"] += D(str(f.get("fee") or "0"))
        p["n_fills"] += 1
        p["intent_ids"].append(f.get("intent_id"))
        if f.get("t") is not None:
            p["first_t"] = min(p["first_t"] or f["t"], f["t"])
            p["last_t"] = max(p["last_t"] or f["t"], f["t"])
        # clocks must not be silently merged into one position
        if p["clock"] != f.get("clock"):
            p["clock"] = "MIXED"
    return out


def settle(pos: Dict[str, Any],
           resolution: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """One position -> its economics, or an explicit open/unknown state."""
    up_q, dn_q = pos["up_qty"], pos["dn_qty"]
    up_c, dn_c = pos["up_cost"], pos["dn_cost"]
    matched = min(up_q, dn_q)
    resid_side = "Up" if up_q > dn_q else ("Down" if dn_q > up_q else None)
    resid_q = abs(up_q - dn_q)
    avg_up = (up_c / up_q) if up_q > 0 else None
    avg_dn = (dn_c / dn_q) if dn_q > 0 else None
    base = {
        "schema": SCHEMA, "venue": pos["venue"], "cohort": pos["cohort"],
        "market_key": pos["market_key"], "clock": pos["clock"],
        "n_fills": pos["n_fills"], "intent_ids": pos["intent_ids"],
        "up_qty": str(up_q), "dn_qty": str(dn_q),
        "up_cost": str(up_c), "dn_cost": str(dn_c),
        "avg_up_price": (str(avg_up.quantize(D("0.000001"))) if avg_up else None),
        "avg_dn_price": (str(avg_dn.quantize(D("0.000001"))) if avg_dn else None),
        "matched_qty": str(matched),
        "residual_side": resid_side, "residual_qty": str(resid_q),
        "fees": str(pos["fees"].quantize(D("0.000001"))),
        # Never estimated. A shadow account is credited nothing, and a number
        # that would be income if it were real does not belong in a total.
        "maker_rebates_credited": "0",
        "rewards_credited": "0",
        "incentives_note": ("venue-credited only; a shadow account is credited "
                            "nothing, so these are 0 by fact, not by estimate"),
    }
    if not resolution or not resolution.get("resolved"):
        return {**base, "state": "OPEN_UNSETTLED", "realized_shadow": None,
                "cost_basis": str((up_c + dn_c + pos["fees"])
                                  .quantize(D("0.000001"))),
                "resolution": (resolution or {}).get("resolution_source",
                                                     "UNKNOWN"),
                "labels": ("the window has not resolved. There is no realized "
                           "line: an unsettled position has a cost, not a "
                           "result")}
    won = resolution["winning_outcome"]
    # a matched pair pays exactly $1 whatever settles -- that is what makes it
    # the low-variance leg, and why it is scored apart from the residual
    matched_pnl = matched * (D("1") - ((avg_up or D("0")) + (avg_dn or D("0"))))
    if resid_side is None:
        resid_pnl = D("0")
    else:
        avg = avg_up if resid_side == "Up" else avg_dn
        payoff = D("1") if won == resid_side else D("0")
        resid_pnl = resid_q * (payoff - (avg or D("0")))
    total = matched_pnl + resid_pnl - pos["fees"]
    return {
        **base, "state": "SETTLED",
        "winning_outcome": won,
        "resolution": resolution.get("resolution_source"),
        "matched_pair_pnl": str(matched_pnl.quantize(D("0.000001"))),
        "residual_pnl": str(resid_pnl.quantize(D("0.000001"))),
        "fees_charged": str(pos["fees"].quantize(D("0.000001"))),
        "realized_shadow": str(total.quantize(D("0.000001"))),
        "labels": ("realized_SHADOW: the fill is evidenced by public prints "
                   "and the window has settled, but NO order was submitted and "
                   "no cash moved. This is a settled counterfactual and may "
                   "never be reported as realized profit."),
    }


def roll_up(rows: Sequence[dict], *, by: str) -> Dict[str, Any]:
    """Totals split on one dimension, with the unsettled kept apart.

    Summing settled and unsettled together would let a cost basis read as a
    loss, or an open position as a win.
    """
    out: Dict[str, Any] = {}
    for r in rows:
        k = str(r.get(by))
        g = out.setdefault(k, {
            "positions": 0, "settled": 0, "open": 0, "fills": 0,
            "paired_positions": 0, "one_sided_positions": 0,
            "matched_pair_pnl": D("0"), "residual_pnl": D("0"),
            "fees": D("0"), "realized_shadow": D("0"),
            "open_cost_basis": D("0")})
        g["positions"] += 1
        g["fills"] += int(r.get("n_fills") or 0)
        # THE DIAGNOSTIC THAT MATTERS MOST, and the first live report had to be
        # inferred from "matched_pair_pnl is 0.00 everywhere". L5 is a paired
        # inventory trader: the matched leg is the low-variance one that pays
        # $1 whatever settles. A position with no matched quantity is not a
        # cheaper version of that -- it is a different, directional bet, and
        # CLAUDE.md is explicit that residual exposure has no demonstrated
        # predictive skill. So it is counted, not left to be read off a zero.
        if D(r.get("matched_qty") or "0") > 0:
            g["paired_positions"] += 1
        else:
            g["one_sided_positions"] += 1
        if r.get("state") == "SETTLED":
            g["settled"] += 1
            g["matched_pair_pnl"] += D(r["matched_pair_pnl"])
            g["residual_pnl"] += D(r["residual_pnl"])
            g["fees"] += D(r["fees_charged"])
            g["realized_shadow"] += D(r["realized_shadow"])
        else:
            g["open"] += 1
            g["open_cost_basis"] += D(r.get("cost_basis") or "0")
    for g in out.values():
        # A SUM OVER NOTHING IS NOT ZERO. With no settled position, "0.00"
        # reads as "made nothing" when it means "nothing has settled yet" --
        # the same confusion between an absence and a measurement that the
        # coverage markers exist to prevent, one level up.
        settled = g["settled"] > 0
        for k in ("matched_pair_pnl", "residual_pnl", "fees",
                  "realized_shadow"):
            g[k] = (str(g[k].quantize(D("0.000001"))) if settled else None)
        g["open_cost_basis"] = str(g["open_cost_basis"]
                                   .quantize(D("0.000001")))
        g["maker_rebates_credited"] = ("0" if settled else None)
        g["rewards_credited"] = ("0" if settled else None)
        g["pair_completion_rate"] = (
            round(g["paired_positions"] / g["positions"], 4)
            if g["positions"] else None)
        if not settled:
            g["note"] = ("no settled position in this group: there is no "
                         "result to report, only a cost basis")
        elif g["paired_positions"] == 0:
            g["note"] = ("EVERY position is one-sided: there is no matched "
                         "leg at all, so this entire result is directional "
                         "residual -- the exposure L5's own record gives no "
                         "reason to believe has skill")
    return out
