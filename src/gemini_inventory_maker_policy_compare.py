"""Read-only A/B/C comparison for Gemini q=10 maker policies.

A: conservative completion policy (existing replay: stop adding overweight side).
B: naive continuous two-sided maker (L5-like inventory tolerance).
C: residual-controlled toxicity-aware maker.

All results are shadow/replay only. No credentials. No order entry.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from decimal import Decimal as D
from pathlib import Path
from typing import Any

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_incentive_mm_replay_v1 import projected_maker_rebate, replay_capture as conservative_replay
from src.gemini_inventory_maker_toxicity_shadow import replay_capture as controlled_replay

ZERO = D("0")
ONE = D("1")
Q10 = D("10")
TOX_GRID = (D("0"), D("0.001"), D("0.0025"), D("0.005"), D("0.01"), D("0.02"), D("0.03"), D("0.04"))


@dataclass
class NaiveMarket:
    yes_qty: D = ZERO
    no_qty: D = ZERO
    yes_cost: D = ZERO
    no_cost: D = ZERO
    rebates: D = ZERO
    yes_quote: D | None = None
    no_quote: D | None = None
    fills: int = 0
    book_updates: int = 0
    eligible_book_updates: int = 0

    def on_book(self, yes_bid: D, yes_ask: D) -> None:
        if not (ZERO < yes_bid < yes_ask < ONE):
            self.yes_quote = self.no_quote = None
            return
        self.book_updates += 1
        self.yes_quote = yes_bid
        self.no_quote = ONE - yes_ask
        mid = (yes_bid + yes_ask) / D("2")
        if mid - self.yes_quote <= D("0.10") and (ONE-mid) - self.no_quote <= D("0.10"):
            self.eligible_book_updates += 1

    def add(self, side: str, price: D, qty: D) -> None:
        fee = gemini_predictions_maker_fee(price, qty)
        cost = price * qty + fee
        self.rebates += projected_maker_rebate(price, qty)
        if side == "YES":
            self.yes_qty += qty; self.yes_cost += cost
        else:
            self.no_qty += qty; self.no_cost += cost
        self.fills += 1

    def on_trade(self, price: D, qty: D, buyer_maker: bool) -> None:
        if qty <= ZERO:
            return
        q = min(qty, Q10)
        if self.yes_quote is not None and buyer_maker and price <= self.yes_quote:
            self.add("YES", self.yes_quote, q)
            return
        if self.no_quote is not None and (not buyer_maker) and price >= ONE - self.no_quote:
            self.add("NO", self.no_quote, q)

    def economics(self) -> dict[str, D]:
        matched = min(self.yes_qty, self.no_qty)
        yunit = self.yes_cost / self.yes_qty if self.yes_qty else ZERO
        nunit = self.no_cost / self.no_qty if self.no_qty else ZERO
        edge = matched * (ONE - yunit - nunit)
        return {"matched": matched, "residual": abs(self.yes_qty-self.no_qty), "edge": edge, "filled_qty": self.yes_qty+self.no_qty}


def naive_replay(path: Path) -> dict[str, Any]:
    markets: dict[str, NaiveMarket] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        try: row = json.loads(raw)
        except Exception: continue
        ticker = str(row.get("ticker") or "")
        if not ticker: continue
        m = markets.setdefault(ticker, NaiveMarket())
        if row.get("type") in {"SHADOW_PLAN", "V4_SHADOW_PLAN"}:
            try: m.on_book(D(str(row["yes_bid"])), D(str(row["yes_ask"])))
            except Exception: pass
        elif row.get("type") == "PUBLIC_TRADE":
            try: m.on_trade(D(str(row["price"])), D(str(row["quantity"])), bool(row["buyer_maker"]))
            except Exception: pass
    matched=residual=edge=filled=rebates=ZERO
    fills=books=eligible=0
    for m in markets.values():
        e=m.economics(); matched+=e["matched"]; residual+=e["residual"]; edge+=e["edge"]; filled+=e["filled_qty"]
        rebates+=m.rebates; fills+=m.fills; books+=m.book_updates; eligible+=m.eligible_book_updates
    exact_be = edge/filled if filled > ZERO and edge > ZERO else ZERO
    return {"fills":fills,"filled_qty":str(filled),"matched_qty":str(matched),"residual_qty":str(residual),"matched_trade_edge_after_fees":str(edge),"exact_break_even_toxicity_per_filled_contract":str(exact_be),"projected_maker_rebates_not_realized":str(rebates),"liquidity_reward_rule_eligible_fraction":eligible/books if books else 0.0}


def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument("capture",type=Path); ap.add_argument("--output",type=Path); ap.add_argument("--stale-pull-bps",type=D,default=D("10")); args=ap.parse_args()
    cons=conservative_replay(args.capture)
    naive=naive_replay(args.capture)
    ctrl=controlled_replay(args.capture,residual_cap=Q10,stale_pull_bps=args.stale_pull_bps,toxicity_grid=TOX_GRID)
    c0=ctrl["toxicity_sweep"][0]
    filled_qty = D(ctrl["toxicity_sweep"][1]["toxicity_penalty"])/D("0.001") if len(ctrl["toxicity_sweep"])>1 else ZERO
    ctrl_be = D(c0["matched_trade_edge_after_fees"])/filled_qty if filled_qty>ZERO and D(c0["matched_trade_edge_after_fees"])>ZERO else ZERO
    result={
      "conservative_completion": {"fills":sum(s["fills"] for s in cons["markets_detail"].values()),"matched_qty":cons["matched_quantity"],"residual_qty":cons["residual_quantity"],"locked_trade_edge_after_fees":cons["locked_trade_edge_after_fees"],"projected_maker_rebates_not_realized":cons["projected_maker_rebates_not_realized"]},
      "naive_continuous_two_sided": naive,
      "residual_controlled_toxicity_aware": {"fills":c0["fills"],"filled_qty":str(filled_qty),"matched_qty":c0["matched_qty"],"residual_qty":c0["residual_qty"],"matched_trade_edge_after_fees":c0["matched_trade_edge_after_fees"],"exact_break_even_toxicity_per_filled_contract":str(ctrl_be),"projected_maker_rebates_not_realized":c0["projected_maker_rebates_not_realized"],"liquidity_reward_rule_eligible_fraction":ctrl["liquidity_reward_rule_eligible_fraction"],"toxicity_sweep":ctrl["toxicity_sweep"]},
      "order_entry":False,"credentials_loaded":False,"realized_pnl":None,"classification":"READ_ONLY_Q10_POLICY_COMPARISON_NOT_REALIZED",
      "warning":"Tape-touch replay has no queue model. Compare robustness, not headline P&L. Rewards are projected only."
    }
    text=json.dumps(result,indent=2,sort_keys=True)
    if args.output: args.output.write_text(text+"\n",encoding="utf-8")
    print(text)

if __name__ == "__main__": main()
