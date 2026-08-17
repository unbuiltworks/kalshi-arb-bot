"""Collect BOTH public venue outcomes for synthetic MLB paper probes.

Research-only. Reads the append-only paper ledger, queries only public market-data
settlement endpoints, and appends a complete outcome row only after BOTH venues have
resolved. It never infers equivalence from one venue, never places/cancels orders,
and has no credentials or live-arm path.
"""
from __future__ import annotations

import argparse
from dataclasses import fields
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from src.cross_venue_ledger import LedgerEvent

D = Decimal
POLY_BASE = "https://gateway.polymarket.us"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
VENUES = ("polymarket_us", "kalshi")
VALID_OUTCOMES = {"YES", "NO"}


def _event_from_row(row: dict[str, Any]) -> LedgerEvent:
    data = dict(row)
    for key in ("quantity", "price", "fee", "payout"):
        data[key] = D(str(data.get(key, "0")))
    allowed = {field.name for field in fields(LedgerEvent)}
    return LedgerEvent(**{key: value for key, value in data.items() if key in allowed})


def load_ledger(path: Path) -> list[LedgerEvent]:
    if not path.exists():
        return []
    rows: list[LedgerEvent] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(_event_from_row(json.loads(line)))
    return rows


def load_existing_group_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            group_id = str(row.get("trade_group_id") or "")
            if group_id:
                out.add(group_id)
    return out


def pending_probe_markets(events: list[LedgerEvent], completed_groups: set[str]) -> dict[str, dict[str, LedgerEvent]]:
    """Return trade groups that have exactly one fill on each required venue."""
    grouped: dict[str, dict[str, LedgerEvent]] = {}
    settled: set[tuple[str, str]] = set()
    for event in events:
        if event.event_type == "settlement":
            settled.add((event.trade_group_id, event.venue))
        elif event.event_type == "fill" and event.venue in VENUES and event.trade_group_id not in completed_groups:
            grouped.setdefault(event.trade_group_id, {})[event.venue] = event
    out: dict[str, dict[str, LedgerEvent]] = {}
    for group_id, fills in grouped.items():
        if set(fills) != set(VENUES):
            continue
        if any((group_id, venue) in settled for venue in VENUES):
            # Outcome collection feeds the two-venue settlement reconciler. If the
            # ledger is already partially settled, fail closed rather than mixing
            # provenance from separate collection paths.
            continue
        out[group_id] = fills
    return out


def _public_json(url: str, timeout: float = 5.0) -> dict[str, Any] | None:
    try:
        req = Request(url, headers={"User-Agent": "kalshi-arb-bot-readonly-research/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def parse_polymarket_settlement(payload: dict[str, Any] | None) -> str | None:
    """Map documented 1.00/0.00 settlement prices to YES/NO; fail closed otherwise."""
    if not payload or "settlement" not in payload:
        return None
    try:
        value = D(str(payload["settlement"]))
    except Exception:
        return None
    if value == D("1"):
        return "YES"
    if value == D("0"):
        return "NO"
    return None


def parse_kalshi_market(payload: dict[str, Any] | None) -> tuple[str | None, int | None]:
    """Use public market result only when Kalshi reports the market as settled."""
    market = (payload or {}).get("market")
    if not isinstance(market, dict):
        return None, None
    status = str(market.get("status") or "").lower()
    result = str(market.get("result") or "").upper()
    if status != "settled" or result not in VALID_OUTCOMES:
        return None, None
    settlement_ts = market.get("settlement_ts")
    # Keep the source timestamp as text in audit rows; the downstream reconciler
    # accepts an optional millisecond timestamp, so omit when conversion is unsafe.
    return result, settlement_ts if isinstance(settlement_ts, int) else None


def collect_complete_outcomes(
    events: list[LedgerEvent],
    completed_groups: set[str],
    *,
    get_json: Callable[[str], dict[str, Any] | None] = _public_json,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    pending = pending_probe_markets(events, completed_groups)
    rows: list[dict[str, Any]] = []
    stats = {"pending_trade_groups": len(pending), "both_settled": 0, "one_or_both_unsettled": 0, "venue_disagreements": 0}
    for group_id, fills in sorted(pending.items()):
        poly_slug = fills["polymarket_us"].market_id
        kalshi_ticker = fills["kalshi"].market_id
        poly_url = f"{POLY_BASE}/v1/markets/{quote(poly_slug, safe='')}/settlement"
        kalshi_url = f"{KALSHI_BASE}/markets/{quote(kalshi_ticker, safe='')}"
        poly_outcome = parse_polymarket_settlement(get_json(poly_url))
        kalshi_outcome, _kalshi_ts = parse_kalshi_market(get_json(kalshi_url))
        if poly_outcome is None or kalshi_outcome is None:
            stats["one_or_both_unsettled"] += 1
            continue
        row = {
            "trade_group_id": group_id,
            "polymarket_us_outcome": poly_outcome,
            "kalshi_outcome": kalshi_outcome,
            "polymarket_us_market_id": poly_slug,
            "kalshi_market_id": kalshi_ticker,
            "outcomes_agree": poly_outcome == kalshi_outcome,
            "source": "PUBLIC_VENUE_SETTLEMENT_ENDPOINTS",
            "read_only": True,
            "order_entry": False,
        }
        rows.append(row)
        stats["both_settled"] += 1
        if not row["outcomes_agree"]:
            stats["venue_disagreements"] += 1
    return rows, stats


def append_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def run(ledger_path: Path, outcomes_path: Path) -> dict[str, Any]:
    events = load_ledger(ledger_path)
    completed = load_existing_group_ids(outcomes_path)
    rows, stats = collect_complete_outcomes(events, completed)
    append_rows(outcomes_path, rows)
    result = {
        **stats,
        "new_complete_outcome_rows": len(rows),
        "outcomes_path": str(outcomes_path),
        "settlement_policy": "WRITE_ONLY_AFTER_BOTH_PUBLIC_VENUES_RESOLVE",
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
    }
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=Path("logs/mlb_prop_paper_ledger.jsonl"))
    parser.add_argument("--outcomes", type=Path, default=Path("logs/mlb_prop_paper_outcomes.jsonl"))
    args = parser.parse_args()
    run(args.ledger, args.outcomes)


if __name__ == "__main__":
    main()
