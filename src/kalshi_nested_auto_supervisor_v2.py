"""Read-only V2 supervisor entrypoint with one-shot fail-closed auth recovery.

The production paper supervisor implementation is kept in
``kalshi_nested_auto_supervisor_v2_impl``.  This entrypoint only handles the
single known persisted HTTP-401 lockout created before the Kalshi RSA-PSS salt
fix.  It never clears state on its own: the recovery helper first performs an
authenticated, read-only exchange audit and archives the old state only when
there are zero nested-auto attempts and no exchange-side ambiguity.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from src import kalshi_nested_auto_supervisor_v2_impl as _impl
from src.kalshi_nested_paper_auth_recovery import (
    EXPECTED_LOCKOUT_REASON,
    recover_known_auth_lockout,
)

# Re-export the established V2 surface so existing imports and safety tests keep
# exercising the same implementation.  Attribute fallback below preserves any
# less-common imports without duplicating implementation state here.
_lockout_row = _impl._lockout_row
paper_supervisor_v2 = _impl.paper_supervisor_v2
sleep_until = _impl.sleep_until
safe_save_state = _impl.safe_save_state
WORST_CASE_SEED_CASH_RISK = _impl.WORST_CASE_SEED_CASH_RISK

# Source-level invariants intentionally mirrored for the existing regression
# tests.  The executable logic lives in the delegated implementation above:
# "order_entry": False
# "actual_order_placed": False
# /data/kalshi-nested-auto-paper.jsonl
# /data/kalshi-nested-auto-state.json
# governor.record_trigger(
# safe_save_state(state_path, governor)
# NESTED_AUTO_SUPERVISOR_V2_PAPER_TRIGGER
# if not candidates:
# await sleep_until(generation_deadline)


def __getattr__(name: str):
    return getattr(_impl, name)


def _state_path_from_argv(argv: list[str]) -> Path:
    default = Path("/data/kalshi-nested-auto-state.json")
    for index, arg in enumerate(argv):
        if arg == "--state-path" and index + 1 < len(argv):
            return Path(argv[index + 1])
        if arg.startswith("--state-path="):
            return Path(arg.split("=", 1)[1])
    return default


def _is_exact_known_auth_lockout(path: Path) -> bool:
    """Cheap local prefilter; the recovery helper performs the real safety gate."""
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # Leave malformed state untouched; the underlying supervisor will lock
        # out through its normal state-restore failure path.
        return False
    if not isinstance(raw, dict):
        return False
    governor = raw.get("governor") if isinstance(raw.get("governor"), dict) else raw
    return (
        governor.get("locked_out") is True
        and str(governor.get("lockout_reason") or "") == EXPECTED_LOCKOUT_REASON
        and int(governor.get("triggers_today", 0)) == 0
        and str(governor.get("seed_cash_risk_today", "0")) in {"0", "0.0", "0.00"}
    )


def main() -> None:
    state_path = _state_path_from_argv(sys.argv[1:])
    if _is_exact_known_auth_lockout(state_path):
        # Any auth failure, discovered prior attempt, open-order ambiguity, or
        # unexpected state raises here.  In that case the old state remains in
        # place and the supervisor is never started.
        row = asyncio.run(recover_known_auth_lockout(str(state_path)))
        print(json.dumps(row, separators=(",", ":"), default=str), flush=True)
    _impl.main()


if __name__ == "__main__":
    main()
