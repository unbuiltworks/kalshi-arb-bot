"""Read-only exact rule fingerprinting for Kalshi <-> Polymarket US MLB totals.

This collector exists to answer one narrow question: can an apparent cross-venue
price lock be treated as settlement-guaranteed?  It binds the exact public market
IDs and market text to the current audited Polymarket US sports lifecycle rules and
the existing Kalshi game-total lifecycle profile.  It never authorizes execution.

No credentials, trading endpoints, approval-registry writes, or live arming exist
in this module.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Mapping

import httpx

from src.mlb_game_rule_certifications import PROFILES

KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"
POLY_PUBLIC = "https://gateway.polymarket.us"
REQUIRED_FAMILY = "game_total"
POLY_SPORTS_RULE_SOURCE = "https://docs.polymarket.us/faqs/sports-faqs"
POLY_SPORTS_RULE_AUDIT_DATE = "2026-08-18"
ALLOWED_SOURCE_SIDES = frozenset({"YES", "NO"})
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

# Canonical summary of the current Polymarket US Sports FAQ as reviewed on the
# audit date above.  In particular, MLB's declared official result governs totals
# regardless of innings; otherwise postponement/interruption/cancellation can end
# in LFMP based on the contract expiration/official-announcement path.
POLY_US_GAME_TOTAL_PROFILE = {
    "official_source": "governing_body_primary_then_official_competition_then_secondary_reporting",
    "normal_outcome": "combined_runs_compared_with_line",
    "extra_innings": "included",
    "minimum_completion": "official_mlb_result_governs_regardless_of_innings_else_completion_before_expiration_or_lfmp",
    "not_started": "rescheduled_result_if_before_contract_expiration_else_lfmp",
    "interrupted": "official_result_if_threshold_reached_else_completion_before_expiration_else_lfmp",
    "cancellation": "lfmp_at_official_cancellation_if_not_rescheduled_before_expiration",
    "push_tie": "binary_contract_threshold_rule_no_sportsbook_push",
}


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u2019", "'").split())


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_hash(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _sha256_text(blob)


def _decimal(value: Any) -> Decimal | None:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _family(settlement_key: str) -> str:
    parts = str(settlement_key).split("|")
    return parts[3].strip().lower() if len(parts) > 3 else ""


def _line(settlement_key: str) -> Decimal | None:
    parts = str(settlement_key).split("|")
    return _decimal(parts[9]) if len(parts) > 9 and parts[9] else None


def _comparator(settlement_key: str) -> str:
    parts = str(settlement_key).split("|")
    return parts[8].strip().upper() if len(parts) > 8 else ""


def kalshi_rule_text(market: Mapping[str, Any]) -> str:
    parts = (
        market.get("rules_primary"),
        market.get("rules_secondary"),
        market.get("early_close_condition"),
    )
    return "\n".join(text for text in (_norm_text(v) for v in parts) if text)


def polymarket_rule_text(market: Mapping[str, Any]) -> str:
    parts = (
        market.get("question"),
        market.get("title"),
        market.get("subtitle"),
        market.get("description"),
        market.get("resolutionSource"),
        market.get("spreadTotalSuffix"),
    )
    return "\n".join(text for text in (_norm_text(v) for v in parts) if text)


def kalshi_profile() -> dict[str, str] | None:
    profile = PROFILES.get(("kalshi", REQUIRED_FAMILY))
    if profile is None or not profile.complete:
        return None
    return {name: str(getattr(profile, name)) for name in SEMANTIC_DIMENSIONS}


def polymarket_profile() -> dict[str, str]:
    return dict(POLY_US_GAME_TOTAL_PROFILE)


def semantic_blockers(k_profile: Mapping[str, str], p_profile: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        name for name in SEMANTIC_DIMENSIONS
        if str(k_profile.get(name, "")).strip().lower() != str(p_profile.get(name, "")).strip().lower()
    )


def _poly_type(market: Mapping[str, Any]) -> str:
    return str(market.get("sportsMarketTypeV2") or market.get("sportsMarketType") or "").upper().removeprefix("SPORTS_MARKET_TYPE_")


@dataclass(frozen=True)
class VenueEvidence:
    venue: str
    market_id: str
    source_side: str
    rule_text_sha256: str
    rule_text_length: int
    semantic_profile: dict[str, str] | None
    semantic_source: str


@dataclass(frozen=True)
class KpFingerprint:
    settlement_key: str
    venue_a: VenueEvidence
    venue_b: VenueEvidence
    rule_fingerprint_sha256: str
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


def build_pair_fingerprint(
    *,
    settlement_key: str,
    kalshi_market: Mapping[str, Any],
    polymarket_market: Mapping[str, Any],
    kalshi_source_side: str = "YES",
    polymarket_source_side: str = "YES",
) -> KpFingerprint:
    if _family(settlement_key) != REQUIRED_FAMILY:
        raise ValueError("K/P fingerprint collector only accepts MLB game_total keys")
    if _comparator(settlement_key) not in {"GT", "LT"}:
        raise ValueError("game total settlement key must be GT or LT")

    k_side = str(kalshi_source_side).upper()
    p_side = str(polymarket_source_side).upper()
    if k_side not in ALLOWED_SOURCE_SIDES or p_side not in ALLOWED_SOURCE_SIDES:
        raise ValueError("invalid native source-side orientation")

    k_id = str(kalshi_market.get("ticker") or kalshi_market.get("market_ticker") or "").strip()
    p_id = str(polymarket_market.get("slug") or "").strip()
    if not k_id or not p_id:
        raise ValueError("exact venue market ID missing")

    k_text = kalshi_rule_text(kalshi_market)
    p_text = polymarket_rule_text(polymarket_market)
    k_profile = kalshi_profile()
    p_profile = polymarket_profile()
    blockers: list[str] = []
    if not k_text:
        blockers.append("kalshi_exact_rule_text_missing")
    if not p_text:
        blockers.append("polymarket_exact_market_text_missing")
    if k_profile is None:
        blockers.append("kalshi_complete_semantic_profile_missing")
    else:
        blockers.extend(semantic_blockers(k_profile, p_profile))

    poly_type = _poly_type(polymarket_market)
    if poly_type != "TOTAL":
        blockers.append("polymarket_exact_market_not_total")
    key_line = _line(settlement_key)
    poly_line = _decimal(polymarket_market.get("line"))
    if key_line is None or poly_line is None or abs(key_line) != abs(poly_line):
        blockers.append("polymarket_exact_line_mismatch")

    # A TSC slug is an additional fail-closed identity check, not the rule source.
    if not p_id.lower().startswith("tsc-mlb-"):
        blockers.append("polymarket_exact_product_not_mlb_tsc")

    blockers = list(dict.fromkeys(blockers))
    exact = not blockers

    poly_rule_snapshot = {
        "audit_date": POLY_SPORTS_RULE_AUDIT_DATE,
        "source": POLY_SPORTS_RULE_SOURCE,
        "semantic_profile": p_profile,
    }
    payload = {
        "version": 1,
        "settlement_key": settlement_key,
        "kalshi": {
            "market_id": k_id,
            "source_side": k_side,
            "rule_text_sha256": _sha256_text(k_text) if k_text else "",
            "semantic_profile": k_profile,
        },
        "polymarket_us": {
            "market_id": p_id,
            "source_side": p_side,
            "market_text_sha256": _sha256_text(p_text) if p_text else "",
            "sports_rule_snapshot_sha256": _stable_hash(poly_rule_snapshot),
            "sports_rule_snapshot": poly_rule_snapshot,
        },
    }
    pair_hash = _stable_hash(payload)
    approval = {
        "settlement_key": settlement_key,
        "venue_a": "kalshi",
        "market_a": k_id,
        "source_side_a": k_side,
        "venue_b": "polymarket",
        "market_b": p_id,
        "source_side_b": p_side,
        "rule_fingerprint_sha256": pair_hash,
        "approved_for_execution": False,
    }
    return KpFingerprint(
        settlement_key=settlement_key,
        venue_a=VenueEvidence(
            venue="kalshi", market_id=k_id, source_side=k_side,
            rule_text_sha256=_sha256_text(k_text) if k_text else "",
            rule_text_length=len(k_text), semantic_profile=k_profile,
            semantic_source=str(PROFILES.get(("kalshi", REQUIRED_FAMILY)).source_ref) if PROFILES.get(("kalshi", REQUIRED_FAMILY)) else "",
        ),
        venue_b=VenueEvidence(
            venue="polymarket", market_id=p_id, source_side=p_side,
            rule_text_sha256=_sha256_text(p_text) if p_text else "",
            rule_text_length=len(p_text), semantic_profile=p_profile,
            semantic_source=POLY_SPORTS_RULE_SOURCE,
        ),
        rule_fingerprint_sha256=pair_hash,
        exact_settlement_equivalent=exact,
        execution_eligible=False,  # this collector is intentionally incapable of self-approval
        blockers=tuple(blockers),
        approval_candidate=approval,
    )


async def fetch_exact_public_markets(*, kalshi_ticker: str, polymarket_slug: str, timeout: float = 20.0) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    async with httpx.AsyncClient(timeout=timeout) as http:
        k_resp, p_resp = await asyncio.gather(
            http.get(f"{KALSHI_REST}/markets/{kalshi_ticker}"),
            http.get(f"{POLY_PUBLIC}/v1/market/slug/{polymarket_slug}"),
        )
        k_resp.raise_for_status()
        p_resp.raise_for_status()
    k_payload = k_resp.json()
    p_payload = p_resp.json()
    k_market = k_payload.get("market", k_payload) if isinstance(k_payload, Mapping) else {}
    p_market = p_payload.get("market", p_payload) if isinstance(p_payload, Mapping) else {}
    if not isinstance(k_market, Mapping) or not str(k_market.get("ticker") or ""):
        raise RuntimeError("Kalshi exact market payload not found")
    if not isinstance(p_market, Mapping) or str(p_market.get("slug") or "") != polymarket_slug:
        raise RuntimeError("Polymarket US exact market payload not found")
    return k_market, p_market


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    k_market, p_market = await fetch_exact_public_markets(
        kalshi_ticker=args.kalshi_ticker,
        polymarket_slug=args.polymarket_slug,
    )
    result = build_pair_fingerprint(
        settlement_key=args.settlement_key,
        kalshi_market=k_market,
        polymarket_market=p_market,
        kalshi_source_side=args.kalshi_source_side,
        polymarket_source_side=args.polymarket_source_side,
    ).as_dict()
    result["exact_market_audit"] = {
        "polymarket_question": _norm_text(p_market.get("question")),
        "polymarket_description": _norm_text(p_market.get("description")),
        "polymarket_resolution_source": _norm_text(p_market.get("resolutionSource")),
        "polymarket_type": _poly_type(p_market),
        "polymarket_line": str(p_market.get("line") or ""),
        "polymarket_end_date": str(p_market.get("endDate") or ""),
        "kalshi_rules_primary": _norm_text(k_market.get("rules_primary")),
        "kalshi_rules_secondary": _norm_text(k_market.get("rules_secondary")),
    }
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--settlement-key", required=True)
    p.add_argument("--kalshi-ticker", required=True)
    p.add_argument("--polymarket-slug", required=True)
    p.add_argument("--kalshi-source-side", default="YES")
    p.add_argument("--polymarket-source-side", default="YES")
    args = p.parse_args()
    print(json.dumps(asyncio.run(_run(args)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
