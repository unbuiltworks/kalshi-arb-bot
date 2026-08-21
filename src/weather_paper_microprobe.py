"""Fail-closed weather PAPER micro-probes from exact-market certified SHADOW rows.

No venue client or order-entry path exists here. The <=$1 and <=$5 plans are
alternative sizing views of the same displayed book; only one selected synthetic fill
may be emitted for a quote snapshot.
"""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from typing import Any

from src.cross_venue_ledger import LedgerEvent, make_event
from src.weather_settlement_certified import _fee_cents

D = Decimal
DEFAULT_BUDGETS_CENTS = (100, 500)
DEFAULT_MIN_EDGE_CENTS = D("3")


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _exact_audit_matches(row: dict[str, Any]) -> bool:
    audit = row.get("exact_market_source_audit")
    if not isinstance(audit, dict) or audit.get("verified") is not True:
        return False
    ticker = str(row.get("market_ticker") or "").strip().upper()
    market_date = str(row.get("market_date") or "").strip()
    audit_ticker = str(audit.get("market_ticker") or "").strip().upper()
    audit_date = str(audit.get("market_date") or "").strip()
    provider = str(audit.get("settlement_source_provider") or "").strip().upper()
    source_url = str(audit.get("settlement_source_url") or "").strip()
    return bool(
        ticker
        and market_date
        and audit_ticker == ticker
        and audit_date == market_date
        and provider in {"NWS", "THE_WEATHER_COMPANY"}
        and source_url.startswith("https://")
    )


def _strict_ready(row: dict[str, Any]) -> bool:
    return (
        row.get("category") == "weather"
        and row.get("classification") == "SHADOW"
        and row.get("read_only") is True
        and row.get("order_entry") is False
        and row.get("live_probe_ready") is False
        and row.get("settlement_certified_shadow") is True
        and row.get("ev_certified_shadow") is True
        and row.get("exact_market_source_verified") is True
        and _exact_audit_matches(row)
        and not (row.get("blockers") or [])
    )


def _candidate_for_budget(
    row: dict[str, Any], *, budget_cents: int, min_edge_cents: Decimal
) -> dict[str, Any] | None:
    if not isinstance(budget_cents, int) or isinstance(budget_cents, bool) or budget_cents <= 0:
        return None
    if not _strict_ready(row):
        return None
    best = row.get("best_shadow_side")
    if not isinstance(best, dict):
        return None
    side = str(best.get("side") or "").upper()
    ask = best.get("ask_cents")
    depth = best.get("available_contracts")
    prob = _d(best.get("forecast_side_probability"))
    if side not in {"YES", "NO"}:
        return None
    if not isinstance(ask, int) or isinstance(ask, bool) or not 1 <= ask <= 99:
        return None
    if not isinstance(depth, int) or isinstance(depth, bool) or depth <= 0:
        return None
    if prob is None or prob < 0 or prob > 1:
        return None

    best_plan: dict[str, Any] | None = None
    for qty in range(1, depth + 1):
        try:
            fee_cents = _fee_cents(qty, ask, str(row.get("fee_type")), row.get("fee_multiplier"))
        except Exception:
            return None
        cost_cents = ask * qty + fee_cents
        if cost_cents > budget_cents:
            break
        ev_cents = prob * D(100 * qty) - D(cost_cents)
        edge_per_contract = ev_cents / D(qty)
        if edge_per_contract < min_edge_cents or ev_cents <= 0:
            continue
        best_plan = {
            "budget_cents": budget_cents,
            "side": side,
            "quantity": qty,
            "ask_cents": ask,
            "modeled_fee_cents": fee_cents,
            "paper_cost_cents": cost_cents,
            "modeled_after_fee_ev_cents": str(ev_cents),
            "modeled_after_fee_edge_cents_per_contract": str(edge_per_contract),
            "available_contracts_at_quote": depth,
        }
    return best_plan


def score_paper_probe_plans(
    row: dict[str, Any],
    *,
    budgets_cents: tuple[int, ...] = DEFAULT_BUDGETS_CENTS,
    min_edge_cents: Decimal = DEFAULT_MIN_EDGE_CENTS,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for budget in budgets_cents:
        plan = _candidate_for_budget(row, budget_cents=budget, min_edge_cents=min_edge_cents)
        if plan is not None:
            plans.append(plan)
    return plans


def select_paper_probe(
    row: dict[str, Any],
    *,
    budgets_cents: tuple[int, ...] = DEFAULT_BUDGETS_CENTS,
    min_edge_cents: Decimal = DEFAULT_MIN_EDGE_CENTS,
) -> dict[str, Any] | None:
    plans = score_paper_probe_plans(row, budgets_cents=budgets_cents, min_edge_cents=min_edge_cents)
    if not plans:
        return None
    selected = max(plans, key=lambda p: (D(p["modeled_after_fee_ev_cents"]), p["quantity"], p["budget_cents"]))
    ticker = str(row.get("market_ticker") or "").strip().upper()
    market_date = str(row.get("market_date") or "").strip()
    observed_at = str(row.get("quote_observed_at") or "").strip()
    if not ticker or not market_date or not observed_at:
        return None

    # Deliberately do NOT include quote_observed_at, ask, quantity, budget tier or
    # model probability in the synthetic identity. A polling loop can observe the
    # same resting opportunity many times, and those fields may move while the same
    # capital is still notionally deployed. Until a stateful OPEN/CLOSE episode
    # tracker exists for weather, the conservative fail-closed policy is at most one
    # synthetic paper fill per exact market + side for the life of that contract.
    # This may under-count a true close/re-open, but it cannot inflate P/L by repeatedly
    # consuming the same displayed liquidity.
    seed = f"weather|{ticker}|{market_date}|{selected['side']}"
    out = dict(selected)
    out.update({
        "trade_group_id": "paper-weather-" + sha256(seed.encode("utf-8")).hexdigest()[:24],
        "settlement_key": f"weather:{ticker}",
        "category": "weather",
        "classification": "PAPER",
        "market_ticker": ticker,
        "market_date": market_date,
        "quote_observed_at": observed_at,
        "paper_dedup_scope": "ONE_FILL_PER_EXACT_MARKET_SIDE",
        "exact_market_source_verified": True,
        "simultaneous_synthetic_fill": True,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    })
    return out


def ledger_events_for_paper_probe(plan: dict[str, Any], *, ts_ms: int) -> list[LedgerEvent]:
    if plan.get("classification") != "PAPER" or plan.get("read_only") is not True:
        return []
    if plan.get("order_entry") is not False or plan.get("live_probe_ready") is not False:
        return []
    trade_group_id = str(plan.get("trade_group_id") or "")
    settlement_key = str(plan.get("settlement_key") or "")
    ticker = str(plan.get("market_ticker") or "")
    side = str(plan.get("side") or "").upper()
    qty = plan.get("quantity")
    ask = plan.get("ask_cents")
    fee_d = _d(plan.get("modeled_fee_cents"))
    if not trade_group_id or not settlement_key or not ticker or side not in {"YES", "NO"}:
        return []
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
        return []
    if not isinstance(ask, int) or isinstance(ask, bool) or not 1 <= ask <= 99:
        return []
    if fee_d is None or fee_d < 0:
        return []

    # Stable IDs make reconciliation idempotent across repeated observations of the
    # same exact-market-side paper probe. Because select_paper_probe() also keeps the
    # trade_group_id stable across quote timestamps, this de-duplicates real polling
    # repeats rather than only re-emission of one already-selected plan object.
    fill_external_id = f"{trade_group_id}:fill"
    fee_external_id = f"{trade_group_id}:fee"
    fill = make_event(
        trade_group_id=trade_group_id,
        settlement_key=settlement_key,
        category="weather",
        venue="kalshi",
        market_id=ticker,
        event_type="fill",
        side=side,
        quantity=D(qty),
        price=D(ask) / D(100),
        ts_ms=ts_ms,
        external_id=fill_external_id,
    )
    fee_event = make_event(
        trade_group_id=trade_group_id,
        settlement_key=settlement_key,
        category="weather",
        venue="kalshi",
        market_id=ticker,
        event_type="fee",
        side=side,
        fee=fee_d / D(100),
        ts_ms=ts_ms,
        external_id=fee_external_id,
    )
    return [fill, fee_event]
