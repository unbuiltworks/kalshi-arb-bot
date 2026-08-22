"""Fail-closed hedge-choice layer for Gemini combo covers.

The combo can originate on Gemini while each exact complement is sourced from the
cheapest venue ONLY when identity + full settlement fingerprint are certified.
Uncertified prices are useful shadow diagnostics but can never win live routing.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from src.mlb_game_rule_certifications import assess_pair, audit_matrix

D=Decimal

@dataclass(frozen=True)
class HedgeOption:
    venue: str
    market_id: str
    outcome: str
    all_in_total: D
    depth: D
    quantity: D
    identity_match: bool
    rule_certified: bool
    freshness_ms: int | None = None
    metadata: dict[str,Any] | None = None

    @property
    def executable(self)->bool:
        return bool(self.identity_match and self.rule_certified and self.depth>=self.quantity and self.all_in_total>=0)


def choose_cheapest_certified(options: Iterable[HedgeOption]) -> tuple[HedgeOption|None,list[dict[str,Any]]]:
    rows=[]; eligible=[]
    for o in options:
        rows.append({"venue":o.venue,"market_id":o.market_id,"outcome":o.outcome,"all_in_total":str(o.all_in_total),"depth":str(o.depth),"quantity":str(o.quantity),"identity_match":o.identity_match,"rule_certified":o.rule_certified,"execution_eligible":o.executable,"freshness_ms":o.freshness_ms})
        if o.executable:eligible.append(o)
    eligible.sort(key=lambda x:(x.all_in_total,x.venue,x.market_id))
    return (eligible[0] if eligible else None),rows


def gemini_external_rule_certification(venue: str, family: str) -> dict[str,Any]:
    if venue.lower()=="gemini":
        return {"venue":"gemini","family":family,"exact_settlement_equivalent":True,"execution_eligible":True,"basis":"same_contract_same_venue"}
    assessment=assess_pair("gemini",venue,family)
    return assessment.as_dict()


def current_mlb_external_gate()->dict[str,Any]:
    matrix=audit_matrix()
    eligible=[]
    for family,group in matrix.get("families",{}).items():
        for row in group.get("prediction_vs_prediction",[]):
            if row.get("execution_eligible") and "gemini" in {row.get("left_venue"),row.get("right_venue")}:
                eligible.append({"family":family,**row})
    return {"status":"CROSS_VENUE_HEDGE_ROUTER_SHADOW","gemini_external_certified_pairs":eligible,"certified_pair_count":len(eligible),"selection_rule":"CHEAPEST_IDENTITY_MATCHED_FULL_RULE_CERTIFIED_ONLY","uncertified_quotes_can_never_execute":True,"read_only":True,"order_entry":False}
