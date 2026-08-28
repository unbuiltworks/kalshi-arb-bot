"""Read-only q=1 first-fill replay for raw vs edge-spending initial makers.

The purpose is deliberately narrow: measure whether spending excess fee-adjusted
pair edge on better *initial* maker prices creates materially more evidence of a
first fill. A first fill is what activates the separate dynamic-complement policy.

This replay consumes the retained public JSONL tape used by the existing Gemini
maker replays. It never loads credentials and never mutates venue state.

Evidence modes:
- TAPE_TOUCH_OPTIMISTIC: public trade touches or trades through our virtual quote.
- STRICT_TRADE_THROUGH: public trade must trade strictly through our quote.

Neither mode proves our actual queue position would have filled.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from decimal import Decimal as D
import json
from pathlib import Path
from typing import Any

from src.gemini_incentive_mm_dynamic_complement import aggressive_entry_plan, completed_pair_edge

ONE = D("1")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")
RAW_ENTRY_EDGE = D("0.05")
AGGRESSIVE_TARGET_EDGE = D("0.10")
EVIDENCE_MODES = ("TAPE_TOUCH_OPTIMISTIC", "STRICT_TRADE_THROUGH")


def _d(value: Any) -> D:
    return D(str(value))


def _family(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("asset") or "").upper(), str(row.get("horizon") or "").lower()


def _is_target_family(row: dict[str, Any], asset: str | None, horizon: str | None) -> bool:
    row_asset, row_horizon = _family(row)
    if asset and row_asset != asset.upper():
        return False
    if horizon and row_horizon != horizon.lower():
        return False
    return True


def _fill_side(*, yes_quote: D, no_quote: D, trade_price: D, buyer_maker: bool, strict: bool) -> str | None:
    """Return which resting maker side has compatible public-flow evidence."""
    if buyer_maker:
        hit = trade_price < yes_quote if strict else trade_price <= yes_quote
        return "YES" if hit else None
    yes_equiv_no_offer = ONE - no_quote
    hit = trade_price > yes_equiv_no_offer if strict else trade_price >= yes_equiv_no_offer
    return "NO" if hit else None


@dataclass(frozen=True)
class FillEvidence:
    side: str
    quote: str
    public_trade_price: str
    public_trade_quantity: str
    buyer_maker: bool
    source_row: int
    source_observed_at_utc: str | None


@dataclass
class PolicyState:
    name: str
    yes_quote: D | None = None
    no_quote: D | None = None
    pair_edge: D | None = None
    first_fill: FillEvidence | None = None
    eligible_updates: int = 0
    quote_improvement_total: D = D("0")

    @property
    def filled(self) -> bool:
        return self.first_fill is not None

    def update_quotes(self, yes_quote: D, no_quote: D, pair_edge: D, improvement: D = D("0")) -> None:
        if self.filled:
            return
        self.yes_quote = yes_quote
        self.no_quote = no_quote
        self.pair_edge = pair_edge
        self.eligible_updates += 1
        self.quote_improvement_total += improvement

    def on_trade(self, row: dict[str, Any], source_row: int, strict: bool) -> None:
        if self.filled or self.yes_quote is None or self.no_quote is None:
            return
        price = _d(row["price"])
        quantity = _d(row["quantity"])
        if quantity <= 0:
            return
        side = _fill_side(
            yes_quote=self.yes_quote,
            no_quote=self.no_quote,
            trade_price=price,
            buyer_maker=bool(row["buyer_maker"]),
            strict=strict,
        )
        if side is None:
            return
        quote = self.yes_quote if side == "YES" else self.no_quote
        self.first_fill = FillEvidence(
            side=side,
            quote=str(quote),
            public_trade_price=str(price),
            public_trade_quantity=str(quantity),
            buyer_maker=bool(row["buyer_maker"]),
            source_row=source_row,
            source_observed_at_utc=(str(row.get("observed_at_utc")) if row.get("observed_at_utc") else None),
        )


@dataclass
class MarketComparison:
    ticker: str
    symbol: str
    asset: str
    horizon: str
    raw: PolicyState = field(default_factory=lambda: PolicyState("RAW"))
    aggressive: PolicyState = field(default_factory=lambda: PolicyState("AGGRESSIVE"))
    raw_eligible_updates: int = 0
    aggressive_improved_updates: int = 0
    aggressive_hold_raw_updates: int = 0
    raw_edge_sum: D = D("0")
    aggressive_edge_sum: D = D("0")
    yes_improvement_sum: D = D("0")
    no_improvement_sum: D = D("0")

    def on_book(self, row: dict[str, Any]) -> None:
        if self.raw.filled and self.aggressive.filled:
            return
        yes_bid = _d(row["yes_bid"])
        yes_ask = _d(row["yes_ask"])
        if not (D("0") < yes_bid < yes_ask < ONE):
            return
        raw_yes = yes_bid
        raw_no = ONE - yes_ask
        if not (MIN_PRICE <= raw_yes <= MAX_PRICE and MIN_PRICE <= raw_no <= MAX_PRICE):
            return
        raw_edge = completed_pair_edge(filled_price=raw_yes, complement_price=raw_no)
        if raw_edge < RAW_ENTRY_EDGE:
            return
        self.raw_eligible_updates += 1
        self.raw_edge_sum += raw_edge
        self.raw.update_quotes(raw_yes, raw_no, raw_edge)

        plan = aggressive_entry_plan(
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            min_entry_edge=AGGRESSIVE_TARGET_EDGE,
            min_price=MIN_PRICE,
            max_price=MAX_PRICE,
        )
        aggr_yes = _d(plan["yes_quote"])
        aggr_no = _d(plan["no_quote"])
        aggr_edge = _d(plan["final_edge"])
        yes_imp = _d(plan["yes_improvement"])
        no_imp = _d(plan["no_improvement"])
        improvement = yes_imp + no_imp
        if plan["action"] == "IMPROVE_INITIAL_MAKERS":
            self.aggressive_improved_updates += 1
        else:
            self.aggressive_hold_raw_updates += 1
        self.aggressive_edge_sum += aggr_edge
        self.yes_improvement_sum += yes_imp
        self.no_improvement_sum += no_imp
        self.aggressive.update_quotes(aggr_yes, aggr_no, aggr_edge, improvement)

    def on_trade(self, row: dict[str, Any], source_row: int, strict: bool) -> None:
        self.raw.on_trade(row, source_row, strict)
        self.aggressive.on_trade(row, source_row, strict)

    def summary(self) -> dict[str, Any]:
        raw_fill = asdict(self.raw.first_fill) if self.raw.first_fill else None
        aggr_fill = asdict(self.aggressive.first_fill) if self.aggressive.first_fill else None
        return {
            "ticker": self.ticker,
            "symbol": self.symbol,
            "asset": self.asset,
            "horizon": self.horizon,
            "raw_eligible_updates": self.raw_eligible_updates,
            "aggressive_improved_updates": self.aggressive_improved_updates,
            "aggressive_hold_raw_updates": self.aggressive_hold_raw_updates,
            "raw_first_fill": raw_fill,
            "aggressive_first_fill": aggr_fill,
            "incremental_aggressive_first_fill": bool(aggr_fill and not raw_fill),
            "raw_average_edge": str(self.raw_edge_sum / self.raw_eligible_updates) if self.raw_eligible_updates else None,
            "aggressive_average_edge": str(self.aggressive_edge_sum / self.raw_eligible_updates) if self.raw_eligible_updates else None,
            "average_yes_improvement": str(self.yes_improvement_sum / self.raw_eligible_updates) if self.raw_eligible_updates else None,
            "average_no_improvement": str(self.no_improvement_sum / self.raw_eligible_updates) if self.raw_eligible_updates else None,
        }


def replay(path: Path, *, evidence_mode: str, asset: str | None = "ETH", horizon: str | None = "5m") -> dict[str, Any]:
    if evidence_mode not in EVIDENCE_MODES:
        raise ValueError(f"unsupported evidence mode: {evidence_mode}")
    strict = evidence_mode == "STRICT_TRADE_THROUGH"
    markets: dict[str, MarketComparison] = {}
    source_rows = source_books = source_trades = 0

    for index, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        source_rows += 1
        try:
            row = json.loads(raw_line)
        except Exception:
            continue
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue

        if row.get("type") == "SHADOW_PLAN":
            # SHADOW_PLAN rows carry the market family metadata. They establish
            # which tickers belong to ETH5. PUBLIC_TRADE rows on retained tape do
            # not necessarily repeat asset/horizon, so trades are joined by ticker
            # only after the target market has been established here.
            if not _is_target_family(row, asset, horizon):
                continue
            market = markets.setdefault(ticker, MarketComparison(
                ticker=ticker,
                symbol=str(row.get("symbol") or ""),
                asset=str(row.get("asset") or ""),
                horizon=str(row.get("horizon") or ""),
            ))
            try:
                market.on_book(row)
                source_books += 1
            except Exception:
                continue
            continue

        if row.get("type") == "PUBLIC_TRADE":
            market = markets.get(ticker)
            if market is None:
                # A trade before the first target-family book snapshot cannot hit
                # a virtual quote because no quote would have been established.
                continue
            try:
                market.on_trade(row, index, strict)
                source_trades += 1
            except Exception:
                continue

    eligible = [m for m in markets.values() if m.raw_eligible_updates]
    raw_filled = [m for m in eligible if m.raw.filled]
    aggr_filled = [m for m in eligible if m.aggressive.filled]
    incremental = [m for m in eligible if m.aggressive.filled and not m.raw.filled]
    lost = [m for m in eligible if m.raw.filled and not m.aggressive.filled]
    both = [m for m in eligible if m.raw.filled and m.aggressive.filled]
    improved_updates = sum(m.aggressive_improved_updates for m in eligible)
    eligible_updates = sum(m.raw_eligible_updates for m in eligible)

    def side_counts(items: list[MarketComparison], which: str) -> dict[str, int]:
        out = {"YES": 0, "NO": 0}
        for market in items:
            fill = market.raw.first_fill if which == "raw" else market.aggressive.first_fill
            if fill:
                out[fill.side] = out.get(fill.side, 0) + 1
        return out

    return {
        "type": "GEMINI_DYNAMIC_INITIAL_ENTRY_Q1_REPLAY_V1",
        "classification": "SHADOW_REPLAY_NOT_REALIZED",
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
        "evidence_mode": evidence_mode,
        "target_asset": asset,
        "target_horizon": horizon,
        "raw_entry_edge_floor": str(RAW_ENTRY_EDGE),
        "aggressive_target_edge_floor": str(AGGRESSIVE_TARGET_EDGE),
        "source_rows": source_rows,
        "source_book_rows_target_family": source_books,
        "source_trade_rows_target_family": source_trades,
        "target_markets_seen": len(markets),
        "eligible_markets": len(eligible),
        "eligible_book_updates": eligible_updates,
        "aggressive_improved_book_updates": improved_updates,
        "aggressive_improved_fraction": str(D(improved_updates) / D(eligible_updates)) if eligible_updates else None,
        "raw_markets_with_first_fill_evidence": len(raw_filled),
        "aggressive_markets_with_first_fill_evidence": len(aggr_filled),
        "incremental_first_fill_markets": len(incremental),
        "lost_first_fill_markets": len(lost),
        "both_first_fill_markets": len(both),
        "raw_first_fill_side_counts": side_counts(raw_filled, "raw"),
        "aggressive_first_fill_side_counts": side_counts(aggr_filled, "aggressive"),
        "markets_detail": [m.summary() for m in eligible],
        "warnings": [
            "This is SHADOW replay only; no order was submitted and realized P&L is not computed.",
            "Public tape touch/trade-through is compatible-flow evidence, not proof of actual queue fill priority.",
            "The comparison intentionally measures only the first fill; dynamic complement completion is a separate stage.",
            "Maker rebates and liquidity rewards receive zero credit in all edge calculations.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--asset", default="ETH")
    parser.add_argument("--horizon", default="5m")
    parser.add_argument("--evidence-mode", choices=EVIDENCE_MODES, default="TAPE_TOUCH_OPTIMISTIC")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    result = replay(args.input, evidence_mode=args.evidence_mode, asset=args.asset, horizon=args.horizon)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    printable = {k: v for k, v in result.items() if k != "markets_detail"} if args.compact else result
    print(json.dumps(printable, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
