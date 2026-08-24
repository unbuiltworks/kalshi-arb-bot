"""Fail-closed certification for same-venue Polymarket MLB prop ladders.

This module is read-only. It combines strict same-game/player/stat ladder identity,
official MLB Stats participation evidence, live game state, and market-reported q1
order metadata.

Passing this gate does NOT mean guaranteed arbitrage: Polymarket's suspension/delay
last-fair-market-price branch remains residual rule risk after participation.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any, Mapping

import httpx

from src.mlb_live_participation import candidate_game, fetch_game_boxscores, participation_proof, proof_dict
from src.mlb_prop_rule_equivalence import assess_rule_equivalence
from src.strict_mlb_prop_adapters import _poly_identity

D = Decimal
ONE = D("1")


def _d(value: Any) -> D | None:
    try:
        return D(str(value))
    except Exception:
        return None


def market_order_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    tick = _d(row.get("orderPriceMinTickSize"))
    minimum_qty = _d(row.get("minimumTradeQty"))
    return {
        "tick": tick,
        "minimum_qty": minimum_qty,
        "tick_ok": bool(tick is not None and tick > 0),
        "q1_lot_ok": bool(minimum_qty is not None and minimum_qty > 0 and minimum_qty <= ONE),
    }


async def certify_pairs(
    feed: Mapping[str, Mapping[str, Any]],
    pairs: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], dict[str, dict[str, Any]], dict[str, Any]]:
    """Return live participation+metadata-ready pairs and fail-closed diagnostics."""
    dates = {
        game[0]
        for low_slug, _ in pairs
        for game in [candidate_game(low_slug)]
        if game is not None
    }
    async with httpx.AsyncClient(timeout=12.0) as http:
        try:
            games = await fetch_game_boxscores(http, dates)
        except Exception as exc:
            return [], {}, {
                "pairs_seen": len(pairs),
                "ready_pairs": 0,
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                "fail_closed": True,
            }

    ready: list[tuple[str, str]] = []
    certs: dict[str, dict[str, Any]] = {}
    reasons: Counter[str] = Counter()

    for low_slug, high_slug in pairs:
        low = feed.get(low_slug)
        high = feed.get(high_slug)
        key = f"{low_slug}|{high_slug}"
        if low is None or high is None:
            reasons["MISSING_FEED_ROW"] += 1
            continue
        low_ident = _poly_identity(low)
        high_ident = _poly_identity(high)
        if low_ident is None or high_ident is None:
            reasons["IDENTITY_PARSE_FAILED"] += 1
            continue
        lf, lp, lt, ld, lx = low_ident
        hf, hp, ht, hd, hx = high_ident
        if (lf, lp.lower(), set(lt), ld) != (hf, hp.lower(), set(ht), hd) or not lx < hx:
            reasons["STRICT_IDENTITY_FAILED"] += 1
            continue

        game_key = candidate_game(low_slug)
        game = games.get(game_key) if game_key is not None else None
        game_state = str((game or {}).get("status") or "").upper()
        game_live = game_state == "LIVE"

        proof = participation_proof(
            family=lf,
            player_name=lp,
            poly_slug=low_slug,
            games=games,
        )
        assessment = assess_rule_equivalence(lf, proof)
        low_meta = market_order_metadata(low)
        high_meta = market_order_metadata(high)
        participation_ok = bool(assessment.settlement_equivalent_now)
        metadata_ok = bool(
            low_meta["tick_ok"] and low_meta["q1_lot_ok"]
            and high_meta["tick_ok"] and high_meta["q1_lot_ok"]
        )
        certified = bool(game_live and participation_ok and metadata_ok)

        if not game_live:
            reasons["GAME_NOT_LIVE"] += 1
        elif not participation_ok:
            reasons[assessment.required_gate] += 1
        elif not metadata_ok:
            reasons["TICK_OR_Q1_LOT_METADATA_FAILED"] += 1
        else:
            ready.append((low_slug, high_slug))
            reasons["READY_RULE_RISK"] += 1

        certs[key] = {
            "low_slug": low_slug,
            "high_slug": high_slug,
            "family": lf,
            "player": lp,
            "game_state": game_state or "UNKNOWN",
            "game_live": game_live,
            "participation": proof_dict(proof),
            "rule": assessment.as_dict(),
            "low_order_metadata": {k: str(v) if isinstance(v, D) else v for k, v in low_meta.items()},
            "high_order_metadata": {k: str(v) if isinstance(v, D) else v for k, v in high_meta.items()},
            "participation_ok": participation_ok,
            "metadata_ok": metadata_ok,
            "certified_for_q1_transport": certified,
            "guaranteed_arbitrage": False,
            "rule_classification": "LIVE_RULE_RISK_LFMP_SUSPENSION_NOT_GUARANTEED_ARBITRAGE",
            "order_entry": False,
        }

    return ready, certs, {
        "pairs_seen": len(pairs),
        "ready_pairs": len(ready),
        "games_with_live_or_final_boxscore": len(games),
        "reason_counts": dict(reasons),
        "fail_closed": True,
        "guaranteed_arbitrage": False,
    }


def maker_tick_for_pair(
    feed: Mapping[str, Mapping[str, Any]],
    row: Mapping[str, Any],
) -> D:
    """Return the maker market's reported tick or fail closed."""
    maker = feed.get(str(row.get("maker_slug") or ""))
    if maker is None:
        raise PermissionError("maker metadata missing")
    meta = market_order_metadata(maker)
    tick = meta["tick"]
    if not meta["tick_ok"] or not meta["q1_lot_ok"] or not isinstance(tick, D):
        raise PermissionError("maker tick/q1 lot metadata not certified")
    return tick
