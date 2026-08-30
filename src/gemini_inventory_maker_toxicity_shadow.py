"""Read-only Gemini q=10 inventory-maker replay with adverse-selection stress.

Purpose: compare a continuous two-sided inventory maker against the conservative
cancel-after-first-fill posture without pretending tape-touch fills are executable.
The replay treats matched-book economics, residual inventory, and toxicity as
separate ledgers and reports the toxicity level where the strategy stops working.

NO credentials. NO order entry. NO realized-P&L claims.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from decimal import Decimal as D
from pathlib import Path
from typing import Any

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_incentive_mm_replay_v1 import projected_maker_rebate

ZERO = D("0")
ONE = D("1")
Q10 = D("10")


@dataclass
class Lot:
    qty: D
    price: D
    fee: D
    rebate: D


@dataclass
class Inventory:
    yes: list[Lot] = field(default_factory=list)
    no: list[Lot] = field(default_factory=list)
    yes_qty: D = ZERO
    no_qty: D = ZERO
    yes_quote: D | None = None
    no_quote: D | None = None
    quote_qty: D = Q10
    last_mid: D | None = None
    last_ts: int | None = None
    fill_count: int = 0
    eligible_book_updates: int = 0
    book_updates: int = 0

    def residual_qty(self) -> D:
        return abs(self.yes_qty - self.no_qty)

    def residual_side(self) -> str | None:
        if self.yes_qty > self.no_qty:
            return "YES"
        if self.no_qty > self.yes_qty:
            return "NO"
        return None

    def matched_qty(self) -> D:
        return min(self.yes_qty, self.no_qty)

    def on_book(self, yes_bid: D, yes_ask: D, *, residual_cap: D, stale_pull_bps: D = D("0")) -> None:
        if not (ZERO < yes_bid < yes_ask < ONE):
            self.yes_quote = self.no_quote = None
            return
        self.book_updates += 1
        mid = (yes_bid + yes_ask) / D("2")
        # Crude pre-fill toxicity guard: if midpoint jumped more than threshold since
        # prior snapshot, pull both quotes for this update rather than crediting a fill.
        if self.last_mid is not None and stale_pull_bps > ZERO:
            move = abs(mid - self.last_mid) * D("10000")
            self.last_mid = mid
            if move >= stale_pull_bps:
                self.yes_quote = self.no_quote = None
                return
        self.last_mid = mid

        rside = self.residual_side()
        rqty = self.residual_qty()
        # Continuous two-sided quoting while balanced. Once residual exists, stop
        # adding the overweight side and work only the complement until repaired.
        if rside is None:
            self.yes_quote = yes_bid
            self.no_quote = ONE - yes_ask
        elif rside == "YES":
            self.yes_quote = None
            self.no_quote = ONE - yes_ask if rqty <= residual_cap else ONE - yes_ask
        else:
            self.yes_quote = yes_bid if rqty <= residual_cap else yes_bid
            self.no_quote = None

        midpoint = mid
        no_mid = ONE - midpoint
        if (
            self.quote_qty >= Q10
            and self.yes_quote is not None
            and self.no_quote is not None
            and midpoint - self.yes_quote <= D("0.10")
            and no_mid - self.no_quote <= D("0.10")
        ):
            self.eligible_book_updates += 1

    def _add(self, side: str, price: D, qty: D) -> None:
        fee = gemini_predictions_maker_fee(price, qty)
        rebate = projected_maker_rebate(price, qty)
        lot = Lot(qty, price, fee, rebate)
        if side == "YES":
            self.yes.append(lot); self.yes_qty += qty
        else:
            self.no.append(lot); self.no_qty += qty
        self.fill_count += 1

    def on_trade(self, price: D, quantity: D, buyer_maker: bool, *, residual_cap: D) -> None:
        if quantity <= ZERO:
            return
        if self.yes_quote is not None and buyer_maker and price <= self.yes_quote:
            room = max(ZERO, residual_cap - max(ZERO, self.yes_qty - self.no_qty))
            qty = min(quantity, self.quote_qty, room if self.yes_qty >= self.no_qty else self.quote_qty)
            if qty > ZERO:
                self._add("YES", self.yes_quote, qty)
            return
        if self.no_quote is not None and (not buyer_maker) and price >= ONE - self.no_quote:
            room = max(ZERO, residual_cap - max(ZERO, self.no_qty - self.yes_qty))
            qty = min(quantity, self.quote_qty, room if self.no_qty >= self.yes_qty else self.quote_qty)
            if qty > ZERO:
                self._add("NO", self.no_quote, qty)

    def economics(self, toxicity_per_contract: D) -> dict[str, D]:
        # Average-cost matched-book accounting. This is intentionally conservative
        # about toxicity: every filled contract pays the stress penalty, regardless
        # of whether it later becomes matched.
        yes_cost = sum((x.qty * x.price + x.fee for x in self.yes), ZERO)
        no_cost = sum((x.qty * x.price + x.fee for x in self.no), ZERO)
        yes_rebate = sum((x.rebate for x in self.yes), ZERO)
        no_rebate = sum((x.rebate for x in self.no), ZERO)
        matched = self.matched_qty()
        total_qty = self.yes_qty + self.no_qty
        if self.yes_qty > ZERO:
            yes_unit = yes_cost / self.yes_qty
        else:
            yes_unit = ZERO
        if self.no_qty > ZERO:
            no_unit = no_cost / self.no_qty
        else:
            no_unit = ZERO
        matched_edge = matched * (ONE - yes_unit - no_unit)
        toxicity = total_qty * toxicity_per_contract
        return {
            "matched_qty": matched,
            "matched_trade_edge_after_fees": matched_edge,
            "residual_qty": self.residual_qty(),
            "projected_maker_rebates_not_realized": yes_rebate + no_rebate,
            "toxicity_penalty": toxicity,
            "trade_edge_after_toxicity": matched_edge - toxicity,
        }


def replay_capture(path: Path, *, residual_cap: D = Q10, stale_pull_bps: D = ZERO,
                   toxicity_grid: tuple[D, ...] = (D("0"), D("0.001"), D("0.0025"), D("0.005"), D("0.01"), D("0.02"))) -> dict[str, Any]:
    markets: dict[str, Inventory] = {}
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
        inv = markets.setdefault(ticker, Inventory())
        if row.get("type") in {"SHADOW_PLAN", "V4_SHADOW_PLAN"}:
            try:
                inv.on_book(D(str(row["yes_bid"])), D(str(row["yes_ask"])), residual_cap=residual_cap, stale_pull_bps=stale_pull_bps)
                source_books += 1
            except Exception:
                pass
        elif row.get("type") == "PUBLIC_TRADE":
            try:
                inv.on_trade(D(str(row["price"])), D(str(row["quantity"])), bool(row["buyer_maker"]), residual_cap=residual_cap)
                source_trades += 1
            except Exception:
                pass

    sweep = []
    for delta in toxicity_grid:
        matched = residual = edge = rebates = tox = ZERO
        fills = 0
        for inv in markets.values():
            e = inv.economics(delta)
            matched += e["matched_qty"]
            residual += e["residual_qty"]
            edge += e["matched_trade_edge_after_fees"]
            rebates += e["projected_maker_rebates_not_realized"]
            tox += e["toxicity_penalty"]
            fills += inv.fill_count
        sweep.append({
            "toxicity_per_filled_contract": str(delta),
            "fills": fills,
            "matched_qty": str(matched),
            "residual_qty": str(residual),
            "matched_trade_edge_after_fees": str(edge),
            "toxicity_penalty": str(tox),
            "trade_edge_after_toxicity": str(edge - tox),
            "projected_maker_rebates_not_realized": str(rebates),
            "projected_combined_after_toxicity_if_rebates_paid": str(edge - tox + rebates),
        })

    positive = [D(x["toxicity_per_filled_contract"]) for x in sweep if D(x["trade_edge_after_toxicity"]) >= ZERO]
    break_even_floor = max(positive) if positive else None
    total_books = sum(x.book_updates for x in markets.values())
    eligible = sum(x.eligible_book_updates for x in markets.values())
    return {
        "source": str(path),
        "source_rows": source_rows,
        "source_books": source_books,
        "source_trades": source_trades,
        "markets": len(markets),
        "residual_cap_per_market": str(residual_cap),
        "stale_pull_threshold_bps_per_book_update": str(stale_pull_bps),
        "toxicity_sweep": sweep,
        "break_even_toxicity_lower_bound_from_grid": str(break_even_floor) if break_even_floor is not None else None,
        "liquidity_reward_rule_eligible_book_updates": eligible,
        "book_updates": total_books,
        "liquidity_reward_rule_eligible_fraction": eligible / total_books if total_books else 0.0,
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
        "classification": "READ_ONLY_INVENTORY_MAKER_TOXICITY_SHADOW_NOT_REALIZED",
        "warning": "Tape-touch replay has no queue model. Toxicity sweep is a stress test, not observed post-fill drift. Liquidity rewards are not counted as realized.",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--residual-cap", type=D, default=Q10)
    ap.add_argument("--stale-pull-bps", type=D, default=ZERO)
    args = ap.parse_args()
    result = replay_capture(args.capture, residual_cap=args.residual_cap, stale_pull_bps=args.stale_pull_bps)
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
