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

TICK = D("0.01")

#: COMPLETION-ORDER POLICY KNOBS. All default to the baseline, so a call that
#: passes none of them behaves exactly as before they existed.
#:
#: `complete_improve_ticks` rests the complement BELOW its own touch. It buys
#: a better price at the cost of fill probability, and it is the knob the
#: completion leg should respond to: once a first leg is filled, the move that
#: strands it is the SAME move that makes the complement cheaper, so the
#: completion order is favourably selected and can afford to ask for more.
#:
#: `escalate_after_s` gives that up near the deadline, because an unfilled
#: complement is worth far less than a slightly worse pair.
#:
#: `reduce_after_s` is the other lever on a residual the book will not
#: complete: SELL the first leg back as a taker and take a small certain loss
#: instead of carrying full notional into settlement. Measured against doing
#: nothing, which is what the deployed engine does.


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
           loose_after_s: float = LOOSE_AFTER_S,
           complete_improve_ticks: int = 0,
           complete_drift: Optional[D] = None,
           escalate_after_s: Optional[float] = None,
           reduce_after_s: Optional[float] = None,
           policy_label: str = "JOIN") -> Dict[str, Any]:
    """Chronological, one shared ledger, one outstanding order.

    `ceiling` bounds what a COMPLETION may pay. `open_maker=False` opens at
    the touch as a taker (and is then charged the taker fee) so the opening
    and completion channels can be separated; the completion leg is always
    a resting maker order, which is the policy under test.
    """
    from src.l5_sequential_replay import taker_fee

    ceiling = D(str(ceiling))
    tight_ceiling = min(D(str(tight_ceiling)), ceiling)
    c_drift = REPRICE_DRIFT if complete_drift is None else D(str(complete_drift))
    cash = D(0)
    n_reduced = 0
    reduced_usd = D(0)
    reduced: List[Dict[str, Any]] = []
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
                base = own_maker_cost(resting["side"], bid, ask)
                tol = (REPRICE_DRIFT if resting["kind"] == "OPEN"
                       else c_drift)
                want = base - resting.get("improve", D(0))
                if abs(want - resting["own_px"]) > tol:
                    resting = None
                    n_cancel_drift += 1

        # ---- 2b. REDUCE a residual the book will not complete. Selling the
        # held leg back is a taker trade and is charged the taker fee; it
        # takes a small certain loss instead of carrying full notional into
        # settlement. Only with nothing resting, so it can never race our
        # own complement.
        if (reduce_after_s is not None and leg is not None
                and resting is None and el >= reduce_after_s):
            proceeds = own_maker_cost(leg["side"], bid, ask)
            if 0 < proceeds < 1:
                q = leg["qty"]
                pnl = q * (proceeds - leg["cost"]) - taker_fee(q, proceeds)
                if leg.get("taker"):
                    pnl -= taker_fee(q, leg["cost"])
                realized += pnl
                cash -= q * leg["cost"]
                n_reduced += 1
                reduced_usd += pnl
                reduced.append({
                    "market": mkt, "side": leg["side"],
                    "own_cost": str(leg["cost"]), "qty": str(q),
                    "sold_at": str(proceeds), "net_pnl": str(pnl)})
                leg = None
                equity_peak = max(equity_peak, realized)
                drawdown = min(drawdown, realized - equity_peak)
                continue

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
                imp = TICK * D(int(complete_improve_ticks))
                if escalate_after_s is not None and el >= escalate_after_s:
                    imp = D(0)          # give up the improvement near the end
                px = own_maker_cost(comp, bid, ask) - imp
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
                           "improve": imp,
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
        "policy_label": policy_label,
        "complete_improve_ticks": int(complete_improve_ticks),
        "complete_drift": str(c_drift),
        "escalate_after_s": escalate_after_s,
        "reduce_after_s": reduce_after_s,
        "n_reduced": n_reduced,
        "reduced_usd": str(reduced_usd),
        "reduced": reduced,
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


# ===================================================================
# TWO RESTING LEGS. Models a configuration that is NOT authorized live.
# ===================================================================
#
# THE IDENTITY THAT MOTIVATES IT. Resting both legs at their own touch costs
# `bid + (1 - ask) = 1 - spread`, so a maker-maker pair EARNS EXACTLY THE
# SPREAD and a ceiling C is reachable only when `spread >= 1 - C`. Measured:
# a 0.96 ceiling needs a 4-cent spread, present in 10.0% (PMUS observer) to
# 22.3% (Gemini) of snapshots; a 0.99 ceiling needs 1 cent, present in 100%.
#
# THIS IS WHY `max_live_orders = 1` IS THE BINDING CONSTRAINT and why the
# single-order results above are so poor: with one order we rest a leg, it
# fills when the market comes to it, and then we must CHASE the complement.
# That chase is the adverse-selection trap -- completion fill rate 10.5% to
# 40%, with the strand term dominating the whole result.
#
# WHAT THIS FUNCTION DOES NOT ASSUME. Resting both legs does not make both
# fill. A trending market sweeps one side and leaves the other, which lands
# us in exactly the same strand. So `both_filled` is COUNTED here, never
# assumed, and a one-sided outcome is settled or reduced like any strand.
#
# NOT AN AUTHORIZATION. The deployed `max_live_orders` is 1 and this changes
# nothing about it. Running two resting legs live would need an explicit
# owner decision and its own worst-case-deficit analysis, because two
# resting legs can both fill and both commit capital.

def replay_two_sided(rows: Sequence[Mapping[str, Any]],
                     outcomes: Mapping[str, Mapping[str, Any]],
                     *, ceiling: D = GATE.TAKER_ERA_CEILING,
                     fill_mode: str = FILL_STRICT,
                     reduce_after_s: Optional[float] = None,
                     policy_label: str = "TWO_SIDED") -> Dict[str, Any]:
    from src.l5_sequential_replay import taker_fee

    ceiling = D(str(ceiling))
    cash = D(0)
    realized = D(0)
    rebate = D(0)
    peak_cash = D(0)
    equity_peak = D(0)
    drawdown = D(0)
    legs: Dict[str, Dict[str, Any]] = {}      # side -> resting order
    filled: Dict[str, Dict[str, Any]] = {}    # side -> filled leg
    cur = None
    completed: List[Dict[str, Any]] = []
    strands: List[Dict[str, Any]] = []
    reduced: List[Dict[str, Any]] = []
    n_both = 0
    n_one = 0
    n_none = 0
    n_placed = 0

    def close_window(mkt, bid=None, ask=None):
        nonlocal legs, filled, cash, realized, n_both, n_one, n_none
        if not filled and not legs:
            return
        if len(filled) == 2:
            n_both += 1
        elif len(filled) == 1:
            n_one += 1
        elif legs:
            n_none += 1
        for sd, l in list(filled.items()):
            o = outcomes.get(mkt) or {}
            w = o.get("winner")
            payoff = l["qty"] if w == sd else D(0)
            pnl = payoff - l["qty"] * l["cost"]
            realized += pnl
            cash -= l["qty"] * l["cost"]
            strands.append({
                "market": mkt, "side": sd, "own_cost": str(l["cost"]),
                "qty": str(l["qty"]), "winner": w, "opened_as": "MAKER",
                "settlement_evidence": o.get("evidence",
                                             "NO_OUTCOME_AVAILABLE"),
                "net_pnl": str(pnl)})
        legs = {}
        filled = {}

    for r in rows:
        mkt = str(r["market"])
        el = float(r["elapsed_s"])
        bid, ask = D(str(r["bid"])), D(str(r["ask"]))
        if cur is not None and cur != mkt:
            close_window(cur)
        cur = mkt

        # 1. fills on either resting leg
        for sd in ("UP", "DOWN"):
            o = legs.get(sd)
            if o is None:
                continue
            tk = own_taker_cost(sd, bid, ask)
            if _filled(fill_mode, tk, o["own_px"]):
                filled[sd] = {"cost": o["own_px"], "qty": o["qty"]}
                cash += o["qty"] * o["own_px"]
                peak_cash = max(peak_cash, cash)
                rebate += maker_rebate_credited(o["qty"], o["own_px"])
                del legs[sd]

        # 2. BOTH legs filled -> the pair is locked
        if len(filled) == 2:
            q = min(filled["UP"]["qty"], filled["DOWN"]["qty"])
            pair = filled["UP"]["cost"] + filled["DOWN"]["cost"]
            pnl = q * (D(1) - pair)
            realized += pnl
            completed.append({
                "market": mkt, "entry_own": str(filled["UP"]["cost"]),
                "complement_own": str(filled["DOWN"]["cost"]),
                "pair": str(pair), "qty": str(q), "net_pnl": str(pnl),
                "opened_as": "MAKER", "rebate_credited": "0.00"})
            cash -= (filled["UP"]["qty"] * filled["UP"]["cost"]
                     + filled["DOWN"]["qty"] * filled["DOWN"]["cost"])
            n_both += 1
            filled = {}
            for sd in list(legs):
                del legs[sd]

        # 3. reduce a one-sided fill the book will not complete
        if (reduce_after_s is not None and len(filled) == 1
                and el >= reduce_after_s):
            sd, l = next(iter(filled.items()))
            proceeds = own_maker_cost(sd, bid, ask)
            if 0 < proceeds < 1:
                q = l["qty"]
                pnl = q * (proceeds - l["cost"]) - taker_fee(q, proceeds)
                realized += pnl
                cash -= q * l["cost"]
                reduced.append({"market": mkt, "side": sd,
                                "own_cost": str(l["cost"]), "qty": str(q),
                                "sold_at": str(proceeds),
                                "net_pnl": str(pnl)})
                filled = {}
                for s2 in list(legs):
                    del legs[s2]

        # 4. place BOTH legs when nothing is working and the spread pays
        if not legs and not filled and el <= ENTRY_CUTOFF_S:
            up = own_maker_cost("UP", bid, ask)
            dn = own_maker_cost("DOWN", bid, ask)
            pair = up + dn
            if 0 < up < 1 and 0 < dn < 1 and pair <= ceiling:
                q = floor_to_increment(min(
                    OPENING_LEG_MAX_CONTRACTS,
                    OPENING_NOTIONAL_BUDGET_USD / max(up, dn)))
                if q > 0 and cash + q * pair <= MAX_CASH_DEPLOYED_USD:
                    legs = {"UP": {"own_px": up, "qty": q},
                            "DOWN": {"own_px": dn, "qty": q}}
                    n_placed += 1

        # 5. abandon unfilled legs past the completion cutoff
        if legs and el > COMPLETE_CUTOFF_S:
            legs = {}

        equity_peak = max(equity_peak, realized)
        drawdown = min(drawdown, realized - equity_peak)

    if cur is not None:
        close_window(cur)

    ev = sorted({s["settlement_evidence"] for s in strands})
    tot = n_both + n_one
    return {
        "label": "L5_TWO_SIDED_MAKER_REPLAY_V1",
        "policy_label": policy_label,
        "authorized_for_live": False,
        "ceiling": str(ceiling),
        "fill_mode": fill_mode,
        "reduce_after_s": reduce_after_s,
        "n_rows": len(rows),
        "n_windows": len({str(r["market"]) for r in rows}),
        "n_pairs_placed": n_placed,
        "n_both_filled": n_both,
        "n_one_filled": n_one,
        "n_neither_filled": n_none,
        "both_fill_rate": (None if tot == 0
                           else str((D(n_both) / D(tot)).quantize(
                               D("0.0001")))),
        "n_completed": len(completed),
        "n_stranded": len(strands),
        "n_reduced": len(reduced),
        "completed_pairs": completed,
        "stranded": strands,
        "reduced": reduced,
        "net_realized_usd": str(realized),
        "rebate_credited_usd": str(rebate),
        "net_with_rebate_usd": str(realized + rebate),
        "worst_drawdown_usd": str(drawdown),
        "peak_cash_deployed_usd": str(peak_cash),
        "settlement_evidence_grades": ev,
        "labels": (
            "TWO resting legs, which the deployed max_live_orders=1 does NOT "
            "permit. both_fill_rate is COUNTED, never assumed: a trending "
            "market sweeps one side and leaves a strand. No fee is charged "
            "on a maker leg; the rebate is reported separately and is $0.00 "
            "at the deployed caps. Nothing here is a venue fill."),
    }
