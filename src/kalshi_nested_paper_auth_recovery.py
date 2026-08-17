"""One-shot, read-only recovery gate for a known paper-supervisor auth lockout.

This utility exists only to clear the specific persisted HTTP-401 lockout caused
by the old RSA-PSS signing parameter. It first performs the exchange-backed
read-only recovery audit and refuses to touch local state unless the audit shows
zero nested-auto attempts and no exchange-side ambiguity.

It never creates, cancels, or modifies an exchange order.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path

from src.kalshi_nested_exchange_recovery_audit import audit

EXPECTED_LOCKOUT_REASON = "InvalidStatus:server rejected WebSocket connection: HTTP 401"
logger = logging.getLogger("kalshi_arb.kalshi_nested_paper_auth_recovery")


def _load_locked_state(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("persisted supervisor state is not a JSON object")
    governor = raw.get("governor") if isinstance(raw.get("governor"), dict) else raw
    if governor.get("locked_out") is not True:
        raise RuntimeError("refusing recovery: persisted state is not locked out")
    reason = str(governor.get("lockout_reason") or "")
    if reason != EXPECTED_LOCKOUT_REASON:
        raise RuntimeError(f"refusing recovery: unexpected lockout reason {reason!r}")
    if int(governor.get("triggers_today", 0)) != 0:
        raise RuntimeError("refusing recovery: persisted state records paper triggers")
    if str(governor.get("seed_cash_risk_today", "0")) not in {"0", "0.0", "0.00"}:
        raise RuntimeError("refusing recovery: persisted state records seed risk")
    return raw


async def recover_known_auth_lockout(state_path: str) -> dict:
    path = Path(state_path)
    if not path.exists():
        raise RuntimeError("refusing recovery: expected persisted lockout state is missing")

    _load_locked_state(path)

    exchange = await audit()
    if exchange.get("safe_to_scan") is not True:
        raise RuntimeError("refusing recovery: exchange audit is not safe to scan")
    if int(exchange.get("attempt_count", -1)) != 0:
        raise RuntimeError("refusing recovery: exchange audit found nested-auto attempts")
    if int(exchange.get("seed_only_exposure_count", -1)) != 0:
        raise RuntimeError("refusing recovery: exchange audit found seed-only exposure")
    if list(exchange.get("lockout_reasons") or []):
        raise RuntimeError("refusing recovery: exchange audit returned lockout reasons")
    recovered_governor = exchange.get("governor") or {}
    if recovered_governor.get("locked_out") is True:
        raise RuntimeError("refusing recovery: recovered governor is locked out")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.pre-auth-recovery-{stamp}")
    os.replace(path, backup)
    try:
        os.sync()
    except AttributeError:
        pass

    row = {
        "status": "PAPER_AUTH_LOCKOUT_RECOVERED",
        "state_path": str(path),
        "backup_path": str(backup),
        "prior_lockout_reason": EXPECTED_LOCKOUT_REASON,
        "exchange_attempt_count": 0,
        "exchange_safe_to_scan": True,
        "actual_order_placed": False,
        "read_only": True,
        "order_entry": False,
    }
    logger.info("NESTED_PAPER_AUTH_RECOVERY %s", json.dumps(row, separators=(",", ":")))
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-path", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    row = asyncio.run(recover_known_auth_lockout(args.state_path))
    print(json.dumps(row, separators=(",", ":")))


if __name__ == "__main__":
    main()
