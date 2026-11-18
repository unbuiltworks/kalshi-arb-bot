"""Sports integration of the SHARED stranded-leg policy (l5_completion_policy).

OWNER 2026-11-18: "proceed with the shared stranded-leg policy from
c10cf0903. Use $0.97 as the initial candidate pair-cost cap, but compare
$0.97 and $0.99 ... Use market-progress timing and conservative sports
match-state handling. Never permit above-$1.00 completion solely because a
match is late. Keep the policy disabled by default, preserve the sports EV
gate."

Ownership: the generic policy (stages, ceilings, the never-force rule,
stranded room) belongs to the BTC5m session's src/l5_completion_policy.py
and is NOT modified here. This module only turns the sports lane's state
into that policy's EXECUTABLE inputs and its answer into one engine action.

HOW A SPORTS STRAND IS PRICED
  * Progress: a sports market has a venue gameStartTime but no certain end,
    so progress is reported UNCERTAIN and the policy holds its tightest
    stage. The lane's own is_late() reads OBSERVED match state, so it is
    passed as event_late, which the policy caps at BREAKEVEN (1.00). The
    policy can therefore never complete above $1.00 on a sports market.
  * Sports cap: the owner's candidate stage-0 cap (0.97 or 0.99) is applied
    on top of the policy's ceiling and can only make it STRICTER.
  * Rest price: the completion rests at the highest tick that keeps the
    pair at/under the effective ceiling, under the ask, and strictly under
    the complement's mid (where the policy's never-force rule can accept
    it) -- patient, never chasing the touch.
  * p_win: the held side's executable mid, declared as such.
  * Taker is never authorized here; REDUCE is reported, never executed.
  * The schedule ceiling is NOT an economic gate (shared policy 9c338374f,
    owner: "$1.00 is only breakeven before fees and other execution
    costs"). A completion is only returned when the policy's never-force
    rule (beats holding at the held mid) also passes; nothing here relaxes
    the lane's own gates.
Nothing here places, cancels or authorizes anything.
"""
from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal as D
from typing import Any, Dict, Mapping, Optional

from src import l5_completion_policy as CP

ONE = D(1)
P_WIN_SOURCE = "HELD_SIDE_EXECUTABLE_MID"


def books(lead: str, yes_bid: D, yes_ask: D) -> Dict[str, Dict[str, D]]:
    """Executable bid/ask/mid of the HELD side and the COMPLEMENT."""
    yb, ya = D(yes_bid), D(yes_ask)
    yes = {"bid": yb, "ask": ya, "mid": (yb + ya) / 2}
    no = {"bid": ONE - ya, "ask": ONE - yb, "mid": ONE - (yb + ya) / 2}
    return ({"held": yes, "comp": no} if lead == "YES"
            else {"held": no, "comp": yes})


def effective_ceiling(stage: Mapping[str, Any], sports_cap: Any) -> D:
    """The policy's stage ceiling, made stricter (never looser) by the
    sports cap at stage 0, and never above 1.00 on a sports market."""
    c = D(str(stage["pair_ceiling"]))
    if int(stage.get("stage") or 0) == 0 and sports_cap is not None:
        c = min(c, D(str(sports_cap)))
    return min(c, ONE)


def rest_price(ceiling: D, lead_px: D, comp_ask: D, tick: D,
               comp_mid: Optional[D] = None) -> Optional[D]:
    """Highest tick the pair cap allows, under the ask (post-only) and
    STRICTLY under the complement's mid: the shared policy's never-force
    rule only completes when that beats holding at the held side's mid,
    i.e. when the completion price is below 1 - p_win."""
    px = ((D(ceiling) - D(lead_px)) / tick).to_integral_value(
        rounding=ROUND_FLOOR) * tick
    px = min(px, D(comp_ask) - tick)
    if comp_mid is not None:
        under_mid = ((D(comp_mid) - tick / 2) / tick).to_integral_value(
            rounding=ROUND_FLOOR) * tick
        px = min(px, under_mid)
    return px if px >= tick else None


def decide(*, lead: str, lead_px: Any, need: Any, yes_bid: Any, yes_ask: Any,
           tick: Any, late: bool, sports_cap: Any = D("0.97"),
           game_start: Optional[float] = None, now: Optional[float] = None,
           stranded_now: int = 0, caller_max_stranded: Any = None,
           resting_px: Optional[D] = None, policy=CP) -> Dict[str, Any]:
    """One sports decision through the shared policy.

    Returns {"action": COMPLETE_MAKER|REPRICE_COMPLETION|KEEP|HOLD, "px",
    "qty", "effective_ceiling", "policy": <shared decide() result>}.
    HOLD means: no completion should rest (cancel any that does)."""
    tick = D(str(tick))
    bk = books(lead, D(str(yes_bid)), D(str(yes_ask)))
    prog = {"progress": None, "certain": False,
            "why": "SPORTS_END_UNCERTAIN", "start": game_start, "now": now}
    # the sports cap is passed AS the policy's patient ceiling (so the 0.99
    # comparator really runs at 0.99 now the shared default is 0.97) and is
    # ALSO applied on top below, so it can only ever tighten.
    stage = policy.stage_for(prog, event_late=bool(late),
                             patient_ceiling=D(str(sports_cap)))
    ceil = effective_ceiling(stage, sports_cap)
    px = rest_price(ceil, D(str(lead_px)), bk["comp"]["ask"], tick,
                    bk["comp"]["mid"])
    out: Dict[str, Any] = {"action": "HOLD", "px": None, "qty": D(0),
                           "effective_ceiling": str(ceil), "stage": stage,
                           "sports_cap": str(sports_cap), "policy": None}
    if px is None:
        out["why"] = "REST_PRICE_BELOW_ONE_TICK"
        return out
    res = policy.decide(
        held_side=lead, held_qty=need, held_avg_cost=lead_px,
        complete_bid=px, complete_ask=None, reduce_bid=bk["held"]["bid"],
        p_win=bk["held"]["mid"], fee_per_share="0", taker_authorized=False,
        prog=prog, stranded_now=stranded_now,
        caller_max_stranded=caller_max_stranded, event_late=bool(late),
        p_win_source=P_WIN_SOURCE, patient_ceiling=D(str(sports_cap)))
    out["policy"] = res
    act = res.get("action")
    if act == "MAKER_COMPLETE":
        pc = D(str(lead_px)) + px
        if pc > ceil:                     # belt and braces: never looser
            out["why"] = "SPORTS_CAP"
            return out
        out["px"], out["qty"] = px, D(str(need))
        if resting_px is not None and D(str(resting_px)) == px:
            out["action"] = "KEEP"
        elif resting_px is not None:
            out["action"] = "REPRICE_COMPLETION"
        else:
            out["action"] = "COMPLETE_MAKER"
        return out
    out["why"] = ("REDUCE_NOT_EXECUTED" if act == "REDUCE"
                  else res.get("why") or act)
    return out
