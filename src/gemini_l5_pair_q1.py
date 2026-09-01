"""Adaptive single-pair Gemini ETH5 maker primitive for continuous operation.

Only one q1 YES order and one q1 NO order may be active for this cycle.  The
quote chooses the shallowest symmetric backoff (0c, 1c, 2c) that preserves the
configured fee-adjusted complete-pair edge.  Therefore a same-side cancellation
race cannot create more than one new directional contract in this cycle.

This module is one-cycle only.  The dynamic owner decides whether another cycle
is allowed after authoritative reconciliation.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping

from src import gemini_l5_ladder_q1 as ladder
from src.gemini_incentive_mm_live_probe_q1 import discover_eth5m
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_kalshi_live_service import _gemini_available_usd, _gemini_terms_ok

ONE = D("1")
ZERO = D("0")
TICK = D("0.01")
BACKOFFS = (D("0"), D("0.01"), D("0.02"))
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")
DEFAULT_MIN_PAIR_EDGE = D("0.01")
DEFAULT_FIRST_WAIT = 75.0
DEFAULT_COMPLEMENT_WAIT = 60.0
DEFAULT_MIN_TTE = 105.0

LIVE_ENV = "GEMINI_L5_PAIR_LIVE_Q1"
SID_ENV = "GEMINI_L5_PAIR_SESSION_ID"
ARM_ENV = "GEMINI_L5_PAIR_ARM"
STATE_ENV = "GEMINI_L5_PAIR_STATE_FILE"


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_L5_PAIR_Q1 "
        + json.dumps(
            {"stage": stage, "observed_at_utc": datetime.now(timezone.utc).isoformat(), **fields},
            separators=(",", ":"),
            default=str,
        ),
        flush=True,
    )


def armed() -> bool:
    sid = os.getenv(SID_ENV, "").strip()
    return (
        os.getenv(LIVE_ENV, "").strip().upper() == "YES"
        and bool(sid)
        and os.getenv(ARM_ENV, "").strip() == sid
    )


def _write_state(payload: Mapping[str, Any]) -> None:
    path = Path(os.getenv(STATE_ENV, "/state/gemini-l5-pair-q1.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as handle:
        json.dump(dict(payload), handle, sort_keys=True, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def pair_edge(yes: D, no: D) -> D:
    return (
        ONE
        - yes
        - no
        - gemini_predictions_maker_fee(yes, ONE)
        - gemini_predictions_maker_fee(no, ONE)
    )


def choose_pair(bid: D, ask: D, *, min_edge: D = DEFAULT_MIN_PAIR_EDGE) -> dict[str, D] | None:
    """Choose the most aggressive safe pair from 0/1/2c symmetric backoff."""
    bid = D(str(bid))
    ask = D(str(ask))
    for backoff in BACKOFFS:
        yes = (bid - backoff).quantize(TICK)
        no = (ONE - ask - backoff).quantize(TICK)
        if not (MIN_PRICE <= yes <= MAX_PRICE and MIN_PRICE <= no <= MAX_PRICE):
            continue
        edge = pair_edge(yes, no)
        if edge >= D(str(min_edge)):
            return {"yes": yes, "no": no, "edge": edge, "backoff": backoff}
    return None


def summarize(specs: list[dict[str, Any]], fills: Mapping[str, D]) -> dict[str, Any]:
    yes_qty = no_qty = yes_cost = no_cost = fees = ZERO
    detail: list[dict[str, str]] = []
    for spec in specs:
        qty = D(str(fills.get(spec["id"], ZERO)))
        if qty <= ZERO:
            continue
        fee = gemini_predictions_maker_fee(spec["price"], qty)
        cost = spec["price"] * qty
        fees += fee
        if spec["side"] == "YES":
            yes_qty += qty
            yes_cost += cost
        else:
            no_qty += qty
            no_cost += cost
        detail.append(
            {
                "side": spec["side"],
                "price": str(spec["price"]),
                "qty": str(qty),
                "fee": str(fee),
            }
        )
    return {
        "fills": detail,
        "yes_qty": str(yes_qty),
        "no_qty": str(no_qty),
        "matched_qty": str(min(yes_qty, no_qty)),
        "residual_qty": str(abs(yes_qty - no_qty)),
        "maker_turnover": str(yes_cost + no_cost),
        "maker_fees": str(fees),
    }


async def run(
    *,
    min_edge: D = DEFAULT_MIN_PAIR_EDGE,
    first_wait: float = DEFAULT_FIRST_WAIT,
    complement_wait: float = DEFAULT_COMPLEMENT_WAIT,
    min_tte: float = DEFAULT_MIN_TTE,
) -> None:
    if not armed():
        emit("DISARMED", q_per_side=1, max_new_residual_qty=1, order_entry=False)
        return
    if not await _gemini_terms_ok():
        raise RuntimeError("terms not accepted")
    if await _gemini_available_usd() < D("4"):
        raise RuntimeError("insufficient bounded pair balance")

    event = await discover_eth5m()
    if event is None or event.seconds_to_expiry < float(min_tte):
        emit("STOP", reason="NO_ETH5_WITH_ENOUGH_TTE", order_entry=False, realized_pnl=None)
        return

    bid, bid_size, ask, ask_size = await ladder.fresh_top_retry(event.symbol)
    model = choose_pair(bid, ask, min_edge=D(str(min_edge)))
    if model is None:
        emit(
            "STOP",
            reason="NO_SAFE_PAIR_WITHIN_2C_BACKOFF",
            symbol=event.symbol,
            event_ticker=event.ticker,
            bid=str(bid),
            ask=str(ask),
            min_pair_edge=str(min_edge),
            order_entry=False,
            realized_pnl=None,
        )
        return

    sid = os.getenv(SID_ENV, "").strip()
    specs = [
        {"side": "YES", "price": model["yes"], "id": ladder.cid(sid, "YES", 0), "row": None},
        {"side": "NO", "price": model["no"], "id": ladder.cid(sid, "NO", 0), "row": None},
    ]
    state: dict[str, Any] = {
        "terminal": False,
        "sid": sid,
        "symbol": event.symbol,
        "event": event.ticker,
        "backoff": str(model["backoff"]),
        "entry_edge": str(model["edge"]),
        "orders": [
            {"side": spec["side"], "price": str(spec["price"]), "id": spec["id"]}
            for spec in specs
        ],
    }
    _write_state(state)
    emit(
        "START",
        symbol=event.symbol,
        event_ticker=event.ticker,
        yes=str(model["yes"]),
        no=str(model["no"]),
        backoff=str(model["backoff"]),
        pair_edge=str(model["edge"]),
        yes_queue_hint=str(bid_size),
        no_queue_hint=str(ask_size),
        max_new_residual_qty=1,
        max_new_residual_notional=str(MAX_PRICE),
        order_entry=True,
        realized_pnl=None,
    )

    maker = ladder.base.GeminiMakerSession()
    await maker.connect()
    try:
        posted: list[dict[str, Any]] = []
        for spec in specs:
            payload = {
                "symbol": event.symbol,
                "side": "BUY",
                "type": "LIMIT",
                "timeInForce": "MOC",
                "price": str(spec["price"]),
                "quantity": "1",
                "eventOutcome": spec["side"],
                "clientOrderId": spec["id"],
            }
            ack = await maker.request("order.place", payload, timeout=4.0)
            if int(ack.get("status", 0)) != 200:
                await asyncio.gather(
                    *(ladder.cancel(maker, item, event.symbol) for item in posted),
                    return_exceptions=True,
                )
                await asyncio.sleep(0.25)
                fills = await ladder.fills(specs, event.symbol)
                summary = summarize(specs, fills)
                state.update({"terminal": True, "status": "POST_REJECT", **summary})
                _write_state(state)
                emit("COMPLETE", status="POST_REJECT", **summary, order_entry=False, realized_pnl=None)
                return
            spec["row"] = await ladder.base._find_row(spec["id"], event.symbol)
            if spec["row"] is None:
                raise RuntimeError("accepted exact pair order not discoverable")
            posted.append(spec)

        emit("PAIR_POSTED", n_orders=2, order_entry=True)
        ids = {spec["id"]: spec["side"] for spec in specs}
        first_side: str | None = None
        first_deadline = min(
            time.monotonic() + float(first_wait),
            time.monotonic() + max(1.0, event.seconds_to_expiry - 30.0),
        )
        while time.monotonic() < first_deadline and first_side is None:
            try:
                update = await asyncio.wait_for(maker.events.get(), timeout=0.4)
            except asyncio.TimeoutError:
                continue
            if not isinstance(update, Mapping):
                continue
            client = str(update.get("c") or "")
            qty = D(str(update.get("Z") or "0"))
            status = str(update.get("X") or "").upper()
            if client in ids and qty > ZERO and status in {"FILLED", "PARTIALLY_FILLED"}:
                first_side = ids[client]
                emit(
                    "FIRST_FILL",
                    side=first_side,
                    client_id=client,
                    reported_qty=str(qty),
                    action="KEEP_ONLY_COMPLEMENT_PAIR_ORDER",
                    order_entry=True,
                )

        if first_side is None:
            await asyncio.gather(
                *(ladder.cancel(maker, spec, event.symbol) for spec in specs),
                return_exceptions=True,
            )
            await asyncio.sleep(0.3)
            fills = await ladder.fills(specs, event.symbol)
            summary = summarize(specs, fills)
            state.update({"terminal": True, "status": "NO_FILL_TIMEOUT", **summary})
            _write_state(state)
            emit("COMPLETE", status="NO_FILL_TIMEOUT", **summary, order_entry=False, realized_pnl=None)
            return

        other_side = "NO" if first_side == "YES" else "YES"
        complement_deadline = min(
            time.monotonic() + float(complement_wait),
            time.monotonic() + max(1.0, event.seconds_to_expiry - 25.0),
        )
        complement_seen = False
        while time.monotonic() < complement_deadline and not complement_seen:
            try:
                update = await asyncio.wait_for(maker.events.get(), timeout=0.4)
            except asyncio.TimeoutError:
                continue
            if not isinstance(update, Mapping):
                continue
            client = str(update.get("c") or "")
            qty = D(str(update.get("Z") or "0"))
            status = str(update.get("X") or "").upper()
            if ids.get(client) == other_side and qty > ZERO and status in {"FILLED", "PARTIALLY_FILLED"}:
                complement_seen = True
                emit(
                    "COMPLEMENT_FILL",
                    side=other_side,
                    client_id=client,
                    reported_qty=str(qty),
                    action="CANCEL_PAIR_REMAINDER",
                    order_entry=True,
                )

        await asyncio.gather(
            *(ladder.cancel(maker, spec, event.symbol) for spec in specs),
            return_exceptions=True,
        )
        await asyncio.sleep(0.35)
        fills = await ladder.fills(specs, event.symbol)
        summary = summarize(specs, fills)
        yes_qty = D(summary["yes_qty"])
        no_qty = D(summary["no_qty"])
        if yes_qty > ONE or no_qty > ONE:
            raise RuntimeError(f"q1 pair fill bound violated yes={yes_qty} no={no_qty}")
        status = (
            "MATCHED"
            if yes_qty > ZERO and yes_qty == no_qty
            else "ONE_SIDED_RESIDUAL"
            if yes_qty > ZERO or no_qty > ZERO
            else "NO_AUTHORITATIVE_FILL"
        )
        state.update({"terminal": True, "status": status, **summary})
        _write_state(state)
        emit(
            "COMPLETE",
            status=status,
            **summary,
            settlement_required=(yes_qty > ZERO or no_qty > ZERO),
            order_entry=False,
            realized_pnl=None,
        )
    finally:
        await maker.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
