"""Extract exact-contract public rule evidence for MLB prop shadow pairs.

This module does not decide settlement equivalence.  It only preserves the exact
Polymarket US instrument identity/rules and Kalshi market identity/rules/source
metadata already present in public discovery payloads so an independent audit can
compare them.  Missing evidence fails closed.

Read-only research only.  No authentication or order-entry paths.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and _text(value):
            return _text(value)
    return ""


def _metadata(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = payload.get("metadata")
    return value if isinstance(value, Mapping) else {}


def _sources(payload: Mapping[str, Any]) -> tuple[str, ...]:
    raw = (
        payload.get("settlement_sources")
        or payload.get("settlementSources")
        or payload.get("source_agencies")
        or payload.get("settlement_source")
        or payload.get("settlementSource")
        or ()
    )
    if isinstance(raw, str):
        return (raw.strip(),) if raw.strip() else ()
    if isinstance(raw, Mapping):
        values: Iterable[Any] = raw.values()
    else:
        values = raw if isinstance(raw, Iterable) else ()
    return tuple(_text(value) for value in values if _text(value))


def build_exact_rule_evidence_packet(
    *,
    family: str,
    polymarket: Mapping[str, Any] | None,
    kalshi: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a fail-closed exact-contract evidence packet for later audit.

    ``evidence_complete_for_independent_compare`` means the two exact public rule
    payloads are present.  It does NOT mean the rules are equivalent and it never
    makes a pair probe-ready.
    """
    p = polymarket or {}
    k = kalshi or {}
    pm = _metadata(p)

    poly_id = _first_text(p, "instrument_id", "instrumentId", "id", "symbol", "slug")
    if not poly_id:
        poly_id = _first_text(pm, "instrument_id", "instrumentId", "cftc_instrument_id")
    poly_rules = _first_text(p, "instrument_rules", "instrumentRules") or _first_text(
        pm, "instrument_rules", "instrumentRules"
    )

    kalshi_ticker = _first_text(k, "ticker")
    primary = _first_text(k, "rules_primary", "rules", "market_rules")
    secondary = _first_text(k, "rules_secondary")
    kalshi_rules = "\n".join(part for part in (primary, secondary) if part).strip()
    sources = _sources(k)

    complete = bool(poly_id and poly_rules and kalshi_ticker and kalshi_rules and sources)
    missing: list[str] = []
    if not poly_id:
        missing.append("polymarket_instrument_id")
    if not poly_rules:
        missing.append("polymarket_instrument_rules")
    if not kalshi_ticker:
        missing.append("kalshi_ticker")
    if not kalshi_rules:
        missing.append("kalshi_market_rules")
    if not sources:
        missing.append("kalshi_settlement_sources")

    return {
        "family": _text(family),
        "polymarket_instrument_id": poly_id,
        "polymarket_instrument_rules": poly_rules,
        "kalshi_ticker": kalshi_ticker,
        "kalshi_market_rules": kalshi_rules,
        "kalshi_settlement_sources": list(sources),
        "evidence_complete_for_independent_compare": complete,
        "missing_evidence": missing,
        "equivalent": False,
        "full_rules_audited_shadow": False,
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
    }
