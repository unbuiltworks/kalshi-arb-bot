"""Convert strict MLB v5 snapshots into simultaneous synthetic paper fills.

Research-only sidecar. It has no venue clients, credentials, order-entry, cancel,
or live-arm path. Qualifying quote plans are written to the shared accounting
ledger with one trade_group_id and one timestamp for both synthetic legs.

A paper probe is deliberately *not* a live-readiness decision. The v5 snapshot
must first prove the family-specific participation gate; independent settlement-
rule equivalence remains a hard blocker for any real-money probe.

Persistent active-opportunity state prevents a polling loop from treating the
same unchanged resting depth as fresh executable capacity on every run. A new
synthetic fill group is emitted only on OPEN, MOVE, or a later re-OPEN after the
opportunity disappeared from an intervening snapshot.
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
from src.strict_mlb_prop_snapshot_v5 import run_once

D = Decimal
PROBE_FIELDS = (("probe_1usd", D("1")), ("probe_5usd", D("5")))


def _settlement_key(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(k) or "")
        for k in ("family", "game_date", "player", "threshold", "poly_slug", "kalshi_ticker")
    )


def _group_id(row: dict[str, Any], probe_field: str, ts_ms: int) -> str:
    raw = "|".join((_settlement_key(row), probe_field, str(ts_ms)))
    return "paper-mlb-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def _state_key(row: dict[str, Any], probe_field: str) -> str:
    return f"{_settlement_key(row)}|{probe_field}"


def _opportunity_fingerprint(row: dict[str, Any], probe_field: str) -> str:
    plan = row.get(probe_field) or {}
    payload = {
        "settlement_key": _settlement_key(row),
        "probe_field": probe_field,
        "direction": plan.get("direction"),
        "quantity": str(plan.get("quantity")),
        "poly_price": str(plan.get("poly_price")),
        "kalshi_price": str(plan.get("kalshi_price")),
        "poly_fee": str(plan.get("poly_fee")),
        "kalshi_fee_upper_bound": str(plan.get("kalshi_fee_upper_bound")),
        "total_cost_upper_bound": str(plan.get("total_cost_upper_bound")),
        "conservative_locked_profit": str(plan.get("conservative_locked_profit")),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _load_active_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid paper-probe state: {path}") from exc
    if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        raise RuntimeError(f"invalid paper-probe state shape: {path}")
    return raw


def _write_active_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(path)


def paper_probe_events(
    row: dict[str, Any],
    *,
    probe_field: str,
    min_profit: Decimal = D("0.03"),
    ts_ms: int | None = None,
) -> tuple[list, dict[str, Any]] | None:
    plan = row.get(probe_field)
    if not isinstance(plan, dict):
        return None
    profit = D(str(plan.get("conservative_locked_profit") or "0"))
    if profit < min_profit:
        return None
    if not bool(row.get("book_comparable")):
        return None
    if row.get("synchronized_book_pair") is not True:
        return None
    if row.get("fetch_error"):
        return None
    if not row.get("poly_slug") or not row.get("kalshi_ticker"):
        return None
    if not bool(row.get("participation_gate_satisfied")):
        return None
    if row.get("read_only") is not True or row.get("order_entry") is not False:
        return None
    if row.get("execution_eligible") is not False or row.get("live_probe_ready") is not False:
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
        "synchronized_book_pair": True,
        "quote_receive_skew_ms": row.get("quote_receive_skew_ms"),
        "required_participation_gate": row.get("required_participation_gate"),
        "participation_proof": row.get("participation_proof"),
        "participation_gate_satisfied": True,
        "rule_status": row.get("rule_status"),
        "rules_status": row.get("rules_status", "RULES_PENDING"),
        "full_rules_audited": bool(row.get("full_rules_audited", False)),
        "fully_audited_shadow_ready": bool(row.get("fully_audited_shadow_ready", False)),
        "paper_probe_only": True,
        "live_probe_ready": False,
        "live_blockers": ["INDEPENDENT_SETTLEMENT_AUDIT_REQUIRED", "FRESH_EXPLICIT_APPROVAL_REQUIRED"],
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
    events, audits, _state, _suppressed = build_paper_probes_with_state(
        snapshot, prior_state={}, min_profit=min_profit, ts_ms=ts_ms
    )
    return events, audits


def build_paper_probes_with_state(
    snapshot: dict[str, Any],
    *,
    prior_state: dict[str, str],
    min_profit: Decimal = D("0.03"),
    ts_ms: int | None = None,
) -> tuple[list, list[dict[str, Any]], dict[str, str], int]:
    events = []
    audits: list[dict[str, Any]] = []
    current_state: dict[str, str] = {}
    suppressed_resting_duplicates = 0
    seen: set[tuple[str, str]] = set()
    source = snapshot.get("paper_probe_candidates") or []
    for probe_field, _cap in PROBE_FIELDS:
        for row in source:
            identity = (_settlement_key(row), probe_field)
            if identity in seen:
                continue
            seen.add(identity)
            built = paper_probe_events(row, probe_field=probe_field, min_profit=min_profit, ts_ms=ts_ms)
            if built is None:
                continue
            state_key = _state_key(row, probe_field)
            fingerprint = _opportunity_fingerprint(row, probe_field)
            current_state[state_key] = fingerprint
            prior_fingerprint = prior_state.get(state_key)
            if prior_fingerprint == fingerprint:
                suppressed_resting_duplicates += 1
                continue
            new_events, audit = built
            audit["opportunity_fingerprint"] = fingerprint
            audit["episode_event"] = "OPEN" if prior_fingerprint is None else "MOVE"
            events.extend(new_events)
            audits.append(audit)
    return events, audits, current_state, suppressed_resting_duplicates


async def run(ledger_path: Path, audit_path: Path, state_path: Path, min_profit: Decimal) -> dict[str, Any]:
    prior_state = _load_active_state(state_path)
    snapshot = await run_once()
    events, audits, current_state, suppressed = build_paper_probes_with_state(
        snapshot, prior_state=prior_state, min_profit=min_profit
    )
    ledger = LedgerSidecar(ledger_path)
    for event in events:
        ledger.emit(event)
    ledger.drain_once(max_events=max(1, len(events)))
    if audits:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as fh:
            for row in audits:
                fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    _write_active_state(state_path, current_state)
    result = {
        "paper_probe_groups": len(audits),
        "ledger_events": len(events),
        "minimum_modeled_profit_dollars": str(min_profit),
        "participation_cleared_only": True,
        "synchronized_books_only": True,
        "active_paper_opportunities": len(current_state),
        "suppressed_resting_duplicates": suppressed,
        "episode_semantics": "OPEN_MOVE_REOPEN_NO_RESTING_DEPTH_DOUBLE_COUNT",
        "rules_status": "RULES_PENDING",
        "full_rules_audited": False,
        "fully_audited_shadow_ready_groups": 0,
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
    parser.add_argument("--state", type=Path, default=Path("logs/mlb_prop_paper_probe_state.json"))
    parser.add_argument("--min-profit", type=Decimal, default=D("0.03"))
    args = parser.parse_args()
    asyncio.run(run(args.ledger, args.audit, args.state, args.min_profit))


if __name__ == "__main__":
    main()
