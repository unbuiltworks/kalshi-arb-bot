"""Offline partial-fill replay for Gemini q10 dual-maker reward quoting.

This is a read-only replay on retained public tape. It models a safer policy that is
closer to the observed l5 execution pattern:

1. While flat, advertise q=10 maker bids on YES and NO so the quote size can satisfy
   Gemini's current liquidity-reward minimum-size rule.
2. Only advertise when even a q=1 matched fill at the same prices would remain
   positive after modeled maker fees. Rewards/rebates receive zero credit here.
3. On the FIRST partial fill, cancel the unfilled remainder on that same side and
   resize the complementary quote to exactly the quantity actually filled.
4. Complementary partial fills lock matched chunks; never over-hedge.
5. Re-arm q=10 two-sided quoting only after the residual returns to zero.

Public tape at/through our quote is queue-optimistic: it proves compatible market
flow, not that our specific resting order would have filled. Fragmented public
prints are accumulated because real resting orders can fill in pieces.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from decimal import Decimal as D
import json
from pathlib import Path
from typing import Any

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

ONE = D("1")
ADVERTISED_Q = D("10")
SAFETY_Q = D("1")
MIN_EDGE_PER_CONTRACT = D("0.01")
REWARD_MAX_SPREAD = D("0.20")  # each best bid <=10c from midpoint


@dataclass(frozen=True)
class Fill:
    side: str
    price: str
    quantity: str
    maker_fee: str
    role: str
    evidence: str


@dataclass(frozen=True)
class PairedChunk:
    quantity: str
    yes_price: str
    no_price: str
    allocated_open_fee: str
    complement_fee: str
    locked_edge_after_fees: str
    locked_edge_per_contract_after_fees: str


@dataclass
class PartialLedger:
    yes_quote: D | None = None
    no_quote: D | None = None
    residual_side: str | None = None
    open_price: D | None = None
    open_qty: D = D("0")
    open_fee: D = D("0")
    complement_price: D | None = None
    complement_filled: D = D("0")
    complement_fees: D = D("0")
    fills: list[Fill] = field(default_factory=list)
    paired_chunks: list[PairedChunk] = field(default_factory=list)
    blocked_unsafe_updates: int = 0
    rearm_cycles: int = 0

    @property
    def flat(self) -> bool:
        return self.residual_side is None

    @property
    def residual_qty(self) -> D:
        return max(D("0"), self.open_qty - self.complement_filled) if not self.flat else D("0")

    @property
    def open_fee_per_contract(self) -> D:
        return self.open_fee / self.open_qty if self.open_qty > 0 else D("0")

    @property
    def residual_acquisition_cost(self) -> D:
        if self.flat or self.open_price is None:
            return D("0")
        return self.residual_qty * (self.open_price + self.open_fee_per_contract)

    def _pair_edge(self, yes: D, no: D, quantity: D) -> D:
        return quantity - (yes + no) * quantity - gemini_predictions_maker_fee(yes, quantity) - gemini_predictions_maker_fee(no, quantity)

    def plan_flat(self, bid: D, ask: D) -> bool:
        if not self.flat:
            return False
        yes = D(str(bid))
        no = ONE - D(str(ask))
        safety_edge = self._pair_edge(yes, no, SAFETY_Q) / SAFETY_Q
        if safety_edge < MIN_EDGE_PER_CONTRACT or safety_edge <= 0:
            self.yes_quote = self.no_quote = None
            self.blocked_unsafe_updates += 1
            return False
        self.yes_quote = yes
        self.no_quote = no
        return True

    def _open(self, side: str, quantity: D, evidence: str) -> D:
        side = side.upper()
        if not self.flat or quantity <= 0:
            return D("0")
        quote = self.yes_quote if side == "YES" else self.no_quote
        if quote is None:
            return D("0")
        q = min(D(str(quantity)), ADVERTISED_Q)
        if q <= 0:
            return D("0")
        fee = gemini_predictions_maker_fee(quote, q)
        self.residual_side = side
        self.open_price = quote
        self.open_qty = q
        self.open_fee = fee
        self.complement_filled = D("0")
        self.complement_fees = D("0")
        self.complement_price = self.no_quote if side == "YES" else self.yes_quote
        # Cancel unfilled same-side remainder. Keep only enough opposite-side size
        # to match what actually filled; the quantity is represented by residual_qty.
        if side == "YES":
            self.yes_quote = None
        else:
            self.no_quote = None
        self.fills.append(Fill(side, str(quote), str(q), str(fee), "DIRECTIONAL_OPEN_PARTIAL", evidence))
        return q

    def _complete(self, quantity: D, evidence: str) -> D:
        if self.flat or self.residual_side is None or self.open_price is None or self.complement_price is None:
            return D("0")
        q = min(D(str(quantity)), self.residual_qty)
        if q <= 0:
            return D("0")
        side = "NO" if self.residual_side == "YES" else "YES"
        comp_fee = gemini_predictions_maker_fee(self.complement_price, q)
        allocated_open_fee = self.open_fee_per_contract * q
        yes_price = self.open_price if self.residual_side == "YES" else self.complement_price
        no_price = self.open_price if self.residual_side == "NO" else self.complement_price
        edge = q - (yes_price + no_price) * q - allocated_open_fee - comp_fee
        self.complement_filled += q
        self.complement_fees += comp_fee
        self.fills.append(Fill(side, str(self.complement_price), str(q), str(comp_fee), "PAIR_COMPLETION_PARTIAL", evidence))
        self.paired_chunks.append(PairedChunk(
            quantity=str(q),
            yes_price=str(yes_price),
            no_price=str(no_price),
            allocated_open_fee=str(allocated_open_fee),
            complement_fee=str(comp_fee),
            locked_edge_after_fees=str(edge),
            locked_edge_per_contract_after_fees=str(edge / q),
        ))
        if self.residual_qty <= 0:
            self.residual_side = None
            self.open_price = None
            self.open_qty = D("0")
            self.open_fee = D("0")
            self.complement_price = None
            self.complement_filled = D("0")
            self.complement_fees = D("0")
            self.yes_quote = self.no_quote = None
            self.rearm_cycles += 1
        return q

    def on_trade(self, price: D, quantity: D, buyer_maker: bool) -> None:
        price, quantity = D(str(price)), D(str(quantity))
        if quantity <= 0:
            return
        if self.flat:
            if self.yes_quote is not None and buyer_maker and price <= self.yes_quote:
                self._open("YES", quantity, "TAPE_FRAGMENT_BUYER_MAKER")
            elif self.no_quote is not None and not buyer_maker and price >= ONE - self.no_quote:
                self._open("NO", quantity, "TAPE_FRAGMENT_SELLER_MAKER_NO_EQUIV")
            return
        if self.residual_side == "YES":
            if self.no_quote is not None and not buyer_maker and price >= ONE - self.no_quote:
                self._complete(quantity, "TAPE_FRAGMENT_COMPLEMENT_NO")
        elif self.residual_side == "NO":
            if self.yes_quote is not None and buyer_maker and price <= self.yes_quote:
                self._complete(quantity, "TAPE_FRAGMENT_COMPLEMENT_YES")

    def summary(self) -> dict[str, Any]:
        paired_qty = sum((D(x.quantity) for x in self.paired_chunks), D("0"))
        locked = sum((D(x.locked_edge_after_fees) for x in self.paired_chunks), D("0"))
        positive_qty = sum((D(x.quantity) for x in self.paired_chunks if D(x.locked_edge_after_fees) > 0), D("0"))
        negative_qty = sum((D(x.quantity) for x in self.paired_chunks if D(x.locked_edge_after_fees) < 0), D("0"))
        return {
            "fills": len(self.fills),
            "fill_chronology": [asdict(x) for x in self.fills],
            "paired_chunks": len(self.paired_chunks),
            "paired_quantity": str(paired_qty),
            "positive_paired_quantity": str(positive_qty),
            "negative_paired_quantity": str(negative_qty),
            "locked_edge_total_after_fees": str(locked),
            "average_locked_edge_per_paired_contract_after_fees": str(locked / paired_qty) if paired_qty else None,
            "residual_side": self.residual_side,
            "residual_qty": str(self.residual_qty),
            "residual_acquisition_cost": str(self.residual_acquisition_cost),
            "blocked_unsafe_updates": self.blocked_unsafe_updates,
            "completed_and_rearmed_cycles": self.rearm_cycles,
        }


@dataclass
class Market:
    ticker: str
    symbol: str
    asset: str
    horizon: str
    ledger: PartialLedger = field(default_factory=PartialLedger)
    book_updates: int = 0
    trade_events: int = 0
    two_sided_q10_reward_posture_updates: int = 0
    safe_trade_only_quote_updates: int = 0

    def on_book(self, bid: D, ask: D) -> None:
        bid, ask = D(str(bid)), D(str(ask))
        if not (D("0") < bid < ask < ONE):
            return
        self.book_updates += 1
        if self.ledger.flat:
            safe = self.ledger.plan_flat(bid, ask)
            if safe:
                self.safe_trade_only_quote_updates += 1
                if ask - bid <= REWARD_MAX_SPREAD:
                    self.two_sided_q10_reward_posture_updates += 1

    def on_trade(self, price: D, quantity: D, buyer_maker: bool) -> None:
        self.trade_events += 1
        self.ledger.on_trade(price, quantity, buyer_maker)

    def summary(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "symbol": self.symbol,
            "asset": self.asset,
            "horizon": self.horizon,
            "book_updates": self.book_updates,
            "trade_events": self.trade_events,
            "two_sided_q10_reward_posture_updates": self.two_sided_q10_reward_posture_updates,
            "safe_trade_only_quote_updates": self.safe_trade_only_quote_updates,
            "ledger": self.ledger.summary(),
        }


def replay(path: Path) -> dict[str, Any]:
    markets: dict[str, Market] = {}
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
        market = markets.setdefault(ticker, Market(
            ticker=ticker,
            symbol=str(row.get("symbol") or ""),
            asset=str(row.get("asset") or ""),
            horizon=str(row.get("horizon") or ""),
        ))
        if row.get("type") == "SHADOW_PLAN":
            try:
                market.on_book(D(str(row["yes_bid"])), D(str(row["yes_ask"])))
                source_books += 1
            except Exception:
                pass
        elif row.get("type") == "PUBLIC_TRADE":
            try:
                market.on_trade(D(str(row["price"])), D(str(row["quantity"])), bool(row["buyer_maker"]))
                source_trades += 1
            except Exception:
                pass

    ledgers = [m.ledger for m in markets.values()]
    paired_qty = sum((D(x.quantity) for l in ledgers for x in l.paired_chunks), D("0"))
    locked = sum((D(x.locked_edge_after_fees) for l in ledgers for x in l.paired_chunks), D("0"))
    residual_qty = sum((l.residual_qty for l in ledgers), D("0"))
    residual_cost = sum((l.residual_acquisition_cost for l in ledgers), D("0"))
    total_books = sum(m.book_updates for m in markets.values())
    reward_posture = sum(m.two_sided_q10_reward_posture_updates for m in markets.values())
    safe_updates = sum(m.safe_trade_only_quote_updates for m in markets.values())

    return {
        "type": "GEMINI_DUAL_MAKER_Q10_PARTIAL_REPLAY_V2",
        "classification": "SHADOW_REPLAY_NOT_REALIZED",
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
        "advertised_quantity_while_flat": str(ADVERTISED_Q),
        "partial_fill_policy": "CANCEL_SAME_SIDE_REMAINDER_AND_RESIZE_COMPLEMENT",
        "minimum_trade_only_safety_quantity": str(SAFETY_Q),
        "minimum_trade_only_edge_per_contract": str(MIN_EDGE_PER_CONTRACT),
        "source_rows": source_rows,
        "source_book_rows": source_books,
        "source_trade_rows": source_trades,
        "markets": len(markets),
        "safe_trade_only_quote_updates": safe_updates,
        "two_sided_q10_reward_posture_updates": reward_posture,
        "reward_posture_fraction_of_book_updates": str(D(reward_posture) / D(total_books)) if total_books else None,
        "fills": sum(len(l.fills) for l in ledgers),
        "markets_with_fills": sum(bool(l.fills) for l in ledgers),
        "paired_quantity": str(paired_qty),
        "locked_edge_total_after_fees": str(locked),
        "average_locked_edge_per_paired_contract_after_fees": str(locked / paired_qty) if paired_qty else None,
        "residual_markets": sum(not l.flat for l in ledgers),
        "residual_quantity": str(residual_qty),
        "residual_acquisition_cost": str(residual_cost),
        "completed_and_rearmed_cycles": sum(l.rearm_cycles for l in ledgers),
        "markets_detail": [m.summary() for m in markets.values()],
        "warnings": [
            "Public trade-tape matching is queue-optimistic and is not proof our order would have filled.",
            "Fragmented fills are modeled because resting orders can partially fill; actual queue priority remains unknown.",
            "Liquidity reward posture is a book-update proxy, not Gemini's random per-minute payout sampling and not realized reward dollars.",
            "No maker rebate or liquidity reward dollars are credited to trade-only arbitrage or realized P&L.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    result = replay(args.input)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    printable = {k: v for k, v in result.items() if k != "markets_detail"} if args.compact else result
    print(json.dumps(printable, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
