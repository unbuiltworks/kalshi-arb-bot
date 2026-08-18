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


def _complete_outcome_rows(outcome_rows: Iterable[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Return one unambiguous complete outcome row per group plus conflicted groups.

    Append-only collectors may emit duplicate complete rows. Identical duplicates are
    harmless. If two complete rows disagree on either venue outcome, however, neither
    observation is trusted: accepting the first row would make realized paper P/L
    depend on file order rather than on a stable observed settlement.
    """
    chosen: dict[str, dict[str, Any]] = {}
    outcome_pairs: dict[str, tuple[str, str]] = {}
    conflicts: set[str] = set()
    for row in outcome_rows:
        group_id = str(row.get("trade_group_id") or "")
        if not group_id:
            continue
        poly_outcome = str(row.get("polymarket_us_outcome") or "").upper()
        kalshi_outcome = str(row.get("kalshi_outcome") or "").upper()
        if poly_outcome not in VALID_OUTCOMES or kalshi_outcome not in VALID_OUTCOMES:
            continue
        pair = (poly_outcome, kalshi_outcome)
        previous = outcome_pairs.get(group_id)
        if previous is None:
            outcome_pairs[group_id] = pair
            chosen[group_id] = row
        elif previous != pair:
            conflicts.add(group_id)
    for group_id in conflicts:
        chosen.pop(group_id, None)
    return chosen, conflicts


def _validated_group_fills(
    group_id: str,
    candidates_by_venue: dict[str, list[LedgerEvent]],
) -> tuple[dict[str, LedgerEvent] | None, dict[str, Any] | None]:
    """Return the one exact complementary synthetic fill per venue or fail closed.

    Paper micro-probes are emitted as exactly two simultaneous synthetic fills. A
    malformed/replayed ledger must never let settlement code silently choose the last
    fill seen for a venue: doing that could attach an observed outcome to the wrong
    market/side/quantity and manufacture realized paper P/L.
    """
    if set(candidates_by_venue) != set(VENUES):
        return None, None

    fills: dict[str, LedgerEvent] = {}
    variant_counts: dict[str, int] = {}
    for venue in VENUES:
        unique: dict[tuple[object, ...], LedgerEvent] = {}
        for event in candidates_by_venue[venue]:
            signature = (
                event.market_id,
                event.settlement_key,
                event.category,
                event.side,
                event.quantity,
                event.price,
            )
            unique[signature] = event
        variant_counts[venue] = len(unique)
        if len(unique) != 1:
            return None, {
                "trade_group_id": group_id,
                "status": "AMBIGUOUS_FILL_LEGS_FAIL_CLOSED",
                "fill_variants_by_venue": variant_counts,
                "settlement_events_created": 0,
                "read_only": True,
                "order_entry": False,
            }
        fills[venue] = next(iter(unique.values()))

    poly = fills["polymarket_us"]
    kalshi = fills["kalshi"]
    structurally_valid = (
        poly.settlement_key == kalshi.settlement_key
        and poly.category == kalshi.category
        and poly.quantity > 0
        and poly.quantity == kalshi.quantity
        and poly.side in VALID_OUTCOMES
        and kalshi.side in VALID_OUTCOMES
        and poly.side != kalshi.side
    )
    if not structurally_valid:
        return None, {
            "trade_group_id": group_id,
            "status": "MALFORMED_COMPLEMENTARY_FILL_GROUP_FAIL_CLOSED",
            "polymarket_us_market_id": poly.market_id,
            "kalshi_market_id": kalshi.market_id,
            "same_settlement_key": poly.settlement_key == kalshi.settlement_key,
            "same_category": poly.category == kalshi.category,
            "same_positive_quantity": poly.quantity > 0 and poly.quantity == kalshi.quantity,
            "complementary_sides": poly.side in VALID_OUTCOMES and kalshi.side in VALID_OUTCOMES and poly.side != kalshi.side,
            "settlement_events_created": 0,
            "read_only": True,
            "order_entry": False,
        }
    return fills, None


def settlement_events(
    ledger_events: Iterable[LedgerEvent],
    outcome_rows: Iterable[dict[str, Any]],
) -> tuple[list[LedgerEvent], list[dict[str, Any]]]:
    """Build idempotent venue-specific settlement events for complete outcome rows.

    Outcome rows must identify an existing ``trade_group_id`` and include BOTH
    ``polymarket_us_outcome`` and ``kalshi_outcome`` as YES/NO. Partial or malformed
    rows fail closed. Conflicting complete rows for the same group also fail closed,
    so append order cannot silently choose realized P/L. Existing settlement events
    are never recreated for the exact filled market leg; a settlement for some other
    market on the same venue cannot suppress reconciliation of the intended fill.
    """
    events = list(ledger_events)
    fills_by_group: dict[str, dict[str, list[LedgerEvent]]] = {}
    existing: set[tuple[str, str, str]] = set()
    for event in events:
        if event.event_type == "fill" and event.venue in VENUES:
            fills_by_group.setdefault(event.trade_group_id, {}).setdefault(event.venue, []).append(event)
        elif event.event_type == "settlement":
            existing.add((event.trade_group_id, event.venue, event.market_id))

    complete_rows, conflicts = _complete_outcome_rows(outcome_rows)
    created: list[LedgerEvent] = []
    audits: list[dict[str, Any]] = []

    for group_id in sorted(conflicts):
        audits.append({
            "trade_group_id": group_id,
            "status": "OUTCOME_CONFLICT_FAIL_CLOSED",
            "settlement_events_created": 0,
            "read_only": True,
            "order_entry": False,
        })

    for group_id, row in complete_rows.items():
        poly_outcome = str(row.get("polymarket_us_outcome") or "").upper()
        kalshi_outcome = str(row.get("kalshi_outcome") or "").upper()
        fills, fill_audit = _validated_group_fills(group_id, fills_by_group.get(group_id) or {})
        if fill_audit is not None:
            audits.append(fill_audit)
            continue
        if fills is None:
            continue

        group_created: list[LedgerEvent] = []
        for venue, outcome in (("polymarket_us", poly_outcome), ("kalshi", kalshi_outcome)):
            fill = fills[venue]
            exact_leg = (group_id, venue, fill.market_id)
            if exact_leg in existing:
                continue
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
            existing.add(exact_leg)
        if group_created:
            created.extend(group_created)
            audits.append({
                "trade_group_id": group_id,
                "status": "SETTLED_FROM_OBSERVED_OUTCOMES",
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
    settled_audits = [row for row in audits if row.get("status") == "SETTLED_FROM_OBSERVED_OUTCOMES"]
    conflict_audits = [row for row in audits if str(row.get("status", "")).endswith("FAIL_CLOSED")]
    summary.update({
        "new_settlement_events": len(created),
        "new_settled_trade_groups": len(settled_audits),
        "new_outcome_disagreement_groups": sum(not row["outcomes_agree"] for row in settled_audits),
        "outcome_conflict_groups": sum(row.get("status") == "OUTCOME_CONFLICT_FAIL_CLOSED" for row in conflict_audits),
        "fill_structure_conflict_groups": sum(row.get("status") in {"AMBIGUOUS_FILL_LEGS_FAIL_CLOSED", "MALFORMED_COMPLEMENTARY_FILL_GROUP_FAIL_CLOSED"} for row in conflict_audits),
        "settlement_audits": audits,
        "settlement_semantics": "VENUE_SPECIFIC_OBSERVED_OUTCOMES_NOT_ASSUMED_EQUIVALENT",
        "conflict_policy": "CONFLICTING_OUTCOMES_OR_AMBIGUOUS_FILL_STRUCTURE_FAIL_CLOSED_NO_REALIZED_PNL",
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
