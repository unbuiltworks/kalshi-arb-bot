"""Offline q=10 dual-maker replay for retained Gemini public tape.

Purpose: test the incentive-aware complete-set strategy on an already captured hour
without placing orders or using credentials.

Policy:
- when flat, rest q=10 YES at public YES bid and q=10 NO at 1-public YES ask;
- only open a cycle when BOTH fills would lock positive trade-only edge after current
  modeled Gemini maker fees;
- once one side fills, never add more naked inventory; keep only the pre-committed
  complementary quote that would complete the profitable pair;
- after completion, a later book update may open another cycle;
- tape-touch requires one public maker-side print of at least q=10 at/through the
  virtual quote (queue position still unknown, so optimistic);
- strict fill requires the public top of book to move through the resting quote.

Rewards/rebates are not used to classify the pair and are not realized P&L here.
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
Q = D("10")
DEFAULT_MIN_EDGE_PER_PAIR = D("0.01")


@dataclass(frozen=True)
class Fill:
    side: str
    price: str
    quantity: str
    maker_fee: str
    evidence: str
    role: str


@dataclass(frozen=True)
class Pair:
    yes_price: str
    no_price: str
    quantity: str
    yes_fee: str
    no_fee: str
    locked_edge_total_after_fees: str
    locked_edge_per_pair_after_fees: str


@dataclass
class Ledger:
    label: str
    min_edge_per_pair: D = DEFAULT_MIN_EDGE_PER_PAIR
    yes_quote: D | None = None
    no_quote: D | None = None
    residual_side: str | None = None
    held_yes_price: D | None = None
    held_no_price: D | None = None
    held_yes_fee: D | None = None
    held_no_fee: D | None = None
    fills: list[Fill] = field(default_factory=list)
    pairs: list[Pair] = field(default_factory=list)
    blocked_nonarb_cycles: int = 0

    @property
    def flat(self) -> bool:
        return self.residual_side is None

    @property
    def residual_qty(self) -> D:
        return D("0") if self.flat else Q

    @property
    def residual_acquisition_cost(self) -> D:
        if self.residual_side == "YES" and self.held_yes_price is not None and self.held_yes_fee is not None:
            return self.held_yes_price * Q + self.held_yes_fee
        if self.residual_side == "NO" and self.held_no_price is not None and self.held_no_fee is not None:
            return self.held_no_price * Q + self.held_no_fee
        return D("0")

    def _pair_edge(self, yes_price: D, no_price: D) -> tuple[D, D, D]:
        yf = gemini_predictions_maker_fee(yes_price, Q)
        nf = gemini_predictions_maker_fee(no_price, Q)
        edge = Q - (yes_price + no_price) * Q - yf - nf
        return edge, yf, nf

    def plan_flat(self, bid: D, ask: D) -> None:
        yes_quote = D(str(bid))
        no_quote = ONE - D(str(ask))
        edge, _, _ = self._pair_edge(yes_quote, no_quote)
        if edge / Q < self.min_edge_per_pair:
            self.yes_quote = None
            self.no_quote = None
            self.blocked_nonarb_cycles += 1
            return
        self.yes_quote = yes_quote
        self.no_quote = no_quote

    def _complete_pair(self) -> None:
        assert self.held_yes_price is not None and self.held_no_price is not None
        assert self.held_yes_fee is not None and self.held_no_fee is not None
        edge = Q - (self.held_yes_price + self.held_no_price) * Q - self.held_yes_fee - self.held_no_fee
        self.pairs.append(Pair(
            yes_price=str(self.held_yes_price),
            no_price=str(self.held_no_price),
            quantity=str(Q),
            yes_fee=str(self.held_yes_fee),
            no_fee=str(self.held_no_fee),
            locked_edge_total_after_fees=str(edge),
            locked_edge_per_pair_after_fees=str(edge / Q),
        ))
        self.residual_side = None
        self.held_yes_price = self.held_no_price = None
        self.held_yes_fee = self.held_no_fee = None
        self.yes_quote = self.no_quote = None

    def fill(self, side: str, *, evidence: str) -> bool:
        side = side.upper()
        if side == "YES":
            if self.yes_quote is None or self.residual_side == "YES":
                return False
            price = self.yes_quote
            fee = gemini_predictions_maker_fee(price, Q)
            self.held_yes_price = price
            self.held_yes_fee = fee
        elif side == "NO":
            if self.no_quote is None or self.residual_side == "NO":
                return False
            price = self.no_quote
            fee = gemini_predictions_maker_fee(price, Q)
            self.held_no_price = price
            self.held_no_fee = fee
        else:
            raise ValueError(side)

        role = "DIRECTIONAL_OPEN" if self.residual_side is None else "PAIR_COMPLETION"
        self.fills.append(Fill(side, str(price), str(Q), str(fee), evidence, role))
        if self.residual_side is None:
            self.residual_side = side
            # Do not add more on the filled side. Preserve only the complementary
            # quote from the profitable cycle we committed while flat.
            if side == "YES":
                self.yes_quote = None
            else:
                self.no_quote = None
        else:
            self._complete_pair()
        return True

    def summary(self) -> dict[str, Any]:
        locked = sum((D(p.locked_edge_total_after_fees) for p in self.pairs), D("0"))
        return {
            "label": self.label,
            "fills": len(self.fills),
            "fill_chronology": [asdict(x) for x in self.fills],
            "completed_pairs": len(self.pairs),
            "pairs": [asdict(x) for x in self.pairs],
            "locked_edge_total_after_fees": str(locked),
            "average_locked_edge_per_pair_after_fees": str(locked / D(len(self.pairs)) / Q) if self.pairs else None,
            "residual_side": self.residual_side,
            "residual_qty": str(self.residual_qty),
            "residual_acquisition_cost": str(self.residual_acquisition_cost),
            "blocked_nonarb_cycles": self.blocked_nonarb_cycles,
        }


@dataclass
class Market:
    ticker: str
    symbol: str
    asset: str
    horizon: str
    tape: Ledger = field(default_factory=lambda: Ledger("TAPE_Q10_SINGLE_PRINT_OPTIMISTIC"))
    strict: Ledger = field(default_factory=lambda: Ledger("STRICT_Q10_TRADE_THROUGH"))
    last_bid: D | None = None
    last_ask: D | None = None
    book_updates: int = 0
    trade_events: int = 0
    reward_eligible_book_updates: int = 0
    arb_eligible_book_updates: int = 0

    def on_book(self, bid: D, ask: D) -> None:
        bid, ask = D(str(bid)), D(str(ask))
        if not (D("0") < bid < ask < ONE):
            return

        # Check strict fills against the PREVIOUSLY resting quote.
        if self.strict.yes_quote is not None and ask < self.strict.yes_quote:
            self.strict.fill("YES", evidence="ASK_TRADED_THROUGH_Q10")
        if self.strict.no_quote is not None and bid > ONE - self.strict.no_quote:
            self.strict.fill("NO", evidence="BID_TRADED_THROUGH_NO_EQUIV_Q10")

        self.last_bid, self.last_ask = bid, ask
        self.book_updates += 1
        if self.tape.flat:
            self.tape.plan_flat(bid, ask)
        if self.strict.flat:
            self.strict.plan_flat(bid, ask)

        # Best bids are each half the public spread from midpoint, so a <=20c
        # spread places each side within Gemini's current 10c max-distance rule.
        if ask - bid <= D("0.20"):
            self.reward_eligible_book_updates += 1
        edge = Q - (bid + (ONE - ask)) * Q - gemini_predictions_maker_fee(bid, Q) - gemini_predictions_maker_fee(ONE - ask, Q)
        if edge > 0:
            self.arb_eligible_book_updates += 1

    def on_trade(self, price: D, quantity: D, buyer_maker: bool) -> None:
        self.trade_events += 1
        price, quantity = D(str(price)), D(str(quantity))
        if quantity < Q:
            return
        if self.tape.yes_quote is not None and buyer_maker and price <= self.tape.yes_quote:
            self.tape.fill("YES", evidence="TRADE_TAPE_BUYER_MAKER_SINGLE_PRINT_GE_Q10")
        elif self.tape.no_quote is not None and not buyer_maker and price >= ONE - self.tape.no_quote:
            self.tape.fill("NO", evidence="TRADE_TAPE_SELLER_MAKER_SINGLE_PRINT_GE_Q10")

    def summary(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "symbol": self.symbol,
            "asset": self.asset,
            "horizon": self.horizon,
            "book_updates": self.book_updates,
            "trade_events": self.trade_events,
            "reward_eligible_book_updates": self.reward_eligible_book_updates,
            "arb_eligible_book_updates": self.arb_eligible_book_updates,
            "tape": self.tape.summary(),
            "strict": self.strict.summary(),
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

    def agg(key: str) -> dict[str, Any]:
        ledgers = [getattr(m, key) for m in markets.values()]
        locked = sum((D(p.locked_edge_total_after_fees) for l in ledgers for p in l.pairs), D("0"))
        pairs = sum(len(l.pairs) for l in ledgers)
        return {
            "fills": sum(len(l.fills) for l in ledgers),
            "markets_with_fills": sum(bool(l.fills) for l in ledgers),
            "completed_q10_pairs": pairs,
            "paired_contracts": str(D(pairs) * Q),
            "locked_edge_total_after_fees": str(locked),
            "average_locked_edge_per_contract_after_fees": str(locked / (D(pairs) * Q)) if pairs else None,
            "residual_markets": sum(l.residual_side is not None for l in ledgers),
            "residual_contracts": str(sum((l.residual_qty for l in ledgers), D("0"))),
            "residual_acquisition_cost": str(sum((l.residual_acquisition_cost for l in ledgers), D("0"))),
        }

    return {
        "type": "GEMINI_DUAL_MAKER_Q10_REPLAY_V1",
        "classification": "SHADOW_REPLAY_NOT_REALIZED",
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
        "quantity_per_side": str(Q),
        "min_trade_only_edge_per_pair": str(DEFAULT_MIN_EDGE_PER_PAIR),
        "source_rows": source_rows,
        "source_book_rows": source_books,
        "source_trade_rows": source_trades,
        "markets": len(markets),
        "reward_eligible_book_updates": sum(m.reward_eligible_book_updates for m in markets.values()),
        "arb_eligible_book_updates": sum(m.arb_eligible_book_updates for m in markets.values()),
        "tape": agg("tape"),
        "strict": agg("strict"),
        "markets_detail": [m.summary() for m in markets.values()],
        "warnings": [
            "Tape ledger ignores queue position and requires a single public print >= q10; optimistic on queue, conservative on fragmented fills.",
            "Strict ledger requires public top-of-book trade-through; conservative fill proxy.",
            "Reward eligibility counts book updates, not Gemini random per-minute/day payout snapshots; no reward dollars are credited.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = replay(args.input)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
