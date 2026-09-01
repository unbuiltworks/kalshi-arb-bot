"""Stateful, read-only replay of the bounded l5 scaling hypothesis on Kalshi.

Input is JSONL emitted by :mod:`src.kalshi_l5_threshold_shadow`. The replay
turns quote observations into a deliberately pessimistic maker-fill proxy:

* a virtual buy rests at the prior sampled maker quote;
* touch is NOT a fill;
* fill is credited only if the next observed opposite ask strictly trades
  through the resting price;
* the resting order is then canceled/replaced at the next sampled quote;
* each outcome can accumulate at most three contracts, corresponding to the
  60/70/80% threshold ladder in the first bounded hypothesis.

This is a measurement model, not an executable strategy and not realized P&L.
Settlement economics are shown as portfolio payoffs under both possible outcomes.
Maker fees are presented in two columns: zero-maker-fee and the published general
Kalshi maker-fee formula *if that fee applies to the series*. The replay never
claims the modeled fee was actually charged.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from decimal import Decimal as D, ROUND_CEILING
import json
from pathlib import Path
from typing import Any, Iterable

ONE = D("1")
CENT = D("0.01")
MAX_UNITS_PER_OUTCOME = D("3")
GENERAL_MAKER_RATE = D("0.0175")


def kalshi_general_maker_fee(price: D, contracts: D) -> D:
    """Published general maker-fee formula, rounded up to the next cent.

    This is a scenario model only. Kalshi charges maker fees only on markets
    listed as maker-fee markets in the current fee schedule.
    """
    price = D(str(price))
    contracts = D(str(contracts))
    if contracts <= 0:
        return D("0")
    raw = GENERAL_MAKER_RATE * contracts * price * (ONE - price)
    if raw <= 0:
        return D("0")
    return (raw / CENT).to_integral_value(rounding=ROUND_CEILING) * CENT


@dataclass
class Fill:
    side: str
    price: D
    quantity: D
    modeled_maker_fee_if_applicable: D
    observed_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "price": str(self.price),
            "quantity": str(self.quantity),
            "modeled_maker_fee_if_applicable": str(self.modeled_maker_fee_if_applicable),
            "observed_at": self.observed_at,
        }


@dataclass
class MarketReplay:
    ticker: str
    series: str
    yes_qty: D = D("0")
    no_qty: D = D("0")
    yes_cost: D = D("0")
    no_cost: D = D("0")
    modeled_maker_fees_if_applicable: D = D("0")
    fills: list[Fill] = field(default_factory=list)
    samples: int = 0
    threshold_samples: int = 0
    side_switches: int = 0
    last_favored_side: str | None = None
    pending_yes_quote: D | None = None
    pending_yes_qty: D = D("0")
    pending_no_quote: D | None = None
    pending_no_qty: D = D("0")

    def _fill(self, side: str, price: D, quantity: D, observed_at: str | None) -> None:
        if quantity <= 0:
            return
        if side == "YES":
            remaining = max(D("0"), MAX_UNITS_PER_OUTCOME - self.yes_qty)
        elif side == "NO":
            remaining = max(D("0"), MAX_UNITS_PER_OUTCOME - self.no_qty)
        else:
            raise ValueError(side)
        quantity = min(quantity, remaining)
        if quantity <= 0:
            return
        fee = kalshi_general_maker_fee(price, quantity)
        if side == "YES":
            self.yes_qty += quantity
            self.yes_cost += price * quantity
        else:
            self.no_qty += quantity
            self.no_cost += price * quantity
        self.modeled_maker_fees_if_applicable += fee
        self.fills.append(Fill(side, price, quantity, fee, observed_at))

    @property
    def yes_vwap(self) -> D | None:
        return self.yes_cost / self.yes_qty if self.yes_qty else None

    @property
    def no_vwap(self) -> D | None:
        return self.no_cost / self.no_qty if self.no_qty else None

    @property
    def total_cost(self) -> D:
        return self.yes_cost + self.no_cost

    @property
    def matched_qty(self) -> D:
        return min(self.yes_qty, self.no_qty)

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

    @property
    def matched_vwap_pair_cost(self) -> D | None:
        if self.matched_qty <= 0 or self.yes_vwap is None or self.no_vwap is None:
            return None
        return self.yes_vwap + self.no_vwap

    @property
    def matched_gross_margin(self) -> D | None:
        pair_cost = self.matched_vwap_pair_cost
        if pair_cost is None:
            return None
        return self.matched_qty * (ONE - pair_cost)

    def payoff(self, resolution: str, *, include_modeled_maker_fee: bool = False) -> D:
        payout = self.yes_qty if resolution == "YES" else self.no_qty
        fee = self.modeled_maker_fees_if_applicable if include_modeled_maker_fee else D("0")
        return payout - self.total_cost - fee

    def summary(self) -> dict[str, Any]:
        yes_no_fee = self.payoff("YES")
        no_no_fee = self.payoff("NO")
        yes_with_fee = self.payoff("YES", include_modeled_maker_fee=True)
        no_with_fee = self.payoff("NO", include_modeled_maker_fee=True)
        return {
            "ticker": self.ticker,
            "series": self.series,
            "samples": self.samples,
            "threshold_samples": self.threshold_samples,
            "side_switches": self.side_switches,
            "fills": len(self.fills),
            "fill_chronology": [fill.as_dict() for fill in self.fills],
            "yes_qty": str(self.yes_qty),
            "no_qty": str(self.no_qty),
            "yes_cost": str(self.yes_cost),
            "no_cost": str(self.no_cost),
            "yes_vwap": str(self.yes_vwap) if self.yes_vwap is not None else None,
            "no_vwap": str(self.no_vwap) if self.no_vwap is not None else None,
            "matched_qty": str(self.matched_qty),
            "matched_vwap_pair_cost": str(self.matched_vwap_pair_cost) if self.matched_vwap_pair_cost is not None else None,
            "matched_gross_margin": str(self.matched_gross_margin) if self.matched_gross_margin is not None else None,
            "residual_side": self.residual_side,
            "residual_qty": str(self.residual_qty),
            "total_cost": str(self.total_cost),
            "modeled_general_maker_fee_if_applicable": str(self.modeled_maker_fees_if_applicable),
            "payoff_if_yes_no_maker_fee": str(yes_no_fee),
            "payoff_if_no_no_maker_fee": str(no_no_fee),
            "worst_case_no_maker_fee": str(min(yes_no_fee, no_no_fee)),
            "payoff_if_yes_with_general_maker_fee": str(yes_with_fee),
            "payoff_if_no_with_general_maker_fee": str(no_with_fee),
            "worst_case_with_general_maker_fee": str(min(yes_with_fee, no_with_fee)),
            "classification": "SHADOW_TRADE_THROUGH_REPLAY_NOT_REALIZED",
        }


def _D(row: dict[str, Any], key: str) -> D:
    return D(str(row[key]))


def _desired_qty(row: dict[str, Any]) -> D:
    desired = D(str(row.get("shadow_target_units") or 0))
    return min(MAX_UNITS_PER_OUTCOME, max(D("0"), desired))


def apply_observation(state: MarketReplay, row: dict[str, Any]) -> None:
    """Advance one market replay by one observed two-sided book snapshot."""
    state.samples += 1
    favored = row.get("favored_side")
    if favored in {"YES", "NO"}:
        state.threshold_samples += 1
        if state.last_favored_side and state.last_favored_side != favored:
            state.side_switches += 1
        state.last_favored_side = favored

    yes_ask = _D(row, "yes_ask")
    no_ask = ONE - _D(row, "yes_bid")

    # Credit only strict trade-through of the prior resting order. A mere touch is
    # intentionally not enough because queue position and actual prints are unknown.
    if state.pending_yes_quote is not None and state.pending_yes_qty > 0:
        if yes_ask < state.pending_yes_quote:
            state._fill("YES", state.pending_yes_quote, state.pending_yes_qty, row.get("observed_at"))
    if state.pending_no_quote is not None and state.pending_no_qty > 0:
        if no_ask < state.pending_no_quote:
            state._fill("NO", state.pending_no_quote, state.pending_no_qty, row.get("observed_at"))

    # Cancel/replace both virtual orders each sample. We only seek new inventory on
    # the currently favored side, but previously accumulated opposite inventory is
    # retained exactly as in the working l5 hypothesis.
    state.pending_yes_quote = None
    state.pending_yes_qty = D("0")
    state.pending_no_quote = None
    state.pending_no_qty = D("0")

    desired = _desired_qty(row)
    if favored == "YES" and desired > state.yes_qty:
        state.pending_yes_quote = _D(row, "shadow_yes_quote")
        state.pending_yes_qty = min(MAX_UNITS_PER_OUTCOME - state.yes_qty, desired - state.yes_qty)
    elif favored == "NO" and desired > state.no_qty:
        state.pending_no_quote = _D(row, "shadow_no_quote")
        state.pending_no_qty = min(MAX_UNITS_PER_OUTCOME - state.no_qty, desired - state.no_qty)


def replay_rows(rows: Iterable[dict[str, Any]]) -> dict[str, MarketReplay]:
    states: dict[str, MarketReplay] = {}
    for row in rows:
        if row.get("type") != "KALSHI_L5_THRESHOLD_SHADOW":
            continue
        ticker = str(row.get("ticker") or "")
        series = str(row.get("series") or "")
        if not ticker or not series:
            continue
        state = states.setdefault(ticker, MarketReplay(ticker=ticker, series=series))
        apply_observation(state, row)
    return states


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def aggregate(states: dict[str, MarketReplay]) -> dict[str, Any]:
    summaries = [s.summary() for s in states.values()]
    with_fills = [s for s in states.values() if s.fills]
    both_sides = [s for s in with_fills if s.yes_qty > 0 and s.no_qty > 0]
    sub_one_pairs = [
        s for s in both_sides
        if s.matched_vwap_pair_cost is not None and s.matched_vwap_pair_cost < ONE
    ]
    whole_portfolio_positive = [
        s for s in with_fills if min(s.payoff("YES"), s.payoff("NO")) > 0
    ]
    return {
        "type": "KALSHI_L5_INVENTORY_REPLAY_SUMMARY",
        "markets": len(states),
        "markets_with_trade_through_fills": len(with_fills),
        "markets_with_both_sides_filled": len(both_sides),
        "markets_with_sub_one_matched_vwap": len(sub_one_pairs),
        "markets_with_positive_whole_portfolio_worst_case_no_maker_fee": len(whole_portfolio_positive),
        "total_virtual_fills": sum(len(s.fills) for s in states.values()),
        "max_units_per_outcome": str(MAX_UNITS_PER_OUTCOME),
        "classification": "SHADOW_TRADE_THROUGH_REPLAY_NOT_REALIZED",
        "maker_fee_note": "general 0.0175 quadratic maker formula is scenario-only unless current fee schedule confirms applicability",
        "markets_detail": summaries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = aggregate(replay_rows(load_jsonl(args.input)))
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
