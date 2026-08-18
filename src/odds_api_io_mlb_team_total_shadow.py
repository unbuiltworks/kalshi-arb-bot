"""Read-only Odds-API.io + Gemini MLB team-total hot shadow.

Odds-API.io exposes ``Team Total Home`` and ``Team Total Away`` over the same
WebSocket used by the existing MLB hot scanner. Gemini already normalizes its
``TT`` contracts into the universal ``team_total`` identity. This module bridges
only those two feeds:

* discovers unambiguous Gemini team-total contracts;
* normalizes Odds-API.io team-total WebSocket updates into the same canonical keys;
* reuses the existing event-driven pricing/evaluation loop;
* rejects integer sportsbook lines because they can push;
* performs no order entry and never upgrades settlement confidence.

No Kalshi or Polymarket discovery/subscription is used by this entrypoint.
Settlement equivalence remains blocked by the separate audited team-total rule
registry. This lane is price discovery only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping
import sys

import src.odds_api_io_mlb_hot_shadow as base
from src.mlb_game_side_normalization import normalize_gemini_mlb
from src.three_venue_mlb_hot import HotContract, HotUniverse
from src.universal_three_venue_sports import fetch_gemini_mlb

_BASE_QUOTES_FROM_MARKET = base._quotes_from_market

TEAM_TOTAL_MARKETS = "Team Total Home,Team Total Away"
RULE_GATE = "FULL_TEAM_TOTAL_LIFECYCLE_FINGERPRINT_REQUIRED_BEFORE_ACTIONABLE"


def _quotes_from_market(
    *,
    event_id: str,
    event: Mapping[str, Any],
    book_raw: str,
    market: Mapping[str, Any],
    received_ms: int,
    freshness_ms: int,
):
    """Extend the base quote parser with half-run MLB team totals."""
    out = list(_BASE_QUOTES_FROM_MARKET(
        event_id=event_id,
        event=event,
        book_raw=book_raw,
        market=market,
        received_ms=received_ms,
        freshness_ms=freshness_ms,
    ))
    name = str(market.get("name") or "").strip().lower()
    if name not in {"team total home", "team total away"}:
        return out

    event_ident = base._event_identity(event)
    if event_ident is None:
        return out
    home = base.canonical_team(event.get("home"))
    away = base.canonical_team(event.get("away"))
    subject = home if name == "team total home" else away
    book = base._retail_book_name(book_raw)
    if not subject or not book:
        return out

    updated_ms = base._market_updated_ms(market.get("updatedAt"))
    if updated_ms is None or abs(received_ms - updated_ms) > freshness_ms:
        return out
    rows = market.get("odds")
    if not isinstance(rows, list):
        return out

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        point = base._decimal(row.get("hdp"))
        over = base._prob_cost(row.get("over"))
        under = base._prob_cost(row.get("under"))
        # Half-run lines make over and under exact binary complements. Integer
        # lines can push at sportsbooks and therefore stay out of this shadow.
        if point is None or not base._half(point) or over is None or under is None:
            continue
        key = base._key(
            book=book,
            event=event_ident,
            family="team_total",
            subject=subject,
            stat="runs",
            comparator="GT",
            line=point,
        )
        token = format(point.normalize(), "f")
        out.append(base._mk_quote(
            book=book,
            event_id=f"{event_id}:team_total:{subject}:{token}",
            settlement_key=key,
            yes_cost=over,
            no_cost=under,
            received_ms=received_ms,
        ))
    return out


async def _discover_hot_universe(http) -> HotUniverse:
    """Build a Gemini-only TT universe; the sportsbook WS becomes venue two."""
    gemini_events = await fetch_gemini_mlb(http)
    team_rows = [
        row for row in normalize_gemini_mlb(gemini_events)
        if row.identity.market_family == "team_total"
        and row.identity.scope == "full_contest"
    ]
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in team_rows:
        grouped[row.contract_key].append(row)

    contracts: dict[str, HotContract] = {}
    market_to_keys_raw: dict[tuple[str, str], set[str]] = defaultdict(set)
    ambiguous = 0
    for key, candidates in grouped.items():
        unique = {(row.venue_market_id, row.side): row for row in candidates}
        if len(unique) != 1:
            ambiguous += 1
            continue
        side = next(iter(unique.values()))
        contracts[key] = HotContract(
            key=key,
            sides={"gemini": side},
            kalshi_fee_multiplier=None,
        )
        market_to_keys_raw[("gemini", side.venue_market_id)].add(key)

    families = Counter(
        next(iter(contract.sides.values())).identity.market_family
        for contract in contracts.values()
        if contract.sides
    )
    diagnostics = {
        "raw_events": {"gemini": len(gemini_events), "polymarket": 0, "kalshi": 0},
        "eligible_identity_keys": len(contracts),
        "hot_contracts": len(contracts),
        "ambiguous_keys_rejected": ambiguous,
        "kalshi_fee_blocked_keys": 0,
        "hot_by_venue_set": {"gemini": len(contracts)} if contracts else {},
        "hot_by_family": dict(families),
        "polymarket_subscriptions": 0,
        "kalshi_subscriptions": 0,
        "gemini_instruments": len(contracts),
        "fee_metadata_errors": [],
        "team_total_gemini_rows": len(team_rows),
        "team_total_gemini_only_keys_added": len(contracts),
        "team_total_ambiguous_keys_rejected": ambiguous,
        "team_total_price_discovery_only": True,
        "team_total_rule_gate": RULE_GATE,
        "direct_venues": ["gemini"],
        "retail_feed": "ODDS_API_IO_WEBSOCKET",
        "read_only": True,
        "order_entry": False,
    }
    return HotUniverse(
        contracts=contracts,
        market_to_keys={k: tuple(sorted(v)) for k, v in market_to_keys_raw.items()},
        initial_gemini_events=gemini_events,
        diagnostics=diagnostics,
    )


# Scoped monkeypatches: only this standalone entrypoint imports the extension, so
# the existing game-line and player-prop websocket services keep their behavior.
base._quotes_from_market = _quotes_from_market
base.discover_hot_universe = _discover_hot_universe


def main() -> None:
    if "--markets" not in sys.argv:
        sys.argv.extend(["--markets", TEAM_TOTAL_MARKETS])
    base.main()


if __name__ == "__main__":
    main()
