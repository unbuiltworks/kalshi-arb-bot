"""Read-only exact-market rule fingerprinting for Kalshi <-> Gemini MLB game totals.

The normal-event identity layer can tell us that two contracts both mean, for
example, "ARI/BOS over 12.5 runs".  That is not enough to authorize an arb: the
exceptional settlement tails (postponement, interruption, cancellation, source,
push/tie handling) must also be audited.

This module collects the *exact live rule evidence* for one Kalshi market and one
Gemini contract and produces a deterministic SHA-256 pair fingerprint plus a
reviewable approval-shaped candidate.  It never marks that candidate approved.
Execution eligibility still comes from :mod:`mlb_game_rule_certifications` and an
operator-reviewed approval registry.

No credentials and no order-entry code are used here.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import httpx

from src.mlb_game_rule_certifications import PROFILES, assess_pair

KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"
GEMINI_REST = "https://api.gemini.com/v1/prediction-markets"
REQUIRED_FAMILY = "game_total"
ALLOWED_SOURCE_SIDES = frozenset({"YES", "NO", "CONTRACT"})
SEMANTIC_DIMENSIONS = (
    "official_source",
    "normal_outcome",
    "extra_innings",
    "minimum_completion",
    "not_started",
    "interrupted",
    "cancellation",
    "push_tie",
)


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u2019", "'").split())


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_hash(value: Mapping[str, Any]) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_text(blob)


def _rich_text(node: Any) -> list[str]:
    """Extract visible text from Gemini's rich-text description shape."""
    out: list[str] = []
    if isinstance(node, str):
        text = _norm_text(node)
        if text:
            out.append(text)
    elif isinstance(node, Mapping):
        if str(node.get("nodeType") or "") == "text":
            text = _norm_text(node.get("value"))
            if text:
                out.append(text)
        for value in node.values():
            if isinstance(value, (Mapping, list, tuple)):
                out.extend(_rich_text(value))
    elif isinstance(node, (list, tuple)):
        for value in node:
            out.extend(_rich_text(value))
    return out


def kalshi_rule_text(market: Mapping[str, Any]) -> str:
    parts = [
        market.get("rules_primary"),
        market.get("rules_secondary"),
        market.get("early_close_condition"),
    ]
    return "\n".join(text for text in (_norm_text(v) for v in parts) if text)


def gemini_rule_text(contract: Mapping[str, Any]) -> str:
    parts = _rich_text(contract.get("description"))
    if not parts:
        for key in ("rules", "descriptionText", "resolutionRules"):
            text = _norm_text(contract.get(key))
            if text:
                parts.append(text)
    return "\n".join(parts)


def _market_id(venue: str, row: Mapping[str, Any]) -> str:
    if venue == "kalshi":
        return str(row.get("ticker") or row.get("market_ticker") or "").strip()
    return str(
        row.get("instrumentSymbol")
        or row.get("instrument_symbol")
        or row.get("ticker")
        or ""
    ).strip()


def _rule_url(venue: str, row: Mapping[str, Any]) -> str:
    if venue == "gemini":
        return str(row.get("termsAndConditionsUrl") or row.get("termsLink") or "").strip()
    # Kalshi's exact market payload embeds the operative rules.  Keep an optional
    # explicit URL if the API ever exposes one, but do not invent a URL.
    return str(row.get("rules_url") or row.get("rulesUrl") or "").strip()


def _profile_snapshot(venue: str) -> dict[str, str] | None:
    profile = PROFILES.get((venue, REQUIRED_FAMILY))
    if profile is None or not profile.complete:
        return None
    return {name: str(getattr(profile, name)) for name in SEMANTIC_DIMENSIONS}


def _settlement_family(settlement_key: str) -> str:
    parts = str(settlement_key).split("|")
    return parts[3].strip().lower() if len(parts) > 3 else ""


@dataclass(frozen=True)
class VenueRuleEvidence:
    venue: str
    market_id: str
    source_side: str
    rule_text_sha256: str
    rule_text_length: int
    rule_url: str
    semantic_profile: dict[str, str] | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PairRuleFingerprint:
    settlement_key: str
    venue_a: VenueRuleEvidence
    venue_b: VenueRuleEvidence
    rule_fingerprint_sha256: str
    certification_status: str
    exact_settlement_equivalent: bool
    execution_eligible: bool
    blockers: tuple[str, ...]
    approval_candidate: dict[str, Any]
    read_only: bool = True
    order_entry: bool = False

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["blockers"] = list(self.blockers)
        return row


def _evidence(venue: str, row: Mapping[str, Any], source_side: str) -> VenueRuleEvidence:
    source_side = str(source_side or "").upper()
    if source_side not in ALLOWED_SOURCE_SIDES:
        raise ValueError(f"invalid {venue} source side")
    market_id = _market_id(venue, row)
    if not market_id:
        raise ValueError(f"{venue} exact market ID missing")
    text = kalshi_rule_text(row) if venue == "kalshi" else gemini_rule_text(row)
    return VenueRuleEvidence(
        venue=venue,
        market_id=market_id,
        source_side=source_side,
        rule_text_sha256=_sha256_text(text) if text else "",
        rule_text_length=len(text),
        rule_url=_rule_url(venue, row),
        semantic_profile=_profile_snapshot(venue),
    )


def build_pair_fingerprint(
    *,
    settlement_key: str,
    kalshi_market: Mapping[str, Any],
    gemini_contract: Mapping[str, Any],
    kalshi_source_side: str,
    gemini_source_side: str,
) -> PairRuleFingerprint:
    """Build exact K/G evidence without ever self-authorizing execution."""
    if _settlement_family(settlement_key) != REQUIRED_FAMILY:
        raise ValueError("exact K/G fingerprint collector only accepts MLB game_total keys")

    k = _evidence("kalshi", kalshi_market, kalshi_source_side)
    g = _evidence("gemini", gemini_contract, gemini_source_side)
    assessment = assess_pair("kalshi", "gemini", REQUIRED_FAMILY)

    blockers = list(assessment.blockers)
    if not k.rule_text_sha256:
        blockers.append("kalshi_exact_rule_text_missing")
    if not g.rule_text_sha256:
        blockers.append("gemini_exact_rule_text_missing")
    if not g.rule_url:
        blockers.append("gemini_exact_terms_url_missing")
    if k.semantic_profile is None:
        blockers.append("kalshi_complete_semantic_profile_missing")
    if g.semantic_profile is None:
        blockers.append("gemini_complete_semantic_profile_missing")
    blockers = list(dict.fromkeys(blockers))

    fingerprint_payload = {
        "version": 1,
        "settlement_key": settlement_key,
        "venues": [
            {
                "venue": k.venue,
                "market_id": k.market_id,
                "source_side": k.source_side,
                "rule_text_sha256": k.rule_text_sha256,
                "rule_url": k.rule_url,
                "semantic_profile": k.semantic_profile,
            },
            {
                "venue": g.venue,
                "market_id": g.market_id,
                "source_side": g.source_side,
                "rule_text_sha256": g.rule_text_sha256,
                "rule_url": g.rule_url,
                "semantic_profile": g.semantic_profile,
            },
        ],
    }
    pair_hash = _stable_hash(fingerprint_payload)

    # This deliberately mirrors RuleApproval's shape, but it is *always* false.
    # An audited human/code-review step must explicitly change the registry entry.
    approval_candidate = {
        "settlement_key": settlement_key,
        "venue_a": "kalshi",
        "market_a": k.market_id,
        "source_side_a": k.source_side,
        "venue_b": "gemini",
        "market_b": g.market_id,
        "source_side_b": g.source_side,
        "rule_fingerprint_sha256": pair_hash,
        "approved_for_execution": False,
    }

    execution_eligible = bool(
        assessment.execution_eligible
        and assessment.exact_settlement_equivalent
        and not blockers
    )
    return PairRuleFingerprint(
        settlement_key=settlement_key,
        venue_a=k,
        venue_b=g,
        rule_fingerprint_sha256=pair_hash,
        certification_status=assessment.status.value,
        exact_settlement_equivalent=assessment.exact_settlement_equivalent,
        execution_eligible=execution_eligible,
        blockers=tuple(blockers),
        approval_candidate=approval_candidate,
    )


def _walk_contracts(node: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(node, Mapping):
        contracts = node.get("contracts")
        if isinstance(contracts, list):
            for contract in contracts:
                if isinstance(contract, Mapping):
                    yield contract
        for key in ("events", "data"):
            child = node.get(key)
            if isinstance(child, (Mapping, list, tuple)):
                yield from _walk_contracts(child)
    elif isinstance(node, (list, tuple)):
        for child in node:
            yield from _walk_contracts(child)


def find_gemini_contract(payload: Any, instrument_symbol: str) -> Mapping[str, Any] | None:
    target = str(instrument_symbol or "").strip().upper()
    matches = [
        row for row in _walk_contracts(payload)
        if _market_id("gemini", row).upper() == target
    ]
    return matches[0] if len(matches) == 1 else None


async def fetch_exact_live_rules(
    *,
    kalshi_ticker: str,
    gemini_symbol: str,
    timeout: float = 15.0,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Fetch public exact rule evidence only; no authenticated endpoints."""
    async with httpx.AsyncClient(timeout=timeout) as http:
        k_resp, g_resp = await asyncio.gather(
            http.get(f"{KALSHI_REST}/markets/{kalshi_ticker}"),
            http.get(
                f"{GEMINI_REST}/events",
                params={"status": "active", "sport": "baseball", "limit": 500, "offset": 0},
            ),
        )
        k_resp.raise_for_status()
        g_resp.raise_for_status()
    k_payload = k_resp.json()
    k_market = k_payload.get("market", k_payload) if isinstance(k_payload, Mapping) else {}
    if not isinstance(k_market, Mapping) or not _market_id("kalshi", k_market):
        raise RuntimeError("Kalshi exact market payload not found")
    contract = find_gemini_contract(g_resp.json(), gemini_symbol)
    if contract is None:
        raise RuntimeError("Gemini exact contract was not found uniquely in active MLB events")
    return k_market, contract


async def _run_cli(args: argparse.Namespace) -> dict[str, Any]:
    if args.kalshi_json and args.gemini_json:
        k_market = json.loads(Path(args.kalshi_json).read_text(encoding="utf-8"))
        g_contract = json.loads(Path(args.gemini_json).read_text(encoding="utf-8"))
    else:
        if not args.kalshi_ticker or not args.gemini_symbol:
            raise ValueError("provide both live market IDs or both JSON fixture paths")
        k_market, g_contract = await fetch_exact_live_rules(
            kalshi_ticker=args.kalshi_ticker,
            gemini_symbol=args.gemini_symbol,
        )
    result = build_pair_fingerprint(
        settlement_key=args.settlement_key,
        kalshi_market=k_market,
        gemini_contract=g_contract,
        kalshi_source_side=args.kalshi_source_side,
        gemini_source_side=args.gemini_source_side,
    )
    return result.as_dict()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settlement-key", required=True)
    parser.add_argument("--kalshi-ticker")
    parser.add_argument("--gemini-symbol")
    parser.add_argument("--kalshi-source-side", default="YES")
    parser.add_argument("--gemini-source-side", default="YES")
    parser.add_argument("--kalshi-json")
    parser.add_argument("--gemini-json")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run_cli(args)), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
