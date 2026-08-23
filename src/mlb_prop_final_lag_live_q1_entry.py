"""Production entrypoint for the bounded MLB final-lag q1 executor.

The existing sports live-budget guard recognizes ``sports_gk_*`` strategies. Keep
this first final-lag canary inside that already-approved cumulative $5 namespace so
introducing the new lane cannot silently create a second risk budget.

The underlying handler uses an asyncio lock to serialize executions. Python's
``logging.Handler`` also owns an attribute named ``lock`` and normally treats it as
an RLock. The production entrypoint therefore makes logging's acquire/release hooks
no-ops for this one event-loop handler; its asyncio lock remains exclusively for
execution serialization rather than logging internals.

Any nonzero fill that is not exactly q=1 is reclassified UNKNOWN. The parent then
hard-halts and preserves the reservation rather than releasing capital while an
unexpected fractional position may exist.

Kalshi strict-prop discovery is serialized and retries HTTP 429 with short bounded
backoff. A persistently rate-limited family fails soft instead of causing the other
families to disappear from the retained candidate universe. Quote freshness gates
remain unchanged; this patch only smooths low-frequency discovery requests.

Polymarket prop search is seeded from strictly parseable player names in that same
current Kalshi slate. This improves discovery recall only: every result still passes
the unchanged Polymarket identity parser, exact player/stat/threshold/game matcher,
schedule guard, rule audit, stability, pricing, revalidation, and live-budget gates.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import logging
from statistics import median
from typing import Any

import httpx

from src import current_polymarket_mlb_props as poly_discovery
from src import mlb_prop_final_lag_live_q1 as impl

logger = logging.getLogger("kalshi_arb.mlb_prop_final_lag_live_q1_entry")
impl.STRATEGY = "sports_gk_final_lag_kp_q1"


def _unresolved(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    terminal = {"REALIZED", "TRADE_REALIZED", "SETTLED", "CAPITAL_RELEASED"}
    for row in impl._shared_sports_live_rows(rows):
        strategy = str(row.get("strategy") or "")
        if "final_lag" not in strategy:
            continue
        if str(row.get("status") or "").upper() in terminal:
            continue
        actual = impl._d(row.get("capital_filled"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        reserved = impl._d(metadata.get("reserved_usd"))
        if actual > 0 or reserved > 0:
            return row
    return None


impl._unresolved_final_lag = _unresolved

# ``FinalLagLiveHandler.__init__`` intentionally reuses ``self.lock`` as an
# asyncio.Lock. Prevent the synchronous logging framework from acquiring/releasing
# that object; execution code still uses ``async with self.lock`` normally.
impl.FinalLagLiveHandler.acquire = lambda self: None
impl.FinalLagLiveHandler.release = lambda self: None

_base_submit = impl._submit


async def _submit_fail_closed(leg: Any) -> Any:
    result = await _base_submit(leg)
    filled = impl._d(result.filled_quantity)
    if filled == impl.D("0") or filled == impl.ONE:
        return result
    return impl.VenueResult(
        venue=result.venue,
        order_id=result.order_id,
        status="UNKNOWN",
        filled_quantity=result.filled_quantity,
        average_price=result.average_price,
        reason=f"UNEXPECTED_PARTIAL_FILL_{result.filled_quantity}",
        reconciled=result.reconciled,
    )


impl._submit = _submit_fail_closed


async def _kalshi_events_throttled(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    series_list = list(dict.fromkeys(impl.decided.base.FAMILY_SERIES.values()))
    for index, series in enumerate(series_list):
        if index:
            await asyncio.sleep(0.10)
        group: list[dict[str, Any]] | None = None
        for attempt in range(3):
            response = await http.get(
                f"{impl.decided.base.KALSHI}/events",
                params={
                    "limit": 200,
                    "status": "open",
                    "series_ticker": series,
                    "with_nested_markets": "true",
                },
            )
            if response.status_code == 200:
                body = response.json()
                group = list(body.get("events") or [])
                break
            if response.status_code != 429:
                response.raise_for_status()
            await asyncio.sleep(0.15 * (2 ** attempt))
        if group is None:
            logger.warning("FINAL_LAG_KALSHI_DISCOVERY_RATE_LIMIT series=%s attempts=3", series)
            continue
        events.extend(group)
    return events


def _discovery_shape(candidates: list[Any], poly: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure independent contract inventory without changing candidate selection."""
    by_slug = {str(row.get("slug") or ""): row for row in poly if str(row.get("slug") or "")}
    semantic: Counter[tuple[Any, ...]] = Counter()
    by_family: Counter[str] = Counter()
    for candidate in candidates:
        by_family[str(candidate.family)] += 1
        row = by_slug.get(str(candidate.poly_slug))
        identity = impl.decided.base._poly_identity(row) if row is not None else None
        start = impl.decided.base.polymarket_start_utc(row) if row is not None else None
        if identity is not None and start is not None:
            family, player, teams, game_date, threshold = identity
            key = (
                str(family),
                impl.decided.base._name(player),
                tuple(sorted(str(team).lower() for team in teams)),
                str(game_date),
                str(threshold),
                start.isoformat(),
            )
        else:
            key = (
                str(candidate.family),
                impl.decided.base._name(candidate.player),
                str(candidate.game_date),
                str(candidate.threshold),
                str(candidate.poly_slug),
            )
        semantic[key] += 1
    fanout = list(semantic.values())
    return {
        "exact_pairs": len(candidates),
        "unique_poly_contracts": len({str(row.poly_slug) for row in candidates}),
        "unique_kalshi_contracts": len({str(row.kalshi_ticker) for row in candidates}),
        "unique_semantic_contracts": len(semantic),
        "duplicate_pair_rows": max(0, len(candidates) - len(semantic)),
        "max_pairs_per_semantic_contract": max(fanout) if fanout else 0,
        "median_pairs_per_semantic_contract": float(median(fanout)) if fanout else 0.0,
        "by_family_pair_rows": dict(sorted(by_family.items())),
    }


async def _discover_seeded(http: httpx.AsyncClient) -> list[Any]:
    """Reuse one Kalshi slate fetch to seed broader strict Polymarket discovery."""
    kalshi = await _kalshi_events_throttled(http)
    player_queries = poly_discovery.kalshi_seed_players(kalshi)
    poly = await poly_discovery.fetch_current_poly_mlb_props(
        http,
        ttl_seconds=impl.decided.DEFAULT_DISCOVERY_SECONDS,
        player_queries=player_queries,
    )
    candidates = impl.decided.base.discover_strict_candidates(poly, kalshi)
    shape = _discovery_shape(candidates, poly)
    logger.warning(
        "FINAL_LAG_DYNAMIC_DISCOVERY kalshi_events=%d seed_players=%d strict_poly_props=%d exact_candidates=%d order_entry_changed=false risk_gates_changed=false",
        len(kalshi), len(player_queries), len(poly), len(candidates),
    )
    logger.warning(
        "FINAL_LAG_DISCOVERY_SHAPE %s order_entry_changed=false risk_gates_changed=false",
        shape,
    )
    return candidates


# Preserve the throttled fetch for any other control-plane caller in this process.
impl.decided.base._fetch_kalshi_events = _kalshi_events_throttled
# The final-lag scanner now fetches Kalshi once, derives strict player seeds, expands
# Polymarket search, then applies the same exact matcher. No execution gate changes.
impl.decided._discover = _discover_seeded


if __name__ == "__main__":
    impl.main()
