"""Read-only Gemini incentive/MM replay with optimistic and strict ledgers.

V2 reuses the rebate-first q10 policy from v1, but runs two independent fill
models on the same retained public tape:

- tape_touch: public maker-side prints can fill up to the resting virtual size;
- strict_trade_through: a previously resting quote fills only when the next public
  top of book moves fully through that quote.

Both ledgers enforce the same inventory rule: after one side fills, stop adding
that side and only quote the complement when trade/settlement economics are
non-negative after modeled Gemini maker fees. Projected rebates remain separate
and are never realized P&L or used to rescue a negative pair.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from decimal import Decimal as D
import json
from pathlib import Path
from typing import Any

from src.gemini_incentive_mm_replay_v1 import Ledger, ONE, ZERO


class StrictLedger(Ledger):
    """Ledger that credits fills only after top-of-book trade-through."""

    def fill_resting_quote(self, side: str, evidence: str) -> bool:
        side = side.upper()
        if side == "YES":
            quote = self.yes_quote
        elif side == "NO":
            quote = self.no_quote
        else:
            raise ValueError(side)
        if quote is None or self.quote_size <= ZERO:
            return False

        q = self.quote_size
        if self.flat:
            q = min(q, self.max_residual_qty)
            if q <= ZERO:
                return False
            self._record_open(side, quote, q, evidence)
            return True

        if self.residual_side == side:
            return False
        q = min(q, self.residual_qty)
        if q <= ZERO:
            return False
        before = len(self.fills)
        self._record_completion(side, quote, q, evidence)
        return len(self.fills) > before


@dataclass
class Market:
    ticker: str
    tape_touch: Ledger = field(default_factory=Ledger)
    strict_trade_through: StrictLedger = field(default_factory=StrictLedger)
    book_updates: int = 0
    trade_events: int = 0

    def on_book(self, yes_bid: D, yes_ask: D) -> None:
        bid, ask = D(str(yes_bid)), D(str(yes_ask))
        if not (ZERO < bid < ask < ONE):
            return

        strict = self.strict_trade_through
        # Check fills against the quotes that were resting BEFORE this book update.
        # YES maker bid is strictly traded through when public YES ask moves below it.
        if strict.yes_quote is not None and ask < strict.yes_quote:
            strict.fill_resting_quote("YES", "STRICT_ASK_MOVED_THROUGH_YES_QUOTE")
        # NO maker bid q is equivalent to a YES ask of 1-q; it is strictly traded
        # through when public YES bid moves above that equivalent price.
        if strict.no_quote is not None and bid > ONE - strict.no_quote:
            strict.fill_resting_quote("NO", "STRICT_BID_MOVED_THROUGH_NO_EQUIV")

        self.tape_touch.on_book(bid, ask)
        strict.on_book(bid, ask)
        self.book_updates += 1

    def on_trade(self, price: D, quantity: D, buyer_maker: bool) -> None:
        self.tape_touch.on_trade(price, quantity, buyer_maker)
        self.trade_events += 1

    def summary(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "book_updates": self.book_updates,
            "trade_events": self.trade_events,
            "tape_touch": self.tape_touch.summary(),
            "strict_trade_through": self.strict_trade_through.summary(),
        }


def _aggregate(markets: dict[str, Market], attr: str) -> dict[str, Any]:
    ledgers = [getattr(m, attr) for m in markets.values()]
    summaries = [ledger.summary() for ledger in ledgers]
    locked = sum((D(s["locked_trade_edge_after_fees"]) for s in summaries), ZERO)
    projected = sum((D(s["projected_maker_rebates_not_realized"]) for s in summaries), ZERO)
    matched = sum((D(s["matched_quantity"]) for s in summaries), ZERO)
    residual = sum((D(s["residual_qty"]) for s in summaries), ZERO)
    return {
        "markets_with_fills": sum(1 for s in summaries if s["fills"]),
        "markets_with_pairs": sum(1 for s in summaries if s["completed_pairs"]),
        "fill_count": sum(int(s["fills"]) for s in summaries),
        "fill_quantity": str(sum((D(s["fill_quantity"]) for s in summaries), ZERO)),
        "matched_quantity": str(matched),
        "residual_quantity": str(residual),
        "locked_trade_edge_after_fees": str(locked),
        "projected_maker_rebates_not_realized": str(projected),
        "projected_combined_if_rebates_paid": str(locked + projected),
        "liquidity_reward_rule_eligible_book_updates": sum(
            int(s["liquidity_reward_rule_eligible_book_updates"]) for s in summaries
        ),
        "blocked_unprofitable_completion": sum(
            int(s["blocked_unprofitable_completion"]) for s in summaries
        ),
    }


def replay_capture(path: Path) -> dict[str, Any]:
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
        market = markets.setdefault(ticker, Market(ticker=ticker))
        row_type = row.get("type")
        if row_type in {"SHADOW_PLAN", "V4_SHADOW_PLAN"}:
            try:
                market.on_book(D(str(row["yes_bid"])), D(str(row["yes_ask"])))
                source_books += 1
            except Exception:
                continue
        elif row_type == "PUBLIC_TRADE":
            try:
                market.on_trade(
                    D(str(row["price"])),
                    D(str(row["quantity"])),
                    bool(row["buyer_maker"]),
                )
                source_trades += 1
            except Exception:
                continue

    return {
        "type": "GEMINI_INCENTIVE_MM_REPLAY_V2",
        "classification": "READ_ONLY_SHADOW_REPLAY_NOT_REALIZED",
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
        "source": str(path),
        "source_rows": source_rows,
        "source_books": source_books,
        "source_trades": source_trades,
        "markets": len(markets),
        "quote_quantity": "10",
        "tape_touch": _aggregate(markets, "tape_touch"),
        "strict_trade_through": _aggregate(markets, "strict_trade_through"),
        "markets_detail": {ticker: market.summary() for ticker, market in markets.items()},
        "warnings": [
            "Tape-touch ignores queue position and may over-credit fills.",
            "Strict trade-through is conservative but still does not prove our real queue position or executable fill rate.",
            "Maker rebates are projected only; liquidity reward payout share is not estimated.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = replay_capture(args.capture)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
