"""Collect exact-contract rule evidence for strict Polymarket US <-> Kalshi MLB props.

This is an audit-input collector, not an equivalence classifier.  It resolves the
same strict identity universe used by the MLB pricing scanner, fetches the exact
public market metadata when available, and emits evidence packets for independent
rule comparison.  No packet can make a pair live-ready.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from src import strict_mlb_prop_snapshot as base
from src.strict_mlb_prop_snapshot_v2 import _fetch_poly_mlb_search
from src import strict_mlb_prop_snapshot_v3 as v3
from src.mlb_prop_rule_evidence import build_exact_rule_evidence_packet

logger = logging.getLogger("kalshi_arb.mlb_prop_rule_evidence_snapshot")
base._fetch_poly_mlb = _fetch_poly_mlb_search


def _poly_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("slug") or ""): row for row in rows if str(row.get("slug") or "")}


def _kalshi_index(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for event in events:
        for market in event.get("markets") or []:
            ticker = str(market.get("ticker") or "")
            if ticker:
                out[ticker] = dict(market)
    return out


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        poly_rows, kalshi_events = await asyncio.gather(
            base._fetch_poly_mlb(http), base._fetch_kalshi_events(http)
        )
        candidates = base.discover_strict_candidates(poly_rows, kalshi_events)
        p_index = _poly_index(poly_rows)
        k_index = _kalshi_index(kalshi_events)

        # Keep request volume bounded exactly like the rotating price scanner.
        slot = int(__import__("time").time() // v3.ROTATION_SECONDS)
        selected, batch_start = v3.select_batch(candidates, slot=slot)
        semaphore = asyncio.Semaphore(v3.PRICE_CONCURRENCY)

        async def collect(candidate: base.StrictCandidate) -> dict[str, Any]:
            poly = dict(p_index.get(candidate.poly_slug) or {})
            kalshi = dict(k_index.get(candidate.kalshi_ticker) or {})

            # Enrich from exact-market endpoints where public metadata is available.
            # Endpoint/schema failures are non-fatal: missing fields remain explicit
            # and the packet stays fail-closed.
            async with semaphore:
                poly_exact, kalshi_exact = await asyncio.gather(
                    v3._fetch_optional(http, f"{base.POLY}/v1/markets/{candidate.poly_slug}"),
                    v3._fetch_optional(http, f"{base.KALSHI}/markets/{candidate.kalshi_ticker}"),
                )
            poly_body, poly_error = poly_exact
            kalshi_body, kalshi_error = kalshi_exact
            if isinstance(poly_body, dict):
                exact_market = poly_body.get("market") if isinstance(poly_body.get("market"), dict) else poly_body
                poly.update(dict(exact_market))
            if isinstance(kalshi_body, dict):
                exact_market = kalshi_body.get("market") if isinstance(kalshi_body.get("market"), dict) else kalshi_body
                kalshi.update(dict(exact_market))

            packet = build_exact_rule_evidence_packet(
                family=candidate.family,
                polymarket=poly,
                kalshi=kalshi,
            )
            return {
                "family": candidate.family,
                "poly_slug": candidate.poly_slug,
                "kalshi_ticker": candidate.kalshi_ticker,
                "player": candidate.player,
                "threshold": str(candidate.threshold),
                "game_date": candidate.game_date,
                "start_skew_seconds": candidate.start_skew_seconds,
                "exact_rule_evidence": packet,
                "poly_metadata_fetch_error": poly_error,
                "kalshi_metadata_fetch_error": kalshi_error,
                "full_rules_audited_shadow": False,
                "live_probe_ready": False,
                "read_only": True,
                "order_entry": False,
            }

        rows = await asyncio.gather(*(collect(candidate) for candidate in selected)) if selected else []

    complete = [
        row for row in rows
        if bool((row.get("exact_rule_evidence") or {}).get("evidence_complete_for_independent_compare"))
    ]
    result = {
        "total_strict_candidates": len(candidates),
        "batch_size": len(selected),
        "batch_start": batch_start,
        "rotation_slot": slot,
        "evidence_complete_for_independent_compare": len(complete),
        "evidence_incomplete": len(rows) - len(complete),
        "rows": rows,
        "audit_warning": (
            "Evidence completeness only means exact public rule/source payloads were captured. "
            "It is not a settlement-equivalence determination and cannot make a live probe ready."
        ),
        "full_rules_audited_shadow_pairs": 0,
        "live_probe_ready_pairs": 0,
        "read_only": True,
        "order_entry": False,
    }
    logger.info("MLB_PROP_RULE_EVIDENCE_SNAPSHOT %s", json.dumps(result, separators=(",", ":")))
    print(json.dumps(result, sort_keys=True))
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
