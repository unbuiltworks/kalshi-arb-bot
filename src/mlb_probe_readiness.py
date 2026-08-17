"""Fail-closed readiness classification for MLB cross-venue paper micro-probes.

Consumes rows emitted by ``strict_mlb_prop_snapshot_v4``/later participation-aware
wrappers.  A merely positive price discrepancy is not sufficient for promotion:
we keep economics, participation, and settlement-rule equivalence as independent
gates and prefer at least three cents of conservative modeled net profit.

This module is read-only.  It has no venue clients, credentials, or order-entry path.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

D = Decimal
DEFAULT_PREFERRED_NET = D("0.03")


def _decimal(value: Any) -> Decimal | None:
    try:
        return D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _plan_profit(row: dict[str, Any], cap: str) -> Decimal | None:
    plan = row.get(cap)
    if not isinstance(plan, dict):
        return None
    return _decimal(plan.get("conservative_locked_profit"))


def classify_probe_row(
    row: dict[str, Any],
    *,
    preferred_net: Decimal = DEFAULT_PREFERRED_NET,
) -> dict[str, Any]:
    """Classify one exact-identity MLB row without weakening any safety gate."""
    p1 = _plan_profit(row, "probe_1usd")
    p5 = _plan_profit(row, "probe_5usd")
    best = max((p for p in (p1, p5) if p is not None), default=None)

    participation_ok = bool(row.get("participation_gate_satisfied"))
    rules_ok = bool(row.get("settlement_equivalent_now"))
    exact_identity_ok = bool(row.get("exact_identity", True)) and row.get("start_skew_seconds") is not None
    current_book_ok = bool(row.get("book_comparable"))

    blockers: list[str] = []
    if not exact_identity_ok:
        blockers.append("EXACT_IDENTITY_PENDING")
    if not current_book_ok:
        blockers.append("CURRENT_BOOK_PENDING")
    if best is None or best <= 0:
        blockers.append("NO_POSITIVE_MODELED_PLAN")
    elif best < preferred_net:
        blockers.append("BELOW_PREFERRED_3C_NET")
    if not participation_ok:
        blockers.append("PARTICIPATION_PENDING")
    if not rules_ok:
        blockers.append("RULES_PENDING")

    preferred_economics = best is not None and best >= preferred_net
    paper_probe_ready = exact_identity_ok and current_book_ok and preferred_economics and participation_ok and rules_ok

    return {
        "family": row.get("family"),
        "poly_slug": row.get("poly_slug"),
        "kalshi_ticker": row.get("kalshi_ticker"),
        "best_capped_conservative_net": str(best) if best is not None else None,
        "preferred_net_threshold": str(preferred_net),
        "preferred_economics": preferred_economics,
        "exact_identity_ok": exact_identity_ok,
        "current_book_ok": current_book_ok,
        "participation_ok": participation_ok,
        "rules_ok": rules_ok,
        "blockers": blockers,
        "paper_probe_ready": paper_probe_ready,
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
    }


def summarize_probe_rows(
    rows: Iterable[dict[str, Any]],
    *,
    preferred_net: Decimal = DEFAULT_PREFERRED_NET,
) -> dict[str, Any]:
    classified = [classify_probe_row(row, preferred_net=preferred_net) for row in rows]
    ready = [row for row in classified if row["paper_probe_ready"]]
    preferred = [row for row in classified if row["preferred_economics"]]
    return {
        "rows": classified,
        "preferred_economic_rows": len(preferred),
        "paper_probe_ready_rows": len(ready),
        "live_probe_ready_rows": 0,
        "preferred_net_threshold": str(preferred_net),
        "read_only": True,
        "order_entry": False,
        "note": (
            "A >=3c conservative modeled net is only an economics gate. Exact identity, current books, "
            "participation, and independently audited settlement equivalence must also pass before a paper probe is ready."
        ),
    }
