"""Fail-closed settlement-latency audit for MLB synthetic paper probes.

Research-only. Consumes ledger events that already exist; it has no network clients,
credentials, order-entry, cancel, sizing, or live-arm path.

A settlement timestamp that is absent or not later than the synthetic fill timestamp
must not be interpreted as real settlement latency. The settlement reconciler has a
legacy fallback to fill.ts_ms when an outcome collector omits settlement time; this
audit deliberately quarantines that shape rather than turning it into a zero-latency
observation.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from src.cross_venue_ledger import LedgerEvent

D = Decimal
VENUES = ("polymarket_us", "kalshi")


@dataclass(frozen=True)
class GroupLatency:
    trade_group_id: str
    fill_ts_ms: int
    polymarket_us_latency_ms: int | None
    kalshi_latency_ms: int | None
    both_settlements_observed: bool
    latency_verified: bool
    status: str

    @property
    def max_latency_ms(self) -> int | None:
        if not self.latency_verified:
            return None
        assert self.polymarket_us_latency_ms is not None
        assert self.kalshi_latency_ms is not None
        return max(self.polymarket_us_latency_ms, self.kalshi_latency_ms)


def audit_settlement_latency(events: Iterable[LedgerEvent]) -> dict[str, object]:
    fills: dict[str, dict[str, list[LedgerEvent]]] = defaultdict(lambda: defaultdict(list))
    settlements: dict[str, dict[str, list[LedgerEvent]]] = defaultdict(lambda: defaultdict(list))

    for event in events:
        if event.venue not in VENUES:
            continue
        if event.event_type == "fill":
            fills[event.trade_group_id][event.venue].append(event)
        elif event.event_type == "settlement":
            settlements[event.trade_group_id][event.venue].append(event)

    rows: list[GroupLatency] = []
    for group_id in sorted(fills):
        group_fills = fills[group_id]
        if any(len(group_fills.get(v, [])) != 1 for v in VENUES):
            rows.append(GroupLatency(group_id, 0, None, None, False, False, "AMBIGUOUS_OR_MISSING_FILL_FAIL_CLOSED"))
            continue
        poly_fill = group_fills["polymarket_us"][0]
        kalshi_fill = group_fills["kalshi"][0]
        if poly_fill.ts_ms != kalshi_fill.ts_ms:
            rows.append(GroupLatency(group_id, min(poly_fill.ts_ms, kalshi_fill.ts_ms), None, None, False, False, "NON_SIMULTANEOUS_SYNTHETIC_FILL_FAIL_CLOSED"))
            continue

        fill_ts = poly_fill.ts_ms
        lats: dict[str, int | None] = {}
        bad = False
        for venue in VENUES:
            venue_settlements = settlements.get(group_id, {}).get(venue, [])
            if len(venue_settlements) != 1:
                lats[venue] = None
                bad = True
                continue
            settlement = venue_settlements[0]
            latency = settlement.ts_ms - fill_ts
            if latency <= 0:
                lats[venue] = None
                bad = True
            else:
                lats[venue] = latency

        both = all(len(settlements.get(group_id, {}).get(v, [])) == 1 for v in VENUES)
        verified = not bad and all(lats.get(v) is not None for v in VENUES)
        status = "VERIFIED_SETTLEMENT_LATENCY" if verified else "MISSING_OR_NONPOSITIVE_SETTLEMENT_TIME_FAIL_CLOSED"
        rows.append(GroupLatency(group_id, fill_ts, lats.get("polymarket_us"), lats.get("kalshi"), both, verified, status))

    verified_rows = [row for row in rows if row.latency_verified]
    max_latencies = sorted(row.max_latency_ms for row in verified_rows if row.max_latency_ms is not None)
    mean_max = None
    if max_latencies:
        mean_max = sum(max_latencies) / len(max_latencies)

    return {
        "groups_with_fills": len(rows),
        "verified_latency_groups": len(verified_rows),
        "unverified_latency_groups": len(rows) - len(verified_rows),
        "max_settlement_latency_ms_observations": max_latencies,
        "mean_max_settlement_latency_ms": mean_max,
        "groups": [
            {
                "trade_group_id": row.trade_group_id,
                "fill_ts_ms": row.fill_ts_ms,
                "polymarket_us_latency_ms": row.polymarket_us_latency_ms,
                "kalshi_latency_ms": row.kalshi_latency_ms,
                "max_latency_ms": row.max_latency_ms,
                "both_settlements_observed": row.both_settlements_observed,
                "latency_verified": row.latency_verified,
                "status": row.status,
            }
            for row in rows
        ],
        "latency_semantics": "OBSERVED_SETTLEMENT_TS_MUST_BE_STRICTLY_AFTER_SIMULTANEOUS_SYNTHETIC_FILL",
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
    }
