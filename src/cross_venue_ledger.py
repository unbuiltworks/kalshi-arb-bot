"""Non-blocking cross-venue accounting sidecar.

This module is intentionally off the quote/order critical path. Producers only
construct a small immutable event and enqueue it to an in-memory SimpleQueue.
Persistence and reconciliation happen asynchronously in a background worker or
from later venue-history replays.

No venue client or order-entry capability exists here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, Thread
from time import time_ns
from typing import Iterable

D = Decimal

EVENT_TYPES = {"order", "fill", "fee", "settlement"}
SIDES = {"YES", "NO"}


@dataclass(frozen=True)
class LedgerEvent:
    trade_group_id: str
    settlement_key: str
    category: str
    venue: str
    market_id: str
    event_type: str
    ts_ms: int
    side: str | None = None
    quantity: Decimal = D("0")
    price: Decimal = D("0")
    fee: Decimal = D("0")
    payout: Decimal = D("0")
    external_id: str | None = None

    def __post_init__(self) -> None:
        if not self.trade_group_id or not self.settlement_key:
            raise ValueError("trade_group_id and settlement_key are required")
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported event_type={self.event_type!r}")
        if self.side is not None and self.side not in SIDES:
            raise ValueError(f"unsupported side={self.side!r}")
        for name, value in (("quantity", self.quantity), ("price", self.price), ("fee", self.fee), ("payout", self.payout)):
            if value < 0:
                raise ValueError(f"{name} must be non-negative")

    def as_json(self) -> str:
        row = asdict(self)
        for key in ("quantity", "price", "fee", "payout"):
            row[key] = str(row[key])
        return json.dumps(row, separators=(",", ":"), sort_keys=True)


class LedgerSidecar:
    """Best-effort local queue + append-only writer.

    ``emit`` performs no file, database, or network I/O. The worker owns all
    persistence. If persistence fails, trading logic remains independent; callers
    can recover accounting later from venue histories via ``reconcile_events``.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._queue: SimpleQueue[LedgerEvent] = SimpleQueue()
        self._stop = Event()
        self._thread: Thread | None = None

    def emit(self, event: LedgerEvent) -> None:
        self._queue.put(event)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="cross-venue-ledger", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def drain_once(self, max_events: int = 1000) -> int:
        rows: list[LedgerEvent] = []
        for _ in range(max_events):
            try:
                rows.append(self._queue.get_nowait())
            except Empty:
                break
        if not rows:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(row.as_json() + "\n")
        return len(rows)

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.drain_once() == 0:
                self._stop.wait(0.05)
        self.drain_once(max_events=100_000)


def make_event(
    *,
    trade_group_id: str,
    settlement_key: str,
    category: str,
    venue: str,
    market_id: str,
    event_type: str,
    side: str | None = None,
    quantity: Decimal = D("0"),
    price: Decimal = D("0"),
    fee: Decimal = D("0"),
    payout: Decimal = D("0"),
    external_id: str | None = None,
    ts_ms: int | None = None,
) -> LedgerEvent:
    return LedgerEvent(
        trade_group_id=trade_group_id,
        settlement_key=settlement_key,
        category=category,
        venue=venue,
        market_id=market_id,
        event_type=event_type,
        ts_ms=(time_ns() // 1_000_000) if ts_ms is None else ts_ms,
        side=side,
        quantity=quantity,
        price=price,
        fee=fee,
        payout=payout,
        external_id=external_id,
    )


def reconcile_events(*event_sets: Iterable[LedgerEvent]) -> list[LedgerEvent]:
    """Merge persisted/local and venue-history events idempotently.

    Venue ``external_id`` is preferred for dedupe. Synthetic events without an
    external id use their complete economic identity so replay can recover state
    without double counting.
    """
    unique: dict[tuple[object, ...], LedgerEvent] = {}
    for events in event_sets:
        for event in events:
            if event.external_id:
                key = (event.venue, event.event_type, event.external_id)
            else:
                key = (
                    event.trade_group_id,
                    event.settlement_key,
                    event.venue,
                    event.market_id,
                    event.event_type,
                    event.ts_ms,
                    event.side,
                    event.quantity,
                    event.price,
                    event.fee,
                    event.payout,
                )
            unique[key] = event
    return sorted(unique.values(), key=lambda row: (row.ts_ms, row.venue, row.market_id))


def summarize_ledger(events: Iterable[LedgerEvent]) -> dict[str, object]:
    """Report realized/unrealized/locked accounting and unmatched-leg exposure.

    A multi-venue trade group is realized only after every venue that has a fill in
    that group has a settlement event. Partial venue settlement remains pending so a
    single early venue payout can never make a cross-venue paper probe look realized.
    """
    rows = list(events)
    groups: dict[str, dict[str, object]] = {}
    by_venue: dict[str, Decimal] = {}
    by_category: dict[str, Decimal] = {}

    for event in rows:
        group = groups.setdefault(event.trade_group_id, {
            "settlement_key": event.settlement_key,
            "category": event.category,
            "cost": D("0"),
            "fees": D("0"),
            "payout": D("0"),
            "yes_qty": D("0"),
            "no_qty": D("0"),
            "venues": set(),
            "fill_venues": set(),
            "settled_venues": set(),
        })
        group["venues"].add(event.venue)
        if event.event_type == "fill":
            group["fill_venues"].add(event.venue)
            cost = event.quantity * event.price
            group["cost"] += cost
            if event.side == "YES":
                group["yes_qty"] += event.quantity
            elif event.side == "NO":
                group["no_qty"] += event.quantity
            by_venue[event.venue] = by_venue.get(event.venue, D("0")) - cost
            by_category[event.category] = by_category.get(event.category, D("0")) - cost
        elif event.event_type == "fee":
            group["fees"] += event.fee
            by_venue[event.venue] = by_venue.get(event.venue, D("0")) - event.fee
            by_category[event.category] = by_category.get(event.category, D("0")) - event.fee
        elif event.event_type == "settlement":
            group["settled_venues"].add(event.venue)
            group["payout"] += event.payout
            by_venue[event.venue] = by_venue.get(event.venue, D("0")) + event.payout
            by_category[event.category] = by_category.get(event.category, D("0")) + event.payout

    realized = D("0")
    locked = D("0")
    pending = 0
    partial_settlement_groups = 0
    unmatched_exposure = D("0")
    cleaned: dict[str, object] = {}
    for trade_group_id, group in sorted(groups.items()):
        cost = D(str(group["cost"]))
        fees = D(str(group["fees"]))
        payout = D(str(group["payout"]))
        yes_qty = D(str(group["yes_qty"]))
        no_qty = D(str(group["no_qty"]))
        fill_venues = set(group["fill_venues"])
        settled_venues = set(group["settled_venues"])
        fully_settled = bool(fill_venues) and fill_venues.issubset(settled_venues)
        partially_settled = bool(settled_venues) and not fully_settled
        pnl = payout - cost - fees if fully_settled else D("0")
        if fully_settled:
            realized += pnl
        else:
            pending += 1
            if partially_settled:
                partial_settlement_groups += 1
            matched = min(yes_qty, no_qty)
            # This is only pre-settlement complement accounting. Venue-specific
            # rule/index disagreement can still destroy the apparent lock.
            locked += matched - cost - fees
        unmatched = abs(yes_qty - no_qty)
        unmatched_exposure += unmatched
        cleaned[trade_group_id] = {
            "settlement_key": group["settlement_key"],
            "category": group["category"],
            "venues": sorted(group["venues"]),
            "fill_venues": sorted(fill_venues),
            "settled_venues": sorted(settled_venues),
            "cost": str(cost),
            "fees": str(fees),
            "payout": str(payout),
            "realized_pnl": str(pnl),
            "pending_settlement": not fully_settled,
            "partial_settlement": partially_settled,
            "matched_quantity": str(min(yes_qty, no_qty)),
            "unmatched_leg_quantity": str(unmatched),
        }

    return {
        "trade_groups": cleaned,
        "realized_pnl": str(realized),
        "unrealized_pnl": "0",
        "locked_pnl_before_basis_risk": str(locked),
        "pending_settlement_groups": pending,
        "partial_settlement_groups": partial_settlement_groups,
        "unmatched_leg_exposure_contracts": str(unmatched_exposure),
        "pnl_by_venue": {key: str(value) for key, value in sorted(by_venue.items())},
        "pnl_by_category": {key: str(value) for key, value in sorted(by_category.items())},
        "read_only_accounting_sidecar": True,
        "order_entry": False,
    }
