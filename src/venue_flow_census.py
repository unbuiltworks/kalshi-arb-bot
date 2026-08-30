"""Policy-free / policy-light passive-flow census for prediction-market venues.

The raw census measures a venue's *ceiling* for L5-style two-sided passive inventory
formation without assuming a quote placement policy. The q-aware cap curve then adds
only two execution constraints that are unavoidable for a bounded maker:

1. at most q contracts can be filled from any one observed trade event; and
2. heavy-side inventory is not allowed to exceed residual cap R.

This is still an optimistic observed-flow ceiling: it assumes our resting quote would
have participated in every qualifying event and ignores queue position, price level,
fees, and adverse selection.

Current adapter: retained Gemini PUBLIC_TRADE tape, where buyer_maker=True maps
to passive YES-bid flow and buyer_maker=False maps to passive NO-bid-equivalent
flow. The normalized core is venue-agnostic so Polymarket US / polymarket.com
adapters can be added without changing census logic.

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
Q10 = D("10")
DEFAULT_RESIDUAL_CAPS = (D("10"), D("25"), D("50"))
# L5 calibration only: $1,510.80 / 38,956 matched pairs ~= 3.878c/pair.
# We intentionally round to 3.9c and label the result as a benchmark-calibrated
# gross ceiling, not Gemini expected P&L.
L5_GROSS_LOCKED_EDGE_PER_MATCHED_PAIR = D("0.039")


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
    trades: list[PassiveTrade] = field(default_factory=list)

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
        self.trades.append(trade)

    @property
    def matched_ceiling(self) -> D:
        return min(self.yes_flow, self.no_flow)

    @property
    def residual_floor_if_all_flow_captured(self) -> D:
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


def _sorted_trades(trades: list[PassiveTrade]) -> list[PassiveTrade]:
    # Preserve capture order when timestamps are absent/equal; Python sort is stable.
    return sorted(trades, key=lambda t: float("inf") if t.timestamp is None else t.timestamp)


def q_aware_residual_cap_ceiling(
    trades: Iterable[PassiveTrade],
    *,
    q: D = Q10,
    residual_cap: D,
) -> dict[str, D | int]:
    """Optimistic chronological ceiling for a bounded-q passive maker.

    Every observed trade event can contribute at most q contracts. Light-side flow
    is always accepted because it reduces existing imbalance. Heavy-side flow is
    accepted only up to the remaining residual budget. This intentionally does not
    model quote price or queue position.
    """
    yes = no = ZERO
    accepted_events = 0
    skipped_heavy_events = 0

    for trade in _sorted_trades(list(trades)):
        event_qty = min(q, trade.quantity)
        if event_qty <= ZERO:
            continue

        if trade.passive_side == "YES":
            side_qty, other_qty = yes, no
        elif trade.passive_side == "NO":
            side_qty, other_qty = no, yes
        else:
            continue

        current_imbalance = abs(yes - no)
        is_light_side = side_qty < other_qty

        if is_light_side:
            accepted = event_qty
        else:
            # When balanced, this side becomes heavy; when already heavy it adds risk.
            remaining = residual_cap - current_imbalance
            accepted = max(ZERO, min(event_qty, remaining))
            if accepted <= ZERO:
                skipped_heavy_events += 1
                continue

        if trade.passive_side == "YES":
            yes += accepted
        else:
            no += accepted
        accepted_events += 1

    matched = min(yes, no)
    residual = abs(yes - no)
    ratio = matched / residual if residual > ZERO else None
    return {
        "yes_accepted": yes,
        "no_accepted": no,
        "matched": matched,
        "residual": residual,
        "ratio": ratio,
        "accepted_events": accepted_events,
        "skipped_heavy_events": skipped_heavy_events,
    }


def census(
    trades: Iterable[PassiveTrade],
    *,
    q: D = Q10,
    residual_caps: tuple[D, ...] = DEFAULT_RESIDUAL_CAPS,
    gross_edge_per_matched_pair: D = L5_GROSS_LOCKED_EDGE_PER_MATCHED_PAIR,
) -> dict:
    windows: dict[tuple[str, str], WindowFlow] = defaultdict(WindowFlow)
    for trade in trades:
        windows[(trade.venue, trade.market)].add(trade)

    detail = {}
    total_matched = total_residual = total_yes = total_no = ZERO
    total_trades = two_sided_windows = 0
    alternations: list[float] = []
    cap_totals: dict[D, dict[str, D | int]] = {
        cap: {"matched": ZERO, "residual": ZERO, "accepted_events": 0, "skipped_heavy_events": 0}
        for cap in residual_caps
    }

    for (venue, market), flow in sorted(windows.items()):
        matched = flow.matched_ceiling
        residual = flow.residual_floor_if_all_flow_captured
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

        cap_curve = {}
        for cap in residual_caps:
            c = q_aware_residual_cap_ceiling(flow.trades, q=q, residual_cap=cap)
            cap_curve[str(cap)] = {
                "matched_ceiling": str(c["matched"]),
                "ending_residual": str(c["residual"]),
                "matched_to_residual_ratio": (str(c["ratio"]) if c["ratio"] is not None else None),
                "accepted_events": c["accepted_events"],
                "skipped_heavy_events": c["skipped_heavy_events"],
                "l5_calibrated_gross_locked_dollar_ceiling": str(D(c["matched"]) * gross_edge_per_matched_pair),
            }
            cap_totals[cap]["matched"] = D(cap_totals[cap]["matched"]) + D(c["matched"])
            cap_totals[cap]["residual"] = D(cap_totals[cap]["residual"]) + D(c["residual"])
            cap_totals[cap]["accepted_events"] = int(cap_totals[cap]["accepted_events"]) + int(c["accepted_events"])
            cap_totals[cap]["skipped_heavy_events"] = int(cap_totals[cap]["skipped_heavy_events"]) + int(c["skipped_heavy_events"])

        detail[f"{venue}:{market}"] = {
            "venue": venue,
            "market": market,
            "yes_passive_flow": str(flow.yes_flow),
            "no_passive_flow": str(flow.no_flow),
            "yes_trade_count": flow.yes_trades,
            "no_trade_count": flow.no_trades,
            "trade_count": flow.yes_trades + flow.no_trades,
            "raw_contract_matched_ceiling": str(matched),
            "raw_residual_if_all_flow_captured": str(residual),
            "raw_matched_to_residual_ratio": (str(matched / residual) if residual > ZERO else None),
            "alternation_rate_informational_only": alt,
            "two_sided_flow": flow.two_sided,
            "q_aware_residual_cap_curve": cap_curve,
        }

    raw_ratio = total_matched / total_residual if total_residual > ZERO else None
    n = len(windows)
    aggregate_cap_curve = {}
    for cap in residual_caps:
        m = D(cap_totals[cap]["matched"])
        r = D(cap_totals[cap]["residual"])
        ratio = m / r if r > ZERO else None
        aggregate_cap_curve[str(cap)] = {
            "q": str(q),
            "residual_cap_per_window": str(cap),
            "matched_ceiling": str(m),
            "ending_residual": str(r),
            "matched_to_residual_ratio": (str(ratio) if ratio is not None else None),
            "accepted_events": cap_totals[cap]["accepted_events"],
            "skipped_heavy_events": cap_totals[cap]["skipped_heavy_events"],
            "l5_calibrated_gross_locked_dollar_ceiling": str(m * gross_edge_per_matched_pair),
        }

    return {
        "windows": n,
        "trade_count": total_trades,
        "avg_trades_per_window": (total_trades / n if n else 0.0),
        "two_sided_windows": two_sided_windows,
        "two_sided_window_fraction": (two_sided_windows / n if n else 0.0),
        "yes_passive_flow": str(total_yes),
        "no_passive_flow": str(total_no),
        "raw_contract_matched_ceiling": str(total_matched),
        "raw_residual_if_all_flow_captured": str(total_residual),
        "raw_matched_to_residual_ratio": (str(raw_ratio) if raw_ratio is not None else None),
        "mean_window_alternation_rate_informational_only": (sum(alternations) / len(alternations) if alternations else None),
        "q_aware_residual_cap_curve": aggregate_cap_curve,
        "gross_edge_calibration_per_matched_pair": str(gross_edge_per_matched_pair),
        "gross_edge_calibration_source": "L5 one-afternoon realized locked book; calibration only, not Gemini expected value",
        "markets_detail": detail,
        "classification": "READ_ONLY_POLICY_FREE_VENUE_FLOW_CENSUS_NOT_REALIZED",
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
        "warning": "Raw contract flow materially overstates a bounded maker's opportunity when trade events are larger than q. The q-aware cap curve is still optimistic: it ignores queue position, quote price, fees, and adverse selection. Alternation is informational, not a gate metric.",
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
    ap.add_argument("--q", type=D, default=Q10)
    ap.add_argument("--residual-caps", default="10,25,50")
    args = ap.parse_args()
    caps = tuple(D(x.strip()) for x in args.residual_caps.split(",") if x.strip())
    trades = gemini_trades_from_capture(args.capture)
    result = census(trades, q=args.q, residual_caps=caps)
    result["source"] = str(args.capture)
    result["venue_adapter"] = args.venue
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
