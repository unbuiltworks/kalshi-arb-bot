"""Exact state-space portfolio solver for 2-3 event synthetic arbitrage.

This module is pure math / validation: no network I/O and no order entry.
Every instrument is represented by an exact payoff vector over the complete
binary state universe. Prices are expected to be executable all-in taker costs.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations, product
from typing import Iterable, Mapping, Sequence

D = Decimal
ONE = D("1")
ZERO = D("0")


def state_universe(event_ids: Sequence[str]) -> tuple[tuple[int, ...], ...]:
    ids = tuple(event_ids)
    if len(ids) not in {2, 3}:
        raise ValueError("synthetic engine supports exactly 2 or 3 underlying events")
    if len(set(ids)) != len(ids) or any(not x for x in ids):
        raise ValueError("event_ids must be non-empty and unique")
    return tuple(product((0, 1), repeat=len(ids)))


@dataclass(frozen=True)
class Predicate:
    """Conjunction of required binary outcomes, e.g. A=1,B=0."""

    required: tuple[tuple[str, int], ...]

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, int | bool]) -> "Predicate":
        items: list[tuple[str, int]] = []
        for event_id, raw in sorted(mapping.items()):
            value = int(raw)
            if value not in {0, 1}:
                raise ValueError("predicate outcomes must be binary")
            if not event_id:
                raise ValueError("predicate event id is empty")
            items.append((event_id, value))
        if not items:
            raise ValueError("predicate must reference at least one event")
        if len(items) > 3:
            raise ValueError("only singles, doubles, and triples are supported")
        return cls(tuple(items))

    def evaluate(self, event_ids: Sequence[str], state: Sequence[int]) -> bool:
        if len(event_ids) != len(state):
            raise ValueError("state dimension mismatch")
        values = dict(zip(event_ids, state))
        try:
            return all(values[event_id] == expected for event_id, expected in self.required)
        except KeyError as exc:
            raise ValueError(f"predicate references unknown event {exc.args[0]!r}") from exc


@dataclass(frozen=True)
class Instrument:
    venue: str
    market_id: str
    position: str
    predicate: Predicate
    all_in_cost: Decimal
    executable_depth: Decimal
    settlement_fingerprint: str
    received_ms: int
    source_side: str = "CONTRACT"

    def validate(self, event_ids: Sequence[str]) -> None:
        if self.venue not in {"kalshi", "polymarket"}:
            raise ValueError("combo engine only accepts Kalshi/Polymarket instruments")
        if not self.market_id:
            raise ValueError("market_id is required")
        if self.position not in {"YES", "NO"}:
            raise ValueError("position must be YES or NO")
        if not (ZERO <= self.all_in_cost <= ONE):
            raise ValueError("all_in_cost must be within [0,1]")
        if self.executable_depth < ONE:
            raise ValueError("q=1 requires at least one contract of executable depth")
        if not self.settlement_fingerprint:
            raise ValueError("settlement fingerprint is required")
        referenced = {event_id for event_id, _ in self.predicate.required}
        if not referenced.issubset(set(event_ids)):
            raise ValueError("instrument predicate references an event outside this state space")

    def payoff_vector(self, event_ids: Sequence[str]) -> tuple[int, ...]:
        self.validate(event_ids)
        out: list[int] = []
        for state in state_universe(event_ids):
            yes = self.predicate.evaluate(event_ids, state)
            out.append(int(yes if self.position == "YES" else not yes))
        return tuple(out)


@dataclass(frozen=True)
class PortfolioLeg:
    instrument: Instrument
    payoff: tuple[int, ...]


@dataclass(frozen=True)
class PortfolioSolution:
    event_ids: tuple[str, ...]
    states: tuple[tuple[int, ...], ...]
    legs: tuple[PortfolioLeg, ...]
    total_cost: Decimal
    state_payouts: tuple[int, ...]
    min_state_payout: int
    locked_profit: Decimal
    status: str

    def as_dict(self) -> dict:
        return {
            "event_ids": list(self.event_ids),
            "states": [list(s) for s in self.states],
            "legs": [
                {
                    "venue": leg.instrument.venue,
                    "market_id": leg.instrument.market_id,
                    "position": leg.instrument.position,
                    "predicate": dict(leg.instrument.predicate.required),
                    "all_in_cost": str(leg.instrument.all_in_cost),
                    "executable_depth": str(leg.instrument.executable_depth),
                    "settlement_fingerprint": leg.instrument.settlement_fingerprint,
                    "payoff": list(leg.payoff),
                }
                for leg in self.legs
            ],
            "total_cost": str(self.total_cost),
            "state_payouts": list(self.state_payouts),
            "min_state_payout": self.min_state_payout,
            "locked_profit": str(self.locked_profit),
            "status": self.status,
        }


def _certify_fingerprints(
    instruments: Sequence[Instrument],
    approved_fingerprints: Mapping[str, str],
) -> None:
    """Require exact venue+market approval for every candidate instrument."""
    if not approved_fingerprints:
        raise PermissionError("missing exact settlement certification")
    for inst in instruments:
        key = f"{inst.venue}:{inst.market_id}"
        expected = approved_fingerprints.get(key)
        if not expected:
            raise PermissionError(f"instrument {key} is not rule-approved")
        if expected != inst.settlement_fingerprint:
            raise PermissionError(f"instrument {key} rule fingerprint changed")


def solve_min_cost_cover(
    event_ids: Sequence[str],
    instruments: Iterable[Instrument],
    *,
    approved_fingerprints: Mapping[str, str],
    max_instruments: int = 20,
) -> PortfolioSolution | None:
    """Return the exact minimum-cost q=1 portfolio covering every state.

    Because each contract pays at most $1 and all costs are non-negative, buying
    more than one copy of an identical q=1 instrument can never improve the
    minimum-cost solution for a $1 state guarantee. The optimization is therefore
    an exact binary subset problem. With the intentionally narrow 2-3 event scope
    and max 20 candidates, exhaustive search is deterministic and dependency-free.
    """
    ids = tuple(event_ids)
    states = state_universe(ids)
    rows = tuple(instruments)
    if not rows:
        return None
    if len(rows) > max_instruments:
        best_by_payoff: dict[tuple[int, ...], Instrument] = {}
        for inst in rows:
            payoff = inst.payoff_vector(ids)
            prev = best_by_payoff.get(payoff)
            if prev is None or inst.all_in_cost < prev.all_in_cost:
                best_by_payoff[payoff] = inst
        rows = tuple(best_by_payoff.values())
    if len(rows) > max_instruments:
        raise ValueError(f"candidate set too large after lossless dedupe: {len(rows)}")

    for inst in rows:
        inst.validate(ids)
    _certify_fingerprints(rows, approved_fingerprints)

    legs = tuple(PortfolioLeg(inst, inst.payoff_vector(ids)) for inst in rows)
    full_mask = (1 << len(states)) - 1
    masks: list[int] = []
    for leg in legs:
        mask = 0
        for i, pays in enumerate(leg.payoff):
            if pays:
                mask |= 1 << i
        masks.append(mask)

    best_indices: tuple[int, ...] | None = None
    best_cost: Decimal | None = None
    n = len(legs)
    for r in range(1, n + 1):
        for combo in combinations(range(n), r):
            cost = sum((legs[i].instrument.all_in_cost for i in combo), ZERO)
            if best_cost is not None and cost >= best_cost:
                continue
            cover = 0
            for i in combo:
                cover |= masks[i]
                if cover == full_mask:
                    break
            if cover != full_mask:
                continue
            payouts = tuple(sum(legs[i].payoff[s] for i in combo) for s in range(len(states)))
            if min(payouts) < 1:
                continue
            best_cost = cost
            best_indices = combo

    if best_indices is None or best_cost is None:
        return None

    chosen = tuple(legs[i] for i in best_indices)
    payouts = tuple(sum(leg.payoff[s] for leg in chosen) for s in range(len(states)))
    min_payout = min(payouts)
    locked = D(min_payout) - best_cost
    status = "EXECUTABLE_ARBITRAGE" if locked > ZERO else "FULLY_COVERED_NOT_ARBITRAGE"
    return PortfolioSolution(
        event_ids=ids,
        states=states,
        legs=chosen,
        total_cost=best_cost,
        state_payouts=payouts,
        min_state_payout=min_payout,
        locked_profit=locked,
        status=status,
    )
