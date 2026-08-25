"""Deduplicate exact FanDuel <-> Polymarket preview rows into actual trade instructions.

This is still preview-only. FanDuel is manual-only, no capital is reserved, and
settlement-rule differences mean these rows are LIVE_RULE_RISK opportunities,
not guaranteed arbitrage. No order creation path exists here.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from typing import Any, Mapping

from src.fanduel_poly_exact_preview import main_async as run_exact_preview

D = Decimal
ONE = D("1")


def _fd_instruction(row: Mapping[str, Any]) -> dict[str, Any]:
    market_id = str(row.get("fanduel_market_id") or "")
    parts = market_id.split(":")
    kind = parts[1] if len(parts) > 1 else ""
    action = str(row.get("fanduel_semantic_action") or "").upper()
    cost = D(str(row.get("fanduel_implied_cost") or "0"))
    decimal_odds = (ONE / cost) if cost > 0 else D("0")

    selection = market_id
    family = kind
    if kind == "h2h" and len(parts) >= 3:
        selection = f"{parts[2]} moneyline" if action == "YES" else f"opponent of {parts[2]} moneyline"
        family = "moneyline"
    elif kind == "spread" and len(parts) >= 4:
        point = parts[3]
        selection = f"{parts[2]} {point}" if action == "YES" else f"opposite side of {parts[2]} {point}"
        family = "spread"
    elif kind == "total" and len(parts) >= 3:
        selection = f"OVER {parts[2]}" if action == "YES" else f"UNDER {parts[2]}"
        family = "game_total"

    if family == "moneyline":
        rule_risk = (
            "FanDuel moneyline uses the 4.5/5-inning settlement rule and a shorter "
            "suspension window; Polymarket follows official-result/reschedule rules."
        )
    else:
        rule_risk = (
            "FanDuel run-line/total settlement generally requires 8.5/9 innings unless "
            "already determined; Polymarket can use the declared official result for a "
            "shortened game and has different suspension/reschedule handling."
        )

    return {
        "family": family,
        "selection": selection,
        "semantic_action": action,
        "stake_for_1usd_gross_payout": str(cost),
        "observed_decimal_odds": str(decimal_odds),
        "market_id": market_id,
        "rule_risk": rule_risk,
    }


def _actual_trade_key(row: Mapping[str, Any]) -> tuple[str, str]:
    # Multiple semantic settlement keys can encode the same actual two-leg trade
    # (e.g. YES SF and NO CIN at FanDuel). The actual Polymarket instrument+side
    # uniquely anchors the counterpart after exact-key/date validation.
    return str(row.get("slug") or ""), str(row.get("poly_side") or "")


def actionable_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    qualified = [
        row for row in (result.get("rows") or [])
        if isinstance(row, Mapping)
        and row.get("status") == "PREVIEW_QUALIFIED_RULE_AUDIT_REQUIRED"
        and row.get("economics_ok_3c") is True
        and row.get("authenticated_preview_ok") is True
    ]
    # Prefer the direct FanDuel YES representation so instructions say exactly
    # which side to bet instead of describing a complementary NO representation.
    qualified.sort(key=lambda row: 0 if str(row.get("fanduel_semantic_action") or "").upper() == "YES" else 1)

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in qualified:
        key = _actual_trade_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "classification": "LIVE_RULE_RISK_PREVIEW_QUALIFIED_NOT_GUARANTEED_ARB",
            "settlement_key": str(row.get("settlement_key") or ""),
            "polymarket": {
                "slug": str(row.get("slug") or ""),
                "buy_side": str(row.get("poly_side") or ""),
                "max_observed_raw_price": str(row.get("poly_raw_ask") or ""),
                "q1_fee": str(row.get("poly_fee") or ""),
                "q1_depth": str(row.get("poly_q1_depth") or ""),
                "authenticated_preview_ok": True,
            },
            "fanduel": _fd_instruction(row),
            "modeled_normal_rules_total_capital": str(row.get("refreshed_all_in_cost") or ""),
            "modeled_normal_rules_profit_per_1usd_payout": str(row.get("refreshed_locked_profit") or ""),
            "discovery_profit": str(row.get("discovery_unit_profit") or ""),
            "quote_skew_ms": row.get("discovery_quote_skew_ms"),
            "blockers": ["SETTLEMENT_RULE_MISMATCH_RISK", "FANDUEL_MANUAL_ONLY", "CAPITAL_NOT_RESERVED"],
            "order_entry": False,
            "realized": False,
        })
    return out


async def main_async() -> dict[str, Any]:
    preview = await run_exact_preview()
    rows = actionable_rows(preview)
    result = {
        "status": "FANDUEL_POLY_ACTIONABLE_PREVIEW",
        "actual_trade_count": len(rows),
        "trades": rows,
        "rule_certified": False,
        "guaranteed_arbitrage": False,
        "fanduel_transport": "MANUAL_ONLY",
        "capital_reserved": False,
        "order_entry": False,
        "realized_pnl_usd": "0",
    }
    print("FANDUEL_POLY_ACTIONABLE_PREVIEW " + json.dumps(result, separators=(",", ":"), default=str), flush=True)
    return result


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
