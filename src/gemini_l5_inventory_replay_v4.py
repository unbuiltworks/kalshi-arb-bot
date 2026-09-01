"""Offline V4 replay for the l5-style Gemini inventory strategy.

V4 changes the objective from "keep buying the favored side" to "improve the
portfolio".  It replays an existing public V3 capture and never authenticates or
submits orders.

Policy:
- open at most one fresh directional unit at a time;
- never add more of the same side while residual inventory exists;
- only quote the complementary side when the completed pair preserves a configured
  locked edge after modeled Gemini maker fees;
- after a profitable pair is completed, a new q=1 directional unit may be opened
  while there is enough time remaining;
- cap the number of completed pairs per market and refuse very expensive fresh
  directional entries.

This is a shadow replay, not realized or executable P&L.  Tape-touch fills still
ignore queue position; strict fills require the top of book to move through the
resting quote.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from decimal import Decimal as D
import json
from pathlib import Path
from typing import Any

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_l5_inventory_policy import inventory_decision, locked_pair_edge
from src.gemini_l5_threshold_model import plan_threshold_quotes

ONE = D("1")
Q1 = D("1")
DEFAULT_PAIR_FLOOR = D("0.03")
DEFAULT_MAX_DIRECTIONAL_PRICE = D("0.80")
DEFAULT_NEW_INVENTORY_CUTOFF_S = 45.0
DEFAULT_MAX_PAIRS = 3


@dataclass(frozen=True)
class PairRecord:
    held_side: str
    held_price: str
    completion_side: str
    completion_price: str
    held_fee: str
    completion_fee: str
    locked_edge_after_fees: str


@dataclass(frozen=True)
class FillRecord:
    side: str
    price: str
    fee: str
    evidence: str
    role: str


@dataclass
class V4Ledger:
    label: str
    pair_floor: D = DEFAULT_PAIR_FLOOR
    max_directional_price: D = DEFAULT_MAX_DIRECTIONAL_PRICE
    new_inventory_cutoff_s: float = DEFAULT_NEW_INVENTORY_CUTOFF_S
    max_pairs: int = DEFAULT_MAX_PAIRS
    yes_qty: D = D("0")
    no_qty: D = D("0")
    yes_cost: D = D("0")
    no_cost: D = D("0")
    maker_fees: D = D("0")
    residual_side: str | None = None
    residual_price: D | None = None
    residual_fee: D | None = None
    quote_side: str | None = None
    quote_price: D | None = None
    quote_mode: str = "NONE"
    quote_reason: str = ""
    pairs: list[PairRecord] = field(default_factory=list)
    fills: list[FillRecord] = field(default_factory=list)
    blocked_expensive_directional: int = 0
    blocked_unprofitable_completion: int = 0

    @property
    def balanced(self) -> bool:
        return self.residual_side is None

    @property
    def matched_qty(self) -> D:
        return D(len(self.pairs))

    @property
    def residual_qty(self) -> D:
        return D("0") if self.residual_side is None else Q1

    @property
    def residual_acquisition_cost(self) -> D:
        if self.residual_side is None or self.residual_price is None or self.residual_fee is None:
            return D("0")
        return self.residual_price + self.residual_fee

    def _clear_quote(self, mode: str, reason: str) -> None:
        self.quote_side = None
        self.quote_price = None
        self.quote_mode = mode
        self.quote_reason = reason

    def refresh_quote(self, *, bid: D, ask: D, seconds_to_expiry: float, tick: D) -> None:
        bid, ask, tick = D(str(bid)), D(str(ask)), D(str(tick))
        if not (D("0") < bid < ask < ONE):
            self._clear_quote("INVALID_BOOK", "Invalid YES top of book.")
            return

        if self.residual_side is not None:
            if self.residual_price is None or self.residual_fee is None:
                self._clear_quote("HOLD_RESIDUAL_NO_COST_BASIS", "Residual cost basis missing.")
                return
            if self.residual_side == "YES":
                resting = ONE - ask
                passive_ceiling = ONE - bid - tick
            else:
                resting = bid
                passive_ceiling = ask - tick
            decision = inventory_decision(
                yes_qty=self.yes_qty,
                no_qty=self.no_qty,
                residual_unit_price=self.residual_price,
                residual_unit_fee=self.residual_fee,
                complementary_resting_quote=resting,
                complementary_passive_ceiling=passive_ceiling,
                seconds_to_expiry=seconds_to_expiry,
                new_inventory_cutoff_s=self.new_inventory_cutoff_s,
                pair_floor=self.pair_floor,
                price_increment=tick,
            )
            if decision.mode != "PAIR_COMPLETE" or decision.target_side is None or decision.quote is None:
                if decision.mode == "WAIT_FOR_COMPLEMENT":
                    self.blocked_unprofitable_completion += 1
                self._clear_quote(decision.mode, decision.reason)
                return
            self.quote_side = decision.target_side
            self.quote_price = decision.quote
            self.quote_mode = decision.mode
            self.quote_reason = decision.reason
            return

        if len(self.pairs) >= self.max_pairs:
            self._clear_quote("PAIR_CAP_REACHED", "Per-market completed-pair cap reached.")
            return
        if seconds_to_expiry <= self.new_inventory_cutoff_s:
            self._clear_quote("FREEZE_LATE_FLAT", "Do not open fresh residual inventory near expiry.")
            return

        plan = plan_threshold_quotes(bid, ask, price_increment=tick)
        if plan.favored_side is None:
            self._clear_quote("NO_DIRECTIONAL_SIGNAL", "No side clears the threshold ladder.")
            return
        quote = plan.yes_quote if plan.favored_side == "YES" else plan.no_quote
        if quote > self.max_directional_price:
            self.blocked_expensive_directional += 1
            self._clear_quote(
                "DIRECTIONAL_PRICE_CAP",
                "Fresh directional quote exceeds the configured naked-risk price cap.",
            )
            return
        self.quote_side = plan.favored_side
        self.quote_price = quote
        self.quote_mode = "ACCUMULATE_Q1"
        self.quote_reason = "Balanced inventory; permit one fresh q=1 directional maker unit."

    def fill(self, *, side: str, price: D, evidence: str) -> bool:
        side = side.upper()
        price = D(str(price))
        if side not in {"YES", "NO"}:
            raise ValueError(side)
        if self.residual_side is not None and side == self.residual_side:
            # V4's central risk rule: never increase naked inventory on the same side.
            return False

        fee = gemini_predictions_maker_fee(price, Q1)
        if side == "YES":
            self.yes_qty += Q1
            self.yes_cost += price
        else:
            self.no_qty += Q1
            self.no_cost += price
        self.maker_fees += fee

        if self.residual_side is None:
            self.residual_side = side
            self.residual_price = price
            self.residual_fee = fee
            role = "DIRECTIONAL_OPEN"
        else:
            held_side = self.residual_side
            held_price = self.residual_price
            held_fee = self.residual_fee
            assert held_price is not None and held_fee is not None
            edge = locked_pair_edge(held_price, held_fee, price)
            if edge < self.pair_floor:
                # Defensive invariant. Quote planning should make this unreachable.
                if side == "YES":
                    self.yes_qty -= Q1
                    self.yes_cost -= price
                else:
                    self.no_qty -= Q1
                    self.no_cost -= price
                self.maker_fees -= fee
                return False
            self.pairs.append(
                PairRecord(
                    held_side=held_side,
                    held_price=str(held_price),
                    completion_side=side,
                    completion_price=str(price),
                    held_fee=str(held_fee),
                    completion_fee=str(fee),
                    locked_edge_after_fees=str(edge),
                )
            )
            self.residual_side = None
            self.residual_price = None
            self.residual_fee = None
            role = "PAIR_COMPLETION"

        self.fills.append(FillRecord(side, str(price), str(fee), evidence, role))
        return True

    def summary(self) -> dict[str, Any]:
        locked = sum((D(p.locked_edge_after_fees) for p in self.pairs), D("0"))
        return {
            "label": self.label,
            "fills": len(self.fills),
            "fill_chronology": [asdict(f) for f in self.fills],
            "completed_pairs": len(self.pairs),
            "pair_records": [asdict(p) for p in self.pairs],
            "locked_edge_total_after_fees": str(locked),
            "yes_qty": str(self.yes_qty),
            "no_qty": str(self.no_qty),
            "yes_cost": str(self.yes_cost),
            "no_cost": str(self.no_cost),
            "maker_fees": str(self.maker_fees),
            "residual_side": self.residual_side,
            "residual_qty": str(self.residual_qty),
            "residual_acquisition_cost": str(self.residual_acquisition_cost),
            "active_quote_side": self.quote_side,
            "active_quote_price": str(self.quote_price) if self.quote_price is not None else None,
            "active_quote_mode": self.quote_mode,
            "blocked_expensive_directional": self.blocked_expensive_directional,
            "blocked_unprofitable_completion": self.blocked_unprofitable_completion,
            "pair_floor_after_fees": str(self.pair_floor),
            "classification": "SHADOW_REPLAY_NOT_REALIZED",
        }


@dataclass
class V4Market:
    ticker: str
    symbol: str
    asset: str
    horizon: str
    price_increment: D = D("0.01")
    tape: V4Ledger = field(default_factory=lambda: V4Ledger("TAPE_TOUCH_OPTIMISTIC_V4"))
    strict: V4Ledger = field(default_factory=lambda: V4Ledger("STRICT_TRADE_THROUGH_V4"))
    last_bid: D | None = None
    last_ask: D | None = None
    seconds_to_expiry: float = 0.0
    book_updates: int = 0
    trade_events: int = 0

    def _refresh(self) -> None:
        if self.last_bid is None or self.last_ask is None:
            return
        for ledger in (self.tape, self.strict):
            ledger.refresh_quote(
                bid=self.last_bid,
                ask=self.last_ask,
                seconds_to_expiry=self.seconds_to_expiry,
                tick=self.price_increment,
            )

    def on_book(self, *, bid: D, ask: D, seconds_to_expiry: float) -> None:
        bid, ask = D(str(bid)), D(str(ask))
        # Strict fill credit is checked against the previously resting quote before
        # repricing on the new public book.
        if self.strict.quote_side == "YES" and self.strict.quote_price is not None:
            if ask < self.strict.quote_price:
                self.strict.fill(side="YES", price=self.strict.quote_price, evidence="ASK_TRADED_THROUGH")
        elif self.strict.quote_side == "NO" and self.strict.quote_price is not None:
            if bid > ONE - self.strict.quote_price:
                self.strict.fill(
                    side="NO",
                    price=self.strict.quote_price,
                    evidence="BID_TRADED_THROUGH_NO_EQUIV",
                )

        self.last_bid = bid
        self.last_ask = ask
        self.seconds_to_expiry = max(0.0, float(seconds_to_expiry))
        self.book_updates += 1
        self._refresh()

    def on_trade(self, *, price: D, quantity: D, buyer_maker: bool) -> None:
        self.trade_events += 1
        price, quantity = D(str(price)), D(str(quantity))
        # q=1 shadow requires at least one contract of public tape at the quote.
        if quantity < Q1 or self.tape.quote_side is None or self.tape.quote_price is None:
            return
        filled = False
        if self.tape.quote_side == "YES" and buyer_maker and price <= self.tape.quote_price:
            filled = self.tape.fill(
                side="YES",
                price=self.tape.quote_price,
                evidence="TRADE_TAPE_BUYER_MAKER",
            )
        elif self.tape.quote_side == "NO" and not buyer_maker and price >= ONE - self.tape.quote_price:
            filled = self.tape.fill(
                side="NO",
                price=self.tape.quote_price,
                evidence="TRADE_TAPE_SELLER_MAKER_NO_EQUIV",
            )
        if filled:
            # A directional fill immediately changes the objective to pair completion.
            self._refresh()

    def summary(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "symbol": self.symbol,
            "asset": self.asset,
            "horizon": self.horizon,
            "book_updates": self.book_updates,
            "trade_events": self.trade_events,
            "tape_touch": self.tape.summary(),
            "strict_trade_through": self.strict.summary(),
        }


def replay_capture(path: Path) -> dict[str, Any]:
    markets: dict[str, V4Market] = {}
    source_rows = 0
    source_plans = 0
    source_trades = 0

    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        source_rows += 1
        try:
            row = json.loads(raw)
        except Exception:
            continue
        row_type = row.get("type")
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        market = markets.get(ticker)
        if market is None:
            market = V4Market(
                ticker=ticker,
                symbol=str(row.get("symbol") or ""),
                asset=str(row.get("asset") or ""),
                horizon=str(row.get("horizon") or ""),
            )
            markets[ticker] = market

        if row_type == "SHADOW_PLAN":
            try:
                market.on_book(
                    bid=D(str(row["yes_bid"])),
                    ask=D(str(row["yes_ask"])),
                    seconds_to_expiry=float(row.get("seconds_to_expiry") or 0.0),
                )
                source_plans += 1
            except Exception:
                continue
        elif row_type == "PUBLIC_TRADE":
            try:
                market.on_trade(
                    price=D(str(row["price"])),
                    quantity=D(str(row["quantity"])),
                    buyer_maker=bool(row["buyer_maker"]),
                )
                source_trades += 1
            except Exception:
                continue

    def aggregate(key: str) -> dict[str, Any]:
        ledgers = [getattr(m, key) for m in markets.values()]
        pair_records = [p for l in ledgers for p in l.pairs]
        locked = sum((D(p.locked_edge_after_fees) for p in pair_records), D("0"))
        residual_cost = sum((l.residual_acquisition_cost for l in ledgers), D("0"))
        return {
            "fills": sum(len(l.fills) for l in ledgers),
            "markets_with_fills": sum(1 for l in ledgers if l.fills),
            "completed_pairs": len(pair_records),
            "markets_with_completed_pairs": sum(1 for l in ledgers if l.pairs),
            "locked_edge_total_after_fees": str(locked),
            "average_locked_edge_per_pair_after_fees": (
                str(locked / D(len(pair_records))) if pair_records else None
            ),
            "residual_markets": sum(1 for l in ledgers if l.residual_side is not None),
            "residual_qty_total": str(sum((l.residual_qty for l in ledgers), D("0"))),
            "residual_acquisition_cost_total": str(residual_cost),
            "blocked_expensive_directional": sum(l.blocked_expensive_directional for l in ledgers),
            "blocked_unprofitable_completion": sum(l.blocked_unprofitable_completion for l in ledgers),
        }

    return {
        "type": "GEMINI_L5_INVENTORY_REPLAY_V4",
        "classification": "SHADOW_REPLAY_NOT_REALIZED",
        "source": str(path),
        "source_rows": source_rows,
        "source_plans": source_plans,
        "source_trades": source_trades,
        "markets": len(markets),
        "pair_floor_after_fees": str(DEFAULT_PAIR_FLOOR),
        "max_naked_units_per_market": "1",
        "max_completed_pairs_per_market": DEFAULT_MAX_PAIRS,
        "max_directional_entry_price": str(DEFAULT_MAX_DIRECTIONAL_PRICE),
        "tape_touch_warning": "Optimistic: public trade at quote ignores queue position.",
        "strict_warning": "Conservative: requires top of book to move fully through resting quote.",
        "tape_touch": aggregate("tape"),
        "strict_trade_through": aggregate("strict"),
        "markets_detail": [m.summary() for m in markets.values()],
        "realized_pnl": None,
        "order_entry": False,
        "credentials_loaded": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a V3 Gemini l5 capture with pair-first V4 policy")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary = replay_capture(args.input)
    text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
