"""Semantic audit aid for exact Polymarket US <-> Kalshi MLB prop rules.

This module intentionally does *not* decide settlement equivalence.  It turns the
exact public rule evidence packet into a deterministic checklist of rule dimensions
that require independent review.  Text heuristics only identify evidence snippets;
they never promote a pair to FULL_RULES_AUDITED_SHADOW or live-probe readiness.

Read-only research only.  No venue clients, credentials, or order-entry paths.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

DIMENSIONS: dict[str, tuple[str, ...]] = {
    "participation": (
        r"\bstart(?:er|ing|s)?\b",
        r"\bplate appearance\b",
        r"\bfirst pitch\b",
        r"\bparticipat(?:e|es|ed|ion)\b",
        r"\blineup\b",
    ),
    "stat_definition": (
        r"\bhits?\b",
        r"\bruns?\b",
        r"\brbi(?:s)?\b",
        r"\btotal bases?\b",
        r"\bstrikeouts?\b",
        r"\bhome runs?\b",
    ),
    "cancellation_postponement": (
        r"\bcancel(?:led|ed|lation)?\b",
        r"\bpostpon(?:ed|ement)\b",
        r"\bsuspend(?:ed|ed game)?\b",
        r"\bmakeup\b",
        r"\breschedul(?:e|ed|ing)\b",
    ),
    "source_hierarchy": (
        r"\bofficial\b",
        r"\bsource\b",
        r"\bmlb\b",
        r"\bstat(?:istics?|s)\b",
        r"\bsettle(?:ment|d)?\b",
        r"\bfair market price\b",
        r"\blfmp\b",
    ),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _hits(text: str, patterns: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if match:
            token = match.group(0).strip()
            if token and token not in found:
                found.append(token)
    return found


def semantic_diff_from_evidence(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fail-closed semantic-review checklist from exact-rule evidence.

    A dimension is ``evidence_present_both`` only when both exact rule texts contain
    at least one relevant lexical anchor.  That is *not* equivalence: independent
    review must still decide whether the two rule branches mean the same thing.
    """
    poly_rules = _text(packet.get("polymarket_instrument_rules"))
    kalshi_rules = _text(packet.get("kalshi_market_rules"))
    complete = bool(packet.get("evidence_complete_for_independent_compare"))

    dimensions: dict[str, Any] = {}
    review_ready = complete
    for name, patterns in DIMENSIONS.items():
        p_hits = _hits(poly_rules, patterns)
        k_hits = _hits(kalshi_rules, patterns)
        both = bool(p_hits and k_hits)
        review_ready = review_ready and both
        dimensions[name] = {
            "polymarket_anchors": p_hits,
            "kalshi_anchors": k_hits,
            "evidence_present_both": both,
            "equivalence_decision": "REQUIRES_INDEPENDENT_REVIEW",
        }

    missing = [name for name, row in dimensions.items() if not row["evidence_present_both"]]
    return {
        "family": _text(packet.get("family")),
        "polymarket_instrument_id": _text(packet.get("polymarket_instrument_id")),
        "kalshi_ticker": _text(packet.get("kalshi_ticker")),
        "exact_evidence_complete": complete,
        "semantic_review_ready": bool(review_ready),
        "dimensions": dimensions,
        "missing_semantic_dimensions": missing,
        "equivalent": False,
        "full_rules_audited_shadow": False,
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
        "note": (
            "Lexical anchors are triage evidence only. They cannot establish semantic "
            "equivalence or clear the exact-contract rules gate."
        ),
    }
