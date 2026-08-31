"""Bounded live Gemini BTC5m paired-maker canary.

This is the first live expression of the paired-maker game plan.  It scales through
one 5-minute window in q=1 increments rather than deploying the $10 window budget at
once.  Maker rebates are diagnostic upside only; every completed pair must be
non-negative after the currently charged maker fees before incentives.

Hard bounds:
- BTC 5m only; one traded window per process;
- q=1 per new pair, one pair outstanding at a time;
- $10 maximum aggregate matched-pair cost INCLUDING modeled maker fees;
- no new pair in the final 60 seconds;
- WARM/HOT/VERY_HOT flow only; volume changes maker aggressiveness, never normal
  order type;
- missing side is completed maker-first with <=5 bounded reprices;
- if a single maker fill cannot be paired safely, sell that exact q=1 outcome back
  with the already-proven MARKET IOC emergency flatten and stop;
- projected rebates/rewards never enter entry, completion, or realized P&L.

Live mutation is dual-key armed and is additionally protected by the AWS one-use
approval launcher.  Importing/running while disarmed performs no venue/account call.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

import httpx

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src import gemini_short_crypto_dynamic_complement_q1 as dyn
from src import gemini_short_crypto_inventory_local_budget as account_budget
from src import gemini_short_crypto_local_budget as local_base
from src import gemini_incentive_mm_dynamic_complement as policy

ONE = D("1")
ZERO = D("0")
CENT = D("0.01")
MAX_WINDOW_SPEND = D("10.00")
MIN_AVAILABLE_USD = D("12.00")
MAX_WORST_RESCUE_LOSS = D("2.00")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")
WARM_FLOW = D("10")
HOT_FLOW = D("100")
VERY_HOT_FLOW = D("500")
MIN_FLOW_OBSERVE_S = 5.0
NO_NEW_ENTRY_S = 60.0
STOP_MAKER_COMPLETION_S = 30.0
ENTRY_REST_S = 8.0
COMPLEMENT_REST_S = 2.0
MAX_COMPLEMENT_REPRICES = 5
WATCH_SECONDS = 45 * 60
POLL_SECONDS = 2.0
SETTLEMENT_WAIT_SECONDS = 12 * 60

LIVE_ENV = "GEMINI_PAIRED_MAKER_LIVE_10"
PROBE_ID_ENV = "GEMINI_PAIRED_MAKER_LIVE_10_ID"
PROBE_ARM_ENV = "GEMINI_PAIRED_MAKER_LIVE_10_ARM"

REST = "https://api.gemini.com"


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_PAIRED_MAKER_LIVE_10 "
        + json.dumps(
            {"stage": stage, "observed_at_utc": datetime.now(timezone.utc).isoformat(), **fields},
            separators=(",", ":"), default=str,
        ),
        flush=True,
    )


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    probe_id = os.getenv(PROBE_ID_ENV, "").strip()
    return bool(_enabled(LIVE_ENV) and probe_id and os.getenv(PROBE_ARM_ENV, "").strip() == probe_id)


def _clean(value: str, limit: int = 18) -> str:
    out = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip())[:limit]
    return out or "probe"


@dataclass
class FlowGauge:
    samples: deque[tuple[float, D]]

    def __init__(self) -> None:
        self.samples = deque()

    def add(self, total_volume: D, now: float | None = None) -> None:
        now = time.monotonic() if now is None else float(now)
        total_volume = D(str(total_volume))
        if self.samples and total_volume < self.samples[-1][1]:
            self.samples.clear()
        self.samples.append((now, total_volume))
        while len(self.samples) > 1 and now - self.samples[0][0] > 60.0:
            self.samples.popleft()

    def observed_flow(self) -> D:
        if len(self.samples) < 2:
            return ZERO
        span = self.samples[-1][0] - self.samples[0][0]
        if span < MIN_FLOW_OBSERVE_S:
            return ZERO
        return max(ZERO, self.samples[-1][1] - self.samples[0][1])

    def state(self) -> str:
        flow = self.observed_flow()
        if flow >= VERY_HOT_FLOW:
            return "VERY_HOT"
        if flow >= HOT_FLOW:
            return "HOT"
        if flow >= WARM_FLOW:
            return "WARM"
        return "DEAD"


def after_fee_pair_edge(yes_price: D, no_price: D) -> D:
    return policy.completed_pair_edge(
        filled_price=D(str(yes_price)), complement_price=D(str(no_price))
    )


def entry_quotes(bid: D, ask: D, flow_state: str) -> tuple[D, D, D] | None:
    """Return q1 maker prices and after-fee edge, or None if unsafe.

    WARM rests one cent behind both top bids. HOT/VERY_HOT join the top bids. We do
    not backsolve a negative-fee trade into existence: if the chosen maker prices do
    not break even before rebates, the engine simply waits.
    """
    bid, ask = D(str(bid)), D(str(ask))
    if not (ZERO < bid < ask < ONE):
        return None
    if flow_state == "WARM":
        backoff = CENT
    elif flow_state in {"HOT", "VERY_HOT"}:
        backoff = ZERO
    else:
        return None
    yes = bid - backoff
    no = ONE - ask - backoff
    if not (MIN_PRICE <= yes <= MAX_PRICE and MIN_PRICE <= no <= MAX_PRICE):
        return None
    edge = after_fee_pair_edge(yes, no)
    if edge < ZERO:
        return None
    return yes, no, edge


def can_start_pair(total_net_spend: D, seconds_to_expiry: float, flow_state: str) -> bool:
    if flow_state not in {"WARM", "HOT", "VERY_HOT"}:
        return False
    if seconds_to_expiry <= NO_NEW_ENTRY_S:
        return False
    # Reserve a full $1 for the next complete set. $10 is a ceiling, never a target.
    return D(str(total_net_spend)) + ONE <= MAX_WINDOW_SPEND + D("0.0000001")


def recycle_delay(flow_state: str) -> float:
    return 3.0 if flow_state == "WARM" else 1.0 if flow_state == "HOT" else 0.5


def _order_price(row: Mapping[str, Any] | None, fallback: D) -> D:
    if row:
        raw = row.get("avgExecutionPrice") or row.get("avg_execution_price") or row.get("price")
        if raw not in {None, ""}:
            value = D(str(raw))
            if ZERO < value < ONE:
                return value
    return D(str(fallback))


def _client_prefix(probe_id: str, ticker: str, pair_index: int) -> str:
    digest = hashlib.sha256(f"{probe_id}:{ticker}:{pair_index}".encode()).hexdigest()[:10]
    return f"p10-{digest}-{pair_index}"[:22]


def _buy_params(symbol: str, outcome: str, price: D, client_id: str) -> dict[str, str]:
    return {
        "symbol": symbol,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "MOC",
        "price": str(D(str(price))),
        "quantity": "1",
        "eventOutcome": outcome.upper(),
        "clientOrderId": client_id,
    }


async def _event_body(ticker: str) -> dict[str, Any]:
    async with httpx.AsyncClient(
        timeout=8.0, follow_redirects=True,
        headers={"User-Agent": "arb-gemini-paired-live10/1.0", "Cache-Control": "no-cache"},
    ) as http:
        r = await http.get(f"{REST}/v1/prediction-markets/events/{ticker}")
    r.raise_for_status()
    body = r.json()
    if not isinstance(body, dict):
        raise RuntimeError("Gemini event lookup malformed")
    return body


async def _event_volume(ticker: str) -> D:
    body = await _event_body(ticker)
    return D(str(body.get("volume") or "0"))


async def _wait_settled(ticker: str, symbol: str) -> dict[str, Any]:
    deadline = time.monotonic() + SETTLEMENT_WAIT_SECONDS
    while time.monotonic() < deadline:
        body = await _event_body(ticker)
        status = str(body.get("status") or "").lower()
        contracts = body.get("contracts") or []
        match = next(
            (c for c in contracts if isinstance(c, Mapping) and str(c.get("instrumentSymbol") or "") == symbol),
            None,
        )
        if status == "settled" and match and str(match.get("status") or "").lower() == "settled":
            side = str(match.get("resolutionSide") or "").lower()
            if side in {"yes", "no"}:
                return {"resolution_side": side, "resolved_at": match.get("resolvedAt") or body.get("resolvedAt")}
        await asyncio.sleep(5.0)
    raise RuntimeError("paired-maker live10 settlement timeout")


async def _flatten_filled(
    *, outcome: str, symbol: str, filled_price: D, original_client_id: str,
) -> dict[str, Any]:
    params = _buy_params(symbol, outcome, filled_price, original_client_id)
    result = await base._flatten(params)
    qty = D(str(result.get("filled_quantity") or "0"))
    sell_price = D(str(result.get("average_fill_price") or "0"))
    sell_fee = D(str(result.get("fee_paid") or "0")) if result.get("fee_paid") not in {None, ""} else ZERO
    buy_fee = base.gemini_predictions_maker_fee(D(str(filled_price)), ONE)
    pnl = sell_price - D(str(filled_price)) - buy_fee - sell_fee if qty == ONE else None
    return {**result, "rescue_realized_pnl": None if pnl is None else str(pnl)}


async def _cancel_one(
    maker: base.GeminiMakerSession, *, client_id: str, symbol: str,
    row: Mapping[str, Any] | None, active_ids: set[str],
) -> tuple[dict[str, Any], D, Mapping[str, Any] | None]:
    cancel, _, terminal, race_fill = await dyn._cancel(
        maker, client_id=client_id, symbol=symbol, row=row,
    )
    if cancel.get("ok") or race_fill > ZERO:
        active_ids.discard(client_id)
    return cancel, race_fill, terminal


async def _complete_missing(
    maker: base.GeminiMakerSession,
    *,
    event: base.Event,
    probe_id: str,
    pair_index: int,
    filled_outcome: str,
    filled_price: D,
    filled_client_id: str,
    existing_complement_id: str | None,
    existing_complement_row: Mapping[str, Any] | None,
    existing_complement_price: D,
    active_ids: set[str],
    all_ids: list[str],
) -> dict[str, Any]:
    missing = "NO" if filled_outcome == "YES" else "YES"

    if existing_complement_id is not None:
        cancel, race_fill, terminal = await _cancel_one(
            maker, client_id=existing_complement_id, symbol=event.symbol,
            row=existing_complement_row, active_ids=active_ids,
        )
        emit(
            "COMPLEMENT_INITIAL_CANCEL",
            pair_index=pair_index, missing=missing, client_order_id=existing_complement_id,
            cancel_ok=bool(cancel.get("ok")), cancel_race_fill=str(race_fill),
        )
        if race_fill == ONE:
            price = _order_price(terminal, existing_complement_price)
            return {"paired": True, "missing_price": price, "missing_client_id": existing_complement_id}
        if race_fill > ZERO or not cancel.get("ok"):
            return {"hard_halt": True, "reason": "COMPLEMENT_CANCEL_AMBIGUOUS"}

    deadline = min(
        time.monotonic() + 20.0,
        time.monotonic() + max(1.0, event.seconds_to_expiry - STOP_MAKER_COMPLETION_S),
    )
    prefix = _client_prefix(probe_id, event.ticker, pair_index)
    for seq in range(1, MAX_COMPLEMENT_REPRICES + 1):
        if time.monotonic() >= deadline or event.seconds_to_expiry <= STOP_MAKER_COMPLETION_S:
            break
        bid, _, ask, _ = await base.fresh_top(event.symbol)
        edge_ceiling = policy.max_edge_safe_complement_price(
            filled_price=D(str(filled_price)), min_final_edge=ZERO,
            increment=event.price_increment, min_price=MIN_PRICE, max_price=MAX_PRICE,
        )
        if edge_ceiling is None:
            break
        passive = policy.maker_passive_ceiling(
            missing_outcome=missing, yes_bid=bid, yes_ask=ask, increment=event.price_increment,
        )
        target = policy.floor_to_increment(min(edge_ceiling, passive, MAX_PRICE), event.price_increment)
        if target < MIN_PRICE:
            break
        if policy.completed_pair_edge(filled_price=D(str(filled_price)), complement_price=target) < ZERO:
            break

        cid = f"{prefix}-{missing.lower()}-r{seq}"[:36]
        ack, row, ack_ms = await dyn._post_maker(
            maker, symbol=event.symbol, outcome=missing, price=target, client_id=cid,
        )
        accepted = int(ack.get("status", 0)) == 200
        emit(
            "COMPLEMENT_POST", pair_index=pair_index, sequence=seq, missing=missing,
            client_order_id=cid, price=str(target), after_fee_edge=str(
                policy.completed_pair_edge(filled_price=D(str(filled_price)), complement_price=target)
            ), ack_status=ack.get("status"), ack_ms=round(ack_ms, 3), accepted=accepted,
        )
        if not accepted:
            return {"hard_halt": True, "reason": "COMPLEMENT_POST_REJECTED"}
        active_ids.add(cid)
        all_ids.append(cid)
        fill = await dyn._wait_for_specific_fill(
            maker, client_id=cid, deadline=min(deadline, time.monotonic() + COMPLEMENT_REST_S),
        )
        if fill == ONE:
            active_ids.discard(cid)
            terminal = await base._order_by_client(cid, event.symbol)
            return {"paired": True, "missing_price": _order_price(terminal, target), "missing_client_id": cid}
        if fill > ZERO:
            return {"hard_halt": True, "reason": "FRACTIONAL_COMPLEMENT_FILL"}
        cancel, race_fill, terminal = await _cancel_one(
            maker, client_id=cid, symbol=event.symbol, row=row, active_ids=active_ids,
        )
        if race_fill == ONE:
            return {"paired": True, "missing_price": _order_price(terminal, target), "missing_client_id": cid}
        if race_fill > ZERO or not cancel.get("ok"):
            return {"hard_halt": True, "reason": "COMPLEMENT_REPRICE_CANCEL_AMBIGUOUS"}

    flatten = await _flatten_filled(
        outcome=filled_outcome, symbol=event.symbol, filled_price=filled_price,
        original_client_id=filled_client_id,
    )
    qty = D(str(flatten.get("filled_quantity") or "0"))
    if qty != ONE:
        return {"hard_halt": True, "reason": "EMERGENCY_FLATTEN_NOT_FULL", "flatten": flatten}
    pnl = D(str(flatten.get("rescue_realized_pnl") or "0"))
    return {
        "flattened": True,
        "flatten": flatten,
        "rescue_realized_pnl": pnl,
        "within_rescue_loss_cap": pnl >= -MAX_WORST_RESCUE_LOSS,
    }


async def _execute_one_pair(
    maker: base.GeminiMakerSession,
    *, event: base.Event, probe_id: str, pair_index: int,
    yes_price: D, no_price: D, active_ids: set[str], all_ids: list[str],
) -> dict[str, Any]:
    prefix = _client_prefix(probe_id, event.ticker, pair_index)
    yes_id = f"{prefix}-yes"[:36]
    no_id = f"{prefix}-no"[:36]
    yes_params = _buy_params(event.symbol, "YES", yes_price, yes_id)
    no_params = _buy_params(event.symbol, "NO", no_price, no_id)

    yes_ack, yes_row, yes_ms = await dyn._post_maker(
        maker, symbol=event.symbol, outcome="YES", price=yes_price, client_id=yes_id,
    )
    if int(yes_ack.get("status", 0)) != 200:
        emit("PAIR_POST_STOP", pair_index=pair_index, side="YES", ack_status=yes_ack.get("status"), mutation=False)
        return {"mutation": False, "paired": False, "no_fill": True}
    active_ids.add(yes_id); all_ids.append(yes_id)

    no_ack, no_row, no_ms = await dyn._post_maker(
        maker, symbol=event.symbol, outcome="NO", price=no_price, client_id=no_id,
    )
    if int(no_ack.get("status", 0)) != 200:
        cancel, race_fill, terminal = await _cancel_one(
            maker, client_id=yes_id, symbol=event.symbol, row=yes_row, active_ids=active_ids,
        )
        if race_fill == ONE:
            actual_yes = _order_price(terminal, yes_price)
            flatten = await _flatten_filled(
                outcome="YES", symbol=event.symbol, filled_price=actual_yes, original_client_id=yes_id,
            )
            return {"mutation": True, "flattened": True, "flatten": flatten, "hard_halt": True,
                    "reason": "SECOND_POST_REJECTED_AFTER_YES_FILL"}
        return {"mutation": True, "hard_halt": True, "reason": "SECOND_POST_REJECTED"}
    active_ids.add(no_id); all_ids.append(no_id)

    emit(
        "PAIR_RESTING", pair_index=pair_index, yes_client_id=yes_id, no_client_id=no_id,
        yes_price=str(yes_price), no_price=str(no_price), after_fee_edge=str(after_fee_pair_edge(yes_price, no_price)),
        yes_ack_ms=round(yes_ms, 3), no_ack_ms=round(no_ms, 3), q=1,
    )

    first = await dyn._wait_for_first_fill(
        maker, yes_id=yes_id, no_id=no_id, yes_price=yes_price, no_price=no_price,
        deadline=time.monotonic() + ENTRY_REST_S,
    )
    if first is not None:
        if D(str(first["quantity"])) != ONE:
            return {"mutation": True, "hard_halt": True, "reason": "FRACTIONAL_FIRST_FILL"}
        filled_outcome = str(first["outcome"])
        filled_id = str(first["client_id"])
        filled_price = D(str(first["price"]))
        active_ids.discard(filled_id)
        if filled_outcome == "YES":
            comp_id, comp_row, comp_price = no_id, no_row, no_price
        else:
            comp_id, comp_row, comp_price = yes_id, yes_row, yes_price
        completed = await _complete_missing(
            maker, event=event, probe_id=probe_id, pair_index=pair_index,
            filled_outcome=filled_outcome, filled_price=filled_price, filled_client_id=filled_id,
            existing_complement_id=comp_id, existing_complement_row=comp_row,
            existing_complement_price=comp_price, active_ids=active_ids, all_ids=all_ids,
        )
        if completed.get("paired"):
            missing_price = D(str(completed["missing_price"]))
            yes_actual = filled_price if filled_outcome == "YES" else missing_price
            no_actual = missing_price if filled_outcome == "YES" else filled_price
            edge = after_fee_pair_edge(yes_actual, no_actual)
            if edge < ZERO:
                return {"mutation": True, "hard_halt": True, "reason": "ACTUAL_PAIR_NEGATIVE_AFTER_FEES"}
            return {"mutation": True, "paired": True, "yes_price": yes_actual, "no_price": no_actual,
                    "edge": edge, "net_cost": ONE - edge}
        return {"mutation": True, **completed}

    # Nothing was observed during the resting interval. Cancel both and reconcile
    # cancel-race fills before deciding whether a naked leg exists.
    yc, yf, yterm = await _cancel_one(
        maker, client_id=yes_id, symbol=event.symbol, row=yes_row, active_ids=active_ids,
    )
    nc, nf, nterm = await _cancel_one(
        maker, client_id=no_id, symbol=event.symbol, row=no_row, active_ids=active_ids,
    )
    if yf > ONE or nf > ONE or (ZERO < yf < ONE) or (ZERO < nf < ONE):
        return {"mutation": True, "hard_halt": True, "reason": "CANCEL_RACE_Q1_BOUND_VIOLATION"}
    if yf == ZERO and nf == ZERO:
        return {"mutation": True, "paired": False, "no_fill": True}
    if yf == ONE and nf == ONE:
        ya = _order_price(yterm, yes_price); na = _order_price(nterm, no_price)
        edge = after_fee_pair_edge(ya, na)
        if edge < ZERO:
            return {"mutation": True, "hard_halt": True, "reason": "CANCEL_RACE_PAIR_NEGATIVE_AFTER_FEES"}
        return {"mutation": True, "paired": True, "yes_price": ya, "no_price": na,
                "edge": edge, "net_cost": ONE - edge}

    filled_outcome = "YES" if yf == ONE else "NO"
    filled_price = _order_price(yterm, yes_price) if yf == ONE else _order_price(nterm, no_price)
    filled_id = yes_id if yf == ONE else no_id
    completed = await _complete_missing(
        maker, event=event, probe_id=probe_id, pair_index=pair_index,
        filled_outcome=filled_outcome, filled_price=filled_price, filled_client_id=filled_id,
        existing_complement_id=None, existing_complement_row=None,
        existing_complement_price=no_price if filled_outcome == "YES" else yes_price,
        active_ids=active_ids, all_ids=all_ids,
    )
    if completed.get("paired"):
        missing_price = D(str(completed["missing_price"]))
        ya = filled_price if filled_outcome == "YES" else missing_price
        na = missing_price if filled_outcome == "YES" else filled_price
        edge = after_fee_pair_edge(ya, na)
        if edge < ZERO:
            return {"mutation": True, "hard_halt": True, "reason": "RESCUED_PAIR_NEGATIVE_AFTER_FEES"}
        return {"mutation": True, "paired": True, "yes_price": ya, "no_price": na,
                "edge": edge, "net_cost": ONE - edge}
    return {"mutation": True, **completed}


async def _cleanup_active(maker: base.GeminiMakerSession, symbol: str | None, active_ids: set[str]) -> None:
    if not symbol:
        return
    for cid in list(active_ids):
        try:
            row = await base._order_by_client(cid, symbol)
            cancel, _, _, race = await dyn._cancel(maker, client_id=cid, symbol=symbol, row=row)
            emit("FINAL_CLEANUP_CANCEL", client_order_id=cid, cancel_ok=bool(cancel.get("ok")), race_fill=str(race))
            if cancel.get("ok") or race > ZERO:
                active_ids.discard(cid)
        except Exception as exc:
            emit("FINAL_CLEANUP_ERROR", client_order_id=cid, error_type=type(exc).__name__)


async def run() -> None:
    if not live_armed():
        emit(
            "DISARMED", order_entry=False, credentials_read=False, max_window_spend=str(MAX_WINDOW_SPEND),
            q_per_pair=1, rewards_counted_as_realized=False,
        )
        return

    if local_base.local_open_state() is not None:
        raise RuntimeError("paired-maker live10 blocked by unresolved local short-crypto reservation")

    probe_id = _clean(os.environ[PROBE_ID_ENV])
    snap = await account_budget.assert_clean_account()
    terms_ok = await base._gemini_terms_ok()
    available = D(str(await base._gemini_available_usd()))
    emit(
        "CLEAN_START", probe_id=probe_id, clean=True, terms_ok=terms_ok, available_usd=str(available),
        max_window_spend=str(MAX_WINDOW_SPEND), max_worst_rescue_loss=str(MAX_WORST_RESCUE_LOSS),
        q_per_pair=1, one_pair_outstanding=True, rewards_counted_as_realized=False, **snap,
    )
    if not terms_ok:
        raise RuntimeError("Gemini Prediction Markets terms not accepted")
    if available < MIN_AVAILABLE_USD:
        raise RuntimeError(f"Gemini available USD below {MIN_AVAILABLE_USD} live10 reserve")

    state_dir = Path(os.getenv("GEMINI_SHORT_CRYPTO_STATE_DIR") or "/tmp")
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"paired_maker_live10_{probe_id}.json"
    active_ids: set[str] = set()
    all_ids: list[str] = []
    maker = base.GeminiMakerSession()
    current_symbol: str | None = None
    traded_event: base.Event | None = None
    total_net_spend = ZERO
    paired_count = 0
    total_pair_edge = ZERO
    pair_index = 0
    rescue_pnl = ZERO
    watch_deadline = time.monotonic() + WATCH_SECONDS
    gauge: FlowGauge | None = None
    gauge_ticker: str | None = None

    def persist(stage: str) -> None:
        payload = {
            "stage": stage, "probe_id": probe_id,
            "event_ticker": None if traded_event is None else traded_event.ticker,
            "symbol": current_symbol, "paired_count": paired_count,
            "total_net_spend": str(total_net_spend), "total_pair_edge": str(total_pair_edge),
            "active_client_order_ids": sorted(active_ids), "all_client_order_ids": all_ids,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(tmp, state_path)

    try:
        await maker.connect()
        while time.monotonic() < watch_deadline:
            event = traded_event or await base.discover_btc5m()
            if event is None:
                emit("WAIT_NO_BTC5M", order_entry=False)
                await asyncio.sleep(POLL_SECONDS)
                continue
            current_symbol = event.symbol
            if gauge_ticker != event.ticker:
                gauge = FlowGauge(); gauge_ticker = event.ticker
                emit("WATCH_WINDOW", ticker=event.ticker, symbol=event.symbol,
                     seconds_to_expiry=round(event.seconds_to_expiry, 1), order_entry=False)
            assert gauge is not None
            if event.seconds_to_expiry <= 0:
                if traded_event is not None:
                    break
                await asyncio.sleep(1.0)
                gauge_ticker = None
                continue

            try:
                total_volume = await _event_volume(event.ticker)
                gauge.add(total_volume)
                flow = gauge.observed_flow()
                flow_state = gauge.state()
            except (httpx.TransportError, asyncio.TimeoutError):
                await asyncio.sleep(POLL_SECONDS)
                continue

            emit(
                "FLOW", ticker=event.ticker, state=flow_state, observed_flow_60s=str(flow),
                total_volume_usd=str(total_volume), seconds_to_expiry=round(event.seconds_to_expiry, 1),
                paired_count=paired_count, total_net_spend=str(total_net_spend), order_entry=False,
            )

            if not can_start_pair(total_net_spend, event.seconds_to_expiry, flow_state):
                if traded_event is not None and event.seconds_to_expiry <= NO_NEW_ENTRY_S:
                    break
                await asyncio.sleep(POLL_SECONDS)
                continue

            bid, _, ask, _ = await base.fresh_top(event.symbol)
            plan = entry_quotes(bid, ask, flow_state)
            if plan is None:
                emit("ENTRY_SKIP", reason="NO_AFTER_FEE_BREAK_EVEN_MAKER_PAIR", state=flow_state,
                     bid=str(bid), ask=str(ask), order_entry=False)
                await asyncio.sleep(POLL_SECONDS)
                continue
            yes_price, no_price, edge = plan
            # Re-read immediately before mutation.
            bid2, _, ask2, _ = await base.fresh_top(event.symbol)
            plan2 = entry_quotes(bid2, ask2, flow_state)
            if plan2 is None:
                emit("ENTRY_SKIP", reason="FRESH_BOOK_LOST_AFTER_FEE_EDGE", order_entry=False)
                await asyncio.sleep(POLL_SECONDS)
                continue
            yes_price, no_price, edge = plan2
            if event.seconds_to_expiry <= NO_NEW_ENTRY_S:
                continue

            pair_index += 1
            result = await _execute_one_pair(
                maker, event=event, probe_id=probe_id, pair_index=pair_index,
                yes_price=yes_price, no_price=no_price, active_ids=active_ids, all_ids=all_ids,
            )
            if result.get("mutation"):
                traded_event = event
            persist("PAIR_RESULT")
            emit("PAIR_RESULT", pair_index=pair_index, **result)

            if result.get("hard_halt"):
                break
            if result.get("flattened"):
                rescue_pnl += D(str(result.get("rescue_realized_pnl") or result.get("flatten", {}).get("rescue_realized_pnl") or "0"))
                break
            if result.get("paired"):
                net_cost = D(str(result["net_cost"]))
                if total_net_spend + net_cost > MAX_WINDOW_SPEND + D("0.0000001"):
                    raise RuntimeError("paired-maker live10 spend ceiling violation")
                total_net_spend += net_cost
                total_pair_edge += D(str(result["edge"]))
                paired_count += 1
                traded_event = event
                persist("PAIRED")
                if not can_start_pair(total_net_spend, event.seconds_to_expiry, flow_state):
                    break
                await asyncio.sleep(recycle_delay(flow_state))
                continue
            await asyncio.sleep(POLL_SECONDS)

        persist("TRADING_COMPLETE")
    finally:
        await _cleanup_active(maker, current_symbol, active_ids)
        await maker.close()
        persist("CLEANUP_COMPLETE")

    if active_ids:
        raise RuntimeError(f"paired-maker live10 terminal cleanup left active ids: {sorted(active_ids)}")

    if paired_count > 0 and traded_event is not None:
        settlement = await _wait_settled(traded_event.ticker, traded_event.symbol)
        realized = D(paired_count) - total_net_spend + rescue_pnl
        emit(
            "SETTLED", ticker=traded_event.ticker, symbol=traded_event.symbol,
            paired_count=paired_count, total_net_spend=str(total_net_spend),
            total_pair_edge=str(total_pair_edge), rescue_realized_pnl=str(rescue_pnl),
            realized_trading_pnl=str(realized), maker_rebates_included=False,
            liquidity_rewards_included=False, **settlement,
            classification="SETTLED_LIVE_MATCHED_PAIRS_REALIZED_TRADING_PNL",
        )
    elif rescue_pnl != ZERO:
        emit(
            "FLATTENED", rescue_realized_pnl=str(rescue_pnl), paired_count=paired_count,
            maker_rebates_included=False, classification="LIVE_RESCUE_FLATTEN_REALIZED_PNL",
        )
    else:
        emit(
            "COMPLETE_NO_SETTLED_TRADE", paired_count=paired_count, total_net_spend=str(total_net_spend),
            realized_trading_pnl=None, classification="LIVE_CANARY_NO_REALIZED_SETTLEMENT",
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
