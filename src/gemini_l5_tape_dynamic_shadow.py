"""Read-only Gemini l5 dynamic inventory shadow driven by public WebSocket trade tape.

No credentials, balances, order placement, cancellation, or account streams are used.
The probe:
- discovers active BTC/ETH 5m + 15m prediction contracts via public REST;
- subscribes to public bookTicker + trade streams;
- applies the existing 60/70/80 l5 threshold ladder;
- keeps virtual inventory when the favored side flips;
- credits two shadow fill ledgers:
  * tape_touch: a public maker-side trade prints at the virtual quote;
  * strict_trade_through: subsequent top-of-book moves fully through the quote.
Neither ledger is executable or realized P&L; tape_touch ignores queue position and is
therefore intentionally labeled optimistic.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import time
from typing import Any

import httpx
import websockets

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_l5_threshold_model import maker_rebate_estimate, plan_threshold_quotes
from src.gemini_l5_threshold_shadow import Event, discover

WS_URL = "wss://ws.gemini.com"
ONE = D("1")
MAX_UNITS_PER_OUTCOME = D("3")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class VirtualFill:
    side: str
    price: D
    quantity: D
    maker_fee: D
    observed_at: str
    evidence: str


@dataclass
class Ledger:
    label: str
    yes_qty: D = D("0")
    no_qty: D = D("0")
    yes_cost: D = D("0")
    no_cost: D = D("0")
    maker_fees: D = D("0")
    modeled_rebate_cent_rounded: D = D("0")
    fills: list[VirtualFill] = field(default_factory=list)

    def capacity(self, side: str) -> D:
        held = self.yes_qty if side == "YES" else self.no_qty
        return max(D("0"), MAX_UNITS_PER_OUTCOME - held)

    def fill(self, side: str, price: D, quantity: D, observed_at: str, evidence: str) -> D:
        quantity = min(D(str(quantity)), self.capacity(side))
        if quantity <= 0:
            return D("0")
        price = D(str(price))
        fee = gemini_predictions_maker_fee(price, quantity)
        _, rebate = maker_rebate_estimate(price, quantity)
        if side == "YES":
            self.yes_qty += quantity
            self.yes_cost += price * quantity
        elif side == "NO":
            self.no_qty += quantity
            self.no_cost += price * quantity
        else:
            raise ValueError(side)
        self.maker_fees += fee
        self.modeled_rebate_cent_rounded += rebate
        self.fills.append(VirtualFill(side, price, quantity, fee, observed_at, evidence))
        return quantity

    @property
    def yes_vwap(self) -> D | None:
        return self.yes_cost / self.yes_qty if self.yes_qty else None

    @property
    def no_vwap(self) -> D | None:
        return self.no_cost / self.no_qty if self.no_qty else None

    @property
    def matched_qty(self) -> D:
        return min(self.yes_qty, self.no_qty)

    @property
    def matched_pair_cost(self) -> D | None:
        if not self.matched_qty or self.yes_vwap is None or self.no_vwap is None:
            return None
        return self.yes_vwap + self.no_vwap

    @property
    def residual_side(self) -> str | None:
        if self.yes_qty > self.no_qty:
            return "YES"
        if self.no_qty > self.yes_qty:
            return "NO"
        return None

    @property
    def residual_qty(self) -> D:
        return abs(self.yes_qty - self.no_qty)

    def payoff(self, resolution: str) -> D:
        payout = self.yes_qty if resolution == "YES" else self.no_qty
        return payout - self.yes_cost - self.no_cost - self.maker_fees

    def summary(self) -> dict[str, Any]:
        pair = self.matched_pair_cost
        yes_payoff = self.payoff("YES")
        no_payoff = self.payoff("NO")
        return {
            "label": self.label,
            "fills": len(self.fills),
            "fill_chronology": [
                {
                    "side": f.side,
                    "price": str(f.price),
                    "quantity": str(f.quantity),
                    "maker_fee": str(f.maker_fee),
                    "observed_at": f.observed_at,
                    "evidence": f.evidence,
                }
                for f in self.fills
            ],
            "yes_qty": str(self.yes_qty),
            "no_qty": str(self.no_qty),
            "yes_vwap": str(self.yes_vwap) if self.yes_vwap is not None else None,
            "no_vwap": str(self.no_vwap) if self.no_vwap is not None else None,
            "matched_qty": str(self.matched_qty),
            "matched_pair_cost": str(pair) if pair is not None else None,
            "locked_gross_per_pair_before_fees": str(ONE - pair) if pair is not None else None,
            "residual_side": self.residual_side,
            "residual_qty": str(self.residual_qty),
            "maker_fees": str(self.maker_fees),
            "modeled_maker_rebate_cent_rounded": str(self.modeled_rebate_cent_rounded),
            "trading_payoff_if_yes": str(yes_payoff),
            "trading_payoff_if_no": str(no_payoff),
            "worst_case_trading_payoff": str(min(yes_payoff, no_payoff)),
            "classification": "SHADOW_NOT_REALIZED",
        }


@dataclass
class MarketState:
    event: Event
    last_bid: D | None = None
    last_ask: D | None = None
    favored_side: str | None = None
    target_units: D = D("0")
    yes_quote: D | None = None
    no_quote: D | None = None
    side_switches: int = 0
    threshold_updates: int = 0
    book_updates: int = 0
    trade_events: int = 0
    tape: Ledger = field(default_factory=lambda: Ledger("TAPE_TOUCH_OPTIMISTIC"))
    strict: Ledger = field(default_factory=lambda: Ledger("STRICT_TRADE_THROUGH"))

    def _desired_gap(self, ledger: Ledger, side: str) -> D:
        held = ledger.yes_qty if side == "YES" else ledger.no_qty
        return max(D("0"), self.target_units - held)

    def on_book(self, bid: D, ask: D, observed_at: str) -> list[dict[str, Any]]:
        bid, ask = D(str(bid)), D(str(ask))
        if not (D("0") < bid < ask < ONE):
            return []
        emitted: list[dict[str, Any]] = []
        # Conservative fill credit requires the public top of book to move fully
        # through a previously resting quote.
        if self.yes_quote is not None and self._desired_gap(self.strict, "YES") > 0 and ask < self.yes_quote:
            q = self.strict.fill("YES", self.yes_quote, self._desired_gap(self.strict, "YES"), observed_at, "ASK_TRADED_THROUGH")
            if q:
                emitted.append({"type": "SHADOW_FILL", "ledger": self.strict.label, "side": "YES", "price": str(self.yes_quote), "quantity": str(q), "observed_at": observed_at})
        if self.no_quote is not None and self._desired_gap(self.strict, "NO") > 0:
            yes_bid_equiv = ONE - self.no_quote
            if bid > yes_bid_equiv:
                q = self.strict.fill("NO", self.no_quote, self._desired_gap(self.strict, "NO"), observed_at, "BID_TRADED_THROUGH_NO_EQUIV")
                if q:
                    emitted.append({"type": "SHADOW_FILL", "ledger": self.strict.label, "side": "NO", "price": str(self.no_quote), "quantity": str(q), "observed_at": observed_at})

        previous_side = self.favored_side
        plan = plan_threshold_quotes(bid, ask, price_increment=self.event.price_increment)
        self.last_bid, self.last_ask = bid, ask
        self.book_updates += 1
        self.favored_side = plan.favored_side
        self.target_units = D(str(plan.shadow_target_units))
        self.yes_quote = plan.yes_quote if plan.favored_side == "YES" else None
        self.no_quote = plan.no_quote if plan.favored_side == "NO" else None
        if plan.favored_side:
            self.threshold_updates += 1
            if previous_side and previous_side != plan.favored_side:
                self.side_switches += 1
        emitted.append({
            "type": "SHADOW_PLAN",
            "observed_at": observed_at,
            "ticker": self.event.ticker,
            "symbol": self.event.symbol,
            "asset": self.event.asset,
            "horizon": self.event.horizon,
            "seconds_to_expiry": round(self.event.seconds_to_expiry, 3),
            "yes_bid": str(bid),
            "yes_ask": str(ask),
            "favored_side": plan.favored_side,
            "favored_probability": str(plan.favored_probability),
            "target_units": plan.shadow_target_units,
            "yes_quote": str(plan.yes_quote),
            "no_quote": str(plan.no_quote),
            "pair_cost_if_both_fill": str(plan.pair_cost),
            "modeled_pair_edge_after_maker_fees": str(plan.modeled_edge),
            "read_only": True,
            "order_entry": False,
        })
        return emitted

    def on_trade(self, price: D, quantity: D, buyer_maker: bool, observed_at: str) -> list[dict[str, Any]]:
        self.trade_events += 1
        price, quantity = D(str(price)), D(str(quantity))
        emitted: list[dict[str, Any]] = []
        # A YES buy maker rests on the YES bid. If the public trade says the buyer
        # was the maker and prints at/below our limit, the tape is consistent with
        # our order filling. Queue position is unknown -> optimistic shadow only.
        if self.yes_quote is not None and buyer_maker and price <= self.yes_quote:
            gap = self._desired_gap(self.tape, "YES")
            q = self.tape.fill("YES", self.yes_quote, min(quantity, gap), observed_at, "TRADE_TAPE_BUYER_MAKER")
            if q:
                emitted.append({"type": "SHADOW_FILL", "ledger": self.tape.label, "side": "YES", "price": str(self.yes_quote), "quantity": str(q), "trade_price": str(price), "observed_at": observed_at})
        # Buying NO maps to selling YES at 1-no_quote on the normalized YES book.
        # A seller-maker execution therefore has buyer_maker=False.
        if self.no_quote is not None and not buyer_maker:
            yes_price_equiv = ONE - self.no_quote
            if price >= yes_price_equiv:
                gap = self._desired_gap(self.tape, "NO")
                q = self.tape.fill("NO", self.no_quote, min(quantity, gap), observed_at, "TRADE_TAPE_SELLER_MAKER_NO_EQUIV")
                if q:
                    emitted.append({"type": "SHADOW_FILL", "ledger": self.tape.label, "side": "NO", "price": str(self.no_quote), "quantity": str(q), "trade_price": str(price), "observed_at": observed_at})
        return emitted

    def summary(self) -> dict[str, Any]:
        return {
            "ticker": self.event.ticker,
            "symbol": self.event.symbol,
            "asset": self.event.asset,
            "horizon": self.event.horizon,
            "book_updates": self.book_updates,
            "trade_events": self.trade_events,
            "threshold_updates": self.threshold_updates,
            "side_switches": self.side_switches,
            "tape_touch": self.tape.summary(),
            "strict_trade_through": self.strict.summary(),
        }


def _parse_book_ticker(message: dict[str, Any]) -> tuple[D, D] | None:
    if "b" not in message or "a" not in message:
        return None
    try:
        return D(str(message["b"])), D(str(message["a"]))
    except Exception:
        return None


def _parse_trade(message: dict[str, Any]) -> tuple[D, D, bool] | None:
    if "p" not in message or "q" not in message or "m" not in message:
        return None
    try:
        maker = message["m"]
        if not isinstance(maker, bool):
            return None
        return D(str(message["p"])), D(str(message["q"])), maker
    except Exception:
        return None


async def capture(*, seconds: float, discover_s: float, output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, seconds)
    stats = {"messages": 0, "book_tickers": 0, "trades": 0, "errors": 0, "discoveries": 0, "subscriptions": 0}
    states: dict[str, MarketState] = {}
    subscribed: set[str] = set()
    last_discover = 0.0

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-l5-tape-shadow/2.0"}) as http:
        async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=2**20) as ws:
            with output.open("w", encoding="utf-8") as fh:
                while time.monotonic() < deadline:
                    now = time.monotonic()
                    if now - last_discover >= discover_s or not subscribed:
                        try:
                            events = await discover(http)
                            stats["discoveries"] += 1
                            desired_streams: list[str] = []
                            for event in events.values():
                                states.setdefault(event.symbol, MarketState(event))
                                desired_streams.extend([f"{event.symbol}@bookTicker", f"{event.symbol}@trade"])
                            new_streams = [stream for stream in desired_streams if stream not in subscribed]
                            if new_streams:
                                await ws.send(json.dumps({"id": str(stats["subscriptions"] + 1), "method": "SUBSCRIBE", "params": new_streams}))
                                subscribed.update(new_streams)
                                stats["subscriptions"] += len(new_streams)
                            last_discover = now
                        except Exception as exc:
                            stats["errors"] += 1
                            row = {"type": "SHADOW_DISCOVERY_ERROR", "observed_at": _iso(), "error_type": type(exc).__name__, "error": str(exc)[:240], "read_only": True, "order_entry": False}
                            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                            fh.flush()

                    timeout = min(1.0, max(0.05, deadline - time.monotonic()))
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        message = json.loads(raw)
                    except Exception:
                        stats["errors"] += 1
                        continue
                    if not isinstance(message, dict):
                        continue
                    stats["messages"] += 1
                    symbol = str(message.get("s") or "")
                    state = states.get(symbol)
                    if state is None:
                        # Subscription acknowledgements and lifecycle messages.
                        continue
                    observed_at = _iso()
                    emitted: list[dict[str, Any]] = []
                    trade = _parse_trade(message)
                    if trade is not None and "t" in message:
                        stats["trades"] += 1
                        emitted.extend(state.on_trade(*trade, observed_at))
                        emitted.append({"type": "PUBLIC_TRADE", "observed_at": observed_at, "ticker": state.event.ticker, "symbol": symbol, "price": str(trade[0]), "quantity": str(trade[1]), "buyer_maker": trade[2], "trade_id": message.get("t"), "read_only": True, "order_entry": False})
                    else:
                        book = _parse_book_ticker(message)
                        if book is not None:
                            stats["book_tickers"] += 1
                            emitted.extend(state.on_book(*book, observed_at))
                    for row in emitted:
                        row.setdefault("read_only", True)
                        row.setdefault("order_entry", False)
                        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
                    if emitted:
                        fh.flush()

    summary = {
        "type": "GEMINI_L5_TAPE_DYNAMIC_SHADOW_SUMMARY",
        **stats,
        "seconds_requested": seconds,
        "discover_s": discover_s,
        "markets": len(states),
        "markets_detail": [state.summary() for state in states.values()],
        "read_only": True,
        "order_entry": False,
        "realized_pnl": None,
        "tape_touch_warning": "Optimistic: public trade at quote ignores queue position.",
        "strict_warning": "Conservative: requires top of book to move fully through resting quote.",
    }
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=3600.0)
    parser.add_argument("--discover-s", type=float, default=10.0)
    parser.add_argument("--output", type=Path, default=Path("artifacts/gemini-l5-tape-dynamic-shadow.jsonl"))
    parser.add_argument("--summary-output", type=Path)
    args = parser.parse_args()
    summary = asyncio.run(capture(seconds=args.seconds, discover_s=args.discover_s, output=args.output))
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
