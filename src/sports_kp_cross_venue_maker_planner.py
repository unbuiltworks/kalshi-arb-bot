"""Read-only Kalshi <-> Polymarket maker-to-hedge planner.

This is intentionally separate from the existing ``sports-kp-live-executor``
transport.  That production service currently uses a K/P-certified source signal
but mutates a same-Polymarket structural maker/hedge pair.  This module answers a
different question: if one leg rested as maker on one venue, what exact price and
quantity could be posted while reserving an immediately executable complementary
hedge on the other venue and preserving the >=3c/contract floor?

No SDK clients, credentials, network calls, order creation, cancellation or live
authorization exist in this module.  Callers must supply certified fee functions,
books, balances, and rule/freshness gates.  Unknown fee or rule semantics remain
fail-closed for execution eligibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Callable, Iterable, Mapping

D = Decimal
FeeFn = Callable[[Decimal, Decimal], Decimal]
DEFAULT_TIERS = (1, 5, 10, 25, 50, 100)


@dataclass(frozen=True)
class SideBook:
    bid: Decimal
    bid_depth: Decimal
    ask: Decimal
    ask_depth: Decimal


@dataclass(frozen=True)
class BinaryVenueBook:
    venue: str
    market_id: str
    yes: SideBook
    no: SideBook
    tick: Decimal = D("0.01")

    def side(self, side: str) -> SideBook:
        normalized = str(side).upper()
        if normalized == "YES":
            return self.yes
        if normalized == "NO":
            return self.no
        raise ValueError(f"unsupported binary side: {side}")


@dataclass(frozen=True)
class CrossVenueGates:
    exact_counterpart: bool
    settlement_rules_certified: bool
    fee_schedule_certified: bool
    quote_fresh: bool
    quote_skew_ok: bool
    hedge_depth_exact: bool
    capital_snapshot_fresh: bool
    no_unresolved_exposure: bool

    def failures(self) -> list[str]:
        return [name for name, value in self.__dict__.items() if not value]


def _floor_tick(value: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        raise ValueError("tick must be positive")
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _usable_capital(
    venue: str,
    balances: Mapping[str, Decimal] | None,
    buffers: Mapping[str, Decimal] | None,
) -> Decimal | None:
    if balances is None or venue not in balances:
        return None
    available = max(D("0"), D(str(balances[venue])))
    buffer = D("0") if buffers is None else max(D("0"), D(str(buffers.get(venue, D("0")))))
    return max(D("0"), available - buffer)


def _max_maker_price_for_floor(
    *,
    quantity: Decimal,
    hedge_ask: Decimal,
    maker_fee_fn: FeeFn,
    hedge_fee_fn: FeeFn,
    min_profit_per_contract: Decimal,
    tick: Decimal,
) -> Decimal | None:
    """Return highest tick price preserving the floor, accounting for fee rounding.

    Maker fees can be quantity/price nonlinear, so this deliberately searches tick
    prices rather than algebraically assuming a constant or zero maker fee.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    hedge_fee = D(str(hedge_fee_fn(hedge_ask, quantity)))
    # Algebraic fee-free upper bound gives a safe search start; maker fees can only
    # push the true maximum lower.
    fee_free_max = D("1") - hedge_ask - min_profit_per_contract - (hedge_fee / quantity)
    start = min(D("0.99"), _floor_tick(fee_free_max, tick))
    px = start
    while px > D("0"):
        maker_fee = D(str(maker_fee_fn(px, quantity)))
        locked = quantity - (quantity * px + maker_fee + quantity * hedge_ask + hedge_fee)
        if locked / quantity >= min_profit_per_contract:
            return px
        px -= tick
    return None


def plan_orientation(
    *,
    maker_book: BinaryVenueBook,
    maker_side: str,
    hedge_book: BinaryVenueBook,
    hedge_side: str,
    maker_fee_fn: FeeFn,
    hedge_fee_fn: FeeFn,
    gates: CrossVenueGates,
    tiers: Iterable[int] = DEFAULT_TIERS,
    min_profit_per_contract: Decimal = D("0.03"),
    balances: Mapping[str, Decimal] | None = None,
    free_buffers: Mapping[str, Decimal] | None = None,
) -> dict:
    """Plan one maker->hedge orientation without mutating either venue."""
    if maker_book.venue == hedge_book.venue:
        raise ValueError("cross-venue planner requires distinct venues")
    maker = maker_book.side(maker_side)
    hedge = hedge_book.side(hedge_side)
    if str(maker_side).upper() == str(hedge_side).upper():
        raise ValueError("guaranteed binary complement requires opposite YES/NO sides")

    maker_usable = _usable_capital(maker_book.venue, balances, free_buffers)
    hedge_usable = _usable_capital(hedge_book.venue, balances, free_buffers)
    balances_supplied = maker_usable is not None and hedge_usable is not None
    rows: list[dict] = []

    for raw_q in tiers:
        q_int = int(raw_q)
        if q_int <= 0:
            raise ValueError("tiers must be positive whole contracts")
        q = D(q_int)
        if hedge.ask_depth < q:
            rows.append({
                "quantity": q_int,
                "status": "HEDGE_DEPTH_BLOCKED",
                "hedge_ask": str(hedge.ask),
                "hedge_ask_depth": str(hedge.ask_depth),
                "execution_eligible": False,
            })
            continue

        max_floor_px = _max_maker_price_for_floor(
            quantity=q,
            hedge_ask=hedge.ask,
            maker_fee_fn=maker_fee_fn,
            hedge_fee_fn=hedge_fee_fn,
            min_profit_per_contract=min_profit_per_contract,
            tick=maker_book.tick,
        )
        if max_floor_px is None:
            rows.append({"quantity": q_int, "status": "EDGE_BLOCKED", "execution_eligible": False})
            continue

        # Competitive maker quote: improve/join the displayed bid, but never cross
        # the ask and never exceed the economic ceiling.  If the displayed bid is
        # already above the ceiling, there is no competitive >=floor maker quote.
        post_only_ceiling = _floor_tick(maker.ask - maker_book.tick, maker_book.tick)
        economic_ceiling = min(max_floor_px, post_only_ceiling)
        if economic_ceiling <= D("0") or maker.bid > economic_ceiling:
            rows.append({
                "quantity": q_int,
                "status": "MAKER_MARKET_TOO_RICH",
                "maker_bid": str(maker.bid),
                "maker_ask": str(maker.ask),
                "max_maker_price": str(max_floor_px),
                "execution_eligible": False,
            })
            continue
        maker_px = max(maker.bid, economic_ceiling)

        maker_fee = D(str(maker_fee_fn(maker_px, q)))
        hedge_fee = D(str(hedge_fee_fn(hedge.ask, q)))
        maker_capital = q * maker_px + maker_fee
        hedge_capital = q * hedge.ask + hedge_fee
        locked_profit = q - maker_capital - hedge_capital
        unit_profit = locked_profit / q
        if unit_profit < min_profit_per_contract:
            rows.append({"quantity": q_int, "status": "EDGE_BLOCKED_AFTER_ROUNDING", "execution_eligible": False})
            continue

        capital_blockers: list[str] = []
        if balances_supplied:
            assert maker_usable is not None and hedge_usable is not None
            if maker_usable < maker_capital:
                capital_blockers.append(maker_book.venue)
            if hedge_usable < hedge_capital:
                capital_blockers.append(hedge_book.venue)
        gate_failures = gates.failures()
        if capital_blockers:
            status = "CAPITAL_BLOCKED"
        elif gate_failures:
            status = "SAFETY_GATE_BLOCKED"
        else:
            status = "PLANNABLE"

        rows.append({
            "quantity": q_int,
            "status": status,
            "maker_venue": maker_book.venue,
            "maker_market_id": maker_book.market_id,
            "maker_side": str(maker_side).upper(),
            "maker_price": str(maker_px),
            "maker_bid": str(maker.bid),
            "maker_ask": str(maker.ask),
            "maker_fee": str(maker_fee),
            "hedge_venue": hedge_book.venue,
            "hedge_market_id": hedge_book.market_id,
            "hedge_side": str(hedge_side).upper(),
            "hedge_ask": str(hedge.ask),
            "hedge_ask_depth": str(hedge.ask_depth),
            "hedge_fee": str(hedge_fee),
            "locked_profit": str(locked_profit),
            "profit_per_contract": str(unit_profit),
            "capital_by_venue": {
                maker_book.venue: str(maker_capital),
                hedge_book.venue: str(hedge_capital),
            },
            "usable_capital_by_venue": {
                maker_book.venue: str(maker_usable) if maker_usable is not None else None,
                hedge_book.venue: str(hedge_usable) if hedge_usable is not None else None,
            },
            "capital_blockers": capital_blockers,
            "gate_failures": gate_failures,
            # A read-only planner never authorizes mutation even when all gates pass.
            "execution_eligible": False,
        })

    plannable = [row for row in rows if row.get("status") == "PLANNABLE"]
    best = max(plannable, key=lambda row: int(row["quantity"]), default=None)
    return {
        "route": f"{maker_book.venue}_MAKER_TO_{hedge_book.venue}_HEDGE",
        "maker_side": str(maker_side).upper(),
        "hedge_side": str(hedge_side).upper(),
        "min_profit_per_contract": str(min_profit_per_contract),
        "balances_supplied": balances_supplied,
        "gate_failures": gates.failures(),
        "tiers": rows,
        "largest_plannable_tier": int(best["quantity"]) if best else 0,
        "largest_plannable_locked_profit": str(best["locked_profit"]) if best else "0",
        "read_only": True,
        "order_entry": False,
        "execution_eligible": False,
    }


def plan_both_venues(
    *,
    kalshi_book: BinaryVenueBook,
    polymarket_book: BinaryVenueBook,
    kalshi_maker_fee_fn: FeeFn,
    kalshi_taker_fee_fn: FeeFn,
    polymarket_maker_fee_fn: FeeFn,
    polymarket_taker_fee_fn: FeeFn,
    gates: CrossVenueGates,
    tiers: Iterable[int] = DEFAULT_TIERS,
    min_profit_per_contract: Decimal = D("0.03"),
    balances: Mapping[str, Decimal] | None = None,
    free_buffers: Mapping[str, Decimal] | None = None,
) -> list[dict]:
    """Evaluate all four complementary cross-venue maker orientations."""
    if kalshi_book.venue != "kalshi" or polymarket_book.venue != "polymarket":
        raise ValueError("expected canonical venue names kalshi and polymarket")
    common = dict(
        gates=gates,
        tiers=tiers,
        min_profit_per_contract=min_profit_per_contract,
        balances=balances,
        free_buffers=free_buffers,
    )
    return [
        plan_orientation(
            maker_book=kalshi_book, maker_side="YES", hedge_book=polymarket_book, hedge_side="NO",
            maker_fee_fn=kalshi_maker_fee_fn, hedge_fee_fn=polymarket_taker_fee_fn, **common,
        ),
        plan_orientation(
            maker_book=kalshi_book, maker_side="NO", hedge_book=polymarket_book, hedge_side="YES",
            maker_fee_fn=kalshi_maker_fee_fn, hedge_fee_fn=polymarket_taker_fee_fn, **common,
        ),
        plan_orientation(
            maker_book=polymarket_book, maker_side="YES", hedge_book=kalshi_book, hedge_side="NO",
            maker_fee_fn=polymarket_maker_fee_fn, hedge_fee_fn=kalshi_taker_fee_fn, **common,
        ),
        plan_orientation(
            maker_book=polymarket_book, maker_side="NO", hedge_book=kalshi_book, hedge_side="YES",
            maker_fee_fn=polymarket_maker_fee_fn, hedge_fee_fn=kalshi_taker_fee_fn, **common,
        ),
    ]
