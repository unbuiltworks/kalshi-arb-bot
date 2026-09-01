"""Crash/startup recovery for Gemini q10 V5.

The recovery layer is deliberately stricter than the normal executor:
- audit is read-only by default;
- mutation requires the normal live dual-key arm *and* an independent recovery arm;
- only the exact durable V5 campaign client IDs may be cancelled;
- ambiguous/multi-price residuals are never guessed;
- if an exact one-sided residual can be proven, reuse V5's bounded single-venue FOK
  rescue; otherwise leave the campaign blocked for manual review;
- recovery never starts a fresh campaign in the same process.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal as D
import os
from typing import Any, Mapping

from src import gemini_q10_production_executor_v5 as v5
from src.gemini_prediction_book_cache import GeminiTopCache

ZERO = D("0")
RECOVERY_ARM_ENV = "GEMINI_Q10_V5_RECOVERY_ARM"
TERMINAL = {
    "RECOVERED_FLAT", "RECOVERY_UNWIND_FLAT", "SETTLED", "COMPLETE", "CLOSED",
}


def recovery_armed() -> bool:
    aid = os.getenv(v5.v1.ARM_ID_ENV, "").strip()
    return bool(v5.live_armed() and aid and os.getenv(RECOVERY_ARM_ENV, "").strip() == aid)


def _first(state: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = state.get(key)
        if value not in (None, ""):
            return value
    return None


def _state_contract(state: Mapping[str, Any]) -> dict[str, Any]:
    symbol = str(_first(state, "symbol", "market") or "").strip()
    trade_id = str(_first(state, "trade_id", "run_id", "campaign_id") or "").strip()
    yes_id = str(_first(state, "yes_client_order_id", "yes_id", "yes_client_id") or "").strip()
    no_id = str(_first(state, "no_client_order_id", "no_id", "no_client_id") or "").strip()
    yes_quote = _first(state, "yes_quote", "yes_price")
    no_quote = _first(state, "no_quote", "no_price")
    increment = D(str(_first(state, "price_increment", "increment") or "0.01"))
    status = str(state.get("status") or "UNKNOWN").upper()
    return {
        "symbol": symbol, "trade_id": trade_id, "yes_id": yes_id, "no_id": no_id,
        "yes_quote": None if yes_quote in (None, "") else D(str(yes_quote)),
        "no_quote": None if no_quote in (None, "") else D(str(no_quote)),
        "increment": increment, "status": status,
    }


def _is_open_state(state: Mapping[str, Any] | None) -> bool:
    if not state:
        return False
    if state.get("open") is False:
        return False
    return str(state.get("status") or "").upper() not in TERMINAL


def _row_open(row: Mapping[str, Any] | None) -> bool:
    if not row:
        return False
    status = str(row.get("status") or row.get("orderStatus") or row.get("X") or "").upper()
    if status in {"FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "DONE"}:
        return False
    return True


def _fill_price(row: Mapping[str, Any] | None, fallback: D | None) -> D | None:
    if row:
        for key in ("avgExecutionPrice", "averageExecutionPrice", "price"):
            value = row.get(key)
            if value not in (None, ""):
                try:
                    p = D(str(value))
                except Exception:
                    continue
                if ZERO < p < D("1"):
                    return p
    return fallback


@dataclass(frozen=True)
class RecoveryAudit:
    status: str
    open_state: bool
    symbol: str | None
    yes_fill: D
    no_fill: D
    residual_outcome: str | None
    residual_quantity: D
    exact_residual: bool
    active_known_orders: int
    account_positions: int
    account_orders: int
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "open_state": self.open_state,
            "symbol": self.symbol,
            "yes_fill": str(self.yes_fill),
            "no_fill": str(self.no_fill),
            "residual_outcome": self.residual_outcome,
            "residual_quantity": str(self.residual_quantity),
            "exact_residual": self.exact_residual,
            "active_known_orders": self.active_known_orders,
            "account_positions": self.account_positions,
            "account_orders": self.account_orders,
            "reason": self.reason,
        }


async def audit() -> tuple[RecoveryAudit, dict[str, Any] | None, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    state = v5.v1._read_state()
    snap = await v5.v1._flat_snapshot()
    if not _is_open_state(state):
        return RecoveryAudit(
            status="CLEAN_NO_OPEN_STATE", open_state=False, symbol=None,
            yes_fill=ZERO, no_fill=ZERO, residual_outcome=None, residual_quantity=ZERO,
            exact_residual=True, active_known_orders=0,
            account_positions=int(snap["positions"]), account_orders=int(snap["orders"]),
        ), state, None, None

    assert state is not None
    contract = _state_contract(state)
    if not contract["symbol"] or not contract["trade_id"] or not contract["yes_id"] or not contract["no_id"]:
        return RecoveryAudit(
            status="UNRESOLVED_STATE_CONTRACT_INCOMPLETE", open_state=True,
            symbol=contract["symbol"] or None, yes_fill=ZERO, no_fill=ZERO,
            residual_outcome=None, residual_quantity=ZERO, exact_residual=False,
            active_known_orders=0, account_positions=int(snap["positions"]),
            account_orders=int(snap["orders"]), reason="missing durable symbol/trade/order ids",
        ), state, None, None

    yrow, nrow, yf, nf = await v5.v1.probe._reconcile(
        contract["yes_id"], contract["no_id"], contract["symbol"],
    )
    active = int(_row_open(yrow)) + int(_row_open(nrow))
    residual = abs(yf - nf)
    outcome = "YES" if yf > nf else "NO" if nf > yf else None

    # Exact auto-recovery is allowed for a pure one-sided fill. If both sides have
    # non-zero unequal fills, the marginal unmatched cost basis may not be provable
    # from a single average price; leave that case blocked rather than guess.
    exact = bool(
        residual == ZERO
        or ((yf > ZERO and nf == ZERO) or (nf > ZERO and yf == ZERO))
    )
    status = "OPEN_STATE_FLAT_FILLS" if residual == ZERO else "OPEN_STATE_EXACT_RESIDUAL" if exact else "OPEN_STATE_AMBIGUOUS_RESIDUAL"
    return RecoveryAudit(
        status=status, open_state=True, symbol=contract["symbol"], yes_fill=yf, no_fill=nf,
        residual_outcome=outcome, residual_quantity=residual, exact_residual=exact,
        active_known_orders=active, account_positions=int(snap["positions"]),
        account_orders=int(snap["orders"]),
    ), state, yrow, nrow


async def recover() -> dict[str, Any]:
    report, state, yrow, nrow = await audit()
    out = {"classification":"V5_STARTUP_RECOVERY", "audit":report.as_dict(), "order_entry":False}
    if not report.open_state:
        out["status"] = "NOTHING_TO_RECOVER"
        return out
    if not recovery_armed():
        out["status"] = "RECOVERY_REQUIRED_BUT_NOT_ARMED"
        return out
    assert state is not None
    contract = _state_contract(state)
    if not report.exact_residual:
        state.update(status="RECOVERY_HARD_HALT_AMBIGUOUS_RESIDUAL", recovery_audit=report.as_dict(), open=True)
        v5.v1._write_state(state)
        out["status"] = state["status"]
        return out

    maker = v5.v1.base.GeminiMakerSession()
    cache: GeminiTopCache | None = None
    cache_stop: asyncio.Event | None = None
    cache_task: asyncio.Task | None = None
    try:
        await maker.connect()
        # Cancel only exact durable campaign orders. Never broad-cancel unrelated orders.
        cancels = []
        if _row_open(yrow):
            cancels.append(await v5.v1.base._cancel_maker(maker, row=yrow))
        if _row_open(nrow):
            cancels.append(await v5.v1.base._cancel_maker(maker, row=nrow))
        if cancels:
            await asyncio.sleep(0.12)
        yrow2, nrow2, yf, nf = await v5.v1.probe._reconcile(
            contract["yes_id"], contract["no_id"], contract["symbol"],
        )
        residual = abs(yf - nf)
        outcome = "YES" if yf > nf else "NO" if nf > yf else None
        state.update(recovery_cancels=cancels, recovery_yes_fill=str(yf), recovery_no_fill=str(nf))
        v5.v1._write_state(state)

        if residual == ZERO:
            account = await v5.v1._flat_snapshot()
            if int(account["orders"]) == 0 and int(account["positions"]) == 0:
                state.update(status="RECOVERED_FLAT", open=False, recovery_account=account)
                v5.v1._write_state(state)
                return {**out, "status":"RECOVERED_FLAT", "order_entry":True, "account":account}
            state.update(status="RECOVERY_HARD_HALT_POSITION_MISMATCH", open=True, recovery_account=account)
            v5.v1._write_state(state)
            return {**out, "status":state["status"], "order_entry":True, "account":account}

        # Only a pure one-sided residual is automatically rescued.
        if not ((yf > ZERO and nf == ZERO) or (nf > ZERO and yf == ZERO)) or outcome is None or residual > v5.Q:
            state.update(status="RECOVERY_HARD_HALT_RESIDUAL_CHANGED", open=True)
            v5.v1._write_state(state)
            return {**out, "status":state["status"], "order_entry":True}

        row = yrow2 if outcome == "YES" else nrow2
        fallback = contract["yes_quote"] if outcome == "YES" else contract["no_quote"]
        filled_price = _fill_price(row, fallback)
        if filled_price is None:
            state.update(status="RECOVERY_HARD_HALT_NO_FILL_PRICE", open=True)
            v5.v1._write_state(state)
            return {**out, "status":state["status"], "order_entry":True}

        cache = GeminiTopCache(contract["symbol"], stale_ms=750)
        cache_stop = asyncio.Event()
        cache_task = asyncio.create_task(cache.run(cache_stop))
        await cache.wait_fresh(timeout=3.0, max_age_ms=v5.BOOK_MAX_AGE_MS)
        result = await v5._bounded_fok(
            maker, cache=cache, symbol=contract["symbol"], filled_outcome=outcome,
            filled_price=filled_price, quantity=residual, increment=contract["increment"],
            trade_id=f"{contract['trade_id']}-recovery", state=state,
        )
        state.update(recovery_result=result)
        if result.get("flattened"):
            action = str((result.get("route") or {}).get("action") or "")
            account = await v5.v1._flat_snapshot()
            if action == "UNWIND" and int(account["orders"]) == 0 and int(account["positions"]) == 0:
                state.update(status="RECOVERY_UNWIND_FLAT", open=False, recovery_account=account)
            elif action == "COMPLETE":
                state.update(status="RECOVERY_PAIRED_NOT_SETTLED", open=True, recovery_account=account)
            else:
                state.update(status="RECOVERY_HARD_HALT_POST_RESCUE_MISMATCH", open=True, recovery_account=account)
        else:
            state.update(status="RECOVERY_HARD_HALT_UNFLATTENED", open=True)
        v5.v1._write_state(state)
        return {**out, "status":state["status"], "order_entry":True, "rescue":result}
    finally:
        if cache_stop is not None:
            cache_stop.set()
        if cache_task is not None:
            cache_task.cancel()
            await asyncio.gather(cache_task, return_exceptions=True)
        await maker.close()


if __name__ == "__main__":
    result = asyncio.run(recover())
    print("GEMINI_Q10_V5_RECOVERY=" + __import__("json").dumps(result, separators=(",", ":"), default=str))
