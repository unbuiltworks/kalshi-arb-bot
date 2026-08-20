"""One-shot ledger reconciliation for a persisted confirmed flatten incident.

Reads the existing live result JSON from the canary volume, verifies that it is a
known-flat ONE_LEG_FILLED incident, then emits the missing emergency-flatten FILL and
TRADE_REALIZED events using deterministic event ids. No venue orders are submitted.
"""
from __future__ import annotations

import argparse
import asyncio
import json

import httpx

from src import gemini_kalshi_incident_accounting as incident
from src import gemini_kalshi_sol_live_canary_v2 as v2


def load_confirmed_flatten_record(run_id: str) -> tuple[str, dict, object]:
    clean = v2._clean_run_id(run_id)
    _arm, _claim, result_path = v2._paths(clean)
    if not result_path.is_file():
        raise FileNotFoundError(f"persistent result not found for {clean}: {result_path}")
    record = json.loads(result_path.read_text(encoding="utf-8"))
    pnl = incident.confirmed_flattened_one_leg_pnl(record)
    if pnl is None:
        raise RuntimeError(f"run {clean} is not a confirmed fully-flattened one-leg incident")
    return clean, record, pnl


async def run(run_id: str) -> None:
    clean, record, pnl = load_confirmed_flatten_record(run_id)
    async with httpx.AsyncClient(timeout=20.0) as http:
        await incident._record_confirmed_flatten(http, clean, record, pnl)
    print(json.dumps({
        "run_id": clean,
        "status": "LEDGER_RECONCILED_CONFIRMED_FLATTEN",
        "realized_net_pnl_usd": str(pnl),
        "order_entry": False,
    }, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.run_id))


if __name__ == "__main__":
    main()
