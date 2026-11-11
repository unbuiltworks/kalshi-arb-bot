"""Shared capital allocator for simultaneous PMUS + Gemini L5 workers.

OWNER 5554263833 items 5 and 7 (design, prebuilt, not wired to any live
path). Each venue worker keeps its OWN execution/risk/accounting
authority -- open-order reconciliation, deadman, ledger, venue health --
and asks this allocator only for capital: per-venue and total exposure,
per-venue and total daily-loss stops, per-venue concurrency, and a
single reservation per (venue, market) so capital is never reserved
twice. A venue outage flips that venue's health gate and refuses ITS
reservations only; the other venue keeps working. Only the TOTAL
daily-loss stop freezes both, by design.

Pure in-memory state with a JSON snapshot for the shared dashboard; the
caller persists it. Realized P&L enters only through `record_realized`,
which the workers call with venue-confirmed settlement rows (never a
mark).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Any, Mapping, Optional

HEALTH_OK = "OK"
HEALTH_OUTAGE = "OUTAGE"
HEALTH_DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class VenueLimits:
    venue: str
    max_open_exposure_usd: D
    max_concurrent_markets: int
    daily_loss_stop_usd: D                 # negative number, e.g. -1.00
    per_market_cap_usd: D


@dataclass
class Decision:
    granted: bool
    reason: str
    venue: str
    market: str
    usd: str
    snapshot: dict = field(default_factory=dict)


class Allocator:
    def __init__(self, venues: Mapping[str, VenueLimits],
                 total_exposure_cap_usd: D, total_daily_loss_stop_usd: D) -> None:
        self.venues = dict(venues)
        self.total_cap = D(str(total_exposure_cap_usd))
        self.total_loss_stop = D(str(total_daily_loss_stop_usd))
        self.reservations: dict[tuple[str, str], D] = {}
        self.realized: dict[str, D] = {v: D("0") for v in venues}
        self.health: dict[str, dict] = {v: {"state": HEALTH_OK, "reason": None}
                                        for v in venues}

    # ---- authority the workers keep; only the numbers come here ----
    def set_health(self, venue: str, state: str, reason: Optional[str] = None) -> None:
        if venue not in self.venues:
            raise KeyError(venue)
        self.health[venue] = {"state": state, "reason": reason}

    def record_realized(self, venue: str, usd: Any) -> None:
        """Venue-confirmed realized P&L only (settlement rows)."""
        if venue not in self.venues:
            raise KeyError(venue)
        self.realized[venue] += D(str(usd))

    def venue_exposure(self, venue: str) -> D:
        return sum((u for (v, _), u in self.reservations.items() if v == venue),
                   D("0"))

    def total_exposure(self) -> D:
        return sum(self.reservations.values(), D("0"))

    def venue_markets(self, venue: str) -> int:
        return sum(1 for (v, _) in self.reservations if v == venue)

    def total_realized(self) -> D:
        return sum(self.realized.values(), D("0"))

    def reserve(self, venue: str, market: str, usd: Any) -> Decision:
        amt = D(str(usd))
        lim = self.venues.get(venue)

        def no(reason: str) -> Decision:
            return Decision(False, reason, venue, market, str(amt), self.snapshot())

        if lim is None:
            return no("unknown_venue")
        if amt <= 0:
            return no("non_positive_amount")
        if self.total_realized() <= self.total_loss_stop:
            return no("total_daily_loss_stop")          # freezes every venue
        if self.realized[venue] <= lim.daily_loss_stop_usd:
            return no("venue_daily_loss_stop")
        if self.health[venue]["state"] != HEALTH_OK:
            return no(f"venue_health:{self.health[venue]['state']}")
        if (venue, market) in self.reservations:
            return no("already_reserved")                # never double-reserve
        if amt > lim.per_market_cap_usd:
            return no("per_market_cap")
        if self.venue_markets(venue) >= lim.max_concurrent_markets:
            return no("venue_concurrency")
        if self.venue_exposure(venue) + amt > lim.max_open_exposure_usd:
            return no("venue_exposure_cap")
        if self.total_exposure() + amt > self.total_cap:
            return no("total_exposure_cap")
        self.reservations[(venue, market)] = amt
        return Decision(True, "granted", venue, market, str(amt), self.snapshot())

    def release(self, venue: str, market: str) -> bool:
        return self.reservations.pop((venue, market), None) is not None

    def snapshot(self) -> dict:
        return {
            "total": {"exposure": str(self.total_exposure()),
                      "cap": str(self.total_cap),
                      "realized_today": str(self.total_realized()),
                      "daily_loss_stop": str(self.total_loss_stop)},
            "venues": {
                v: {"exposure": str(self.venue_exposure(v)),
                    "cap": str(l.max_open_exposure_usd),
                    "markets": self.venue_markets(v),
                    "max_markets": l.max_concurrent_markets,
                    "realized_today": str(self.realized[v]),
                    "daily_loss_stop": str(l.daily_loss_stop_usd),
                    "health": self.health[v]}
                for v, l in self.venues.items()},
            "reservations": [{"venue": v, "market": m, "usd": str(u)}
                             for (v, m), u in sorted(self.reservations.items())],
        }

    def to_json(self) -> str:
        return json.dumps(self.snapshot(), sort_keys=True)


def default_allocator() -> Allocator:
    """The current authorized numbers: PMUS R1 ($0.05 filled per market,
    3 markets, -$1.00 daily stop) and a Gemini lane that is DISARMED
    (zero exposure allowed) until the owner authorizes a probe."""
    return Allocator(
        {"PMUS": VenueLimits("PMUS", D("0.15"), 3, D("-1.00"), D("0.05")),
         "GEMINI": VenueLimits("GEMINI", D("0"), 0, D("-1.00"), D("0"))},
        total_exposure_cap_usd=D("0.15"), total_daily_loss_stop_usd=D("-1.00"))
