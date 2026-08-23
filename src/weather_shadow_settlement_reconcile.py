"""Fail-closed settlement reconciliation for read-only weather PAPER/SHADOW evidence.

This module never submits orders. It joins one synthetic paper fill to one later
*official* weather settlement only when exact market/date/source provenance agrees.
The goal is to measure realized paper P/L without letting forecast-model EV or a
changed recurring Series template stand in for terminal settlement truth.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


_ALLOWED_PROVIDERS = {"NWS", "THE_WEATHER_COMPANY"}


def _strict_true(value: Any) -> bool:
    return value is True


def _decimal(value: Any) -> Decimal | None:
    try:
        out = Decimal(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _normalized_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().rstrip("/").lower()


def _derive_terminal_outcome(
    *, shadow_row: dict[str, Any], settlement: dict[str, Any]
) -> tuple[str | None, list[str]]:
    """Recompute YES/NO from terminal value and independently audited strike semantics.

    Bounded ``between`` contracts replay the already-audited floor/cap bounds. Tail
    contracts deliberately do *not* infer inclusivity from Kalshi ``strike_type`` or a
    ticker suffix. They are accepted only when the exact-market rule audit preserved a
    literal comparator and threshold and explicitly marked that comparator audited.

    The terminal value itself also needs an explicit exact-market audit. Weather rules
    can differ on whether the source value is used as published, rounded, or otherwise
    transformed before applying a bracket. Until a specific transform is implemented
    and tested here, only an explicitly audited ``AS_PUBLISHED`` value is accepted.
    This prevents an apparently exact boundary/tie result from being manufactured by an
    implicit rounding assumption.
    """
    blockers: list[str] = []
    if shadow_row.get("settlement_certified_shadow") is not True:
        blockers.append("QUOTE_TIME_SETTLEMENT_CERTIFICATION_UNVERIFIED")

    if shadow_row.get("terminal_value_semantics_audited") is not True:
        blockers.append("TERMINAL_VALUE_SEMANTICS_UNAUDITED")
    value_transform = str(shadow_row.get("terminal_value_transform") or "").strip().upper()
    if value_transform != "AS_PUBLISHED":
        blockers.append("TERMINAL_VALUE_TRANSFORM_UNSUPPORTED")

    strike_type = str(shadow_row.get("strike_type") or "").strip().lower()
    floor = _decimal(shadow_row.get("floor_strike"))
    cap = _decimal(shadow_row.get("cap_strike"))
    terminal_value = _decimal(settlement.get("settlement_value"))

    if terminal_value is None:
        blockers.append("TERMINAL_SETTLEMENT_VALUE_UNVERIFIED")

    if strike_type == "between":
        if floor is None or cap is None or floor > cap:
            blockers.append("TERMINAL_BRACKET_BOUNDS_INVALID")
        if blockers:
            return None, sorted(set(blockers))
        return ("YES" if floor <= terminal_value <= cap else "NO"), []

    if strike_type in {"less", "greater"}:
        if shadow_row.get("terminal_comparator_audited") is not True:
            blockers.append("TERMINAL_TAIL_COMPARATOR_UNAUDITED")
        operator = str(shadow_row.get("terminal_operator") or "").strip()
        threshold = _decimal(shadow_row.get("terminal_threshold"))
        allowed = {"<", "<="} if strike_type == "less" else {">", ">="}
        if operator not in allowed:
            blockers.append("TERMINAL_TAIL_OPERATOR_INVALID")
        if threshold is None:
            blockers.append("TERMINAL_TAIL_THRESHOLD_INVALID")
        if blockers:
            return None, sorted(set(blockers))
        comparisons = {
            "<": terminal_value < threshold,
            "<=": terminal_value <= threshold,
            ">": terminal_value > threshold,
            ">=": terminal_value >= threshold,
        }
        return ("YES" if comparisons[operator] else "NO"), []

    blockers.append("TERMINAL_STRIKE_SEMANTICS_UNSUPPORTED")
    return None, sorted(set(blockers))


def reconcile_weather_paper_fill(
    *,
    shadow_row: dict[str, Any],
    paper_fill: dict[str, Any],
    settlement: dict[str, Any],
) -> dict[str, Any]:
    """Return settlement-adjusted paper P/L or fail closed with explicit blockers."""
    blockers: list[str] = []

    if shadow_row.get("category") != "weather" or shadow_row.get("classification") not in {"PAPER", "SHADOW"}:
        blockers.append("NOT_WEATHER_PAPER_SHADOW")
    if shadow_row.get("order_entry") is not False or shadow_row.get("live_probe_ready") is not False:
        blockers.append("ORIGIN_NOT_READ_ONLY")

    market_ticker = str(shadow_row.get("market_ticker") or "").strip().upper()
    market_date = str(shadow_row.get("market_date") or "").strip()
    fill_ticker = str(paper_fill.get("market_ticker") or "").strip().upper()
    fill_date = str(paper_fill.get("market_date") or "").strip()
    settle_ticker = str(settlement.get("market_ticker") or "").strip().upper()
    settle_date = str(settlement.get("market_date") or "").strip()
    if not market_ticker or fill_ticker != market_ticker or settle_ticker != market_ticker:
        blockers.append("EXACT_MARKET_MISMATCH")
    if not market_date or fill_date != market_date or settle_date != market_date:
        blockers.append("EXACT_MARKET_DATE_MISMATCH")

    exact_audit = shadow_row.get("exact_market_source_audit")
    exact_verified = shadow_row.get("exact_market_source_verified") is True
    if not isinstance(exact_audit, dict) or exact_audit.get("verified") is not True or not exact_verified:
        blockers.append("QUOTE_TIME_EXACT_MARKET_SOURCE_UNVERIFIED")
        certified_provider = ""
        certified_source_url = ""
    else:
        audit_ticker = str(exact_audit.get("market_ticker") or "").strip().upper()
        audit_date = str(exact_audit.get("market_date") or "").strip()
        if audit_ticker != market_ticker or audit_date != market_date:
            blockers.append("QUOTE_TIME_EXACT_MARKET_AUDIT_MISMATCH")
        certified_provider = str(exact_audit.get("settlement_source_provider") or "").strip().upper()
        certified_source_url = _normalized_url(exact_audit.get("settlement_source_url"))

    settlement_provider = str(settlement.get("settlement_source_provider") or "").strip().upper()
    if certified_provider not in _ALLOWED_PROVIDERS or settlement_provider != certified_provider:
        blockers.append("SETTLEMENT_SOURCE_MISMATCH")

    settlement_source_url = _normalized_url(settlement.get("settlement_source_url"))
    if not certified_source_url or not settlement_source_url or settlement_source_url != certified_source_url:
        blockers.append("SETTLEMENT_SOURCE_URL_MISMATCH")

    if not _strict_true(settlement.get("official_source_verified")):
        blockers.append("OFFICIAL_SETTLEMENT_SOURCE_UNVERIFIED")
    if not _strict_true(settlement.get("finality_verified")):
        blockers.append("SETTLEMENT_FINALITY_UNVERIFIED")
    if not _strict_true(settlement.get("correction_window_checked")):
        blockers.append("CORRECTION_WINDOW_UNVERIFIED")

    # Current Weather Company temperature markets can expose preliminary readings that
    # differ from the final reported value. Generic source/finality flags are therefore
    # not sufficient evidence for TWC settlements: the attached record itself must be
    # explicitly identified and independently verified as the terminal FINAL record.
    # Historical NWS/CLI contracts remain on their separate exact-source/finality path.
    terminal_record_stage = str(settlement.get("terminal_record_stage") or "").strip().upper()
    terminal_record_stage_verified = _strict_true(settlement.get("terminal_record_stage_verified"))
    if settlement_provider == "THE_WEATHER_COMPANY":
        if not terminal_record_stage_verified:
            blockers.append("TWC_TERMINAL_RECORD_STAGE_UNVERIFIED")
        if terminal_record_stage != "FINAL":
            blockers.append("TWC_TERMINAL_RECORD_NOT_FINAL")

    outcome = str(settlement.get("outcome") or "").strip().upper()
    if outcome not in {"YES", "NO"}:
        blockers.append("SETTLEMENT_OUTCOME_UNVERIFIED")

    derived_outcome, outcome_blockers = _derive_terminal_outcome(
        shadow_row=shadow_row, settlement=settlement
    )
    blockers.extend(outcome_blockers)
    if derived_outcome is not None and outcome != derived_outcome:
        blockers.append("SETTLEMENT_OUTCOME_VALUE_MISMATCH")

    side = str(paper_fill.get("side") or "").strip().upper()
    qty = paper_fill.get("quantity")
    price_cents = paper_fill.get("price_cents")
    fee_cents = paper_fill.get("modeled_fee_cents")
    if side not in {"YES", "NO"}:
        blockers.append("PAPER_FILL_SIDE_INVALID")
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
        blockers.append("PAPER_FILL_QUANTITY_INVALID")
    if not isinstance(price_cents, int) or isinstance(price_cents, bool) or not 1 <= price_cents <= 99:
        blockers.append("PAPER_FILL_PRICE_INVALID")
    fee = _decimal(fee_cents)
    if fee is None or fee < 0:
        blockers.append("PAPER_FILL_FEE_INVALID")

    blockers = sorted(set(blockers))
    if blockers:
        return {
            "reconciled": False,
            "blockers": blockers,
            "category": "weather",
            "classification": "PAPER",
            "market_ticker": market_ticker or None,
            "market_date": market_date or None,
            "paper_realized_pnl_cents": None,
            "read_only": True,
            "order_entry": False,
            "live_probe_ready": False,
        }

    cost_cents = Decimal(price_cents * qty) + fee
    payout_cents = Decimal(100 * qty) if side == derived_outcome else Decimal(0)
    pnl_cents = payout_cents - cost_cents
    return {
        "reconciled": True,
        "blockers": [],
        "category": "weather",
        "classification": "PAPER",
        "market_ticker": market_ticker,
        "market_date": market_date,
        "side": side,
        "quantity": qty,
        "paper_cost_cents": str(cost_cents),
        "paper_payout_cents": str(payout_cents),
        "paper_realized_pnl_cents": str(pnl_cents),
        "settlement_outcome": derived_outcome,
        "settlement_value": str(_decimal(settlement.get("settlement_value"))),
        "settlement_source_provider": settlement_provider,
        "settlement_source_url": settlement.get("settlement_source_url"),
        "terminal_record_stage": terminal_record_stage or None,
        "terminal_record_stage_verified": terminal_record_stage_verified,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
