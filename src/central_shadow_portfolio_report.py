"""Central read-only portfolio ranking for cross-venue research sleeves.

Consumes already-computed shadow lane summaries and ranks sleeves by a deliberately
conservative capacity score:

    net_edge_per_dollar * executable_probability * depth_dollars / capital_lock_hours

Settlement confidence and basis risk are reported separately and never silently
converted into execution eligibility. Any sleeve with a hard blocker receives a zero
promotable score even when its research-capacity score is large.

The report keeps theoretical shadow economics and later-settled paper realized P/L
strictly separate. Synthetic simultaneous fills are useful capacity evidence, but are
not realized economics until both venue outcomes have been observed and reconciled.

When polling-based episode analysis supplies an interval-censored upper bound for
capital lock time, portfolio ranking uses that upper bound rather than the optimistic
last-seen lower bound. If an upstream censor-aware analyzer explicitly supplies the
upper-bound field but cannot provide a finite bound (for example because an episode is
still right-censored at log end), ranking is withheld rather than silently falling back
to the optimistic point estimate.

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


def _strict_bool(value: Any) -> bool:
    """Only accept an actual JSON/Python boolean as verification evidence.

    ``bool("false")`` is True in Python. Treating stringly-typed telemetry as proof
    would let a producer accidentally turn an explicitly false verification marker
    into a verified observation/settlement window and manufacture a $/day run rate.
    Fail closed instead.
    """
    return value is True


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
    observation_window_verified: bool = False
    settled_paper_trade_groups: int = 0
    realized_paper_profit_dollars: Decimal = D("0")
    settlement_window_verified: bool = False
    capital_lock_hours_upper_bound: Decimal | None = None
    capital_lock_upper_bound_supplied: bool = False

    @property
    def conservative_capital_lock_hours(self) -> Decimal:
        upper = self.capital_lock_hours_upper_bound
        if upper is not None and upper > 0:
            return upper
        if self.capital_lock_upper_bound_supplied:
            return D("0")
        return self.capital_lock_hours

    @property
    def capital_lock_basis(self) -> str:
        upper = self.capital_lock_hours_upper_bound
        if upper is not None and upper > 0:
            return "INTERVAL_CENSORED_UPPER_BOUND"
        if self.capital_lock_upper_bound_supplied:
            return "RIGHT_CENSORED_OR_INVALID_UPPER_BOUND_WITHHELD"
        return "SUPPLIED_POINT_ESTIMATE"

    @property
    def capacity_score(self) -> Decimal:
        lock_hours = self.conservative_capital_lock_hours
        if lock_hours <= 0:
            return D("0")
        return (
            max(D("0"), self.net_edge_per_dollar)
            * min(D("1"), max(D("0"), self.executable_probability))
            * max(D("0"), self.depth_dollars)
            / lock_hours
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
        if not self.observation_window_verified or self.observed_hours <= 0:
            return None
        return self.shadow_profit_dollars * D("24") / self.observed_hours

    @property
    def realized_paper_dollars_per_day(self) -> Decimal | None:
        if (
            not self.observation_window_verified
            or not self.settlement_window_verified
            or self.observed_hours <= 0
            or self.settled_paper_trade_groups <= 0
        ):
            return None
        return self.realized_paper_profit_dollars * D("24") / self.observed_hours


def sleeve_from_mapping(row: dict[str, Any]) -> Sleeve:
    upper_bound_supplied = "capital_lock_hours_upper_bound" in row
    upper_raw = row.get("capital_lock_hours_upper_bound")
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
        observation_window_verified=_strict_bool(row.get("observation_window_verified", False)),
        settled_paper_trade_groups=int(row.get("settled_paper_trade_groups") or 0),
        realized_paper_profit_dollars=_d(row.get("realized_paper_profit_dollars")),
        settlement_window_verified=_strict_bool(row.get("settlement_window_verified", False)),
        capital_lock_hours_upper_bound=(None if upper_raw in (None, "") else _d(upper_raw)),
        capital_lock_upper_bound_supplied=upper_bound_supplied,
    )


def _serialize(sleeve: Sleeve, rank: int) -> dict[str, Any]:
    shadow_run_rate = sleeve.observed_shadow_dollars_per_day
    realized_run_rate = sleeve.realized_paper_dollars_per_day
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
        "capital_lock_hours_upper_bound": (
            str(sleeve.capital_lock_hours_upper_bound)
            if sleeve.capital_lock_hours_upper_bound is not None
            else None
        ),
        "capital_lock_upper_bound_supplied": sleeve.capital_lock_upper_bound_supplied,
        "conservative_capital_lock_hours": str(sleeve.conservative_capital_lock_hours),
        "capital_lock_basis": sleeve.capital_lock_basis,
        "settlement_confidence": str(sleeve.settlement_confidence),
        "basis_risk": str(sleeve.basis_risk),
        "hard_blockers": list(sleeve.hard_blockers),
        "observed_episodes": sleeve.observed_episodes,
        "observed_hours": str(sleeve.observed_hours),
        "shadow_profit_dollars": str(sleeve.shadow_profit_dollars),
        "observation_window_verified": sleeve.observation_window_verified,
        "observed_shadow_dollars_per_day": str(shadow_run_rate) if shadow_run_rate is not None else None,
        "run_rate_status": (
            "FULL_OBSERVATION_WINDOW_VERIFIED_THEORETICAL_SHADOW"
            if shadow_run_rate is not None
            else "WITHHELD_UNVERIFIED_OR_ZERO_OBSERVATION_WINDOW"
        ),
        "settled_paper_trade_groups": sleeve.settled_paper_trade_groups,
        "realized_paper_profit_dollars": str(sleeve.realized_paper_profit_dollars),
        "settlement_window_verified": sleeve.settlement_window_verified,
        "realized_paper_dollars_per_day": str(realized_run_rate) if realized_run_rate is not None else None,
        "realized_run_rate_status": (
            "FULL_OBSERVATION_AND_SETTLEMENT_WINDOW_VERIFIED"
            if realized_run_rate is not None
            else "WITHHELD_UNSETTLED_OR_UNVERIFIED_SETTLEMENT_WINDOW"
        ),
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
            "Capacity score = net edge per dollar × executable probability × displayed/consumable depth dollars ÷ conservative capital lock hours. "
            "When polling supplies interval-censored lock duration, the upper-bound capital lock is used for ranking rather than the optimistic last-seen lower bound. "
            "If a censor-aware caller explicitly supplies no finite upper bound (for example a right-censored active episode), capacity ranking is withheld instead of falling back to the point estimate. "
            "Settlement confidence and basis risk are applied separately; hard blockers force promotable_score to zero. "
            "Theoretical shadow $/day is withheld unless observed_hours covers the full scanner window and its verification marker is a real boolean true. "
            "Realized paper $/day is reported separately and only after venue-specific settlements are reconciled for that verified window."
        ),
    }
