"""Live canary runner: frozen Policy D on Kalshi 15m crypto, tiny size.

Purpose: measure REAL fill rate and fill quality against the shadow
(MAKER_PRINT_PROXY) numbers, at a cost bounded hard by per-window and
per-session caps. This is a measurement instrument, not a scaled deployment.

Safety posture (all enforced in code, none overridable upward from CLI):
  - decision_core is the only thing that decides quotes; this file only
    reconciles orders to its Decision and can only TIGHTEN its caps.
  - action is always "buy"; maker quotes are post_only=True.
  - per-window worst-settlement loss cap: $1.00 (decision_core default).
  - session stops after --max-windows windows (default 5) => worst-case
    session loss is bounded by max_windows x $1 + fees.
  - dead-man: if market data goes stale > DEADMAN_STALE_S, cancel-all and
    halt that market. On any exit path (SIGINT, crash), cancel-all runs.
  - startup sweep cancels any resting orders left on our series by a
    previous crashed run before quoting anything.
  - default is DRY RUN (no orders placed, intents logged). --live is
    required to send orders, and KALSHI_ENV selects demo vs prod.

Accounting rule (user-mandated): incentives/rebates NEVER enter the edge or
P&L here. These series carry no incentive programs anyway; if that changes,
credits are tracked separately as incentive_pending, not in realized P&L.

Run (from repo root, .env populated):
    python3 -m src.kalshi_maker.live_canary                 # dry run
    python3 -m src.kalshi_maker.live_canary --live          # real orders, env per KALSHI_ENV
    python3 -m src.kalshi_maker.live_canary --live --max-windows 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx

from src.auth import KalshiClient
from src.kalshi_maker.decision_core import (
    Book,
    Decision,
    Inventory,
    QuoteIntent,
    Tape,
    completion_action,
    decide,
)

SERIES = ("KXBTC15M", "KXETH15M")

# Canary caps — may only be tightened relative to decision_core defaults.
CANARY_QUOTE_SIZE = 1          # contracts per quote (decision_core default is 5)
CANARY_MAX_SPEND = 3.0         # dollars per window (core default 10)
CANARY_MAX_WORST_LOSS = 1.0    # dollars per window (== core default, the ARB contract)
DEFAULT_MAX_WINDOWS = 5        # session stops itself after this many windows

POLL_SECONDS = 2.0
DISCOVER_SECONDS = 30.0
REQUOTE_MIN_INTERVAL_S = 3.0   # don't churn cancel/replace faster than this
DEADMAN_STALE_S = 15.0         # no fresh data for this long => cancel-all + halt market
SETTLE_POLL_SECONDS = 20.0

MAKER_FEE_RATE = 0.0175        # ceil-cent per Kalshi maker schedule (fallback if fill lacks fee)
TAKER_FEE_RATE = 0.07


def _now() -> float:
    return time.time()


def _ceil_cent_fee(rate: float, count: float, price: float) -> float:
    return math.ceil(rate * count * price * (1.0 - price) * 100.0) / 100.0


def _parse_iso(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class EventLog:
    """JSONL event log + stdout mirror."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def write(self, kind: str, **fields: Any) -> None:
        row = {"t": _now(), "kind": kind, **fields}
        line = json.dumps(row, separators=(",", ":"), default=str)
        self._fh.write(line + "\n")
        self._fh.flush()
        print(line, flush=True)

    def close(self) -> None:
        self._fh.close()


@dataclass
class RestingOrder:
    order_id: str
    side: str        # "Up" | "Down"
    price: float     # dollars, in the side's own scale
    size: float
    client_order_id: str


@dataclass
class MarketState:
    ticker: str
    close_ts: Optional[float]
    tape: Tape = field(default_factory=Tape)
    inventory: Inventory = field(default_factory=Inventory)
    resting: Dict[str, RestingOrder] = field(default_factory=dict)  # by order_id
    seen_trades: Set[str] = field(default_factory=set)
    seen_fills: Set[str] = field(default_factory=set)
    fees_paid: float = 0.0
    last_data_ts: float = field(default_factory=_now)
    last_requote_ts: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    quoted: bool = False           # did we ever place (or intend) a quote here
    completion_done: bool = False
    price_field_style: Optional[str] = None  # learned: "dollars" | "cents"


class Canary:
    def __init__(self, *, live: bool, max_windows: int, out_dir: Path,
                 series: tuple = SERIES) -> None:
        self.live = live
        self.max_windows = max_windows
        self.series = series
        self.markets: Dict[str, MarketState] = {}
        self.settling: Dict[str, MarketState] = {}
        self.windows_quoted = 0
        self.session_realized = 0.0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.log = EventLog(out_dir / f"canary_{stamp}.jsonl")
        self.client: Optional[KalshiClient] = None
        self.public = httpx.AsyncClient(timeout=10.0,
                                        headers={"User-Agent": "arb-canary/1.0"})
        from config.settings import ENV, REST_URL  # late import: reads .env
        self.env = ENV
        self.rest_url = REST_URL
        if live:
            self.client = KalshiClient()  # raises if key missing => fail fast

    # ---------------- public data ----------------

    async def discover(self) -> None:
        for series in self.series:
            try:
                resp = await self.public.get(
                    f"{self.rest_url}/markets",
                    params={"series_ticker": series, "status": "open", "limit": 100},
                )
            except httpx.HTTPError as exc:
                self.log.write("discover_error", series=series, error=type(exc).__name__)
                continue
            if resp.status_code != 200:
                continue
            for market in resp.json().get("markets", []):
                ticker = market.get("ticker")
                if not ticker or ticker in self.markets or ticker in self.settling:
                    continue
                if self.windows_quoted + len(self.markets) >= self.max_windows:
                    continue
                state = MarketState(ticker=ticker,
                                    close_ts=_parse_iso(market.get("close_time")))
                self.markets[ticker] = state
                self.log.write("window_tracked", market=ticker,
                               close_time=market.get("close_time"))

    async def refresh_tape(self, st: MarketState) -> None:
        resp = await self.public.get(f"{self.rest_url}/markets/trades",
                                     params={"ticker": st.ticker, "limit": 100})
        if resp.status_code != 200:
            return
        st.last_data_ts = _now()
        rows = resp.json().get("trades", [])
        for trade in reversed(rows):  # oldest first into the rolling tape
            tid = trade.get("trade_id")
            if not tid or tid in st.seen_trades:
                continue
            st.seen_trades.add(tid)
            taker = trade.get("taker_side")
            side = "Up" if taker == "yes" else "Down" if taker == "no" else None
            try:
                qty = float(trade.get("count_fp") or trade.get("count") or 0)
            except (TypeError, ValueError):
                qty = 0.0
            if side and qty > 0:
                st.tape.add_print(side, qty)

    async def read_book(self, st: MarketState) -> Book:
        resp = await self.public.get(f"{self.rest_url}/markets/{st.ticker}/orderbook",
                                     params={"depth": 1})
        if resp.status_code != 200:
            return Book(None, None)
        st.last_data_ts = _now()
        book = (resp.json() or {}).get("orderbook_fp") or {}

        def top(levels: Any) -> Optional[float]:
            if not levels:
                return None
            try:
                return max(float(price) for price, _qty in levels)
            except (TypeError, ValueError):
                return None

        return Book(up_bid=top(book.get("yes_dollars")),
                    down_bid=top(book.get("no_dollars")))

    # ---------------- order plumbing (live only) ----------------

    def _order_payload(self, st: MarketState, side: str, price: float,
                       count: int, post_only: bool, style: str) -> dict:
        payload: dict = {
            "ticker": st.ticker,
            "action": "buy",
            "side": "yes" if side == "Up" else "no",
            "type": "limit",
            "count": count,
            "client_order_id": str(uuid.uuid4()),
            "post_only": post_only,
        }
        # Books can show sub-cent prices (e.g. 0.997). Preserve precision in the
        # dollars style; for the integer-cents fallback FLOOR into [1, 99] —
        # flooring a buy below the bid only rests deeper, never crosses.
        if style == "dollars":
            key = "yes_price_dollars" if side == "Up" else "no_price_dollars"
            clamped = min(max(price, 0.001), 0.999)
            payload[key] = f"{clamped:.4f}".rstrip("0").rstrip(".")
        else:
            key = "yes_price" if side == "Up" else "no_price"
            payload[key] = min(max(int(price * 100), 1), 99)
        return payload

    async def place_order(self, st: MarketState, intent: QuoteIntent,
                          post_only: bool) -> Optional[RestingOrder]:
        count = int(intent.size)
        if count < 1:
            return None
        if not self.live:
            self.log.write("dry_run_order", market=st.ticker, side=intent.side,
                           price=intent.price, count=count, post_only=post_only,
                           reason=intent.reason)
            return None
        styles = [st.price_field_style] if st.price_field_style else ["dollars", "cents"]
        for style in styles:
            payload = self._order_payload(st, intent.side, intent.price, count,
                                          post_only, style)
            resp = await self.client.post("/portfolio/orders", json=payload)
            if resp.status_code in (200, 201):
                st.price_field_style = style
                order = (resp.json() or {}).get("order", {})
                oid = order.get("order_id") or order.get("id") or ""
                status = order.get("status", "")
                self.log.write("order_placed", market=st.ticker, side=intent.side,
                               price=intent.price, count=count, post_only=post_only,
                               order_id=oid, status=status, reason=intent.reason,
                               client_order_id=payload["client_order_id"])
                if oid and status not in ("canceled", "cancelled", "executed"):
                    return RestingOrder(oid, intent.side, intent.price, float(count),
                                        payload["client_order_id"])
                return None
            # ALWAYS capture the error body (V5.4 lesson: never lose a 400 body).
            body = resp.text[:500]
            self.log.write("order_rejected", market=st.ticker, side=intent.side,
                           price=intent.price, style=style,
                           status_code=resp.status_code, body=body)
            if resp.status_code != 400:
                break  # only 400 suggests a schema/style issue worth retrying
        return None

    async def cancel_order(self, st: MarketState, order: RestingOrder,
                           reason: str) -> None:
        if not self.live:
            return
        resp = await self.client.delete(f"/portfolio/orders/{order.order_id}")
        ok = resp.status_code in (200, 204)
        # 404 = already gone (filled or expired) — reconcile via fills poll.
        self.log.write("order_cancel", market=st.ticker, order_id=order.order_id,
                       ok=ok, status_code=resp.status_code, reason=reason,
                       body=None if ok else resp.text[:300])

    async def cancel_all(self, st: MarketState, reason: str) -> None:
        for order in list(st.resting.values()):
            await self.cancel_order(st, order, reason)
        st.resting.clear()

    async def startup_sweep(self) -> None:
        """Cancel any resting orders a previous crashed run left on our series."""
        if not self.live:
            return
        resp = await self.client.get("/portfolio/orders",
                                     params={"status": "resting", "limit": 200})
        if resp.status_code != 200:
            self.log.write("startup_sweep_error", status_code=resp.status_code,
                           body=resp.text[:300])
            return
        for order in resp.json().get("orders", []):
            ticker = order.get("ticker", "")
            if any(ticker.startswith(s) for s in self.series):
                oid = order.get("order_id") or order.get("id")
                if oid:
                    resp2 = await self.client.delete(f"/portfolio/orders/{oid}")
                    self.log.write("startup_sweep_cancel", market=ticker,
                                   order_id=oid, status_code=resp2.status_code)

    async def poll_fills(self, st: MarketState) -> None:
        if not self.live:
            return
        resp = await self.client.get("/portfolio/fills",
                                     params={"ticker": st.ticker, "limit": 100})
        if resp.status_code != 200:
            return
        for fill in resp.json().get("fills", []):
            fid = fill.get("trade_id") or fill.get("fill_id") or fill.get("id")
            if not fid or fid in st.seen_fills:
                continue
            st.seen_fills.add(fid)
            side = "Up" if fill.get("side") == "yes" else "Down"
            try:
                count = float(fill.get("count_fp") or fill.get("count") or 0)
            except (TypeError, ValueError):
                count = 0.0
            price_raw = (fill.get("yes_price_dollars") if side == "Up"
                         else fill.get("no_price_dollars"))
            try:
                price = float(price_raw)
            except (TypeError, ValueError):
                yp = fill.get("yes_price")
                price = (float(yp) / 100.0 if side == "Up"
                         else 1.0 - float(yp) / 100.0) if yp is not None else 0.0
            if count <= 0 or price <= 0:
                continue
            is_taker = bool(fill.get("is_taker"))
            fee_raw = fill.get("fee_dollars") or fill.get("fee")
            try:
                fee = float(fee_raw)
            except (TypeError, ValueError):
                fee = _ceil_cent_fee(TAKER_FEE_RATE if is_taker else MAKER_FEE_RATE,
                                     count, price)
            st.inventory.apply_fill(side, count, price)
            st.fees_paid += fee
            oid = fill.get("order_id", "")
            if oid in st.resting:
                order = st.resting[oid]
                order.size -= count
                if order.size <= 0:
                    st.resting.pop(oid, None)
            self.log.write("fill", market=st.ticker, side=side, count=count,
                           price=price, fee=fee, is_taker=is_taker, order_id=oid,
                           inv_up=st.inventory.up_qty, inv_down=st.inventory.down_qty,
                           spend=st.inventory.spend,
                           worst_loss=st.inventory.worst_settlement_loss())

    # ---------------- per-market step ----------------

    async def step_market(self, st: MarketState) -> None:
        now = _now()
        # Dead-man: stale data => flat and halted for this market.
        if now - st.last_data_ts > DEADMAN_STALE_S and not st.halted:
            st.halted = True
            st.halt_reason = "DEADMAN_STALE"
            self.log.write("deadman_halt", market=st.ticker,
                           stale_s=round(now - st.last_data_ts, 1))
            await self.cancel_all(st, "DEADMAN_STALE")
        try:
            await self.refresh_tape(st)
            await self.poll_fills(st)
            book = await self.read_book(st)
        except httpx.HTTPError as exc:
            self.log.write("data_error", market=st.ticker, error=type(exc).__name__)
            return
        if st.halted and now - st.last_data_ts <= DEADMAN_STALE_S \
                and st.halt_reason == "DEADMAN_STALE":
            st.halted = False
            st.halt_reason = ""
            self.log.write("deadman_recovered", market=st.ticker)

        seconds_to_expiry = (st.close_ts - now) if st.close_ts else None
        decision: Decision = decide(
            st.tape, book, st.inventory, seconds_to_expiry,
            halted=st.halted,
            quote_size=CANARY_QUOTE_SIZE,
            max_spend=CANARY_MAX_SPEND,
            max_worst_loss=CANARY_MAX_WORST_LOSS,
        )

        if decision.cancel_all:
            if st.resting:
                await self.cancel_all(st, decision.reason)
        elif now - st.last_requote_ts >= REQUOTE_MIN_INTERVAL_S:
            wanted = {(q.side, round(q.price, 2)) for q in decision.quotes}
            for oid, order in list(st.resting.items()):
                if (order.side, round(order.price, 2)) not in wanted:
                    await self.cancel_order(st, order, "REQUOTE")
                    st.resting.pop(oid, None)
            have_sides = {o.side for o in st.resting.values()}
            for quote in decision.quotes:
                if quote.side in have_sides:
                    continue
                if not st.quoted:
                    st.quoted = True
                    self.windows_quoted += 1
                    self.log.write("window_engaged", market=st.ticker,
                                   windows_quoted=self.windows_quoted,
                                   max_windows=self.max_windows)
                placed = await self.place_order(st, quote, post_only=True)
                if placed:
                    st.resting[placed.order_id] = placed
            st.last_requote_ts = now

        # Bounded defensive completion (taker), at most once per window.
        if not st.completion_done and seconds_to_expiry is not None:
            comp = completion_action(st.inventory, book, seconds_to_expiry,
                                     max_worst_loss=CANARY_MAX_WORST_LOSS)
            if comp is not None:
                st.completion_done = True
                self.log.write("completion_intent", market=st.ticker,
                               side=comp.side, price=comp.price, count=comp.size)
                await self.place_order(st, comp, post_only=False)

        # Window over: stop quoting, move to settlement watch.
        if seconds_to_expiry is not None and seconds_to_expiry <= 0:
            await self.cancel_all(st, "WINDOW_CLOSED")
            self.markets.pop(st.ticker, None)
            if st.inventory.spend > 0:
                self.settling[st.ticker] = st
                self.log.write("window_closed_settling", market=st.ticker,
                               spend=st.inventory.spend,
                               inv_up=st.inventory.up_qty,
                               inv_down=st.inventory.down_qty)
            else:
                self.log.write("window_closed_flat", market=st.ticker,
                               quoted=st.quoted)

    async def check_settlements(self) -> None:
        for ticker, st in list(self.settling.items()):
            try:
                resp = await self.public.get(f"{self.rest_url}/markets/{ticker}")
            except httpx.HTTPError:
                continue
            if resp.status_code != 200:
                continue
            result = (resp.json().get("market") or {}).get("result", "")
            if result not in ("yes", "no"):
                continue
            payout = st.inventory.up_qty if result == "yes" else st.inventory.down_qty
            realized = payout * 1.0 - st.inventory.spend - st.fees_paid
            self.session_realized += realized
            self.settling.pop(ticker, None)
            self.log.write("window_settled", market=ticker, result=result,
                           payout=payout, spend=st.inventory.spend,
                           fees=st.fees_paid, realized=round(realized, 4),
                           session_realized=round(self.session_realized, 4))

    # ---------------- main loop ----------------

    async def run(self) -> None:
        self.log.write("canary_start", live=self.live, env=self.env,
                       max_windows=self.max_windows, quote_size=CANARY_QUOTE_SIZE,
                       max_spend=CANARY_MAX_SPEND,
                       max_worst_loss=CANARY_MAX_WORST_LOSS, series=list(self.series))
        if self.live:
            status = await self.client.get_exchange_status()
            self.log.write("exchange_status", **{k: status.get(k) for k in
                                                 ("exchange_active", "trading_active")})
            await self.startup_sweep()
        last_discover = 0.0
        last_settle = 0.0
        try:
            while True:
                now = _now()
                if now - last_discover >= DISCOVER_SECONDS:
                    last_discover = now
                    await self.discover()
                for st in list(self.markets.values()):
                    await self.step_market(st)
                if now - last_settle >= SETTLE_POLL_SECONDS:
                    last_settle = now
                    await self.check_settlements()
                if (self.windows_quoted >= self.max_windows
                        and not self.markets and not self.settling):
                    self.log.write("session_complete",
                                   windows_quoted=self.windows_quoted,
                                   session_realized=round(self.session_realized, 4))
                    break
                if self.windows_quoted >= self.max_windows:
                    # No new windows; let live ones finish and settle.
                    self.markets = {k: v for k, v in self.markets.items() if v.quoted}
                await asyncio.sleep(POLL_SECONDS)
        finally:
            # Flat on every exit path.
            for st in list(self.markets.values()):
                await self.cancel_all(st, "SHUTDOWN")
            self.log.write("canary_end",
                           session_realized=round(self.session_realized, 4),
                           unsettled=list(self.settling.keys()))
            await self.public.aclose()
            if self.client:
                await self.client.close()
            self.log.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Policy D live canary (tiny, capped)")
    parser.add_argument("--live", action="store_true",
                        help="actually place orders (default: dry run, log intents only)")
    parser.add_argument("--max-windows", type=int, default=DEFAULT_MAX_WINDOWS)
    parser.add_argument("--out-dir", default="data/kalshi_canary")
    args = parser.parse_args()
    max_windows = min(args.max_windows, 10)  # hard ceiling regardless of CLI
    canary = Canary(live=args.live, max_windows=max_windows, out_dir=Path(args.out_dir))
    try:
        asyncio.run(canary.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
