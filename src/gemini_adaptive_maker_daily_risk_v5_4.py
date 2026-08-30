"""Persistent UTC-day loss ledger for Gemini adaptive maker V5.4.

V5's legacy daily stop measured drawdown from the start of each process. A new
approval/state directory therefore reset the baseline, which is not a true daily
loss stop. V5.4 stores one account-level day baseline outside per-approval campaign
state and fails closed for live execution when that ledger is missing or malformed.

This module performs no venue calls and no order entry. Callers supply the reconciled
available USD from a flat account preflight.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import os
from pathlib import Path
import tempfile
from typing import Any

ZERO = D("0")
RISK_DIR_ENV = "GEMINI_ADAPTIVE_V5_4_RISK_STATE_DIR"
RISK_FILE = "daily-risk.json"


@dataclass(frozen=True)
class DailyRiskDecision:
    status: str
    date_utc: str
    baseline_available_usd: D | None
    current_available_usd: D
    drawdown_usd: D | None
    loss_limit_usd: D
    blocked: bool
    ledger_path: str
    rolled_over: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "date_utc": self.date_utc,
            "baseline_available_usd": None if self.baseline_available_usd is None else str(self.baseline_available_usd),
            "current_available_usd": str(self.current_available_usd),
            "drawdown_usd": None if self.drawdown_usd is None else str(self.drawdown_usd),
            "loss_limit_usd": str(self.loss_limit_usd),
            "blocked": self.blocked,
            "ledger_path": self.ledger_path,
            "rolled_over": self.rolled_over,
        }


def _today_utc(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).date().isoformat()


def risk_path() -> Path:
    raw = os.getenv(RISK_DIR_ENV, "").strip()
    if not raw:
        raise RuntimeError(f"{RISK_DIR_ENV} is required")
    return Path(raw) / RISK_FILE


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd, tmp = tempfile.mkstemp(prefix=".daily-risk-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def load() -> dict[str, Any] | None:
    path = risk_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"invalid V5.4 daily risk ledger: {type(exc).__name__}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("invalid V5.4 daily risk ledger: root is not object")
    for key in ("date_utc", "baseline_available_usd"):
        if raw.get(key) in (None, ""):
            raise RuntimeError(f"invalid V5.4 daily risk ledger: missing {key}")
    try:
        baseline = D(str(raw["baseline_available_usd"]))
    except Exception as exc:
        raise RuntimeError("invalid V5.4 daily risk ledger: bad baseline") from exc
    if baseline < ZERO:
        raise RuntimeError("invalid V5.4 daily risk ledger: negative baseline")
    return raw


def seed(*, date_utc: str, baseline_available_usd: D, current_available_usd: D,
         source: str, overwrite: bool = False) -> dict[str, Any]:
    """Explicit administrative seed; never called implicitly by live execution."""
    path = risk_path()
    if path.exists() and not overwrite:
        raise RuntimeError("V5.4 daily risk ledger already exists")
    baseline = D(str(baseline_available_usd))
    current = D(str(current_available_usd))
    if baseline < ZERO or current < ZERO:
        raise ValueError("available USD values must be non-negative")
    drawdown = max(ZERO, baseline - current)
    data = {
        "version": 1,
        "date_utc": str(date_utc),
        "baseline_available_usd": str(baseline),
        "last_available_usd": str(current),
        "max_observed_drawdown_usd": str(drawdown),
        "source": str(source),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write(path, data)
    return data


def check(*, current_available_usd: D, loss_limit_usd: D, account_flat: bool,
          allow_rollover: bool, now: datetime | None = None, persist_observation: bool = True) -> DailyRiskDecision:
    """Check global day drawdown; optionally roll baseline only on a new UTC day.

    Same-day baseline is immutable. A process restart or new approval cannot reset it.
    New-day rollover requires a reconciled flat account and an existing valid ledger.
    """
    current = D(str(current_available_usd))
    limit = D(str(loss_limit_usd))
    if current < ZERO or limit <= ZERO:
        raise ValueError("invalid daily risk inputs")
    today = _today_utc(now)
    path = risk_path()
    ledger = load()
    if ledger is None:
        return DailyRiskDecision(
            status="RISK_LEDGER_MISSING",
            date_utc=today,
            baseline_available_usd=None,
            current_available_usd=current,
            drawdown_usd=None,
            loss_limit_usd=limit,
            blocked=True,
            ledger_path=str(path),
        )

    ledger_day = str(ledger["date_utc"])
    baseline = D(str(ledger["baseline_available_usd"]))
    rolled = False
    if ledger_day != today:
        if not allow_rollover or not account_flat:
            return DailyRiskDecision(
                status="RISK_LEDGER_ROLLOVER_REQUIRED",
                date_utc=today,
                baseline_available_usd=baseline,
                current_available_usd=current,
                drawdown_usd=max(ZERO, baseline - current),
                loss_limit_usd=limit,
                blocked=True,
                ledger_path=str(path),
            )
        baseline = current
        ledger_day = today
        ledger = {
            "version": 1,
            "date_utc": today,
            "baseline_available_usd": str(current),
            "last_available_usd": str(current),
            "max_observed_drawdown_usd": "0",
            "source": "UTC_DAY_ROLLOVER_FLAT_ACCOUNT",
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_write(path, ledger)
        rolled = True

    drawdown = max(ZERO, baseline - current)
    prior_max = D(str(ledger.get("max_observed_drawdown_usd") or "0"))
    observed_max = max(prior_max, drawdown)
    if persist_observation:
        ledger["last_available_usd"] = str(current)
        ledger["max_observed_drawdown_usd"] = str(observed_max)
        ledger["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(path, ledger)

    blocked = drawdown >= limit
    return DailyRiskDecision(
        status="DAILY_LOSS_STOP_GLOBAL" if blocked else "DAILY_RISK_OK",
        date_utc=ledger_day,
        baseline_available_usd=baseline,
        current_available_usd=current,
        drawdown_usd=drawdown,
        loss_limit_usd=limit,
        blocked=blocked,
        ledger_path=str(path),
        rolled_over=rolled,
    )
