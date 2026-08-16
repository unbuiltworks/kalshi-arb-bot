"""Convert strict MLB v4 snapshots into simultaneous synthetic paper fills.

Research-only sidecar. It has no venue clients, credentials, order-entry, cancel,
or live-arm path. Qualifying quote plans are written to the shared accounting
ledger with one trade_group_id and one timestamp for both synthetic legs.

A paper probe is deliberately *not* a live-readiness decision. Participation and
independent settlement-rule equivalence remain hard blockers.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from time import time_ns
from typing import Any

from src.cross_venue_ledger import LedgerSidecar, make_event
from src.strict_mlb_prop_snapshot_v4 import run_once

D = Decimal
PROBE_FIELDS = (("probe_1usd", D("1")), ("probe_5usd", D("5")))


def _settlement_key(row: dict[str, Any]) -> str:
    """Exact venue identity for one matched proposition.

    Date/player/stat/threshold alone is not enough for MLB doubleheaders: the same
    player can appear twice on one date with an identical prop. Include both venue
    market identifiers so paper fills can never collapse distinct scheduled games.
    """
    return "|".join(
        str(row.get(k) or "")
        for k in (
            "family",
            "game_date",
            "player",
            "threshold",
            "poly_slug",
            "kalshi_ticker",
        )
    )


def _group_id(row: dict[str, Any], probe_field: str, ts_ms: int) -> str:
    raw = "|".join(
        (
            _settlement_key(row),
            probe_field,
            str(ts_ms),
        )
    )
    return "paper-mlb-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def paper_probe_events(
    row: dict[str, Any],
    *,
    probe_field: str,
    min_profit: Decimal = D("0.03"),
    ts_ms: int | None = None,
) -> tuple[list, dict[str, Any]] | None:
    """Return simultaneous ledger events plus audit metadata for one capped plan."""
    plan = row.get(probe_field)
    if not isinstance(plan, dict):
        return None
    profit = D(str(plan.get("conservative_locked_profit") or "0"))
    if profit < min_profit:
        return None
    if not bool(row.get("book_comparable")):
        return None
    if row.get("fetch_error"):
        return None
    if not row.get("poly_slug") or not row.get("kalshi_ticker"):
        return None

    quantity = D(str(plan["quantity"]))
    poly_px = D(str(plan["poly_price"]))
    kalshi_px = D(str(plan["kalshi_price"]))
    poly_fee = D(str(plan["poly_fee"]))
    kalshi_fee = D(str(plan["kalshi_fee_upper_bound"]))
    direction = str(plan["direction"])
    if direction == "POLY_YES_KALSHI_NO":
        poly_side, kalshi_side = "YES", "NO"
    elif direction == "KALSHI_YES_POLY_NO":
        poly_side, kalshi_side = "NO", "YES"
    else:
        return None

    now_ms = time_ns() // 1_000_000 if ts_ms is None else ts_ms
    key = _settlement_key(row)
    group = _group_id(row, probe_field, now_ms)
    category = "mlb_prop_paper"
    common = dict(trade_group_id=group, settlement_key=key, category=category, ts_ms=now_ms)
    events = [
        make_event(**common, venue="polymarket_us", market_id=str(row["poly_slug"]), event_type="fill", side=poly_side, quantity=quantity, price=poly_px),
        make_event(**common, venue="kalshi", market_id=str(row["kalshi_ticker"]), event_type="fill", side=kalshi_side, quantity=quantity, price=kalshi_px),
    ]
    if poly_fee > 0:
        events.append(make_event(**common, venue="polymarket_us", market_id=str(row["poly_slug"]), event_type="fee", fee=poly_fee))
    if kalshi_fee > 0:
        events.append(make_event(**common, venue="kalshi", market_id=str(row["kalshi_ticker"]), event_type="fee", fee=kalshi_fee))

    audit = {
        "trade_group_id": group,
        "settlement_key": key,
        "probe_field": probe_field,
        "capital_cap": str(plan.get("capital_cap")),
        "modeled_profit": str(profit),
        "direction": direction,
        "quantity": str(quantity),
        "poly_slug": str(row["poly_slug"]),
        "kalshi_ticker": str(row["kalshi_ticker"]),
        "simultaneous_synthetic_fill_ts_ms": now_ms,
        "required_participation_gate": row.get("required_participation_gate"),
        "rule_status": row.get("rule_status"),
        "settlement_equivalent_now": bool(row.get("settlement_equivalent_now")),
        "live_probe_ready": False,
        "live_blockers": ["PARTICIPATION_GATE_REQUIRED", "INDEPENDENT_SETTLEMENT_AUDIT_REQUIRED"],
        "read_only": True,
        "order_entry": False,
    }
    return events, audit


def build_paper_probes(
    snapshot: dict[str, Any],
    *,
    min_profit: Decimal = D("0.03"),
    ts_ms: int | None = None,
) -> tuple[list, list[dict[str, Any]]]:
    """Build at most one $1 and one $5 paper probe per exact venue identity."""
    events = []
    audits: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for probe_field, _cap in PROBE_FIELDS:
        source = snapshot.get("top_probe_1usd" if probe_field == "probe_1usd" else "top_probe_5usd") or []
        for row in source:
            identity = (_settlement_key(row), probe_field)
            if identity in seen:
                continue
            built = paper_probe_events(row, probe_field=probe_field, min_profit=min_profit, ts_ms=ts_ms)
            if built is None:
                continue
            new_events, audit = built
            events.extend(new_events)
            audits.append(audit)
            seen.add(identity)
    return events, audits


async def run(ledger_path: Path, audit_path: Path, min_profit: Decimal) -> dict[str, Any]:
    snapshot = await run_once()
    events, audits = build_paper_probes(snapshot, min_profit=min_profit)
    ledger = LedgerSidecar(ledger_path)
    for event in events:
        ledger.emit(event)
    ledger.drain_once(max_events=max(1, len(events)))
    if audits:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as fh:
            for row in audits:
                fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    result = {
        "paper_probe_groups": len(audits),
        "ledger_events": len(events),
        "minimum_modeled_profit_dollars": str(min_profit),
        "live_probe_ready_groups": 0,
        "read_only": True,
        "order_entry": False,
    }
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=Path("logs/mlb_prop_paper_ledger.jsonl"))
    parser.add_argument("--audit", type=Path, default=Path("logs/mlb_prop_paper_probes.jsonl"))
    parser.add_argument("--min-profit", type=Decimal, default=D("0.03"))
    args = parser.parse_args()
    asyncio.run(run(args.ledger, args.audit, args.min_profit))


if __name__ == "__main__":
    main()
