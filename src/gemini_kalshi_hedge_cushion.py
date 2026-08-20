"""Pure fee-aware Kalshi hedge-cushion math for Gemini/Kalshi 15m research.

This module contains no network or order-entry code. It computes how far a Kalshi FOK
limit can be made more aggressive while preserving an exact after-fee profit floor.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from src.gemini_kalshi_live_probe_guard import recompute_payload_locked_profit
D=Decimal; CENT=D("0.01"); MIN_PRICE=D("0.01"); MAX_PRICE=D("0.99")
@dataclass(frozen=True)
class HedgeCushion:
    side:str; original_yes_price:Decimal; cushioned_yes_price:Decimal
    original_locked_profit:Decimal; cushioned_locked_profit:Decimal; profit_floor:Decimal
    cushion_cents:int; payload:dict[str,Any]
    @property
    def edge_spent(self): return self.original_locked_profit-self.cushioned_locked_profit
    @property
    def has_cushion(self): return self.cushion_cents>0

def _candidate_prices(side, original):
    if side=="bid":
        p=original
        while p<=MAX_PRICE: yield p; p+=CENT
    elif side=="ask":
        p=original
        while p>=MIN_PRICE: yield p; p-=CENT
    else: raise ValueError(f"unsupported Kalshi side={side!r}")

def best_fee_aware_kalshi_hedge(gemini_payload, kalshi_payload, *, kalshi_fee_multiplier, profit_floor=D("0.03")):
    side=str(kalshi_payload.get("side") or "").lower(); original=D(str(kalshi_payload.get("price"))); floor=D(profit_floor)
    if side not in {"bid","ask"}: raise ValueError("Kalshi hedge side must be bid or ask")
    if not MIN_PRICE<=original<=MAX_PRICE: raise ValueError("Kalshi hedge price outside prediction range")
    original_profit=recompute_payload_locked_profit(gemini_payload,kalshi_payload,kalshi_fee_multiplier=D(kalshi_fee_multiplier))
    if original_profit<floor: raise ValueError(f"original hedge is below profit floor original={original_profit} floor={floor}")
    best_price=original; best_profit=original_profit; best_payload=deepcopy(kalshi_payload)
    for candidate in _candidate_prices(side,original):
        payload=deepcopy(kalshi_payload); payload["price"]=str(candidate)
        profit=recompute_payload_locked_profit(gemini_payload,payload,kalshi_fee_multiplier=D(kalshi_fee_multiplier))
        if profit>=floor: best_price=candidate; best_profit=profit; best_payload=payload
    cents=int((abs(best_price-original)/CENT).to_integral_value())
    return HedgeCushion(side,original,best_price,original_profit,best_profit,floor,cents,best_payload)

def kalshi_cumulative_depth(book, *, side, yes_limit, quantity=D("1")):
    side=str(side).lower(); limit=D(yes_limit); required=D(quantity)
    if side not in {"bid","ask"}: raise ValueError("Kalshi side must be bid or ask")
    crossed=[]
    if side=="bid":
        for no_price,qty in sorted(book.no.items(),key=lambda r:r[0],reverse=True):
            yes_price=D("1")-D(no_price)
            if yes_price<=limit and D(qty)>0: crossed.append((yes_price,D(qty)))
    else:
        for yes_price,qty in sorted(book.yes.items(),key=lambda r:r[0],reverse=True):
            if D(yes_price)>=limit and D(qty)>0: crossed.append((D(yes_price),D(qty)))
    cumulative=sum((q for _p,q in crossed),D("0"))
    return {"side":side,"yes_limit":str(limit),"required_quantity":str(required),"cumulative_quantity":str(cumulative),"fillable":cumulative>=required,"levels_crossed":len(crossed)}
