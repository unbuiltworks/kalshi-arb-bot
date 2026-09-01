"""Read-only replay for incentive-aware two-sided Gemini Predictions market making.

Consumes public capture rows (book snapshots + public trades) and simulates a
conservative policy at the Gemini Liquidity Rewards minimum quote size (q=10):

- when flat, rest both YES and NO maker bids at the current best bid equivalents;
- once one side fills, stop adding that side and work only the complement;
- only complete a residual if the matched trade/settlement economics are non-negative
  after modeled Gemini maker fees (rebates never rescue an arb classification);
- estimate maker rebates separately as PROJECTED, never realized;
- track whether the resting quote shape would satisfy the public liquidity-reward
  size/spread rules, without estimating a payout share.

This module has no credentials and no order entry. Tape-touch fills are optimistic
because queue position is unknown. Strict/executable claims require a later live probe.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, asdict
from decimal import Decimal as D, ROUND_FLOOR
import json
from pathlib import Path
from typing import Any

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

ONE = D("1")
ZERO = D("0")
DEFAULT_QUOTE_QTY = D("10")
DEFAULT_MIN_TRADE_EDGE_PER_PAIR = D("0.00")
DEFAULT_MAX_RESIDUAL_QTY = D("10")
DEFAULT_LIQUIDITY_MAX_DISTANCE = D("0.10")
DEFAULT_REBATE_RATE = D("0.30")
DEFAULT_TAKER_RATE = D("0.07")
REBATE_MIN_PRICE = D("0.20")
REBATE_MAX_PRICE = D("0.80")


def _floor_cent(value: D) -> D:
    return (value * D("100")).to_integral_value(rounding=ROUND_FLOOR) / D("100")


def projected_maker_rebate(price: D, quantity: D) -> D:
    """Current published Gemini maker rebate estimate; projected only."""
    price, quantity = D(str(price)), D(str(quantity))
    if quantity <= ZERO or price < REBATE_MIN_PRICE or price > REBATE_MAX_PRICE:
        return ZERO
    raw = DEFAULT_REBATE_RATE * DEFAULT_TAKER_RATE * quantity * price * (ONE - price)
    return _floor_cent(raw)


@dataclass
class Fill:
    side: str
    price: D
    quantity: D
    fee: D
    projected_rebate: D
    evidence: str


@dataclass
class Pair:
    quantity: D
    yes_price: D
    no_price: D
    yes_fee: D
    no_fee: D
    trade_edge: D
    trade_edge_per_pair: D
    projected_rebate: D


@dataclass
class Ledger:
    quote_qty: D = DEFAULT_QUOTE_QTY
    min_trade_edge_per_pair: D = DEFAULT_MIN_TRADE_EDGE_PER_PAIR
    max_residual_qty: D = DEFAULT_MAX_RESIDUAL_QTY
    yes_qty: D = ZERO
    no_qty: D = ZERO
    yes_cost: D = ZERO
    no_cost: D = ZERO
    yes_fees: D = ZERO
    no_fees: D = ZERO
    yes_rebate_projected: D = ZERO
    no_rebate_projected: D = ZERO
    residual_side: str | None = None
    residual_qty: D = ZERO
    residual_unit_price: D | None = None
    residual_unit_fee: D | None = None
    residual_unit_projected_rebate: D | None = None
    yes_quote: D | None = None
    no_quote: D | None = None
    quote_size: D = ZERO
    pairs: list[Pair] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    book_updates: int = 0
    eligible_book_updates: int = 0
    blocked_unprofitable_completion: int = 0

    @property
    def flat(self) -> bool:
        return self.residual_side is None

    def _pair_edge(self, held_side: str, held_price: D, held_fee: D, comp_price: D, comp_qty: D) -> tuple[D, D]:
        comp_fee = gemini_predictions_maker_fee(comp_price, comp_qty)
        if held_side == "YES":
            raw = comp_qty - (held_price + comp_price) * comp_qty - held_fee - comp_fee
        else:
            raw = comp_qty - (comp_price + held_price) * comp_qty - comp_fee - held_fee
        return raw, comp_fee

    def on_book(self, yes_bid: D, yes_ask: D) -> None:
        yes_bid, yes_ask = D(str(yes_bid)), D(str(yes_ask))
        if not (ZERO < yes_bid < yes_ask < ONE):
            self.yes_quote = self.no_quote = None
            self.quote_size = ZERO
            return
        self.book_updates += 1
        midpoint = (yes_bid + yes_ask) / D("2")

        if self.flat:
            self.yes_quote = yes_bid
            self.no_quote = ONE - yes_ask
            self.quote_size = self.quote_qty
            yes_distance = midpoint - self.yes_quote
            no_mid = ONE - midpoint
            no_distance = no_mid - self.no_quote
            if (
                self.quote_qty >= D("10")
                and yes_distance <= DEFAULT_LIQUIDITY_MAX_DISTANCE
                and no_distance <= DEFAULT_LIQUIDITY_MAX_DISTANCE
            ):
                self.eligible_book_updates += 1
            return

        # Residual exists: only quote the missing side, capped at residual qty.
        self.quote_size = min(self.residual_qty, self.quote_qty)
        if self.residual_side == "YES":
            candidate = ONE - yes_ask
            edge, _ = self._pair_edge("YES", self.residual_unit_price or ZERO, self.residual_unit_fee or ZERO, candidate, self.quote_size)
            per_pair = edge / self.quote_size if self.quote_size > ZERO else ZERO
            if per_pair >= self.min_trade_edge_per_pair:
                self.yes_quote = None
                self.no_quote = candidate
            else:
                self.yes_quote = self.no_quote = None
                self.blocked_unprofitable_completion += 1
        else:
            candidate = yes_bid
            edge, _ = self._pair_edge("NO", self.residual_unit_price or ZERO, self.residual_unit_fee or ZERO, candidate, self.quote_size)
            per_pair = edge / self.quote_size if self.quote_size > ZERO else ZERO
            if per_pair >= self.min_trade_edge_per_pair:
                self.yes_quote = candidate
                self.no_quote = None
            else:
                self.yes_quote = self.no_quote = None
                self.blocked_unprofitable_completion += 1

    def _record_open(self, side: str, price: D, quantity: D, evidence: str) -> None:
        fee = gemini_predictions_maker_fee(price, quantity)
        rebate = projected_maker_rebate(price, quantity)
        if side == "YES":
            self.yes_qty += quantity; self.yes_cost += price * quantity; self.yes_fees += fee; self.yes_rebate_projected += rebate
        else:
            self.no_qty += quantity; self.no_cost += price * quantity; self.no_fees += fee; self.no_rebate_projected += rebate
        self.fills.append(Fill(side, price, quantity, fee, rebate, evidence))
        self.residual_side = side
        self.residual_qty = quantity
        self.residual_unit_price = price
        self.residual_unit_fee = fee
        self.residual_unit_projected_rebate = rebate

    def _record_completion(self, side: str, price: D, quantity: D, evidence: str) -> None:
        held_side = self.residual_side
        held_price = self.residual_unit_price
        held_fee = self.residual_unit_fee
        held_rebate = self.residual_unit_projected_rebate or ZERO
        assert held_side is not None and held_price is not None and held_fee is not None
        edge, fee = self._pair_edge(held_side, held_price, held_fee, price, quantity)
        per_pair = edge / quantity
        if per_pair < self.min_trade_edge_per_pair:
            return
        rebate = projected_maker_rebate(price, quantity)
        if side == "YES":
            self.yes_qty += quantity; self.yes_cost += price * quantity; self.yes_fees += fee; self.yes_rebate_projected += rebate
            yes_price, no_price = price, held_price
            yes_fee, no_fee = fee, held_fee
        else:
            self.no_qty += quantity; self.no_cost += price * quantity; self.no_fees += fee; self.no_rebate_projected += rebate
            yes_price, no_price = held_price, price
            yes_fee, no_fee = held_fee, fee
        self.fills.append(Fill(side, price, quantity, fee, rebate, evidence))
        self.pairs.append(Pair(quantity, yes_price, no_price, yes_fee, no_fee, edge, per_pair, held_rebate + rebate))
        self.residual_qty -= quantity
        if self.residual_qty <= ZERO:
            self.residual_side = None
            self.residual_qty = ZERO
            self.residual_unit_price = None
            self.residual_unit_fee = None
            self.residual_unit_projected_rebate = None

    def on_trade(self, price: D, quantity: D, buyer_maker: bool) -> None:
        price, quantity = D(str(price)), D(str(quantity))
        if quantity <= ZERO:
            return
        # Optimistic tape-touch fill model: credit no more than the simulated resting size.
        if self.yes_quote is not None and buyer_maker and price <= self.yes_quote:
            q = min(quantity, self.quote_size)
            if self.flat:
                q = min(q, self.max_residual_qty)
                if q > ZERO:
                    self._record_open("YES", self.yes_quote, q, "TAPE_TOUCH_BUYER_MAKER")
            elif self.residual_side == "NO":
                q = min(q, self.residual_qty)
                if q > ZERO:
                    self._record_completion("YES", self.yes_quote, q, "TAPE_TOUCH_PAIR_COMPLETE")
            return
        if self.no_quote is not None and (not buyer_maker) and price >= ONE - self.no_quote:
            q = min(quantity, self.quote_size)
            if self.flat:
                q = min(q, self.max_residual_qty)
                if q > ZERO:
                    self._record_open("NO", self.no_quote, q, "TAPE_TOUCH_SELLER_MAKER_NO")
            elif self.residual_side == "YES":
                q = min(q, self.residual_qty)
                if q > ZERO:
                    self._record_completion("NO", self.no_quote, q, "TAPE_TOUCH_PAIR_COMPLETE")

    def summary(self) -> dict[str, Any]:
        locked = sum((p.trade_edge for p in self.pairs), ZERO)
        projected_rebates = self.yes_rebate_projected + self.no_rebate_projected
        return {
            "fills": len(self.fills),
            "fill_quantity": str(sum((f.quantity for f in self.fills), ZERO)),
            "completed_pairs": len(self.pairs),
            "matched_quantity": str(sum((p.quantity for p in self.pairs), ZERO)),
            "locked_trade_edge_after_fees": str(locked),
            "projected_maker_rebates_not_realized": str(projected_rebates),
            "projected_combined_if_rebates_paid": str(locked + projected_rebates),
            "residual_side": self.residual_side,
            "residual_qty": str(self.residual_qty),
            "book_updates": self.book_updates,
            "liquidity_reward_rule_eligible_book_updates": self.eligible_book_updates,
            "liquidity_reward_rule_eligible_fraction": (
                self.eligible_book_updates / self.book_updates if self.book_updates else 0.0
            ),
            "blocked_unprofitable_completion": self.blocked_unprofitable_completion,
            "pairs": [{k: str(v) if isinstance(v, D) else v for k, v in asdict(p).items()} for p in self.pairs],
            "classification": "SHADOW_REPLAY_PROJECTED_REWARDS_NOT_REALIZED",
        }


def replay_capture(path: Path) -> dict[str, Any]:
    markets: dict[str, Ledger] = {}
    source_rows = source_books = source_trades = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        source_rows += 1
        try:
            row = json.loads(raw)
        except Exception:
            continue
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        ledger = markets.setdefault(ticker, Ledger())
        if row.get("type") in {"SHADOW_PLAN", "V4_SHADOW_PLAN"}:
            try:
                ledger.on_book(D(str(row["yes_bid"])), D(str(row["yes_ask"])))
                source_books += 1
            except Exception:
                pass
        elif row.get("type") == "PUBLIC_TRADE":
            try:
                ledger.on_trade(D(str(row["price"])), D(str(row["quantity"])), bool(row["buyer_maker"]))
                source_trades += 1
            except Exception:
                pass

    summaries = {ticker: ledger.summary() for ticker, ledger in markets.items()}
    locked = sum((D(s["locked_trade_edge_after_fees"]) for s in summaries.values()), ZERO)
    projected = sum((D(s["projected_maker_rebates_not_realized"]) for s in summaries.values()), ZERO)
    residual_qty = sum((D(s["residual_qty"]) for s in summaries.values()), ZERO)
    matched_qty = sum((D(s["matched_quantity"]) for s in summaries.values()), ZERO)
    return {
        "source": str(path),
        "source_rows": source_rows,
        "source_books": source_books,
        "source_trades": source_trades,
        "markets": len(markets),
        "markets_with_fills": sum(1 for s in summaries.values() if s["fills"]),
        "markets_with_pairs": sum(1 for s in summaries.values() if s["completed_pairs"]),
        "matched_quantity": str(matched_qty),
        "residual_quantity": str(residual_qty),
        "locked_trade_edge_after_fees": str(locked),
        "projected_maker_rebates_not_realized": str(projected),
        "projected_combined_if_rebates_paid": str(locked + projected),
        "markets_detail": summaries,
        "quote_quantity": str(DEFAULT_QUOTE_QTY),
        "liquidity_rewards_minimum_size_modeled": "10",
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
        "classification": "READ_ONLY_SHADOW_REPLAY_NOT_REALIZED",
        "warning": "Tape-touch ignores queue position; liquidity-reward payout share is not estimated.",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    result = replay_capture(args.capture)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
