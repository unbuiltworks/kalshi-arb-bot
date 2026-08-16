"""Reconcile synthetic MLB cross-venue paper probes to observed venue outcomes.

Research-only accounting. This module has no network client, credentials, order-entry,
cancel, or live-arm path. It consumes the append-only synthetic ledger plus an
operator/collector-produced JSONL file containing independently observed outcomes on
BOTH venues. It records venue-specific settlement payouts so settlement mismatches
are visible rather than silently treated as a guaranteed $1 complementary payout.
"""
from __future__ import annotations

import argparse
from dataclasses import fields
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Iterable

from src.cross_venue_ledger import LedgerEvent, LedgerSidecar, make_event, reconcile_events, summarize_ledger

D = Decimal
VALID_OUTCOMES = {"YES", "NO"}
VENUES = ("polymarket_us", "kalshi")


def _event_from_row(row: dict[str, Any]) -> LedgerEvent:
    data = dict(row)
    for key in ("quantity", "price", "fee", "payout"):
        data[key] = D(str(data.get(key, "0")))
    allowed = {field.name for field in fields(LedgerEvent)}
    return LedgerEvent(**{key: value for key, value in data.items() if key in allowed})


def load_ledger(path: Path) -> list[LedgerEvent]:
    if not path.exists():
        return []
    out: list[LedgerEvent] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(_event_from_row(json.loads(line)))
    return out


def load_outcomes(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def settlement_events(
    ledger_events: Iterable[LedgerEvent],
    outcome_rows: Iterable[dict[str, Any]],
) -> tuple[list[LedgerEvent], list[dict[str, Any]]]:
    """Build idempotent venue-specific settlement events for complete outcome rows.

    Outcome rows must identify an existing ``trade_group_id`` and include BOTH
    ``polymarket_us_outcome`` and ``kalshi_outcome`` as YES/NO. Partial or malformed
    rows fail closed. Existing settlement events are never recreated.
    """
    events = list(ledger_events)
    fills_by_group: dict[str, dict[str, LedgerEvent]] = {}
    existing: set[tuple[str, str]] = set()
    for event in events:
        if event.event_type == "fill" and event.venue in VENUES:
            fills_by_group.setdefault(event.trade_group_id, {})[event.venue] = event
        elif event.event_type == "settlement":
            existing.add((event.trade_group_id, event.venue))

    created: list[LedgerEvent] = []
    audits: list[dict[str, Any]] = []
    seen_rows: set[str] = set()
    for row in outcome_rows:
        group_id = str(row.get("trade_group_id") or "")
        if not group_id or group_id in seen_rows:
            continue
        seen_rows.add(group_id)
        poly_outcome = str(row.get("polymarket_us_outcome") or "").upper()
        kalshi_outcome = str(row.get("kalshi_outcome") or "").upper()
        if poly_outcome not in VALID_OUTCOMES or kalshi_outcome not in VALID_OUTCOMES:
            continue
        fills = fills_by_group.get(group_id) or {}
        if set(fills) != set(VENUES):
            continue

        group_created: list[LedgerEvent] = []
        for venue, outcome in (("polymarket_us", poly_outcome), ("kalshi", kalshi_outcome)):
            if (group_id, venue) in existing:
                continue
            fill = fills[venue]
            payout = fill.quantity if fill.side == outcome else D("0")
            settled_ts_ms = int(row.get(f"{venue}_settled_ts_ms") or row.get("settled_ts_ms") or fill.ts_ms)
            event = make_event(
                trade_group_id=fill.trade_group_id,
                settlement_key=fill.settlement_key,
                category=fill.category,
                venue=venue,
                market_id=fill.market_id,
                event_type="settlement",
                payout=payout,
                external_id=str(row.get(f"{venue}_settlement_id") or "") or None,
                ts_ms=settled_ts_ms,
            )
            group_created.append(event)
            existing.add((group_id, venue))
        if group_created:
            created.extend(group_created)
            audits.append({
                "trade_group_id": group_id,
                "polymarket_us_outcome": poly_outcome,
                "kalshi_outcome": kalshi_outcome,
                "outcomes_agree": poly_outcome == kalshi_outcome,
                "settlement_events_created": len(group_created),
                "read_only": True,
                "order_entry": False,
            })
    return created, audits


def reconcile_paper_ledger(ledger_path: Path, outcomes_path: Path) -> dict[str, Any]:
    before = load_ledger(ledger_path)
    created, audits = settlement_events(before, load_outcomes(outcomes_path))
    if created:
        sidecar = LedgerSidecar(ledger_path)
        for event in created:
            sidecar.emit(event)
        sidecar.drain_once(max_events=len(created))
    merged = reconcile_events(before, created)
    summary = summarize_ledger(merged)
    summary.update({
        "new_settlement_events": len(created),
        "new_settled_trade_groups": len(audits),
        "new_outcome_disagreement_groups": sum(not row["outcomes_agree"] for row in audits),
        "settlement_audits": audits,
        "settlement_semantics": "VENUE_SPECIFIC_OBSERVED_OUTCOMES_NOT_ASSUMED_EQUIVALENT",
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
    })
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=Path("logs/mlb_prop_paper_ledger.jsonl"))
    parser.add_argument("--outcomes", type=Path, default=Path("logs/mlb_prop_paper_outcomes.jsonl"))
    args = parser.parse_args()
    print(json.dumps(reconcile_paper_ledger(args.ledger, args.outcomes), sort_keys=True))


if __name__ == "__main__":
    main()
