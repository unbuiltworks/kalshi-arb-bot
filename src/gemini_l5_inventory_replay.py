"""Stateful read-only replay for Gemini l5-style BTC/ETH short crypto.

Consumes JSONL from ``gemini_l5_threshold_shadow``. Virtual maker orders are
credited only after strict trade-through on the next public book sample; touch is
not enough. The replay scales 1/2/3 contracts at the 60/70/80% ladder and retains
opposite-side inventory after reversals.

Trading cost, Gemini maker fees, and modeled maker rebates are reported in
separate ledgers. Rebates and liquidity rewards are never classified as realized.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from decimal import Decimal as D
import json
from pathlib import Path
from typing import Any, Iterable

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_l5_threshold_model import maker_rebate_estimate

ONE = D("1")
MAX_UNITS_PER_OUTCOME = D("3")


@dataclass
class Fill:
    side: str
    price: D
    quantity: D
    maker_fee: D
    rebate_raw: D
    rebate_cent_rounded: D
    observed_at: str | None = None


@dataclass
class MarketReplay:
    ticker: str
    asset: str
    horizon: str
    yes_qty: D = D("0")
    no_qty: D = D("0")
    yes_cost: D = D("0")
    no_cost: D = D("0")
    maker_fees: D = D("0")
    modeled_rebate_raw: D = D("0")
    modeled_rebate_cent_rounded: D = D("0")
    fills: list[Fill] = field(default_factory=list)
    samples: int = 0
    threshold_samples: int = 0
    side_switches: int = 0
    last_favored_side: str | None = None
    pending_yes_quote: D | None = None
    pending_yes_qty: D = D("0")
    pending_no_quote: D | None = None
    pending_no_qty: D = D("0")
    reward_pool_active_observed: bool = False

    def _fill(self, side: str, price: D, quantity: D, observed_at: str | None) -> None:
        if quantity <= 0:
            return
        if side == "YES":
            quantity = min(quantity, max(D("0"), MAX_UNITS_PER_OUTCOME - self.yes_qty))
        elif side == "NO":
            quantity = min(quantity, max(D("0"), MAX_UNITS_PER_OUTCOME - self.no_qty))
        else:
            raise ValueError(side)
        if quantity <= 0:
            return
        fee = gemini_predictions_maker_fee(price, quantity)
        rebate_raw, rebate_rounded = maker_rebate_estimate(price, quantity)
        if side == "YES":
            self.yes_qty += quantity
            self.yes_cost += price * quantity
        else:
            self.no_qty += quantity
            self.no_cost += price * quantity
        self.maker_fees += fee
        self.modeled_rebate_raw += rebate_raw
        self.modeled_rebate_cent_rounded += rebate_rounded
        self.fills.append(Fill(side, price, quantity, fee, rebate_raw, rebate_rounded, observed_at))

    @property
    def total_contract_cost(self) -> D:
        return self.yes_cost + self.no_cost

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
    def matched_vwap_pair_cost(self) -> D | None:
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

    def trading_payoff(self, resolution: str) -> D:
        payout = self.yes_qty if resolution == "YES" else self.no_qty
        return payout - self.total_contract_cost - self.maker_fees

    def modeled_economic_payoff_with_rebate(self, resolution: str) -> D:
        # Still NOT realized: the rebate is only a model and reward-pool payments
        # are deliberately excluded entirely.
        return self.trading_payoff(resolution) + self.modeled_rebate_cent_rounded

    def summary(self) -> dict[str, Any]:
        yes_trading = self.trading_payoff("YES")
        no_trading = self.trading_payoff("NO")
        yes_with_rebate = self.modeled_economic_payoff_with_rebate("YES")
        no_with_rebate = self.modeled_economic_payoff_with_rebate("NO")
        return {
            "ticker": self.ticker,
            "asset": self.asset,
            "horizon": self.horizon,
            "samples": self.samples,
            "threshold_samples": self.threshold_samples,
            "side_switches": self.side_switches,
            "fills": len(self.fills),
            "fill_chronology": [
                {
                    "side": fill.side,
                    "price": str(fill.price),
                    "quantity": str(fill.quantity),
                    "maker_fee": str(fill.maker_fee),
                    "rebate_raw": str(fill.rebate_raw),
                    "rebate_cent_rounded": str(fill.rebate_cent_rounded),
                    "observed_at": fill.observed_at,
                }
                for fill in self.fills
            ],
            "yes_qty": str(self.yes_qty),
            "no_qty": str(self.no_qty),
            "yes_cost": str(self.yes_cost),
            "no_cost": str(self.no_cost),
            "yes_vwap": str(self.yes_vwap) if self.yes_vwap is not None else None,
            "no_vwap": str(self.no_vwap) if self.no_vwap is not None else None,
            "matched_qty": str(self.matched_qty),
            "matched_vwap_pair_cost": str(self.matched_vwap_pair_cost) if self.matched_vwap_pair_cost is not None else None,
            "residual_side": self.residual_side,
            "residual_qty": str(self.residual_qty),
            "contract_cost": str(self.total_contract_cost),
            "maker_fees": str(self.maker_fees),
            "modeled_maker_rebate_raw": str(self.modeled_rebate_raw),
            "modeled_maker_rebate_cent_rounded": str(self.modeled_rebate_cent_rounded),
            "reward_pool_active_observed": self.reward_pool_active_observed,
            "trading_payoff_if_yes": str(yes_trading),
            "trading_payoff_if_no": str(no_trading),
            "worst_case_trading_payoff": str(min(yes_trading, no_trading)),
            "modeled_payoff_if_yes_with_rebate": str(yes_with_rebate),
            "modeled_payoff_if_no_with_rebate": str(no_with_rebate),
            "worst_case_modeled_payoff_with_rebate": str(min(yes_with_rebate, no_with_rebate)),
            "liquidity_reward_pnl": None,
            "classification": "SHADOW_TRADE_THROUGH_REPLAY_NOT_REALIZED",
        }


def _dec(row: dict[str, Any], key: str) -> D:
    return D(str(row[key]))


def apply_observation(state: MarketReplay, row: dict[str, Any]) -> None:
    state.samples += 1
    favored = row.get("favored_side")
    if favored in {"YES", "NO"}:
        state.threshold_samples += 1
        if state.last_favored_side and state.last_favored_side != favored:
            state.side_switches += 1
        state.last_favored_side = favored
    state.reward_pool_active_observed |= bool(row.get("reward_pool_active"))

    yes_ask = _dec(row, "yes_ask")
    no_ask = ONE - _dec(row, "yes_bid")

    if state.pending_yes_quote is not None and state.pending_yes_qty > 0:
        if yes_ask < state.pending_yes_quote:
            state._fill("YES", state.pending_yes_quote, state.pending_yes_qty, row.get("observed_at"))
    if state.pending_no_quote is not None and state.pending_no_qty > 0:
        if no_ask < state.pending_no_quote:
            state._fill("NO", state.pending_no_quote, state.pending_no_qty, row.get("observed_at"))

    state.pending_yes_quote = None
    state.pending_yes_qty = D("0")
    state.pending_no_quote = None
    state.pending_no_qty = D("0")

    desired = min(MAX_UNITS_PER_OUTCOME, max(D("0"), D(str(row.get("shadow_target_units") or 0))))
    if favored == "YES" and desired > state.yes_qty:
        state.pending_yes_quote = _dec(row, "shadow_yes_quote")
        state.pending_yes_qty = desired - state.yes_qty
    elif favored == "NO" and desired > state.no_qty:
        state.pending_no_quote = _dec(row, "shadow_no_quote")
        state.pending_no_qty = desired - state.no_qty


def replay_rows(rows: Iterable[dict[str, Any]]) -> dict[str, MarketReplay]:
    states: dict[str, MarketReplay] = {}
    for row in rows:
        if row.get("type") != "GEMINI_L5_THRESHOLD_SHADOW":
            continue
        ticker = str(row.get("ticker") or "")
        asset = str(row.get("asset") or "")
        horizon = str(row.get("horizon") or "")
        if not ticker or not asset or not horizon:
            continue
        state = states.setdefault(ticker, MarketReplay(ticker, asset, horizon))
        apply_observation(state, row)
    return states


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def aggregate(states: dict[str, MarketReplay]) -> dict[str, Any]:
    active = [state for state in states.values() if state.fills]
    both = [state for state in active if state.yes_qty > 0 and state.no_qty > 0]
    sub_one = [
        state for state in both
        if state.matched_vwap_pair_cost is not None and state.matched_vwap_pair_cost < ONE
    ]
    return {
        "type": "GEMINI_L5_INVENTORY_REPLAY_SUMMARY",
        "markets": len(states),
        "markets_with_trade_through_fills": len(active),
        "markets_with_both_sides_filled": len(both),
        "markets_with_sub_one_matched_vwap": len(sub_one),
        "total_virtual_fills": sum(len(state.fills) for state in states.values()),
        "max_units_per_outcome": str(MAX_UNITS_PER_OUTCOME),
        "total_modeled_maker_fees": str(sum((state.maker_fees for state in states.values()), D("0"))),
        "total_modeled_maker_rebate_cent_rounded": str(sum((state.modeled_rebate_cent_rounded for state in states.values()), D("0"))),
        "liquidity_rewards": "NOT_MODELED_OR_REALIZED",
        "classification": "SHADOW_TRADE_THROUGH_REPLAY_NOT_REALIZED",
        "markets_detail": [state.summary() for state in states.values()],
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
