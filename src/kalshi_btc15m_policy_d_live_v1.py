"""Bounded live port of frozen Policy D to Kalshi KXBTC15M.

This release intentionally matches the validated Lane-2 shadow semantics rather
than the prior Gemini rescue strategy:

* KXBTC15M only;
* frozen last-30-print, share-weighted tape rule;
* quote the side opposite >10% tape accumulation;
* selected side bid must be >= $0.30;
* one post-only q=1.00 order at a time;
* at most one maker fill per market;
* any partial fill causes immediate cancellation of the remainder;
* filled inventory is held to the market's authoritative Kalshi settlement;
* no averaging, no late new exposure, no automatic restart;
* maximum four market windows in one approved session.

The first live release is intentionally q=1 because a total loss of a filled
contract is therefore < $1 and a four-window session cannot create more than
four contracts of strategy inventory. Rewards/rebates are never modeled in
admission or realized P&L.
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

from src.auth import KalshiClient

ZERO = D("0")
ONE = D("1")
PUBLIC_REST = "https://api.elections.kalshi.com/trade-api/v2"
PROD_REST = "https://external-api.kalshi.com/trade-api/v2"
SERIES = "KXBTC15M"
TAPE_WINDOW = 30
TAPE_THRESHOLD = D("0.10")
PRICE_FLOOR = D("0.30")
MIN_STANCE_SECONDS = 1.0
STOP_NEW_SECONDS = 90.0
QTY = D("1.00")
MAX_WINDOWS_DEFAULT = 4
MAX_SESSION_SECONDS_DEFAULT = 4200.0
POLL_SECONDS = 0.20
DISCOVERY_SECONDS = 1.0
SETTLEMENT_EXTRA_SECONDS = 900.0

ARM_ID_ENV = "KALSHI_POLICY_D_V1_ARM_ID"
ARM_ENV = "KALSHI_POLICY_D_V1_ARM"
LIVE_ENV = "KALSHI_POLICY_D_V1_LIVE"
MAX_WINDOWS_ENV = "KALSHI_POLICY_D_V1_MAX_WINDOWS"
MAX_SESSION_SECONDS_ENV = "KALSHI_POLICY_D_V1_MAX_SESSION_SECONDS"
STATE_DIR_ENV = "KALSHI_POLICY_D_V1_STATE_DIR"
CLASSIFICATION = "LIVE_KALSHI_BTC15M_POLICY_D_V1_OPEN_NOT_REALIZED"


def _now() -> float:
    return time.time()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_dir() -> Path:
    return Path(os.getenv(STATE_DIR_ENV, "/data/kalshi-btc15m-policy-d-v1"))


def state_path() -> Path:
    return state_dir() / "state.json"


def _atomic_json(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(row), separators=(",", ":"), default=str), encoding="utf-8")
    tmp.replace(path)


def read_state() -> dict[str, Any] | None:
    path = state_path()
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"classification": CLASSIFICATION, "open": True, "status": "STATE_PARSE_ERROR"}
    return value if isinstance(value, dict) else {"classification": CLASSIFICATION, "open": True, "status": "STATE_NOT_OBJECT"}


def heartbeat(state: dict[str, Any], status: str, **fields: Any) -> None:
    state.update(fields)
    state["classification"] = CLASSIFICATION
    state["status"] = status
    state["heartbeat_at_utc"] = _iso_now()
    _atomic_json(state_path(), state)


def emit(stage: str, **fields: Any) -> None:
    payload = {"stage": stage, "observed_at_utc": _iso_now(), **fields}
    print("KALSHI_POLICY_D_V1=" + json.dumps(payload, separators=(",", ":"), default=str), flush=True)


def live_armed() -> bool:
    approval = os.getenv(ARM_ID_ENV, "").strip()
    return bool(
        os.getenv(LIVE_ENV, "NO").strip().upper() == "YES"
        and approval
        and os.getenv(ARM_ENV, "").strip() == approval
    )


def max_windows() -> int:
    value = int(os.getenv(MAX_WINDOWS_ENV, str(MAX_WINDOWS_DEFAULT)))
    if not 1 <= value <= 4:
        raise RuntimeError(f"{MAX_WINDOWS_ENV} must be between 1 and 4")
    return value


def max_session_seconds() -> float:
    value = float(os.getenv(MAX_SESSION_SECONDS_ENV, str(MAX_SESSION_SECONDS_DEFAULT)))
    if not 900.0 <= value <= 7200.0:
        raise RuntimeError(f"{MAX_SESSION_SECONDS_ENV} must be between 900 and 7200")
    return value


def compact_approval(value: str) -> str:
    return hashlib.blake2s(value.encode("utf-8"), digest_size=5).hexdigest()


def client_order_id(approval: str, window_no: int) -> str:
    if not 1 <= window_no <= 4:
        raise ValueError("window_no outside bounded release range")
    return f"pd1-{compact_approval(approval)}-{window_no}"


def _parse_time(raw: Any) -> float | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


@dataclass(frozen=True)
class Market:
    ticker: str
    open_ts: float
    close_ts: float
    result: str | None = None

    @property
    def seconds_to_close(self) -> float:
        return self.close_ts - _now()


@dataclass(frozen=True)
class Print:
    trade_id: str
    created_ts: float
    taker_side: str
    yes_price: D
    quantity: D


@dataclass(frozen=True)
class Signal:
    outcome: str
    price: D
    signed_yes_imbalance: D
    tape_n: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "price": str(self.price),
            "signed_yes_imbalance": str(self.signed_yes_imbalance),
            "tape_n": self.tape_n,
            "tape_threshold": str(TAPE_THRESHOLD),
            "price_floor": str(PRICE_FLOOR),
        }


class Tape30:
    def __init__(self) -> None:
        self.rows: deque[tuple[str, D]] = deque(maxlen=TAPE_WINDOW)
        self.seen: set[str] = set()

    def ingest(self, rows: list[Print]) -> int:
        added = 0
        for row in sorted(rows, key=lambda x: (x.created_ts, x.trade_id)):
            if row.trade_id in self.seen:
                continue
            self.seen.add(row.trade_id)
            self.rows.append((row.taker_side, row.quantity))
            added += 1
        if len(self.seen) > 10000:
            # This is only a per-window dedupe set. Retain recent IDs by dropping
            # the old set; REST overlap is harmless because Tape30 is refreshed
            # from recent history again after any rollover.
            self.seen = set(list(self.seen)[-5000:])
        return added

    @property
    def ready(self) -> bool:
        return len(self.rows) == TAPE_WINDOW

    def signed_yes_imbalance(self) -> D | None:
        if not self.ready:
            return None
        yes = sum((q for side, q in self.rows if side == "yes"), ZERO)
        no = sum((q for side, q in self.rows if side == "no"), ZERO)
        total = yes + no
        return None if total <= ZERO else (yes - no) / total

    def contra_outcome(self) -> str | None:
        signed = self.signed_yes_imbalance()
        if signed is None:
            return None
        if signed > TAPE_THRESHOLD:
            return "NO"
        if signed < -TAPE_THRESHOLD:
            return "YES"
        return None

    def snapshot(self) -> dict[str, Any]:
        yes = sum((q for side, q in self.rows if side == "yes"), ZERO)
        no = sum((q for side, q in self.rows if side == "no"), ZERO)
        signed = self.signed_yes_imbalance()
        return {
            "n": len(self.rows), "yes_quantity": str(yes), "no_quantity": str(no),
            "signed_yes_imbalance": None if signed is None else str(signed),
            "contra_outcome": self.contra_outcome(),
        }


def parse_print(row: Mapping[str, Any]) -> Print | None:
    tid = str(row.get("trade_id") or "").strip()
    side = str(row.get("taker_side") or "").strip().lower()
    if not tid or side not in {"yes", "no"}:
        return None
    ts = _parse_time(row.get("created_time")) or _now()
    try:
        price = D(str(row.get("yes_price_dollars") or row.get("yes_price") or "0"))
        qty = D(str(row.get("count_fp") or row.get("count") or "0"))
    except Exception:
        return None
    if not ZERO < price < ONE or qty <= ZERO:
        return None
    return Print(tid, ts, side, price, qty)


def best_bids(book: Mapping[str, Any]) -> tuple[D | None, D | None]:
    fp = book.get("orderbook_fp") if isinstance(book, Mapping) else None
    fp = fp if isinstance(fp, Mapping) else {}
    yes_rows = fp.get("yes_dollars") or []
    no_rows = fp.get("no_dollars") or []

    def best(rows: Any) -> D | None:
        values: list[D] = []
        for level in rows if isinstance(rows, list) else []:
            try:
                price = D(str(level[0]))
                qty = D(str(level[1]))
            except Exception:
                continue
            if ZERO < price < ONE and qty > ZERO:
                values.append(price)
        return max(values) if values else None

    return best(yes_rows), best(no_rows)


def signal_from(tape: Tape30, *, yes_bid: D | None, no_bid: D | None) -> Signal | None:
    outcome = tape.contra_outcome()
    signed = tape.signed_yes_imbalance()
    if outcome is None or signed is None:
        return None
    price = yes_bid if outcome == "YES" else no_bid
    if price is None or price < PRICE_FLOOR or price >= ONE:
        return None
    return Signal(outcome, price, signed, len(tape.rows))


def order_payload(*, market: Market, signal: Signal, approval: str, window_no: int) -> dict[str, Any]:
    """Build a V2 post-only q1 order at the current selected-side best bid."""
    if signal.outcome == "YES":
        side = "bid"
        yes_book_price = signal.price
    elif signal.outcome == "NO":
        side = "ask"
        yes_book_price = ONE - signal.price
    else:
        raise ValueError("signal outcome must be YES or NO")
    expiry = int(market.close_ts - STOP_NEW_SECONDS)
    if expiry <= int(_now()):
        raise RuntimeError("market is inside stop-new-exposure window")
    return {
        "ticker": market.ticker,
        "client_order_id": client_order_id(approval, window_no),
        "side": side,
        "count": "1.00",
        "price": f"{yes_book_price:.4f}",
        "time_in_force": "good_till_canceled",
        "expiration_time": expiry,
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": True,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }


async def discover_current_market(http: httpx.AsyncClient) -> Market | None:
    response = await http.get(
        f"{PUBLIC_REST}/markets",
        params={"series_ticker": SERIES, "status": "open", "limit": 100},
    )
    response.raise_for_status()
    now = _now()
    candidates: list[Market] = []
    for row in response.json().get("markets") or []:
        ticker = str(row.get("ticker") or "")
        if not ticker.startswith(SERIES):
            continue
        open_ts = _parse_time(row.get("open_time"))
        close_ts = _parse_time(row.get("close_time"))
        if open_ts is None or close_ts is None:
            continue
        if open_ts <= now < close_ts - STOP_NEW_SECONDS:
            candidates.append(Market(ticker, open_ts, close_ts, str(row.get("result") or "") or None))
    return min(candidates, key=lambda m: m.close_ts) if candidates else None


async def public_snapshot(http: httpx.AsyncClient, ticker: str) -> tuple[list[Print], D | None, D | None]:
    trades, book = await asyncio.gather(
        http.get(f"{PUBLIC_REST}/markets/trades", params={"ticker": ticker, "limit": 100}),
        http.get(f"{PUBLIC_REST}/markets/{ticker}/orderbook", params={"depth": 1}),
    )
    trades.raise_for_status(); book.raise_for_status()
    parsed = [p for p in (parse_print(row) for row in trades.json().get("trades") or []) if p is not None]
    yes_bid, no_bid = best_bids(book.json())
    return parsed, yes_bid, no_bid


def _rows_from_orders(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = body.get("orders") or []
    return [r for r in rows if isinstance(r, Mapping)]


def _row_fill(row: Mapping[str, Any] | None) -> D:
    if not row:
        return ZERO
    for key in ("fill_count_fp", "fill_count", "filled_count_fp", "filled_count"):
        value = row.get(key)
        if value not in (None, ""):
            try:
                return D(str(value))
            except Exception:
                pass
    return ZERO


def _row_status(row: Mapping[str, Any] | None) -> str:
    return "" if not row else str(row.get("status") or row.get("order_status") or "").upper()


async def exact_order(client: KalshiClient, *, ticker: str, cid: str) -> Mapping[str, Any] | None:
    response = await client.get("/portfolio/orders", params={"ticker": ticker, "limit": 100})
    response.raise_for_status()
    for row in _rows_from_orders(response.json()):
        if str(row.get("client_order_id") or "") == cid:
            return row
    return None


async def position_for(client: KalshiClient, ticker: str) -> D:
    response = await client.get("/portfolio/positions", params={"ticker": ticker, "limit": 100})
    response.raise_for_status()
    for row in response.json().get("market_positions") or []:
        if str(row.get("ticker") or "") == ticker:
            return D(str(row.get("position_fp") or row.get("position") or "0"))
    return ZERO


async def balance_cents(client: KalshiClient) -> D:
    response = await client.get("/portfolio/balance")
    response.raise_for_status()
    return D(str(response.json().get("balance") or "0"))


async def series_exposure(client: KalshiClient) -> dict[str, Any]:
    orders_r, pos_r = await asyncio.gather(
        client.get("/portfolio/orders", params={"limit": 100}),
        client.get("/portfolio/positions", params={"limit": 100}),
    )
    orders_r.raise_for_status(); pos_r.raise_for_status()
    open_orders = []
    for row in _rows_from_orders(orders_r.json()):
        ticker = str(row.get("ticker") or "")
        status = _row_status(row)
        if ticker.startswith(SERIES) and status not in {"CANCELED", "CANCELLED", "FILLED", "EXECUTED", "EXPIRED", "REJECTED"}:
            open_orders.append({"ticker": ticker, "order_id": row.get("order_id"), "status": status})
    positions = []
    for row in pos_r.json().get("market_positions") or []:
        ticker = str(row.get("ticker") or "")
        if not ticker.startswith(SERIES):
            continue
        qty = D(str(row.get("position_fp") or row.get("position") or "0"))
        if qty != ZERO:
            positions.append({"ticker": ticker, "position": str(qty)})
    return {"orders": open_orders, "positions": positions}


async def authenticated_readiness() -> dict[str, Any]:
    client = KalshiClient(base_url=PROD_REST)
    try:
        status, bal, exposure = await asyncio.gather(
            client.get("/exchange/status"), balance_cents(client), series_exposure(client)
        )
        status.raise_for_status()
        return {
            "status": "DISARMED_READY" if not exposure["orders"] and not exposure["positions"] else "EXISTING_KXBTC15M_EXPOSURE_BLOCKS",
            "exchange_status_code": status.status_code,
            "balance_cents": str(bal),
            "kxbtc15m": exposure,
            "live_armed": live_armed(),
            "order_entry": False,
        }
    finally:
        await client.close()


async def cancel_exact(client: KalshiClient, row: Mapping[str, Any]) -> dict[str, Any]:
    order_id = str(row.get("order_id") or "")
    if not order_id:
        return {"status": "NO_ORDER_ID", "ok": False}
    try:
        response = await client.delete(f"/portfolio/events/orders/{order_id}")
    except (httpx.TimeoutException, TimeoutError, asyncio.TimeoutError) as exc:
        return {"status": "CANCEL_ACK_AMBIGUOUS", "ok": False, "error_type": type(exc).__name__}
    return {"status": response.status_code, "ok": response.status_code in {200, 201, 204}}


async def cancel_and_reconcile(client: KalshiClient, *, ticker: str, cid: str, prior: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    cancel = await cancel_exact(client, prior)
    # A definite reject/cancel failure still requires exact reconciliation. Never
    # infer zero fill from the cancel acknowledgement itself.
    for _ in range(12):
        try:
            row = await exact_order(client, ticker=ticker, cid=cid)
        except Exception:
            row = None
        if row is not None:
            status = _row_status(row)
            if status in {"CANCELED", "CANCELLED", "FILLED", "EXECUTED", "EXPIRED", "REJECTED"}:
                return row, cancel
        await asyncio.sleep(0.10)
    return None, cancel


async def settlement_row(client: KalshiClient, ticker: str) -> Mapping[str, Any] | None:
    response = await client.get("/portfolio/settlements", params={"ticker": ticker, "limit": 100})
    if response.status_code != 200:
        return None
    for row in response.json().get("settlements") or []:
        if str(row.get("ticker") or row.get("market_ticker") or "") == ticker:
            return row
    return None


async def wait_for_settlement(client: KalshiClient, *, market: Market, state: dict[str, Any], balance_before_cents: D) -> dict[str, Any]:
    deadline = max(_now() + 60.0, market.close_ts + SETTLEMENT_EXTRA_SECONDS)
    while _now() < deadline:
        settlement = await settlement_row(client, market.ticker)
        pos = await position_for(client, market.ticker)
        if settlement is not None and pos == ZERO:
            bal = await balance_cents(client)
            heartbeat(state, "SETTLED_REALIZED", open=False, settlement=dict(settlement),
                      balance_after_cents=str(bal), account_delta_cents=str(bal - balance_before_cents),
                      settled_at_utc=_iso_now())
            return {
                "status": "SETTLED_REALIZED", "ticker": market.ticker,
                "settlement": dict(settlement), "balance_after_cents": str(bal),
                "account_delta_cents": str(bal - balance_before_cents), "realized": True,
            }
        heartbeat(state, "WAITING_FOR_AUTHORITATIVE_SETTLEMENT", open=True, position=str(pos))
        await asyncio.sleep(2.0)
    return {"status": "SETTLEMENT_NOT_CONFIRMED_WITHIN_BOUND", "ticker": market.ticker,
            "realized": False, "hard_halt": True}


async def run_market(*, client: KalshiClient, http: httpx.AsyncClient, market: Market, window_no: int, approval: str) -> dict[str, Any]:
    tape = Tape30()
    stance_outcome: str | None = None
    stance_since: float | None = None
    cid = client_order_id(approval, window_no)
    balance_before = await balance_cents(client)
    last_signal: Signal | None = None

    while market.seconds_to_close > STOP_NEW_SECONDS:
        prints, yes_bid, no_bid = await public_snapshot(http, market.ticker)
        tape.ingest(prints)
        signal = signal_from(tape, yes_bid=yes_bid, no_bid=no_bid)
        now = _now()
        if signal is None:
            stance_outcome = None; stance_since = None
            await asyncio.sleep(POLL_SECONDS)
            continue
        if signal.outcome != stance_outcome:
            stance_outcome = signal.outcome; stance_since = now
            await asyncio.sleep(POLL_SECONDS)
            continue
        if stance_since is None or now - stance_since < MIN_STANCE_SECONDS:
            await asyncio.sleep(POLL_SECONDS)
            continue
        last_signal = signal
        payload = order_payload(market=market, signal=signal, approval=approval, window_no=window_no)
        state = {
            "classification": CLASSIFICATION, "open": True, "status": "PRE_MUTATION",
            "ticker": market.ticker, "window_no": window_no, "client_order_id": cid,
            "signal": signal.as_dict(), "quantity": str(QTY), "balance_before_cents": str(balance_before),
            "maker_rebates_modeled": False, "liquidity_rewards_modeled": False,
        }
        heartbeat(state, "PRE_MUTATION")
        emit("ORDER_START", ticker=market.ticker, window_no=window_no, signal=signal.as_dict(),
             seconds_to_close=round(market.seconds_to_close, 3), order_entry=False)
        try:
            response = await client.post("/portfolio/events/orders", json=payload)
        except (httpx.TimeoutException, TimeoutError, asyncio.TimeoutError) as exc:
            heartbeat(state, "CREATE_ACK_AMBIGUOUS", open=True, error_type=type(exc).__name__)
            return {"status": "CREATE_ACK_AMBIGUOUS", "hard_halt": True, "order_entry": True}
        safe_body: dict[str, Any] = {}
        try:
            body = response.json()
            if isinstance(body, dict):
                safe_body = {k: body.get(k) for k in ("order_id", "status", "fill_count", "fill_count_fp", "error", "message") if k in body}
        except Exception:
            body = {}
        emit("ORDER_ACK", ticker=market.ticker, http_status=response.status_code, response=safe_body, order_entry=True)
        if response.status_code != 201:
            heartbeat(state, "ORDER_REJECTED", open=False, http_status=response.status_code, response=safe_body)
            await asyncio.sleep(POLL_SECONDS)
            continue
        row = await exact_order(client, ticker=market.ticker, cid=cid)
        if row is None:
            heartbeat(state, "CREATE_ROW_UNRESOLVED", open=True)
            return {"status": "CREATE_ROW_UNRESOLVED", "hard_halt": True, "order_entry": True}
        order_id = str(row.get("order_id") or body.get("order_id") or "")
        heartbeat(state, "MAKER_RESTING", open=True, order_id=order_id)

        while market.seconds_to_close > STOP_NEW_SECONDS:
            current = await exact_order(client, ticker=market.ticker, cid=cid)
            if current is None:
                heartbeat(state, "RESTING_ROW_DISAPPEARED", open=True)
                return {"status": "RESTING_ROW_DISAPPEARED", "hard_halt": True, "order_entry": True}
            filled = _row_fill(current)
            if filled > ZERO:
                final_row, cancel = await cancel_and_reconcile(client, ticker=market.ticker, cid=cid, prior=current)
                if final_row is None:
                    heartbeat(state, "PARTIAL_FILL_CANCEL_AMBIGUOUS", open=True, observed_fill=str(filled), cancel=cancel)
                    return {"status": "PARTIAL_FILL_CANCEL_AMBIGUOUS", "hard_halt": True, "order_entry": True}
                final_fill = _row_fill(final_row)
                if not ZERO < final_fill <= QTY:
                    heartbeat(state, "FILL_BOUND_INVALID", open=True, final_fill=str(final_fill))
                    return {"status": "FILL_BOUND_INVALID", "hard_halt": True, "order_entry": True}
                pos = await position_for(client, market.ticker)
                heartbeat(state, "MAKER_FILL_CONFIRMED", open=True, filled_quantity=str(final_fill),
                          position=str(pos), final_order=dict(final_row), cancel=cancel)
                emit("MAKER_FILL", ticker=market.ticker, side=signal.outcome, price=str(signal.price),
                     filled_quantity=str(final_fill), position=str(pos), order_entry=True)
                settled = await wait_for_settlement(client, market=market, state=state,
                                                    balance_before_cents=balance_before)
                return {**settled, "order_entry": True, "filled_quantity": str(final_fill),
                        "entry_outcome": signal.outcome, "entry_price": str(signal.price),
                        "hard_halt": bool(settled.get("hard_halt"))}

            prints2, yes2, no2 = await public_snapshot(http, market.ticker)
            tape.ingest(prints2)
            next_signal = signal_from(tape, yes_bid=yes2, no_bid=no2)
            reason = None
            if next_signal is None:
                reason = "SIGNAL_GONE"
            elif next_signal.outcome != signal.outcome:
                reason = "SIGNAL_REVERSED"
            elif next_signal.price != signal.price:
                reason = "BEST_BID_CHANGED"
            if reason:
                final_row, cancel = await cancel_and_reconcile(client, ticker=market.ticker, cid=cid, prior=current)
                if final_row is None:
                    heartbeat(state, "PULL_RECONCILE_AMBIGUOUS", open=True, pull_reason=reason, cancel=cancel)
                    return {"status": "PULL_RECONCILE_AMBIGUOUS", "hard_halt": True, "order_entry": True}
                if _row_fill(final_row) > ZERO:
                    fill = _row_fill(final_row)
                    pos = await position_for(client, market.ticker)
                    heartbeat(state, "MAKER_FILL_CONFIRMED_ON_PULL", open=True, filled_quantity=str(fill),
                              position=str(pos), pull_reason=reason, final_order=dict(final_row), cancel=cancel)
                    settled = await wait_for_settlement(client, market=market, state=state,
                                                        balance_before_cents=balance_before)
                    return {**settled, "order_entry": True, "filled_quantity": str(fill),
                            "entry_outcome": signal.outcome, "entry_price": str(signal.price),
                            "hard_halt": bool(settled.get("hard_halt"))}
                heartbeat(state, "ZERO_FILL_PULLED", open=False, pull_reason=reason, cancel=cancel)
                break
            await asyncio.sleep(POLL_SECONDS)
        else:
            current = await exact_order(client, ticker=market.ticker, cid=cid)
            if current is not None:
                final_row, cancel = await cancel_and_reconcile(client, ticker=market.ticker, cid=cid, prior=current)
                if final_row is None:
                    heartbeat(state, "STOP_TIME_CANCEL_AMBIGUOUS", open=True, cancel=cancel)
                    return {"status": "STOP_TIME_CANCEL_AMBIGUOUS", "hard_halt": True, "order_entry": True}
                if _row_fill(final_row) > ZERO:
                    fill = _row_fill(final_row)
                    settled = await wait_for_settlement(client, market=market, state=state,
                                                        balance_before_cents=balance_before)
                    return {**settled, "order_entry": True, "filled_quantity": str(fill),
                            "entry_outcome": signal.outcome, "entry_price": str(signal.price),
                            "hard_halt": bool(settled.get("hard_halt"))}
                heartbeat(state, "STOP_TIME_ZERO_FILL", open=False, cancel=cancel)
            return {"status": "WINDOW_ENDED_ZERO_FILL", "hard_halt": False, "order_entry": True,
                    "ticker": market.ticker}

        # Signal changed, quote was pulled with zero fill. Keep observing the
        # same market, but the release owns one client id per market. A second
        # order would reuse the ID, so deliberately stop this market instead of
        # adding an unqualified multi-quote lifecycle.
        return {"status": "WINDOW_QUOTE_PULLED_ZERO_FILL", "hard_halt": False,
                "order_entry": True, "ticker": market.ticker}

    return {"status": "WINDOW_NO_ELIGIBLE_SIGNAL", "hard_halt": False, "order_entry": False,
            "ticker": market.ticker, "last_signal": None if last_signal is None else last_signal.as_dict()}


async def run() -> dict[str, Any]:
    """Run the bounded q1 Policy-D session; never auto-restart."""
    existing = read_state()
    if existing and bool(existing.get("open")):
        result = {"status": "OPEN_DURABLE_STATE_BLOCKS_NEW_SESSION", "hard_halt": True,
                  "order_entry": False, "state": existing}
        print("KALSHI_POLICY_D_V1_RUNTIME=" + json.dumps(result, separators=(",", ":"), default=str), flush=True)
        return result

    readiness = await authenticated_readiness()
    if not live_armed():
        result = {**readiness, "strategy": "KXBTC15M_POLICY_D_30_10PCT_30C_Q1_HOLD_TO_SETTLEMENT"}
        print("KALSHI_POLICY_D_V1_RUNTIME=" + json.dumps(result, separators=(",", ":"), default=str), flush=True)
        return result
    if readiness["status"] != "DISARMED_READY" or readiness["kxbtc15m"]["orders"] or readiness["kxbtc15m"]["positions"]:
        return {"status": "LIVE_PREFLIGHT_EXPOSURE_BLOCKS", "hard_halt": True,
                "order_entry": False, "readiness": readiness}

    approval = os.environ[ARM_ID_ENV]
    deadline = time.monotonic() + max_session_seconds()
    client = KalshiClient(base_url=PROD_REST)
    results: list[dict[str, Any]] = []
    seen_markets: set[str] = set()
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True,
                                 headers={"User-Agent": "arb-kalshi-btc15m-policy-d-v1"}) as http:
        try:
            while len(results) < max_windows() and time.monotonic() < deadline:
                exposure = await series_exposure(client)
                if exposure["orders"] or exposure["positions"]:
                    result = {"status": "UNEXPECTED_SERIES_EXPOSURE_BETWEEN_WINDOWS", "hard_halt": True,
                              "order_entry": False, "exposure": exposure}
                    results.append(result)
                    break
                market = await discover_current_market(http)
                if market is None or market.ticker in seen_markets:
                    await asyncio.sleep(DISCOVERY_SECONDS)
                    continue
                seen_markets.add(market.ticker)
                window_no = len(results) + 1
                result = await run_market(client=client, http=http, market=market,
                                          window_no=window_no, approval=approval)
                results.append(result)
                emit("WINDOW_RESULT", window_no=window_no, ticker=market.ticker, result=result,
                     order_entry=bool(result.get("order_entry")))
                if result.get("hard_halt"):
                    break
            final_exposure = await series_exposure(client)
            final_balance = await balance_cents(client)
            status = "SESSION_HARD_HALTED" if any(bool(r.get("hard_halt")) for r in results) else "SESSION_COMPLETE"
            result = {
                "status": status, "windows_attempted": len(results), "results": results,
                "final_balance_cents": str(final_balance), "final_kxbtc15m": final_exposure,
                "order_entry": any(bool(r.get("order_entry")) for r in results),
                "maker_rebates_modeled": False, "liquidity_rewards_modeled": False,
            }
            print("KALSHI_POLICY_D_V1_RUNTIME=" + json.dumps(result, separators=(",", ":"), default=str), flush=True)
            return result
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(run())
