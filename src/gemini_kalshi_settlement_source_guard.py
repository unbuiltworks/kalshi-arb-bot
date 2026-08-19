"""Fail-closed settlement-source classification for Gemini/Kalshi crypto research.

This module deliberately does *not* decide that two markets are economically equivalent.
It only classifies source/index evidence. Exact source agreement is at most a candidate
for a deeper methodology audit; any known source mismatch is a hard research-only block.

Current independently verified BTC 15-minute evidence (2026-08-19):
- Gemini BTC 15m contract pages identify Kaiko index ``GRR-KAIKO_BTCUSD_60S``.
- Kalshi crypto price rules identify CF Benchmarks as the source agency / RTI family.

Therefore BTC Gemini<->Kalshi 15m is not a risk-free settlement-equivalent arb sleeve.
The existing ``INDEX_MISMATCH_RESEARCH_ONLY`` gate must remain in place.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettlementSourceEvidence:
    agency: str | None
    index: str | None


@dataclass(frozen=True)
class SettlementSourceClassification:
    status: str
    hard_block: bool
    exact_source_match: bool
    reason: str


def _norm(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def classify_settlement_sources(
    gemini: SettlementSourceEvidence,
    kalshi: SettlementSourceEvidence,
) -> SettlementSourceClassification:
    """Classify source evidence without ever promoting live readiness.

    Rules:
    - missing agency/index evidence => unresolved hard block;
    - known differing agency or index => confirmed mismatch hard block;
    - exact agency+index equality => deeper methodology audit still required.
    """
    ga, gi = _norm(gemini.agency), _norm(gemini.index)
    ka, ki = _norm(kalshi.agency), _norm(kalshi.index)

    if not ga or not gi or not ka or not ki:
        return SettlementSourceClassification(
            status="SETTLEMENT_SOURCE_EVIDENCE_INCOMPLETE_RESEARCH_ONLY",
            hard_block=True,
            exact_source_match=False,
            reason="Both venues require explicit agency and index identifiers.",
        )

    if ga != ka or gi != ki:
        return SettlementSourceClassification(
            status="SETTLEMENT_SOURCE_MISMATCH_CONFIRMED_RESEARCH_ONLY",
            hard_block=True,
            exact_source_match=False,
            reason="Venue settlement agency/index identifiers differ.",
        )

    return SettlementSourceClassification(
        status="SETTLEMENT_SOURCE_MATCH_CANDIDATE_NEEDS_METHOD_AUDIT",
        hard_block=True,
        exact_source_match=True,
        reason="Exact source identifiers match, but methodology/rules equivalence is not yet audited.",
    )


# Independently verified current BTC 15-minute source fingerprints.
# Gemini official 15m contract pages: GRR-KAIKO_BTCUSD_60S (Kaiko).
BTC15M_GEMINI = SettlementSourceEvidence("Kaiko", "GRR-KAIKO_BTCUSD_60S")
# Kalshi uses the CF Benchmarks Bitcoin Real-Time Index family for BTC price resolution.
# The exact per-contract identifier should still be captured from current Kalshi rules/metadata.
BTC15M_KALSHI_PROVIDER_FAMILY = SettlementSourceEvidence("CF Benchmarks", "CF Bitcoin Real-Time Index")
BTC15M_CURRENT_CLASSIFICATION = classify_settlement_sources(
    BTC15M_GEMINI,
    BTC15M_KALSHI_PROVIDER_FAMILY,
)
