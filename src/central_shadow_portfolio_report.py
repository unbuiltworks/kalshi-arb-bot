"""Central read-only portfolio ranking for cross-venue research sleeves.

Consumes already-computed shadow lane summaries and ranks sleeves by a deliberately
conservative capacity score:

    net_edge_per_dollar * executable_probability * depth_dollars / capital_lock_hours

Settlement confidence and basis risk are reported separately and never silently
converted into execution eligibility. Any sleeve with a hard blocker receives a zero
promotable score even when its research-capacity score is large.

No venue clients, credentials, or order-entry paths exist in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

D = Decimal


def _d(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return D(default)
    return D(str(value))


@dataclass(frozen=True)
class Sleeve:
    name: str
    category: str
    venues: tuple[str, ...]
    net_edge_per_dollar: Decimal
    executable_probability: Decimal
    depth_dollars: Decimal
    capital_lock_hours: Decimal
    settlement_confidence: Decimal
    basis_risk: Decimal
    hard_blockers: tuple[str, ...] = ()
    observed_episodes: int = 0
    observed_hours: Decimal = D("0")
    shadow_profit_dollars: Decimal = D("0")

    @property
    def capacity_score(self) -> Decimal:
        if self.capital_lock_hours <= 0:
            return D("0")
        return (
            max(D("0"), self.net_edge_per_dollar)
            * min(D("1"), max(D("0"), self.executable_probability))
            * max(D("0"), self.depth_dollars)
            / self.capital_lock_hours
        )

    @property
    def confidence_adjusted_score(self) -> Decimal:
        confidence = min(D("1"), max(D("0"), self.settlement_confidence))
        basis = min(D("1"), max(D("0"), self.basis_risk))
        return self.capacity_score * confidence * (D("1") - basis)

    @property
    def promotable_score(self) -> Decimal:
        return D("0") if self.hard_blockers else self.confidence_adjusted_score

    @property
    def observed_shadow_dollars_per_day(self) -> Decimal | None:
        if self.observed_hours <= 0:
            return None
        return self.shadow_profit_dollars * D("24") / self.observed_hours


def sleeve_from_mapping(row: dict[str, Any]) -> Sleeve:
    return Sleeve(
        name=str(row["name"]),
        category=str(row.get("category") or "unknown"),
        venues=tuple(str(v) for v in row.get("venues", ())),
        net_edge_per_dollar=_d(row.get("net_edge_per_dollar")),
        executable_probability=_d(row.get("executable_probability")),
        depth_dollars=_d(row.get("depth_dollars")),
        capital_lock_hours=_d(row.get("capital_lock_hours")),
        settlement_confidence=_d(row.get("settlement_confidence")),
        basis_risk=_d(row.get("basis_risk")),
        hard_blockers=tuple(str(v) for v in row.get("hard_blockers", ())),
        observed_episodes=int(row.get("observed_episodes") or 0),
        observed_hours=_d(row.get("observed_hours")),
        shadow_profit_dollars=_d(row.get("shadow_profit_dollars")),
    )


def _serialize(sleeve: Sleeve, rank: int) -> dict[str, Any]:
    run_rate = sleeve.observed_shadow_dollars_per_day
    return {
        "rank": rank,
        "name": sleeve.name,
        "category": sleeve.category,
        "venues": list(sleeve.venues),
        "capacity_score": str(sleeve.capacity_score),
        "confidence_adjusted_score": str(sleeve.confidence_adjusted_score),
        "promotable_score": str(sleeve.promotable_score),
        "net_edge_per_dollar": str(sleeve.net_edge_per_dollar),
        "executable_probability": str(sleeve.executable_probability),
        "depth_dollars": str(sleeve.depth_dollars),
        "capital_lock_hours": str(sleeve.capital_lock_hours),
        "settlement_confidence": str(sleeve.settlement_confidence),
        "basis_risk": str(sleeve.basis_risk),
        "hard_blockers": list(sleeve.hard_blockers),
        "observed_episodes": sleeve.observed_episodes,
        "observed_hours": str(sleeve.observed_hours),
        "shadow_profit_dollars": str(sleeve.shadow_profit_dollars),
        "observed_shadow_dollars_per_day": str(run_rate) if run_rate is not None else None,
        "promotion_status": "RESEARCH_ONLY_BLOCKED" if sleeve.hard_blockers else "NO_HARD_BLOCKER_RESEARCH_ONLY",
    }


def rank_sleeves(rows: Iterable[dict[str, Any] | Sleeve]) -> dict[str, Any]:
    sleeves = [row if isinstance(row, Sleeve) else sleeve_from_mapping(row) for row in rows]
    sleeves.sort(
        key=lambda s: (
            s.promotable_score,
            s.confidence_adjusted_score,
            s.capacity_score,
            s.observed_episodes,
        ),
        reverse=True,
    )
    return {
        "sleeves": [_serialize(s, i + 1) for i, s in enumerate(sleeves)],
        "read_only": True,
        "order_entry": False,
        "target_note": "$480/day is a research capacity target only, never a forecast.",
        "ranking_note": (
            "Capacity score = net edge per dollar × executable probability × displayed/consumable depth dollars ÷ capital lock hours. "
            "Settlement confidence and basis risk are applied separately; hard blockers force promotable_score to zero."
        ),
    }
