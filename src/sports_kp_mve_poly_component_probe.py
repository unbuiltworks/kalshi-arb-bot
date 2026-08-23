"""Read-only MVE-NO + exact Polymarket component Dutch-book probe.

For a Kalshi conjunction MVE with selected component x_k, the scalar identity
combo_NO + x_k >= 1 remains valid if x_k is purchased on another venue ONLY when
the external contract is rule-equivalent to the selected Kalshi component.

This module first tests economics on exact NFL event/team/spread-threshold mappings
using public Polymarket books. Positive rows remain RULE_UNCERTIFIED_SHADOW until
an explicit settlement-rule audit approves them. No private API or order entry.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from typing import Any, Mapping

import httpx

from src.auth import KalshiClient
from src import sports_kp_mve_exact_fee_probe as fees
from src import sports_kp_mve_minimal_no_scan as minimal
from src import sports_kp_mve_same_venue_scan as scan
from src.strict_mlb_prop_snapshot import poly_top_book, polymarket_fee

D = Decimal
ONE = D("1")
TARGET = D("0.03")
POLY = "https://gateway.polymarket.us"

TARGETS = (
    {
        "mve_ticker": "KXMVECROSSCATEGORY-SHARD1-S2026037922EEFBB-AA8CCC11F8D",
        "kalshi_component": "KXNFLSPREAD-26AUG23SEATEN-SEA5",
        "team": "sea",
        "threshold": D("4.5"),
    },
    {
        "mve_ticker": "KXMVECROSSCATEGORY-SHARD1-S2026037922EEFBB-5170A04850F",
        "kalshi_component": "KXNFLSPREAD-26AUG23SEATEN-TEN15",
        "team": "ten",
        "threshold": D("14.5"),
    },
)


def _d(value: Any) -> Decimal | None:
    try:
        return D(str(value))
    except Exception:
        return None


def _desc_threshold(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace("+", "")
    try:
        return abs(D(text))
    except Exception:
        return None


def _team_abbr(side: Mapping[str, Any]) -> str:
    team = side.get("team") if isinstance(side.get("team"), Mapping) else {}
    return str(side.get("teamAbbreviation") or team.get("abbreviation") or "").strip().lower()


def _poly_candidates(events: list[dict[str, Any]], team: str, threshold: Decimal) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        hay = " ".join(str(event.get(k) or "") for k in ("slug", "title")).lower()
        if not ("sea" in hay and "ten" in hay):
            continue
        for market in event.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            mt = str(market.get("sportsMarketTypeV2") or market.get("sportsMarketType") or "").upper()
            if "SPREAD" not in mt:
                continue
            slug = str(market.get("slug") or "")
            for side in market.get("marketSides") or []:
                if not isinstance(side, Mapping) or _team_abbr(side) != team:
                    continue
                if _desc_threshold(side.get("description")) != threshold:
                    continue
                out.append({
                    "event_slug": event.get("slug"),
                    "event_title": event.get("title"),
                    "start_time": event.get("startTime") or event.get("startDate"),
                    "market_slug": slug,
                    "market_title": market.get("title") or market.get("question"),
                    "side_description": side.get("description"),
                    "long": bool(side.get("long")),
                })
    return out


async def run_once() -> dict[str, Any]:
    kclient = KalshiClient(base_url=scan.BASE)
    open_rfqs: list[str] = []
    rows: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            er = await http.get(f"{POLY}/v2/leagues/nfl/events", params={"limit": 500, "offset": 0, "type": "sport", "section": "general"})
            er.raise_for_status()
            body = er.json()
            events = [dict(x) for x in (body.get("events") or body.get("data") or []) if isinstance(x, Mapping)] if isinstance(body, Mapping) else []

            for target in TARGETS:
                candidates = _poly_candidates(events, str(target["team"]), D(str(target["threshold"])))
                mve_ticker = str(target["mve_ticker"])
                mr = await kclient.get(f"/markets/{mve_ticker}")
                mve = scan._market(mr.json() if mr.status_code == 200 and mr.content else {})
                rid, quotes = await scan._quotes_for(kclient, mve_ticker, wait_s=1.5)
                if rid:
                    open_rfqs.append(rid)
                combo_no, qstats = minimal._best_rfq_combo_no(quotes)
                if combo_no is None:
                    rows.append({"mve_ticker": mve_ticker, "classification": "NO_MVE_NO_QUOTE", "order_entry": False})
                    continue
                combo_px = D(str(combo_no["price"]))
                combo_fee = fees.quadratic_fee(combo_px, fees.MVE_TAKER_MULTIPLIER, maker=False)

                if not candidates:
                    rows.append({
                        "mve_ticker": mve_ticker,
                        "kalshi_component": target["kalshi_component"],
                        "team": target["team"], "threshold": str(target["threshold"]),
                        "combo_no_rfq": str(combo_px), "combo_fee": str(combo_fee),
                        "poly_exact_candidates": 0,
                        "classification": "NO_EXACT_POLY_COUNTERPART",
                        "order_entry": False,
                    })
                    continue

                for candidate in candidates:
                    slug = str(candidate["market_slug"])
                    br = await http.get(f"{POLY}/v1/markets/{slug}/book")
                    if br.status_code != 200:
                        continue
                    book = poly_top_book(br.json())
                    if book is None:
                        continue
                    px = book.yes_ask if candidate["long"] else book.no_ask
                    depth = book.yes_depth if candidate["long"] else book.no_depth
                    pfee = polymarket_fee(1, px)
                    all_in = combo_px + combo_fee + px + pfee
                    locked = ONE - all_in
                    rows.append({
                        "mve_ticker": mve_ticker,
                        "mve_title": mve.get("title") or mve.get("yes_sub_title"),
                        "kalshi_selected_component": target["kalshi_component"],
                        "poly_event": candidate["event_title"],
                        "poly_start_time": candidate["start_time"],
                        "poly_market_slug": slug,
                        "poly_market_title": candidate["market_title"],
                        "poly_side_description": candidate["side_description"],
                        "poly_side_is_long": candidate["long"],
                        "poly_raw_ask": str(px),
                        "poly_depth": str(depth),
                        "poly_fee_conservative": str(pfee),
                        "combo_no_rfq": str(combo_px),
                        "combo_taker_fee_exact": str(combo_fee),
                        "all_in_cost": str(all_in),
                        "locked_profit": str(locked),
                        "identity": "KALSHI_MVE_NO + EXTERNAL_RULE_EQUIVALENT_SELECTED_COMPONENT >= 1",
                        "rule_equivalence_certified": False,
                        "classification": "ECONOMIC_CANDIDATE_RULE_AUDIT_REQUIRED" if locked >= TARGET and depth >= ONE else "CROSS_VENUE_SHADOW_NO_TRADE",
                        "quote_stats": qstats,
                        "market_creation": False,
                        "quote_acceptance": False,
                        "order_entry": False,
                        "realized": False,
                    })

        scored = [r for r in rows if r.get("locked_profit") is not None]
        scored.sort(key=lambda r: D(str(r["locked_profit"])), reverse=True)
        out = {
            "status": "SPORTS_MVE_POLY_COMPONENT_PROBE_V1",
            "targets": len(TARGETS),
            "poly_nfl_events": len(events),
            "scored": len(scored),
            "economic_candidates_needing_rule_audit": sum(r.get("classification") == "ECONOMIC_CANDIDATE_RULE_AUDIT_REQUIRED" for r in rows),
            "best": scored[0] if scored else None,
            "target_locked": str(TARGET),
            "rule_equivalence_certified": False,
            "market_creation": False,
            "quote_acceptance": False,
            "order_entry": False,
            "realized": False,
            "rows": rows,
        }
        print("SPORTS_MVE_POLY_COMPONENT_PROBE " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        for rid in open_rfqs:
            try:
                await kclient.delete(f"/communications/rfqs/{rid}")
            except Exception:
                pass
        await kclient.close()


if __name__ == "__main__":
    asyncio.run(run_once())
