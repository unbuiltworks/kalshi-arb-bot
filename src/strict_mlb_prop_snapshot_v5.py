"""Live-participation-aware, rate-safe MLB prop paper-probe scanner.

Read-only research. Compared with v4 this scanner supplies the participation rule
engine with observable MLB game-state proof and prioritizes already-cleared props for
book pricing. Participation clearance is NOT an independent cross-venue settlement
rules audit, so this module never labels a quote live-probe-ready.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src import strict_mlb_prop_snapshot as base
from src import strict_mlb_prop_snapshot_v3 as v3
from src import strict_mlb_prop_snapshot_v4 as v4
from src.strict_mlb_prop_snapshot_v2 import _fetch_poly_mlb_search
from src.mlb_live_participation import fetch_game_boxscores, participation_proof, proof_dict

logger = logging.getLogger("kalshi_arb.strict_mlb_prop_snapshot_v5")
base._fetch_poly_mlb = _fetch_poly_mlb_search
MIN_PREFERRED_NET = Decimal("0.03")


def _paper_probe_flags(assessment, probe5: dict[str, Any] | None) -> tuple[bool, bool]:
    """Return participation-cleared paper-economics flags, never live readiness."""
    positive = bool(assessment.settlement_equivalent_now and probe5)
    preferred = bool(
        positive
        and probe5 is not None
        and Decimal(probe5["conservative_locked_profit"]) >= MIN_PREFERRED_NET
    )
    return positive, preferred


def _empty(candidate: base.StrictCandidate, assessment, proof, error: str | None) -> dict[str, Any]:
    return {
        "family": candidate.family,
        "poly_slug": candidate.poly_slug,
        "kalshi_ticker": candidate.kalshi_ticker,
        "player": candidate.player,
        "threshold": str(candidate.threshold),
        "game_date": candidate.game_date,
        "start_skew_seconds": candidate.start_skew_seconds,
        "rule_status": assessment.status.value,
        "required_participation_gate": assessment.required_gate,
        "participation_proof": proof_dict(proof),
        "settlement_equivalent_now": assessment.settlement_equivalent_now,
        "participation_gate_satisfied": assessment.settlement_equivalent_now,
        "full_rules_audited": False,
        "rules_status": "RULES_PENDING",
        "fully_audited_shadow_ready": False,
        "book_comparable": False,
        "positive_conservative_plan": None,
        "probe_1usd": None,
        "probe_5usd": None,
        "positive_paper_probe": False,
        "preferred_paper_probe": False,
        # Legacy keys remain explicitly false so older consumers fail closed.
        "positive_live_probe": False,
        "preferred_live_probe": False,
        "live_probe_ready": False,
        "fetch_error": error,
        "execution_eligible": False,
        "read_only": True,
        "order_entry": False,
    }


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        poly_markets, kalshi_events = await asyncio.gather(
            base._fetch_poly_mlb(http), base._fetch_kalshi_events(http)
        )
        candidates = base.discover_strict_candidates(poly_markets, kalshi_events)

        # One public MLB schedule + boxscore pass gives participation evidence for
        # the entire slate before we spend venue rate-limit budget pricing books.
        games = await fetch_game_boxscores(http, {c.game_date for c in candidates})
        proofs = {
            c.key: participation_proof(
                family=c.family,
                player_name=c.player,
                poly_slug=c.poly_slug,
                games=games,
            )
            for c in candidates
        }
        assessments = {
            c.key: base.assess_rule_equivalence(c.family, proofs[c.key])
            for c in candidates
        }
        cleared = [c for c in candidates if assessments[c.key].settlement_equivalent_now]
        pending = [c for c in candidates if not assessments[c.key].settlement_equivalent_now]

        series_meta: dict[str, dict[str, Any]] = {}
        for series in sorted(set(base.FAMILY_SERIES.values())):
            body, error = await v3._fetch_optional(http, f"{base.KALSHI}/series/{series}")
            if body is not None and error is None:
                series_meta[series] = dict(body.get("series") or {})

        # Research fast lane: spend the 60-pair pricing budget on candidates whose
        # participation branch has already been resolved. If no candidate has
        # cleared yet, retain v4's rotating shadow behavior for research.
        slot = int(time.time() // v3.ROTATION_SECONDS)
        priority_pool = cleared if cleared else candidates
        selected, batch_start = v3.select_batch(priority_pool, slot=slot)
        semaphore = asyncio.Semaphore(v3.PRICE_CONCURRENCY)

        async def price(candidate: base.StrictCandidate) -> dict[str, Any]:
            proof = proofs[candidate.key]
            assessment = assessments[candidate.key]
            async with semaphore:
                poly_body, poly_error = await v3._fetch_optional(
                    http, f"{base.POLY}/v1/markets/{candidate.poly_slug}/book"
                )
                kalshi_body, kalshi_error = await v3._fetch_optional(
                    http,
                    f"{base.KALSHI}/markets/{candidate.kalshi_ticker}/orderbook",
                    params={"depth": 1},
                )
            if poly_body is None or kalshi_body is None:
                error = ";".join(x for x in (poly_error, kalshi_error) if x) or "book_missing"
                return _empty(candidate, assessment, proof, error)

            pbook = base.poly_top_book(poly_body)
            kbook = base.kalshi_top_book(kalshi_body)
            series = base.FAMILY_SERIES[candidate.family]
            meta = series_meta.get(series) or {}
            fee_type = str(meta.get("fee_type") or "")
            multiplier = base._d(meta.get("fee_multiplier"))
            plan = probe1 = probe5 = None
            if (
                pbook is not None
                and kbook is not None
                and fee_type in {"quadratic", "quadratic_with_maker_fees"}
                and multiplier is not None
                and Decimal("0") <= multiplier <= Decimal("100")
            ):
                plan = base.best_conservative_plan(pbook, kbook, kalshi_fee_multiplier=multiplier)
                probe1 = v4.best_capped_plan(
                    pbook, kbook, kalshi_fee_multiplier=multiplier, capital_cap=Decimal("1")
                )
                probe5 = v4.best_capped_plan(
                    pbook, kbook, kalshi_fee_multiplier=multiplier, capital_cap=Decimal("5")
                )

            positive_paper_probe, preferred_paper_probe = _paper_probe_flags(assessment, probe5)
            return {
                "family": candidate.family,
                "poly_slug": candidate.poly_slug,
                "kalshi_ticker": candidate.kalshi_ticker,
                "player": candidate.player,
                "threshold": str(candidate.threshold),
                "game_date": candidate.game_date,
                "start_skew_seconds": candidate.start_skew_seconds,
                "rule_status": assessment.status.value,
                "required_participation_gate": assessment.required_gate,
                "participation_proof": proof_dict(proof),
                "settlement_equivalent_now": assessment.settlement_equivalent_now,
                "participation_gate_satisfied": assessment.settlement_equivalent_now,
                "full_rules_audited": False,
                "rules_status": "RULES_PENDING",
                "fully_audited_shadow_ready": False,
                "kalshi_fee_type": fee_type,
                "kalshi_fee_multiplier": str(multiplier) if multiplier is not None else None,
                "book_comparable": pbook is not None and kbook is not None,
                "positive_conservative_plan": plan,
                "probe_1usd": probe1,
                "probe_5usd": probe5,
                "positive_paper_probe": positive_paper_probe,
                "preferred_paper_probe": preferred_paper_probe,
                # Participation alone can never promote a live probe.
                "positive_live_probe": False,
                "preferred_live_probe": False,
                "live_probe_ready": False,
                "fetch_error": None,
                "execution_eligible": False,
                "read_only": True,
                "order_entry": False,
            }

        priced = await asyncio.gather(*(price(c) for c in selected)) if selected else []

    positive = [r for r in priced if r.get("positive_conservative_plan")]
    probe1 = [r for r in priced if r.get("probe_1usd")]
    probe5 = [r for r in priced if r.get("probe_5usd")]
    paper_positive = [r for r in priced if r.get("positive_paper_probe")]
    preferred_paper = [r for r in priced if r.get("preferred_paper_probe")]
    failures = [r for r in priced if r.get("fetch_error")]
    paper_candidates = [
        r for r in priced
        if r.get("settlement_equivalent_now")
        and r.get("book_comparable")
        and not r.get("fetch_error")
        and (r.get("probe_1usd") or r.get("probe_5usd"))
    ]

    def top(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda r: Decimal(r[field]["conservative_locked_profit"]),
            reverse=True,
        )[:20]

    result = {
        "total_strict_candidates": len(candidates),
        "participation_cleared_candidates": len(cleared),
        "participation_pending_candidates": len(pending),
        "mlb_games_with_boxscores": len(games),
        "pricing_pool": "PARTICIPATION_CLEARED" if cleared else "ALL_PENDING_RESEARCH",
        "batch_size": len(selected),
        "batch_start": batch_start,
        "rotation_slot": slot,
        "book_comparable": sum(bool(r["book_comparable"]) for r in priced),
        "fetch_failures": len(failures),
        "conservative_positive_pairs": len(positive),
        "probe_1usd_positive_pairs": len(probe1),
        "probe_5usd_positive_pairs": len(probe5),
        "positive_paper_probe_pairs": len(paper_positive),
        "preferred_paper_probe_pairs": len(preferred_paper),
        "paper_probe_candidates": paper_candidates,
        "top_paper_probes": top(paper_positive, "probe_5usd") if paper_positive else [],
        "preferred_paper_probe_packets": top(preferred_paper, "probe_5usd") if preferred_paper else [],
        # Legacy aggregate keys remain fail-closed for older consumers.
        "positive_live_probe_pairs": 0,
        "preferred_live_probe_pairs": 0,
        "top_live_probes": [],
        "ready_probe_packets": [],
        "live_probe_ready_pairs": 0,
        "preferred_probe_threshold": str(MIN_PREFERRED_NET),
        "rules_status": "RULES_PENDING",
        "full_rules_audited": False,
        "fully_audited_shadow_ready_pairs": 0,
        "rule_note": (
            "MLB game-state evidence can clear the family-specific participation branch, but that is not a full "
            "Polymarket US↔Kalshi settlement-rule audit. <=$1/<= $5 results are paper-shadow economics only until "
            "independent rules evidence promotes the exact family to FULL_RULES_AUDITED_SHADOW."
        ),
        "read_only": True,
        "order_entry": False,
        "execution_eligible_pairs": 0,
    }
    logger.info("STRICT_MLB_PROP_SNAPSHOT_V5 %s", json.dumps(result, separators=(",", ":")))
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
