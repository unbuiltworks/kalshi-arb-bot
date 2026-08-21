"""Read-only cross-venue replacement-cost shadow for generated sports MVEs.

This module asks a narrow question after the existing Kalshi MVE generator has
created/scored a basket: would any component hedge have been cheaper on Gemini
Predictions at q=1?  Normal-event identity matching is strict and NFL-only for
this first production slice.  Gemini rule audits remain incomplete, so every
replacement is shadow-only and MUST NOT be promoted to the live executor here.

The module intentionally reuses the component-market cache captured by
sports_kp_live_combo_supervisor_v8, avoiding another broad Kalshi scan.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
import re
from typing import Any

import httpx

from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_sports_adapter import canonical_team, normalize_events
from src.gemini_sports_inventory import fetch_active_sports_events
from src.nfl_gk_hot_depth_probe import GeminiNflBooks

D = Decimal
ONE = D("1")
NFL_TEAMS = {
    "ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB",
    "HOU","IND","JAC","KC","LV","LAC","LAR","MIA","MIN","NE","NO","NYG","NYJ",
    "PHI","PIT","SEA","SF","TB","TEN","WSH",
}


def _d(v: Any) -> Decimal | None:
    try:
        out = D(str(v))
    except Exception:
        return None
    return out if out.is_finite() else None


def _split_teams(compact: str) -> tuple[str, str] | None:
    text = str(compact or "").upper()
    found: list[tuple[str, str]] = []
    aliases = set(NFL_TEAMS) | {"WAS"}
    for a in aliases:
        for b in aliases:
            if a == b or a + b != text:
                continue
            ca, cb = canonical_team(a), canonical_team(b)
            if ca in NFL_TEAMS and cb in NFL_TEAMS and ca != cb:
                found.append(tuple(sorted((ca, cb))))
    unique = sorted(set(found))
    return unique[0] if len(unique) == 1 else None


def _event_bits(event_ticker: str) -> tuple[str, datetime, tuple[str, str]] | None:
    m = re.fullmatch(
        r"KXNFL(?P<family>GAME|SPREAD|TOTAL)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<teams>[A-Z]{4,7})",
        str(event_ticker or "").upper(),
    )
    if not m:
        return None
    try:
        day = datetime.strptime(m.group("yy") + m.group("mon") + m.group("dd"), "%y%b%d")
    except ValueError:
        return None
    teams = _split_teams(m.group("teams"))
    if teams is None:
        return None
    family = {"GAME":"moneyline", "SPREAD":"spread", "TOTAL":"game_total"}[m.group("family")]
    return family, day, teams


def _kalshi_identity_key(event_ticker: str, market_ticker: str) -> tuple[Any, ...] | None:
    bits = _event_bits(event_ticker)
    if bits is None:
        return None
    family, day, teams = bits
    suffix = str(market_ticker or "").upper().rsplit("-", 1)[-1]
    if family == "moneyline":
        team = canonical_team(suffix)
        if team not in teams:
            return None
        return (day.date().isoformat(), teams, family, team.lower(), "", "YES", None)
    if family == "spread":
        m = re.fullmatch(r"(?P<team>[A-Z]{2,3})(?P<n>\d+)", suffix)
        if not m:
            return None
        team = canonical_team(m.group("team"))
        if team not in teams:
            return None
        line = D(m.group("n")) - D("0.5")
        return (day.date().isoformat(), teams, family, team.lower(), "points", "GT", line)
    if family == "game_total":
        m = re.fullmatch(r"(?P<n>\d+)", suffix)
        if not m:
            return None
        line = D(m.group("n")) - D("0.5")
        return (day.date().isoformat(), teams, family, "contest", "points", "GT", line)
    return None


def _gemini_key(row: Any) -> tuple[Any, ...] | None:
    ident = row.identity
    if ident.event.sport != "football" or ident.event.league != "nfl":
        return None
    if ident.market_family not in {"moneyline", "spread", "game_total"}:
        return None
    day = datetime.utcfromtimestamp(ident.event.scheduled_start_ms / 1000).date().isoformat()
    teams = tuple(sorted(canonical_team(x).upper() for x in ident.event.participants))
    stat = ident.stat
    # The universal Gemini adapter may label football spread/total units as
    # points or leave the metric blank. Normalize only this unit spelling; all
    # proposition dimensions remain exact.
    if ident.market_family in {"spread", "game_total"} and stat in {"", "points"}:
        stat = "points"
    return (day, teams, ident.market_family, ident.subject, stat, ident.comparator, ident.line)


def _kalshi_component(market: dict[str, Any], selected_side: str, *, inverse: bool = False) -> tuple[str, Decimal, Decimal, Decimal] | None:
    # Normal coverage buys the complement of the selected component. Inverse
    # coverage buys the selected component itself.
    side = selected_side.lower() if inverse else ("no" if selected_side.lower() == "yes" else "yes")
    if side == "yes":
        price = _d(market.get("yes_ask_dollars"))
        if price is None:
            nb = _d(market.get("no_bid_dollars")); price = ONE - nb if nb is not None else None
        depth = _d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
    elif side == "no":
        price = _d(market.get("no_ask_dollars"))
        if price is None:
            yb = _d(market.get("yes_bid_dollars")); price = ONE - yb if yb is not None else None
        depth = _d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
    else:
        return None
    if price is None or depth is None or depth < ONE or not (D("0") < price < ONE):
        return None
    fee = ((D("0.07") * price * (ONE-price)) * D("100")).to_integral_value(rounding="ROUND_CEILING") / D("100")
    return side, price, fee, depth


def _gemini_component(book: Any, side: str) -> tuple[Decimal, Decimal, Decimal] | None:
    if side == "yes":
        price, depth = book.yes_ask, book.yes_ask_size
    elif side == "no":
        price, depth = ONE - book.yes_bid, book.yes_bid_size
    else:
        return None
    if depth < ONE or not (D("0") < price < ONE):
        return None
    return price, gemini_predictions_taker_fee(price, ONE), depth


async def annotate_generated_result(result: dict[str, Any], market_cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [x for x in (result.get("ranked") or []) if isinstance(x, dict)]
    if not rows:
        result["cross_venue_shadow_candidates"] = 0
        result["cross_venue_shadow_best"] = None
        return result

    kalshi_keys: dict[str, tuple[Any, ...]] = {}
    for row in rows:
        for leg in row.get("legs") or []:
            if not isinstance(leg, dict):
                continue
            mt = str(leg.get("market_ticker") or "")
            key = _kalshi_identity_key(str(leg.get("event_ticker") or ""), mt)
            if mt and key is not None:
                kalshi_keys[mt] = key
    if not kalshi_keys:
        result["cross_venue_shadow_candidates"] = 0
        result["cross_venue_shadow_best"] = None
        return result

    async with httpx.AsyncClient(timeout=15.0) as http:
        events = await fetch_active_sports_events(http)
    normalized = normalize_events(events)
    by_key: dict[tuple[Any, ...], list[Any]] = {}
    for g in normalized:
        key = _gemini_key(g)
        if key is not None:
            by_key.setdefault(key, []).append(g)

    matches: dict[str, Any] = {}
    for mt, key in kalshi_keys.items():
        candidates = by_key.get(key) or []
        # Ambiguity fails closed; never choose between multiple Gemini contracts.
        if len(candidates) == 1:
            matches[mt] = candidates[0]
    symbols = sorted({g.instrument_symbol for g in matches.values()})
    books: dict[str, Any] = {}
    if symbols:
        store = GeminiNflBooks(symbols)
        task = asyncio.create_task(store.run())
        try:
            deadline = asyncio.get_running_loop().time() + 4.0
            while asyncio.get_running_loop().time() < deadline:
                books = {s: store.quote(s, max_age_ms=1500) for s in symbols}
                books = {s:b for s,b in books.items() if b is not None}
                if len(books) == len(symbols):
                    break
                await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    positives = 0
    best_row: dict[str, Any] | None = None
    for row in rows:
        base_all_in = _d(row.get("all_in_cost_conservative"))
        if base_all_in is None:
            continue
        savings = D("0")
        replacements: list[dict[str, Any]] = []
        for leg in row.get("legs") or []:
            if not isinstance(leg, dict):
                continue
            mt = str(leg.get("market_ticker") or "")
            g = matches.get(mt); market = market_cache.get(mt) or {}
            if g is None or g.instrument_symbol not in books or not market:
                continue
            k = _kalshi_component(market, str(leg.get("side") or ""), inverse=False)
            if k is None:
                continue
            side, kp, kf, kd = k
            gp = _gemini_component(books[g.instrument_symbol], side)
            if gp is None:
                continue
            gprice, gfee, gdepth = gp
            kcost, gcost = kp + kf, gprice + gfee
            leg_savings = max(D("0"), kcost - gcost)
            replacements.append({
                "kalshi_market_ticker": mt,
                "gemini_symbol": g.instrument_symbol,
                "hedge_side": side,
                "kalshi_all_in_q1": str(kcost),
                "gemini_all_in_q1": str(gcost),
                "savings_q1": str(leg_savings),
                "kalshi_depth_q1": str(kd),
                "gemini_depth_q1": str(gdepth),
                "normal_event_identity_match": True,
                "rules_certified": False,
                "execution_eligible": False,
            })
            savings += leg_savings
        cross_all_in = base_all_in - savings
        locked = ONE - cross_all_in
        shadow = {
            "direction": "COMBO_YES_PLUS_CHEAPEST_COMPONENT_COMPLEMENTS",
            "matched_gemini_components": len(replacements),
            "total_component_savings_q1": str(savings),
            "all_in_cost_shadow": str(cross_all_in),
            "locked_profit_shadow": str(locked),
            "classification": "POSITIVE_ECONOMICS_RULES_UNCERTIFIED" if locked > 0 else "NO_TRADE",
            "execution_eligible": False,
            "reason": "Gemini sports settlement rules are not yet fully fingerprint-certified against these Kalshi legs",
            "replacements": replacements,
        }
        row["cross_venue_shadow"] = shadow
        if locked > 0:
            positives += 1
        candidate = {"ticker": row.get("ticker"), "legs": row.get("legs"), **shadow}
        if best_row is None or D(candidate["locked_profit_shadow"]) > D(best_row["locked_profit_shadow"]):
            best_row = candidate

    result["cross_venue_shadow_candidates"] = positives
    result["cross_venue_shadow_best"] = best_row
    result["cross_venue_shadow_execution_enabled"] = False
    result["cross_venue_shadow_matched_kalshi_components"] = len(matches)
    result["cross_venue_shadow_gemini_books"] = len(books)
    return result
