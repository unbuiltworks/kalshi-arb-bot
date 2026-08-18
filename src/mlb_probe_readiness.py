"""Fail-closed readiness classification for MLB cross-venue paper micro-probes.

Consumes rows emitted by ``strict_mlb_prop_snapshot_v4``/later participation-aware
wrappers. A merely positive price discrepancy is not sufficient for promotion:
we keep economics, participation, and *independent exact-contract* settlement-rule
equivalence as separate gates and prefer at least three cents of conservative modeled
net profit.

Important v5 compatibility note: ``strict_mlb_prop_snapshot_v5`` currently uses
``settlement_equivalent_now`` for the narrower *participation branch resolved*
concept. That must never be interpreted here as proof that the two venues' full
contract terms were independently audited.

This module is read-only. It has no venue clients, credentials, or order-entry path.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from src.mlb_prop_rule_audit_guard import AUDIT_CONTRACT

D = Decimal
DEFAULT_PREFERRED_NET = D("0.03")
PARTICIPATION_STATUSES = frozenset({"EQUIVALENT_AFTER_PARTICIPATION"})


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


def _participation_ok(row: dict[str, Any]) -> bool:
    """Resolve the family-specific in-game participation gate only."""
    if "participation_gate_satisfied" in row:
        return bool(row.get("participation_gate_satisfied"))
    if str(row.get("rule_status") or "") in PARTICIPATION_STATUSES:
        return True
    return bool(row.get("settlement_equivalent_now"))


def _rules_audited_ok(row: dict[str, Any]) -> bool:
    """Require the exact-contract audit guard's evidence contract; fail closed.

    Generic booleans/status strings such as ``full_rules_audited=true`` or
    ``settlement_confidence=FULL_RULES_AUDITED_SHADOW`` are deliberately ignored.
    They can be hand-authored and do not prove that exact Polymarket instrument rules
    and exact Kalshi market rules/source hierarchy were independently checked.
    """
    audit = row.get("exact_rule_audit")
    if not isinstance(audit, dict):
        return False
    if audit.get("audit_contract") != AUDIT_CONTRACT:
        return False
    if audit.get("full_rules_audited_shadow") is not True:
        return False
    if audit.get("equivalent") is not True:
        return False
    if str(audit.get("family") or "") != str(row.get("family") or ""):
        return False
    if str(audit.get("kalshi_ticker") or "") != str(row.get("kalshi_ticker") or ""):
        return False
    if not str(audit.get("polymarket_instrument_id") or "").strip():
        return False
    return all(
        audit.get(field) is True
        for field in (
            "participation_branch_checked",
            "stat_definition_checked",
            "cancellation_postponement_checked",
            "source_hierarchy_checked",
        )
    )


def classify_probe_row(
    row: dict[str, Any],
    *,
    preferred_net: Decimal = DEFAULT_PREFERRED_NET,
) -> dict[str, Any]:
    """Classify one exact-identity MLB row without weakening any safety gate."""
    p1 = _plan_profit(row, "probe_1usd")
    p5 = _plan_profit(row, "probe_5usd")
    best = max((p for p in (p1, p5) if p is not None), default=None)

    participation_ok = _participation_ok(row)
    rules_ok = _rules_audited_ok(row)
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
    paper_probe_ready = exact_identity_ok and current_book_ok and preferred_economics and participation_ok
    fully_audited_shadow_ready = paper_probe_ready and rules_ok

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
        "fully_audited_shadow_ready": fully_audited_shadow_ready,
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
    paper_ready = [row for row in classified if row["paper_probe_ready"]]
    audited_ready = [row for row in classified if row["fully_audited_shadow_ready"]]
    preferred = [row for row in classified if row["preferred_economics"]]
    return {
        "rows": classified,
        "preferred_economic_rows": len(preferred),
        "paper_probe_ready_rows": len(paper_ready),
        "fully_audited_shadow_ready_rows": len(audited_ready),
        "live_probe_ready_rows": 0,
        "preferred_net_threshold": str(preferred_net),
        "read_only": True,
        "order_entry": False,
        "note": (
            "A >=3c conservative modeled net is only an economics gate. Exact identity, current books, "
            "and the participation gate may qualify an automatic synthetic paper micro-probe; independently "
            "audited exact-contract venue settlement equivalence is additionally required before "
            "FULL_RULES_AUDITED_SHADOW. Generic audit booleans/status strings do not clear that gate. "
            "This layer never grants live execution readiness."
        ),
    }
