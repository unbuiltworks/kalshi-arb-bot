"""Read-only Odds-API.io + Gemini MLB team-total hot shadow.

Odds-API.io exposes ``Team Total Home`` and ``Team Total Away`` over the same
WebSocket used by the existing MLB hot scanner.  Gemini already normalizes its
``TT`` contracts into the universal ``team_total`` identity.  This module only
bridges those two existing pieces:

* adds Gemini-only team-total keys to cold discovery so a sportsbook quote may be
  the second venue;
* normalizes Odds-API.io team-total WebSocket updates into those canonical keys;
* reuses the existing event-driven pricing/evaluation loop;
* performs no order entry and never upgrades settlement confidence.

Settlement equivalence remains blocked by the separate audited team-total rule
registry on the sports-router branch.  This lane is price discovery only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping
import sys

import src.odds_api_io_mlb_hot_shadow as base
from src.mlb_game_side_normalization import normalize_gemini_mlb
from src.three_venue_mlb_hot import HotContract, HotUniverse

_BASE_QUOTES_FROM_MARKET = base._quotes_from_market
_BASE_DISCOVER_HOT_UNIVERSE = base.discover_hot_universe

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
    """Extend the base parser with team totals while preserving all base markets."""
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
    """Add unambiguous Gemini team-total contracts to the base hot universe.

    The base universe requires two prediction venues before a key becomes hot.
    That is appropriate for its three-venue router but would discard Gemini TT
    keys before the sportsbook WebSocket has a chance to become venue two.
    """
    universe = await _BASE_DISCOVER_HOT_UNIVERSE(http)
    contracts = dict(universe.contracts)
    market_to_keys_raw: dict[tuple[str, str], set[str]] = defaultdict(set)
    for marker, keys in universe.market_to_keys.items():
        market_to_keys_raw[marker].update(keys)

    team_rows = [
        row for row in normalize_gemini_mlb(universe.initial_gemini_events)
        if row.identity.market_family == "team_total"
        and row.identity.scope == "full_contest"
    ]
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in team_rows:
        grouped[row.contract_key].append(row)

    added = 0
    ambiguous = 0
    for key, candidates in grouped.items():
        unique = {
            (row.venue_market_id, row.side): row
            for row in candidates
        }
        if len(unique) != 1:
            ambiguous += 1
            continue
        side = next(iter(unique.values()))
        if key in contracts:
            continue
        contracts[key] = HotContract(
            key=key,
            sides={"gemini": side},
            kalshi_fee_multiplier=None,
        )
        market_to_keys_raw[("gemini", side.venue_market_id)].add(key)
        added += 1

    diagnostics = dict(universe.diagnostics)
    families = Counter(
        next(iter(contract.sides.values())).identity.market_family
        for contract in contracts.values()
        if contract.sides
    )
    diagnostics.update({
        "team_total_gemini_rows": len(team_rows),
        "team_total_gemini_only_keys_added": added,
        "team_total_ambiguous_keys_rejected": ambiguous,
        "hot_by_family": dict(families),
        "team_total_price_discovery_only": True,
        "team_total_rule_gate": RULE_GATE,
        "read_only": True,
        "order_entry": False,
    })
    return HotUniverse(
        contracts=contracts,
        market_to_keys={k: tuple(sorted(v)) for k, v in market_to_keys_raw.items()},
        initial_gemini_events=universe.initial_gemini_events,
        diagnostics=diagnostics,
    )


# Scoped monkeypatches: only the standalone team-total entrypoint imports this
# module, so existing Odds-API.io services keep their current behavior.
base._quotes_from_market = _quotes_from_market
base.discover_hot_universe = _discover_hot_universe


def main() -> None:
    if "--markets" not in sys.argv:
        sys.argv.extend(["--markets", TEAM_TOTAL_MARKETS])
    base.main()


if __name__ == "__main__":
    main()
