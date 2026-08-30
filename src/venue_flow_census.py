"""Policy-free passive-flow census for prediction-market venues.

The census measures a venue's *ceiling* for L5-style two-sided passive inventory
formation without assuming any quoting policy. It answers: if we had been the
passive counterparty to every observed trade on both outcome sides, how much of
that flow could have formed matched inventory and how much directional residual
would necessarily remain?

Current adapter: retained Gemini PUBLIC_TRADE tape, where buyer_maker=True maps
to passive YES-bid flow and buyer_maker=False maps to passive NO-bid-equivalent
flow. The core operates on normalized trades so Polymarket US / polymarket.com
adapters can be added without changing the census logic.

Read-only. No credentials. No order entry. No realized-P&L claims.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal as D
from pathlib import Path
from typing import Iterable

ZERO = D("0")


@dataclass(frozen=True)
class PassiveTrade:
    venue: str
    market: str
    passive_side: str  # YES or NO
    quantity: D
    timestamp: float | None = None


@dataclass
class WindowFlow:
    yes_flow: D = ZERO
    no_flow: D = ZERO
    yes_trades: int = 0
    no_trades: int = 0
    sides: list[str] = field(default_factory=list)

    def add(self, trade: PassiveTrade) -> None:
        if trade.quantity <= ZERO or trade.passive_side not in {"YES", "NO"}:
            return
        if trade.passive_side == "YES":
            self.yes_flow += trade.quantity
            self.yes_trades += 1
        else:
            self.no_flow += trade.quantity
            self.no_trades += 1
        self.sides.append(trade.passive_side)

    @property
    def matched_ceiling(self) -> D:
        return min(self.yes_flow, self.no_flow)

    @property
    def residual_floor(self) -> D:
        return abs(self.yes_flow - self.no_flow)

    @property
    def alternation_rate(self) -> float | None:
        if len(self.sides) < 2:
            return None
        flips = sum(1 for a, b in zip(self.sides, self.sides[1:]) if a != b)
        return flips / (len(self.sides) - 1)

    @property
    def two_sided(self) -> bool:
        return self.yes_flow > ZERO and self.no_flow > ZERO


def census(trades: Iterable[PassiveTrade]) -> dict:
    windows: dict[tuple[str, str], WindowFlow] = defaultdict(WindowFlow)
    for trade in trades:
        windows[(trade.venue, trade.market)].add(trade)

    detail = {}
    total_matched = total_residual = total_yes = total_no = ZERO
    total_trades = two_sided_windows = 0
    alternations: list[float] = []

    for (venue, market), flow in sorted(windows.items()):
        matched = flow.matched_ceiling
        residual = flow.residual_floor
        total_yes += flow.yes_flow
        total_no += flow.no_flow
        total_matched += matched
        total_residual += residual
        total_trades += flow.yes_trades + flow.no_trades
        if flow.two_sided:
            two_sided_windows += 1
        alt = flow.alternation_rate
        if alt is not None:
            alternations.append(alt)
        detail[f"{venue}:{market}"] = {
            "venue": venue,
            "market": market,
            "yes_passive_flow": str(flow.yes_flow),
            "no_passive_flow": str(flow.no_flow),
            "yes_trade_count": flow.yes_trades,
            "no_trade_count": flow.no_trades,
            "trade_count": flow.yes_trades + flow.no_trades,
            "max_matched_formation_ceiling": str(matched),
            "minimum_residual_if_all_flow_captured": str(residual),
            "matched_to_residual_ceiling_ratio": (str(matched / residual) if residual > ZERO else None),
            "alternation_rate": alt,
            "two_sided_flow": flow.two_sided,
        }

    ratio = total_matched / total_residual if total_residual > ZERO else None
    n = len(windows)
    return {
        "windows": n,
        "trade_count": total_trades,
        "avg_trades_per_window": (total_trades / n if n else 0.0),
        "two_sided_windows": two_sided_windows,
        "two_sided_window_fraction": (two_sided_windows / n if n else 0.0),
        "yes_passive_flow": str(total_yes),
        "no_passive_flow": str(total_no),
        "max_matched_formation_ceiling": str(total_matched),
        "minimum_residual_if_all_flow_captured": str(total_residual),
        "matched_to_residual_ceiling_ratio": (str(ratio) if ratio is not None else None),
        "mean_window_alternation_rate": (sum(alternations) / len(alternations) if alternations else None),
        "markets_detail": detail,
        "classification": "READ_ONLY_POLICY_FREE_VENUE_FLOW_CENSUS_NOT_REALIZED",
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
        "warning": "This is an observed-flow ceiling, not an executable fill estimate. Capturing every passive-side trade is impossible in practice and ignores queue position, quote price, fees, and adverse selection.",
    }


def gemini_trades_from_capture(path: Path) -> list[PassiveTrade]:
    out: list[PassiveTrade] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except Exception:
            continue
        if row.get("type") != "PUBLIC_TRADE":
            continue
        market = str(row.get("ticker") or "")
        if not market:
            continue
        try:
            qty = D(str(row["quantity"]))
        except Exception:
            continue
        # Gemini tape expresses YES-contract trades. A resting buyer of YES is
        # passive YES-side inventory; a resting seller of YES is equivalent to
        # passive NO-side inventory for complete-set accounting.
        passive_side = "YES" if bool(row.get("buyer_maker")) else "NO"
        ts_raw = row.get("ts") or row.get("timestamp")
        try:
            ts = float(ts_raw) if ts_raw is not None else None
        except Exception:
            ts = None
        out.append(PassiveTrade("gemini", market, passive_side, qty, ts))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("--venue", choices=["gemini"], default="gemini")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    trades = gemini_trades_from_capture(args.capture)
    result = census(trades)
    result["source"] = str(args.capture)
    result["venue_adapter"] = args.venue
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
