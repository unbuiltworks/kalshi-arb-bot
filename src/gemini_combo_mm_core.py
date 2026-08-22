"""Pure safety/economics core for the Gemini sports combo market-maker.

No network I/O and no order entry. Shared rules:
- exact quantity-rounded prediction-market fees;
- generalized same-Gemini combo-cover identity;
- dynamic max-safe maker pricing;
- q>1 partial-fill-safe price envelope (every fill size 1..q passes);
- conservative physical-event correlation filtering;
- fillability / expected-dollars-per-hour scoring;
- capital-reserved selection for several simultaneous quotes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_CEILING
from math import exp
import re
from typing import Any, Iterable, Mapping, Sequence

D = Decimal
ONE = D("1")
TARGET = D("0.03")
MAKER = D("0.0175")
TAKER = D("0.07")


def dec(v: Any) -> D | None:
    try: x = D(str(v))
    except Exception: return None
    return x if x.is_finite() else None


def fee_total(price: D, qty: D, rate: D) -> D:
    price, qty, rate = D(price), D(qty), D(rate)
    if qty < 0 or rate < 0 or not D("0") <= price <= ONE:
        raise ValueError("invalid fee input")
    raw = rate * qty * price * (ONE - price)
    return (raw * D("100")).to_integral_value(rounding=ROUND_CEILING) / D("100")


def combo_cover_value(values: Sequence[D]) -> D:
    """prod(A_i)+sum(1-A_i), guaranteed >=1 for A_i in [0,1], n>=2."""
    if len(values) < 2: raise ValueError("need >=2 components")
    product, complements = ONE, D("0")
    for raw in values:
        x = D(raw)
        if not D("0") <= x <= ONE: raise ValueError("component outside [0,1]")
        product *= x
        complements += ONE - x
    return product + complements


def max_safe_combo_price(*, qty: int, hedge_all_in_total: D, target: D = TARGET) -> D | None:
    if qty < 1: raise ValueError("qty must be >=1")
    q = D(qty)
    for cents in range(99, 0, -1):
        p = D(cents) / D("100")
        total = p*q + fee_total(p, q, MAKER) + D(hedge_all_in_total)
        if (q-total)/q >= D(target): return p
    return None


def partial_fill_safe_price(*, max_qty: int, hedge_total_by_fill_qty: Mapping[int, D], target: D = TARGET) -> tuple[D | None, dict[int, D | None]]:
    """A q>1 maker quote is safe only if every possible partial fill is safe."""
    if max_qty < 1: raise ValueError("max_qty must be >=1")
    by_qty: dict[int, D | None] = {}
    for q in range(1, max_qty+1):
        hedge = hedge_total_by_fill_qty.get(q)
        by_qty[q] = None if hedge is None else max_safe_combo_price(qty=q, hedge_all_in_total=D(hedge), target=target)
    if any(by_qty[q] is None for q in range(1, max_qty+1)): return None, by_qty
    return min(x for x in by_qty.values() if x is not None), by_qty


def physical_event_key(event_ticker: str, title: str) -> str:
    # Total/spread/moneyline for the same contest normally share title before ':'.
    base = re.sub(r"\s+", " ", str(title or "").split(":", 1)[0].strip().lower())
    if base: return base
    ticker = str(event_ticker or "").upper().removeprefix("GEMI-")
    parts = ticker.split("-")
    if len(parts) >= 5 and re.fullmatch(r"\d{10}", parts[1] or ""):
        return "-".join(parts[:4]).lower()
    return ticker.lower()


@dataclass(frozen=True)
class HedgeDepth:
    qty: int
    raw_total: D
    fee_total: D
    all_in_total: D
    worst_price: D
    units_available: D


@dataclass(frozen=True)
class ComboLeg:
    event_ticker: str
    physical_event: str
    title: str
    symbol: str
    contract_id: str
    label: str
    required_outcome: str
    complement_outcome: str
    selected_mid: D
    expiry_ts: float
    terms_url: str
    hedge_depth: Mapping[int, HedgeDepth]


@dataclass
class ComboPlan:
    legs: tuple[ComboLeg, ...]
    max_qty: int
    fair_proxy: D
    exact_qty_safe_price: D | None
    partial_fill_safe_price: D | None
    safe_price_by_fill_qty: dict[int, D | None]
    hedge_total_exact_qty: D
    locked_per_contract: D | None
    fair_gap: D | None
    fillability_score: float
    fill_probability_1h: float
    expected_dollars_per_hour: D
    capital_reserve: D
    external_hedge_shadow: list[dict[str, Any]] = field(default_factory=list)
    external_hedge_execution_eligible: bool = False

    @property
    def leg_count(self) -> int: return len(self.legs)

    @property
    def combo_key(self) -> str:
        return "+".join(f"{x.contract_id}:{x.required_outcome}" for x in self.legs)


def fillability(*, safe_price: D, fair_proxy: D, leg_count: int, min_units: D, hours_to_expiry: float, historical_fill_rate: float | None = None) -> tuple[float, float]:
    gap = max(0.0, float(D(fair_proxy)-D(safe_price)))
    price_score = exp(-24.0*gap)
    depth_score = max(0.15, min(1.0, float(D(min_units))/25.0))
    leg_score = max(0.45, 1.0 - 0.10*max(0, leg_count-2))
    expiry_score = max(0.35, min(1.0, 8.0/max(1.0, hours_to_expiry)))
    prior = 0.20 if historical_fill_rate is None else max(0.02, min(0.95, historical_fill_rate))
    score = max(0.001, min(1.0, 0.8*(price_score*depth_score*leg_score*expiry_score)+0.2*prior))
    return score, max(0.001, min(0.90, 0.75*score))


def make_plan(*, legs: Sequence[ComboLeg], max_qty: int, now_ts: float, target: D = TARGET, historical_fill_rate: float | None = None) -> ComboPlan | None:
    if not 2 <= len(legs) <= 6: return None
    if len({x.contract_id for x in legs}) != len(legs): return None
    # Conservative pre-create correlation gate. Gemini may still reject some pairs.
    if len({x.physical_event for x in legs}) != len(legs): return None
    fair = ONE
    for leg in legs: fair *= D(leg.selected_mid)
    hedge_by_q: dict[int,D] = {}
    for q in range(1,max_qty+1):
        rows=[leg.hedge_depth.get(q) for leg in legs]
        if any(r is None for r in rows): continue
        hedge_by_q[q]=sum((D(r.all_in_total) for r in rows if r is not None),D("0"))
    if max_qty not in hedge_by_q: return None
    exact=max_safe_combo_price(qty=max_qty,hedge_all_in_total=hedge_by_q[max_qty],target=target)
    partial,by_q=partial_fill_safe_price(max_qty=max_qty,hedge_total_by_fill_qty=hedge_by_q,target=target)
    if partial is None:
        return ComboPlan(tuple(legs),max_qty,fair,exact,None,by_q,hedge_by_q[max_qty],None,None,0.0,0.0,D("0"),D("0"))
    qd=D(max_qty)
    total=partial*qd+fee_total(partial,qd,MAKER)+hedge_by_q[max_qty]
    locked=(qd-total)/qd
    gap=fair-partial
    min_units=min((leg.hedge_depth[1].units_available for leg in legs),default=D("0"))
    hours=max(0.01,(max(x.expiry_ts for x in legs)-now_ts)/3600.0)
    score,p1h=fillability(safe_price=partial,fair_proxy=fair,leg_count=len(legs),min_units=min_units,hours_to_expiry=hours,historical_fill_rate=historical_fill_rate)
    locked_total=max(D("0"),qd-total)
    reserve=partial*qd+fee_total(partial,qd,MAKER)+hedge_by_q[max_qty]
    return ComboPlan(tuple(legs),max_qty,fair,exact,partial,by_q,hedge_by_q[max_qty],locked,gap,score,p1h,locked_total*D(str(p1h)),reserve)


def reserve_candidates(plans: Iterable[ComboPlan], *, available_capital: D, max_resting: int = 5) -> tuple[list[ComboPlan],D]:
    remaining=D(available_capital); chosen: list[ComboPlan]=[]; seen_components:set[tuple[str,str]]=set()
    eligible=[p for p in plans if p.partial_fill_safe_price is not None and p.locked_per_contract is not None and p.locked_per_contract>=TARGET]
    eligible.sort(key=lambda p:(p.expected_dollars_per_hour,D(str(p.fillability_score)),-(p.fair_gap or D("99"))),reverse=True)
    for p in eligible:
        if len(chosen)>=max_resting: break
        components={(x.contract_id,x.complement_outcome) for x in p.legs}
        if components & seen_components: continue
        if p.capital_reserve>remaining: continue
        chosen.append(p); remaining-=p.capital_reserve; seen_components.update(components)
    return chosen,remaining
