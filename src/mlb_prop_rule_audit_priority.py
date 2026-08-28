"""Prioritize exact MLB prop rule audits using current paper economics.

This module never decides settlement equivalence and never promotes a pair to live
readiness. It joins read-only v4 snapshot rows with exact public rule-evidence
packets and ranks the most economically relevant RULES_PENDING pairs for
independent semantic review.

The <=$1 and <=$5 plans are alternative sizing views of the same displayed books,
so each exact market/direction contributes only one priority row. Missing identity,
unsafe books, incomplete evidence, or non-positive economics fail closed.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

D = Decimal


def _d(value: Any) -> Decimal | None:
    try:
        return D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _plan_key(row: Mapping[str, Any], plan: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("family") or ""),
        str(row.get("poly_slug") or ""),
        str(row.get("kalshi_ticker") or ""),
        str(plan.get("direction") or ""),
    )


def _best_plan(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Collapse $1/$5 alternative views to the highest conservative profit."""
    candidates: list[Mapping[str, Any]] = []
    for field in ("probe_1usd", "probe_5usd"):
        plan = row.get(field)
        if isinstance(plan, Mapping):
            profit = _d(plan.get("conservative_locked_profit"))
            cost = _d(plan.get("total_cost_upper_bound"))
            if profit is not None and profit > 0 and cost is not None and cost > 0:
                candidates.append(plan)
    if not candidates:
        return None
    return max(candidates, key=lambda p: _d(p.get("conservative_locked_profit")) or D("0"))


def _evidence_index(packets: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], Mapping[str, Any]]:
    out: dict[tuple[str, str], Mapping[str, Any]] = {}
    for packet in packets:
        key = (str(packet.get("family") or ""), str(packet.get("kalshi_ticker") or ""))
        if all(key):
            out[key] = packet
    return out


def build_rule_audit_priority_queue(
    snapshot_rows: Iterable[Mapping[str, Any]],
    evidence_packets: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return fail-closed independent-review priorities for positive exact pairs."""
    evidence = _evidence_index(evidence_packets)
    best_by_exact_direction: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    skipped = {
        "unsafe_or_unsynchronized": 0,
        "no_positive_plan": 0,
        "missing_identity": 0,
        "missing_exact_evidence": 0,
    }

    for row in snapshot_rows:
        if not bool(row.get("book_comparable")) or not bool(row.get("synchronized_book_pair")):
            skipped["unsafe_or_unsynchronized"] += 1
            continue
        family = str(row.get("family") or "")
        poly_slug = str(row.get("poly_slug") or "")
        kalshi_ticker = str(row.get("kalshi_ticker") or "")
        if not family or not poly_slug or not kalshi_ticker:
            skipped["missing_identity"] += 1
            continue
        plan = _best_plan(row)
        if plan is None:
            skipped["no_positive_plan"] += 1
            continue

        packet = evidence.get((family, kalshi_ticker))
        if not packet or not bool(packet.get("evidence_complete_for_independent_compare")):
            skipped["missing_exact_evidence"] += 1
            evidence_complete = False
            poly_instrument_id = ""
            missing_evidence = list((packet or {}).get("missing_evidence") or ["exact_rule_evidence"])
        else:
            evidence_complete = True
            poly_instrument_id = str(packet.get("polymarket_instrument_id") or "")
            missing_evidence = []

        profit = _d(plan.get("conservative_locked_profit")) or D("0")
        cost = _d(plan.get("total_cost_upper_bound")) or D("0")
        roi = profit / cost if cost > 0 else D("0")
        quantity = int(plan.get("quantity") or 0)
        skew_ms = int(row.get("quote_receive_skew_ms") or 0)
        priority_score = profit * (D("1") + min(D("1"), roi))

        item = {
            "family": family,
            "poly_slug": poly_slug,
            "polymarket_instrument_id": poly_instrument_id,
            "kalshi_ticker": kalshi_ticker,
            "direction": str(plan.get("direction") or ""),
            "quantity": quantity,
            "conservative_locked_profit": str(profit),
            "total_cost_upper_bound": str(cost),
            "conservative_roi": str(roi),
            "quote_receive_skew_ms": skew_ms,
            "exact_rule_evidence_complete": evidence_complete,
            "missing_evidence": missing_evidence,
            "independent_review_required": True,
            "full_rules_audited_shadow": False,
            "live_probe_ready": False,
            "priority_score": str(priority_score),
            "read_only": True,
            "order_entry": False,
        }
        key = _plan_key(row, plan)
        prior = best_by_exact_direction.get(key)
        if prior is None or D(item["conservative_locked_profit"]) > D(prior["conservative_locked_profit"]):
            best_by_exact_direction[key] = item

    items = sorted(
        best_by_exact_direction.values(),
        key=lambda x: (
            bool(x["exact_rule_evidence_complete"]),
            D(x["priority_score"]),
            D(x["conservative_locked_profit"]),
        ),
        reverse=True,
    )
    for rank, item in enumerate(items, 1):
        item["audit_rank"] = rank

    return {
        "audit_queue": items,
        "queue_size": len(items),
        "evidence_complete_queue_size": sum(bool(x["exact_rule_evidence_complete"]) for x in items),
        "skipped": skipped,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready_pairs": 0,
        "note": (
            "Economic priority only. Exact rule evidence completeness is not equivalence; "
            "every queued pair remains RULES_PENDING until independently reviewed."
        ),
    }
