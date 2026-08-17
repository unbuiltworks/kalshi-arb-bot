"""Atomic disk persistence for the nested auto risk governor."""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any

from src.kalshi_nested_auto_governor import AutoRiskGovernor, GovernorLimits


def save_governor_state(path: Path, governor: AutoRiskGovernor) -> None:
    """Atomically persist full governor state on the same filesystem."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    payload = json.dumps(governor.state(), separators=(",", ":"), sort_keys=True) + "\n"
    try:
        with temp.open("w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, target)
        # Best-effort directory fsync so a crash immediately after replace does
        # not silently lose the rename on filesystems that support it.
        try:
            fd = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
    finally:
        temp.unlink(missing_ok=True)


def load_governor_state(
    path: Path,
    *,
    limits: GovernorLimits,
    now: datetime,
) -> tuple[AutoRiskGovernor, str]:
    """Load governor state; malformed state raises instead of resetting limits."""
    target = Path(path)
    if not target.exists():
        governor = AutoRiskGovernor(limits)
        # Initialize the ET day immediately so a persisted empty state is explicit.
        governor._roll_day_if_needed(now)
        return governor, "NEW_STATE"
    with target.open("r", encoding="utf-8") as fh:
        raw: Any = json.load(fh)
    governor = AutoRiskGovernor.from_state(raw, limits=limits, now=now)
    return governor, "RESTORED_STATE"
