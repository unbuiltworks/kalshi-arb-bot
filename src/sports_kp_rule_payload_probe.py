"""Read-only exact-rule payload probe for current MLB Kalshi/Polymarket counterparts.

This intentionally performs no pricing and imports no order transport. It joins the
existing strict normal-event identities, then emits only settlement/rule-relevant
fields from the exact raw venue records for a small family sample.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
import json
import logging
from typing import Any, Mapping

import httpx

from src.mlb_game_side_normalization import normalize_kalshi_mlb, normalize_polymarket_mlb
from src.universal_three_venue_sports import fetch_kalshi_mlb, fetch_polymarket_mlb

logger = logging.getLogger("kalshi_arb.sports_kp_rule_payload_probe")
FAMILIES = ("moneyline", "spread", "game_total")

RULE_WORDS = (
    "rule", "settle", "result", "close", "expire", "void", "cancel", "postpon",
    "source", "stat", "game", "period", "inning", "overtime", "extra", "tie", "push",
    "question", "title", "subtitle", "description", "line", "type", "status",
)


def _relevant(record: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        lower = str(key).lower()
        if not any(word in lower for word in RULE_WORDS):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[str(key)] = value
        elif isinstance(value, Mapping):
            nested = _relevant(value)
            if nested:
                out[str(key)] = nested
        elif isinstance(value, list):
            rows = []
            for item in value[:8]:
                if isinstance(item, Mapping):
                    nested = _relevant(item)
                    if nested:
                        rows.append(nested)
                elif isinstance(item, (str, int, float, bool)) or item is None:
                    rows.append(item)
            if rows:
                out[str(key)] = rows
    return out


def _poly_market_map(events: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    out = {}
    for event in events:
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            slug = str(market.get("slug") or market.get("id") or "")
            if slug:
                out[slug] = (event, dict(market))
    return out


def _kalshi_market_map(events: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    out = {}
    for event in events:
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            ticker = str(market.get("ticker") or "")
            if ticker:
                out[ticker] = (event, dict(market))
    return out


async def run_once() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        poly, kalshi = await asyncio.gather(fetch_polymarket_mlb(http), fetch_kalshi_mlb(http))

    pnorm = normalize_polymarket_mlb(poly)
    knorm = normalize_kalshi_mlb(kalshi)
    grouped: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for row in pnorm + knorm:
        grouped[row.contract_key][row.identity.venue].append(row)

    pmap = _poly_market_map(poly)
    kmap = _kalshi_market_map(kalshi)
    samples: list[dict[str, Any]] = []
    seen_family: set[str] = set()
    for key, by_venue in grouped.items():
        if not {"polymarket", "kalshi"}.issubset(by_venue):
            continue
        puniq = {(r.venue_market_id, r.side): r for r in by_venue["polymarket"]}
        kuniq = {(r.venue_market_id, r.side): r for r in by_venue["kalshi"]}
        if len(puniq) != 1 or len(kuniq) != 1:
            continue
        prow = next(iter(puniq.values()))
        krow = next(iter(kuniq.values()))
        family = prow.identity.market_family
        if family not in FAMILIES or family in seen_family:
            continue
        if family != krow.identity.market_family:
            continue
        praw = pmap.get(prow.venue_market_id)
        kraw = kmap.get(krow.venue_market_id)
        if praw is None or kraw is None:
            continue
        pevent, pmarket = praw
        kevent, kmarket = kraw
        samples.append({
            "contract_key": key,
            "family": family,
            "identity": {
                "subject": prow.identity.subject,
                "stat": prow.identity.stat,
                "comparator": prow.identity.comparator,
                "line": None if prow.identity.line is None else str(prow.identity.line),
                "poly_start_ms": prow.identity.event.scheduled_start_ms,
                "kalshi_start_ms": krow.identity.event.scheduled_start_ms,
            },
            "polymarket": {
                "slug": prow.venue_market_id,
                "side": prow.side,
                "event": _relevant(pevent),
                "market": _relevant(pmarket),
                "raw_event_keys": sorted(str(k) for k in pevent),
                "raw_market_keys": sorted(str(k) for k in pmarket),
            },
            "kalshi": {
                "ticker": krow.venue_market_id,
                "side": krow.side,
                "event": _relevant(kevent),
                "market": _relevant(kmarket),
                "raw_event_keys": sorted(str(k) for k in kevent),
                "raw_market_keys": sorted(str(k) for k in kmarket),
            },
            "read_only": True,
            "order_entry": False,
        })
        seen_family.add(family)
        if len(seen_family) == len(FAMILIES):
            break

    summary = {
        "raw": {"polymarket_events": len(poly), "kalshi_events": len(kalshi)},
        "normalized": {"polymarket_sides": len(pnorm), "kalshi_sides": len(knorm)},
        "sampled_families": sorted(seen_family),
        "samples": samples,
        "read_only": True,
        "order_entry": False,
    }
    logger.warning("SPORTS_KP_RULE_PAYLOAD_PROBE %s", json.dumps(summary, separators=(",", ":"), default=str))
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    print(json.dumps(asyncio.run(run_once()), sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
