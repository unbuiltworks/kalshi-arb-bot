"""A CAUSAL maker placement/cancellation policy, replayed without lookahead.

WHAT MAKES THIS CAUSAL. The policy function sees one book snapshot, its own
inventory and the clock. It never sees a later row, and in particular it
never sees one of L5's fills. That matters because 83.4% of L5's classifiable
fills are MAKER fills (`src/l5_chain_observer/decode.py:l5_role`), so his
public print is the moment his order was HIT -- an unknown interval after he
placed it. Triggering our placement on his print would be a lookahead on our
own entry time and would model an order we could not have had resting.

So nothing here consults an L5 fill. The policy posts from the book.

THE OWN-TERMS MAKER PRICE. The book is quoted in UP terms. Buying UP as a
maker means resting a bid, so our own cost is `bid`. Buying DOWN means
resting an offer on the YES side, so our own cost is `1 - ask`. Both are
cheaper than the corresponding taker cost by the spread -- that is the whole
point of being a maker, and it is why the completion leg is the one worth
resting.

THE FILL MODEL, AND WHY IT IS INHERENTLY ADVERSE ON AN OPENING LEG. A resting
order is filled only when someone crosses to it, which shows up in a
bid/ask recording as the opposite touch coming to or through our price. So
`own_taker_cost(side, bid, ask) <= our_resting_own_price` is the fill test
(`STRICT` requires it to come strictly THROUGH, which is the conservative
reading of queue priority: at exactly our price, size ahead of us in the
queue may absorb the whole cross).

A resting BID therefore fills exactly when the market falls to it. On an
opening leg that is adverse by construction -- we buy as the price leaves us
-- and it is why the measured opening-leg edge is -$0.0827 per filled
contract with 91.63% of fills adverse.

THE ASYMMETRY THAT MOTIVATES THE WHOLE EXERCISE. The same move that is
adverse for an opening leg is FAVOURABLE for a completion. Once we are long
UP, a falling market makes DOWN cheaper, and DOWN is exactly what completes
the pair. Maker completion is favourably selected by the same mechanism that
makes maker opening adversely selected. That is the reason to rest the
completion and to bound it with a ceiling that does not reserve margin for a
fee a maker never pays.

NO FEE, NO ASSUMED REBATE. A maker is charged nothing, so `charge_maker_fee`
does not exist. The rebate is computed at the published schedule WITH the
venue's per-print cent rounding and reported on its own line; at the deployed
caps it is $0.00 almost everywhere, so `net_realized_usd` excludes it and
`net_with_rebate_usd` is shown beside it, never merged.
"""
from __future__ import annotations

from decimal import Decimal as D, ROUND_HALF_EVEN
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.l5_sequential_replay import (
    ENTRY_CUTOFF_S, COMPLETE_CUTOFF_S, MAX_CASH_DEPLOYED_USD,
    OPENING_NOTIONAL_BUDGET_USD, OPENING_LEG_MAX_CONTRACTS,
    QUANTITY_INCREMENT, TERMINAL_BOOK_INFERRED, floor_to_increment,
    own_taker_cost,
)
from src import l5_maker_pair_gate as GATE

CENT = D("0.01")
MAKER_THETA = D("0.0125")

#: how far the book may drift from our resting price before we cancel and
#: repost. L5 reprices within seconds; holding one price for a whole window
#: is one of the execution mismatches already on record.
REPRICE_DRIFT = D("0.02")

#: a resting order is abandoned rather than chased once this much of the
#: window is gone (opening) -- the same cutoffs the frozen rule uses.
FILL_AT_TOUCH = "AT_TOUCH"       # fills when the market comes TO our price
FILL_STRICT = "STRICT"           # fills only when it comes THROUGH it

#: WHEN THE LOOSE CEILING MAY BE USED, measured from the window start.
#:
#: WHY A PATIENT CEILING IS NOT OPTIONAL. The first version of this replay
#: completed at the FIRST pair that satisfied the ceiling. Raising the
#: ceiling from 0.96 to 0.99 then produced the SAME five completions and
#: $0.04 LESS profit -- it rescued no strand and merely let a completion
#: that had runway settle for less. That is dilution, and it is a defect in
#: an eager policy rather than an argument about the ceiling.
#:
#: So the loose ceiling is a LAST RESORT, not a standing permission: while
#: the window still has runway the tight 0.96 bound applies, and only once a
#: leg is close to being stranded may the completion pay up to the maker
#: ceiling. That is the "COMPLETE is often delayed until pricing improves"
#: discipline in L5_OPERATING_MODEL, and it makes the ceiling change
#: weakly positive by construction: it can only act where the tight bound
#: was going to strand the leg anyway.
LOOSE_AFTER_S = 600.0


def own_maker_cost(side: str, bid: D, ask: D) -> D:
    """Our own cost of RESTING a buy of `side`, in own terms."""
    return D(str(bid)) if str(side).upper() == "UP" else D(1) - D(str(ask))


def maker_rebate_credited(qty: D, own_px: D) -> D:
    """The venue's per-print credit: banker's rounding to the cent, which is
    what actually reaches the account. Returns 0 below the cent floor."""
    raw = MAKER_THETA * D(str(qty)) * D(str(own_px)) * (D(1) - D(str(own_px)))
    return raw.quantize(CENT, rounding=ROUND_HALF_EVEN)


def _filled(mode: str, taker_now: D, resting: D) -> bool:
    return taker_now < resting if mode == FILL_STRICT else taker_now <= resting


def replay(rows: Sequence[Mapping[str, Any]],
           outcomes: Mapping[str, Mapping[str, Any]],
           *, ceiling: D = GATE.TAKER_ERA_CEILING,
           fill_mode: str = FILL_STRICT,
           open_maker: bool = True,
           tight_ceiling: D = GATE.TAKER_ERA_CEILING,
           loose_after_s: float = LOOSE_AFTER_S) -> Dict[str, Any]:
    """Chronological, one shared ledger, one outstanding order.

    `ceiling` bounds what a COMPLETION may pay. `open_maker=False` opens at
    the touch as a taker (and is then charged the taker fee) so the opening
    and completion channels can be separated; the completion leg is always
    a resting maker order, which is the policy under test.
    """
    from src.l5_sequential_replay import taker_fee

    ceiling = D(str(ceiling))
    tight_ceiling = min(D(str(tight_ceiling)), ceiling)
    cash = D(0)
    realized = D(0)
    rebate = D(0)
    peak_cash = D(0)
    equity_peak = D(0)
    drawdown = D(0)
    resting: Optional[Dict[str, Any]] = None   # our live order, if any
    leg: Optional[Dict[str, Any]] = None       # our filled first leg, if any
    completed: List[Dict[str, Any]] = []
    strands: List[Dict[str, Any]] = []
    n_cancel_drift = 0
    n_cancel_time = 0
    n_refused_cash = 0
    adverse_usd = D(0)
    adverse_n = 0

    def settle(l: Dict[str, Any]) -> D:
        o = outcomes.get(l["market"]) or {}
        w = o.get("winner")
        payoff = l["qty"] if w == l["side"] else D(0)
        pnl = payoff - l["qty"] * l["cost"]
        if l.get("taker"):
            pnl -= taker_fee(l["qty"], l["cost"])
        strands.append({
            "market": l["market"], "side": l["side"],
            "own_cost": str(l["cost"]), "qty": str(l["qty"]),
            "winner": w, "opened_as": "TAKER" if l.get("taker") else "MAKER",
            "settlement_evidence": o.get("evidence", "NO_OUTCOME_AVAILABLE"),
            "net_pnl": str(pnl)})
        return pnl

    def roll(mkt: str) -> None:
        nonlocal leg, resting, cash, realized
        if leg is not None and leg["market"] != mkt:
            realized += settle(leg)
            cash -= leg["qty"] * leg["cost"]
            leg = None
            resting = None

    for r in rows:
        mkt = str(r["market"])
        el = float(r["elapsed_s"])
        bid, ask = D(str(r["bid"])), D(str(r["ask"]))
        roll(mkt)
        if resting is not None and resting["market"] != mkt:
            resting = None

        # ---- 1. does our resting order fill at this snapshot?
        if resting is not None:
            tk = own_taker_cost(resting["side"], bid, ask)
            if _filled(fill_mode, tk, resting["own_px"]):
                q, px = resting["qty"], resting["own_px"]
                if resting["kind"] == "OPEN":
                    leg = {"market": mkt, "side": resting["side"],
                           "cost": px, "qty": q, "taker": False,
                           "entry_mid": resting["mid_at_place"]}
                    cash += q * px
                    peak_cash = max(peak_cash, cash)
                    rebate += maker_rebate_credited(q, px)
                    resting = None
                else:
                    pair = leg["cost"] + px
                    pnl = q * (D(1) - pair)
                    if leg.get("taker"):
                        pnl -= taker_fee(q, leg["cost"])
                    realized += pnl
                    rebate += maker_rebate_credited(q, px)
                    completed.append({
                        "market": mkt, "entry_own": str(leg["cost"]),
                        "complement_own": str(px), "pair": str(pair),
                        "qty": str(q), "net_pnl": str(pnl),
                        "rebate_credited": str(
                            maker_rebate_credited(q, px)),
                        "opened_as": ("TAKER" if leg.get("taker")
                                      else "MAKER")})
                    cash -= q * leg["cost"]
                    leg = None
                    resting = None

        # ---- 2. cancellation: time, then drift. Causal: book + clock only.
        if resting is not None:
            cutoff = (ENTRY_CUTOFF_S if resting["kind"] == "OPEN"
                      else COMPLETE_CUTOFF_S)
            if el > cutoff:
                resting = None
                n_cancel_time += 1
            else:
                want = (own_maker_cost(resting["side"], bid, ask)
                        if resting["kind"] == "OPEN"
                        else own_maker_cost(resting["side"], bid, ask))
                if abs(want - resting["own_px"]) > REPRICE_DRIFT:
                    resting = None
                    n_cancel_drift += 1

        # ---- 3. placement. One outstanding order, ever.
        if resting is None:
            if leg is None:
                if el > ENTRY_CUTOFF_S:
                    continue
                side = str(r["side"]).upper()
                px = (own_maker_cost(side, bid, ask) if open_maker
                      else own_taker_cost(side, bid, ask))
                if px <= 0 or px >= 1:
                    continue
                q = floor_to_increment(min(
                    OPENING_LEG_MAX_CONTRACTS,
                    OPENING_NOTIONAL_BUDGET_USD / px))
                if q <= 0:
                    continue
                if cash + q * px > MAX_CASH_DEPLOYED_USD:
                    n_refused_cash += 1
                    continue
                if open_maker:
                    resting = {"market": mkt, "side": side, "own_px": px,
                               "qty": q, "kind": "OPEN",
                               "mid_at_place": (bid + ask) / D(2)}
                else:
                    leg = {"market": mkt, "side": side, "cost": px,
                           "qty": q, "taker": True,
                           "entry_mid": (bid + ask) / D(2)}
                    cash += q * px
                    peak_cash = max(peak_cash, cash)
            else:
                if el > COMPLETE_CUTOFF_S:
                    continue
                comp = "DOWN" if leg["side"] == "UP" else "UP"
                px = own_maker_cost(comp, bid, ask)
                if px <= 0 or px >= 1:
                    continue
                # PATIENT: the loose ceiling is reachable only once the leg is
                # close to being stranded. Before that the tight bound holds.
                eff = ceiling if el >= loose_after_s else tight_ceiling
                if leg["cost"] + px > eff:
                    continue
                if cash + leg["qty"] * px > MAX_CASH_DEPLOYED_USD:
                    n_refused_cash += 1
                    continue
                resting = {"market": mkt, "side": comp, "own_px": px,
                           "qty": leg["qty"], "kind": "COMPLETE",
                           "mid_at_place": (bid + ask) / D(2)}

        equity_peak = max(equity_peak, realized)
        drawdown = min(drawdown, realized - equity_peak)

    if leg is not None:
        realized += settle(leg)

    # adverse selection actually realized on legs that were left stranded
    for s in strands:
        q = D(s["qty"])
        adverse_usd += D(s["net_pnl"])
        adverse_n += 1
    per_ctr = (adverse_usd / sum(D(s["qty"]) for s in strands)
               ) if strands else None

    ev = sorted({s["settlement_evidence"] for s in strands})
    return {
        "label": "L5_MAKER_PLACEMENT_REPLAY_V1",
        "ceiling": str(ceiling),
        "tight_ceiling": str(tight_ceiling),
        "loose_after_s": loose_after_s,
        "fill_mode": fill_mode,
        "open_maker": open_maker,
        "n_rows": len(rows),
        "n_windows": len({str(r["market"]) for r in rows}),
        "n_completed": len(completed),
        "n_stranded": len(strands),
        "completed_pairs": completed,
        "stranded": strands,
        "net_realized_usd": str(realized),
        "rebate_credited_usd": str(rebate),
        "net_with_rebate_usd": str(realized + rebate),
        "first_leg_only_loss_usd": str(
            sum(D(s["net_pnl"]) for s in strands) if strands else D(0)),
        "adverse_per_stranded_contract": (
            None if per_ctr is None else str(per_ctr.quantize(D("0.0001")))),
        "worst_drawdown_usd": str(drawdown),
        "peak_cash_deployed_usd": str(peak_cash),
        "cancels_on_time": n_cancel_time,
        "cancels_on_drift": n_cancel_drift,
        "refused_on_cash": n_refused_cash,
        "settlement_evidence_grades": ev,
        "labels": (
            "Maker legs are charged NO fee and the rebate is reported "
            "separately at the venue's per-print cent rounding; "
            "net_realized_usd EXCLUDES it. No L5 fill is consulted, so no "
            "placement is triggered by a print we could only have seen "
            "after the fact. A fill requires the opposite touch to reach "
            "our resting price; STRICT requires it to pass through."),
    }
