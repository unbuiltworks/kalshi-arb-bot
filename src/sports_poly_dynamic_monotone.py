"""Dynamic read-only discovery for strict Polymarket monotone prop pairs.

A pair is eligible only when both markets independently pass the strict structured
MLB prop identity parser and agree on family/player/game/date while having distinct
numeric thresholds. For low < high, YES(low)+NO(high) has a minimum payout of $1,
and the reverse execution ordering is economically equivalent.

This module never places orders. It only replaces date-specific relay allowlists
with current strict market discovery and exact-book scoring.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

import httpx

from src.strict_mlb_prop_adapters import _poly_identity
from src.sports_kp_poly_relay_live_q1 import D, ONE, TARGET, TAKER_FEE_FLOOR, _ask, _bid, _locked
from src.strict_mlb_prop_snapshot import TopBook

BASE = "https://gateway.polymarket.us"
TICK = D("0.01")


def _group_key(identity: tuple[str, str, tuple[str, str], str, Decimal]) -> tuple[str, str, tuple[str, str], str]:
    family, player, teams, game_date, _ = identity
    return family, " ".join(player.lower().split()), tuple(sorted(teams)), game_date


async def discover_current_pairs(*, horizon_hours: int = 36, max_pages: int = 12) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]]]:
    """Return only strict markets that participate in a monotone low/high pair."""
    horizon = datetime.now(timezone.utc) + timedelta(hours=horizon_hours)
    groups: dict[tuple[str, str, tuple[str, str], str], dict[Decimal, dict[str, Any]]] = defaultdict(dict)

    async with httpx.AsyncClient(timeout=12.0) as http:
        for page in range(max_pages):
            try:
                response = await http.get(
                    f"{BASE}/v1/events",
                    params={
                        "limit": 100,
                        "offset": page * 100,
                        "active": "true",
                        "closed": "false",
                        "ended": "false",
                        "categories": "sports",
                        "startTimeMax": horizon.isoformat().replace("+00:00", "Z"),
                        "orderBy": "startTime",
                        "orderDirection": "asc",
                    },
                )
                response.raise_for_status()
            except Exception:
                break
            body = response.json() if response.content else {}
            events = body.get("events") or [] if isinstance(body, Mapping) else []
            for event in events:
                if not isinstance(event, Mapping):
                    continue
                for raw in event.get("markets") or []:
                    if not isinstance(raw, Mapping):
                        continue
                    market = dict(raw)
                    slug = str(market.get("slug") or "").strip()
                    if not slug:
                        continue
                    identity = _poly_identity(market)
                    if identity is None:
                        continue
                    key = _group_key(identity)
                    threshold = identity[-1]
                    prior = groups[key].get(threshold)
                    if prior is not None and str(prior.get("slug") or "") != slug:
                        groups[key][threshold] = {"_ambiguous": True}
                    elif prior is None:
                        groups[key][threshold] = market
            if len(events) < 100:
                break

    market_map: dict[str, dict[str, Any]] = {}
    pairs: list[tuple[str, str]] = []
    for threshold_map in groups.values():
        clean = [(line, m) for line, m in threshold_map.items() if not m.get("_ambiguous")]
        clean.sort(key=lambda x: x[0])
        # Singles cannot form a monotone hedge. Excluding them prevents unnecessary
        # exact-book requests and reduces startup/API pressure without losing trades.
        if len(clean) < 2:
            continue
        for _, market in clean:
            market_map[str(market["slug"])] = market
        for i in range(len(clean)):
            for j in range(i + 1, len(clean)):
                pairs.append((str(clean[i][1]["slug"]), str(clean[j][1]["slug"])))
    return market_map, pairs


def score_pairs(
    feed: Mapping[str, Mapping[str, Any]],
    books: Mapping[str, TopBook],
    pairs: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Score exact books and rank candidates for maker fill probability.

    Eligibility still requires >=3c at the current maker bid. Ranking then computes
    the most aggressive post-only quote that still preserves the same 3c after-fee
    floor. Candidates closest to the contra ask are tried first.
    """
    rows: list[dict[str, Any]] = []
    for low_slug, high_slug in pairs:
        low, high = feed.get(low_slug), feed.get(high_slug)
        lb, hb = books.get(low_slug), books.get(high_slug)
        if low is None or high is None or lb is None or hb is None:
            continue
        li, hi = _poly_identity(low), _poly_identity(high)
        if li is None or hi is None:
            continue
        lf, lp, lt, ld, lx = li
        hf, hp, ht, hd, hx = hi
        if (lf, " ".join(lp.lower().split()), set(lt), ld) != (hf, " ".join(hp.lower().split()), set(ht), hd) or lx >= hx:
            continue
        for direction, maker_slug, maker_side, maker_bid, maker_ask, hedge_slug, hedge_side, hedge_px, hedge_depth in (
            ("MAKER_LOW_HEDGE_HIGH", low_slug, "YES", _bid(lb, "YES"), _ask(lb, "YES")[0], high_slug, "NO", *_ask(hb, "NO")),
            ("MAKER_HIGH_HEDGE_LOW", high_slug, "NO", _bid(hb, "NO"), _ask(hb, "NO")[0], low_slug, "YES", *_ask(lb, "YES")),
        ):
            locked_at_bid = _locked(maker_bid, hedge_px)
            if maker_bid <= 0 or maker_bid >= ONE or hedge_depth < ONE or locked_at_bid < TARGET:
                continue

            max_for_floor = ONE - hedge_px - TAKER_FEE_FLOOR - TARGET
            improved = min(maker_ask - TICK, max_for_floor)
            executable_maker_quote = max(maker_bid, improved)
            quote_locked = _locked(executable_maker_quote, hedge_px)
            if executable_maker_quote <= 0 or executable_maker_quote >= maker_ask or quote_locked < TARGET:
                continue
            maker_gap_to_ask = maker_ask - executable_maker_quote

            rows.append(
                {
                    "direction": direction,
                    "low_slug": low_slug,
                    "high_slug": high_slug,
                    "maker_slug": maker_slug,
                    "maker_side": maker_side,
                    "maker_price": maker_bid,
                    "maker_ask": maker_ask,
                    "maker_spread": maker_ask - maker_bid,
                    "executable_maker_quote": executable_maker_quote,
                    "maker_gap_to_ask": maker_gap_to_ask,
                    "hedge_slug": hedge_slug,
                    "hedge_side": hedge_side,
                    "hedge_ask": hedge_px,
                    "hedge_depth": hedge_depth,
                    "locked_profit": locked_at_bid,
                    "quote_locked_profit": quote_locked,
                    "low_threshold": lx,
                    "high_threshold": hx,
                    "family": lf,
                    "player": lp,
                }
            )

    rows.sort(key=lambda r: (r["maker_gap_to_ask"], -r["hedge_depth"], -r["quote_locked_profit"]))
    return rows
