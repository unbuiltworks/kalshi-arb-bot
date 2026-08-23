"""Sports K/P q=1 execution hardening for live cumulative MLB prop thresholds.

This entrypoint preserves the existing final-lag live transport and adds two
production fixes:

1. A stable THRESHOLD_REACHED_STABLE_RESEARCH_ONLY signal may use the same bounded
   automatic Kalshi/Polymarket route when its known outcome is YES. Cumulative MLB
   prop stats cannot move back below the threshold absent an official scoring
   correction, so this remains explicitly RULE-RISK / NOT GUARANTEED ARBITRAGE.
2. Hot quote work is restricted to the decision boundary and serialized/paced so
   exact Kalshi execution revalidation is not starved by broad shadow pricing.

No q size, profit floor, cumulative sports budget, settlement classification, or
manual-sportsbook policy is weakened here.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_HALF_EVEN
import logging
import time
from typing import Any

import httpx

# Importing v1 installs the existing dynamic discovery, shared-budget namespace,
# logging-lock fix, and fail-closed partial-fill handling.
from src import mlb_prop_final_lag_live_q1_entry as v1

impl = v1.impl
logger = logging.getLogger("kalshi_arb.mlb_prop_final_lag_live_q1_entry_v2")
D = Decimal
ONE = D("1")
CENT = D("0.01")
POLY_TAKER_THETA = D("0.06")
PRICE_PACE_SECONDS = 0.30
SERIES_TTL_SECONDS = 300.0


def polymarket_taker_fee_current(quantity: Decimal | int, price: Decimal) -> Decimal:
    """Current Polymarket US taker fee, cent-rounded half-even."""
    q = D(str(quantity))
    if q <= 0 or price <= 0 or price >= ONE:
        return D("0")
    raw = POLY_TAKER_THETA * q * price * (ONE - price)
    return raw.quantize(CENT, rounding=ROUND_HALF_EVEN)


# The final-lag branch carried the older 5% research helper. Use the current 6%
# schedule for both shadow gating and final live revalidation.
impl.base.polymarket_fee = polymarket_taker_fee_current
impl.decided.base.polymarket_fee = polymarket_taker_fee_current


def eligible_decision_state(state: str, outcome: str) -> bool:
    state = str(state or "")
    outcome = str(outcome or "").upper()
    if state == "FINAL_STAT_STABLE_RESEARCH_ONLY":
        return outcome in {"YES", "NO"}
    if state == "THRESHOLD_REACHED_STABLE_RESEARCH_ONLY":
        return outcome == "YES"
    return False


def boundary_contract(*, state: str, outcome: str, statistic: int, threshold: Decimal) -> bool:
    """Only price the closest cumulative contract whose decision can create value."""
    stat = D(str(statistic))
    threshold = D(str(threshold))
    outcome = str(outcome or "").upper()
    state = str(state or "")
    if state == "THRESHOLD_REACHED_STABLE_RESEARCH_ONLY":
        return outcome == "YES" and threshold == stat
    if state == "FINAL_STAT_STABLE_RESEARCH_ONLY":
        if outcome == "YES":
            return threshold == stat
        if outcome == "NO":
            return threshold == stat + ONE
    return False


def _empty_priced(candidate: Any, observation: Any) -> dict[str, Any]:
    return {
        "family": candidate.family,
        "player": candidate.player,
        "threshold": str(candidate.threshold),
        "game_date": candidate.game_date,
        "poly_slug": candidate.poly_slug,
        "kalshi_ticker": candidate.kalshi_ticker,
        "mlb_game_pk": observation.game_pk,
        "mlb_game_status": observation.game_status,
        "official_statistic": observation.statistic,
        "known_outcome": observation.outcome,
        "decision_state": observation.decision_state,
        "evidence_source": observation.source,
        "known_winner_edges": [],
        "cross_venue_q1_pair_lock": None,
        "poly_book_error": "BOUNDARY_FILTERED",
        "kalshi_book_error": "BOUNDARY_FILTERED",
        "execution_eligible": False,
        "approved_for_execution": False,
        "read_only": True,
        "order_entry": False,
    }


_base_price_candidate = impl.decided._price_candidate
_price_lock = asyncio.Lock()
_last_price_started = 0.0


async def _price_boundary_paced(
    http: httpx.AsyncClient,
    candidate: Any,
    observation: Any,
    multipliers: dict[str, Decimal],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    if not boundary_contract(
        state=str(observation.decision_state or ""),
        outcome=str(observation.outcome or ""),
        statistic=int(observation.statistic),
        threshold=D(str(candidate.threshold)),
    ):
        return _empty_priced(candidate, observation)

    global _last_price_started
    async with _price_lock:
        wait = PRICE_PACE_SECONDS - (time.monotonic() - _last_price_started)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_price_started = time.monotonic()
        return await _base_price_candidate(http, candidate, observation, multipliers, semaphore)


impl.decided._price_candidate = _price_boundary_paced
# Gather may enqueue many boundary contracts; the global lock/pacer is authoritative.
impl.decided.PRICE_CONCURRENCY = 1


# Reuse discovery-time series fee metadata during hot exact execution revalidation.
_series_cache: dict[str, tuple[float, Decimal]] = {}
_base_series_multipliers = impl.decided._series_multipliers


async def _series_multipliers_cached(http: httpx.AsyncClient) -> dict[str, Decimal]:
    rows = await _base_series_multipliers(http)
    now = time.monotonic()
    for series, multiplier in rows.items():
        _series_cache[str(series)] = (now, D(str(multiplier)))
    return rows


impl.decided._series_multipliers = _series_multipliers_cached


async def _series_multiplier(http: httpx.AsyncClient, series: str) -> Decimal | None:
    cached = _series_cache.get(series)
    now = time.monotonic()
    if cached is not None and now - cached[0] <= SERIES_TTL_SECONDS:
        return cached[1]
    response = await http.get(f"{impl.base.KALSHI}/series/{series}")
    if response.status_code != 200:
        return None
    meta = response.json().get("series") or {}
    multiplier = impl.base._d(meta.get("fee_multiplier"))
    if multiplier is None:
        return None
    _series_cache[series] = (now, D(str(multiplier)))
    return D(str(multiplier))


async def _fresh_edge_fast(row: dict[str, Any], venue: str) -> tuple[dict[str, Any], float] | None:
    outcome = str(row.get("known_outcome") or "").upper()
    if outcome not in {"YES", "NO"} or venue not in impl.AUTO_VENUES:
        return None
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=3.0) as http:
        if venue == "polymarket":
            response = await http.get(f"{impl.base.POLY}/v1/markets/{row['poly_slug']}/book")
            response.raise_for_status()
            book = impl.base.poly_top_book(response.json())
            if book is None:
                return None
            edge = impl.decided._known_winner_edge(venue="polymarket", outcome=outcome, book=book)
        else:
            response = await http.get(
                f"{impl.base.KALSHI}/markets/{row['kalshi_ticker']}/orderbook",
                params={"depth": 1},
            )
            response.raise_for_status()
            book = impl.base.kalshi_top_book(response.json())
            if book is None:
                return None
            series = impl.FAMILY_SERIES.get(str(row.get("family") or ""))
            if not series:
                return None
            multiplier = await _series_multiplier(http, series)
            if multiplier is None:
                return None
            edge = impl.decided._known_winner_edge(
                venue="kalshi",
                outcome=outcome,
                book=book,
                kalshi_multiplier=multiplier,
            )
    elapsed_ms = (time.perf_counter() - started) * 1000
    if edge is None:
        return None
    return edge, elapsed_ms


impl._fresh_edge = _fresh_edge_fast


_base_handle = impl.FinalLagLiveHandler._handle


def _candidate_key(row: dict[str, Any]) -> str:
    return (
        str(row.get("family") or "")
        + "|"
        + str(row.get("poly_slug") or "")
        + "|"
        + str(row.get("kalshi_ticker") or "")
    )


async def _handle_live_threshold(self: Any, row: dict[str, Any]) -> None:
    state = str(row.get("decision_state") or "")
    outcome = str(row.get("known_outcome") or "").upper()
    if not eligible_decision_state(state, outcome):
        return

    # Do not let a tiny research-positive edge consume the base handler's attempted
    # key. Only enter exact execution revalidation if the already-priced q1 edge is
    # at least the existing live threshold.
    actionable = [
        edge
        for edge in (row.get("known_winner_edges") or [])
        if isinstance(edge, dict)
        and str(edge.get("venue") or "").lower() in impl.AUTO_VENUES
        and impl._d(edge.get("known_winner_profit")) >= impl.MIN_PROFIT
        and impl._d(edge.get("displayed_depth")) >= ONE
    ]
    if not actionable:
        return

    promoted = dict(row)
    promoted["source_decision_state"] = state
    # The unchanged base transport is Final-only. This is an internal compatibility
    # shim after the stricter cumulative-state gate above; it does not claim the MLB
    # game is Final and the strategy classification remains RULE_RISK_NOT_ARBITRAGE.
    promoted["decision_state"] = "FINAL_STAT_STABLE_RESEARCH_ONLY"
    key = _candidate_key(promoted)
    try:
        await _base_handle(self, promoted)
    finally:
        # A transient 429/stale revalidation/no-fill must not blacklist the contract
        # for the entire process lifetime. Keep it attempted only while a durable
        # unresolved final-lag exposure actually exists.
        if key in self.attempted and not self.halted:
            try:
                rows = await impl._trades()
                unresolved = impl._unresolved_final_lag(rows)
            except Exception:
                unresolved = {"fail_closed": True}
            if unresolved is None:
                self.attempted.discard(key)


impl.FinalLagLiveHandler._handle = _handle_live_threshold

logger.warning(
    "MLB_KP_THRESHOLD_AUTO_V2_INSTALLED q=1 min_profit=%s budget=%s "
    "states=FINAL_STAT_STABLE_RESEARCH_ONLY,THRESHOLD_REACHED_STABLE_RESEARCH_ONLY "
    "threshold_state_yes_only=true price_pace_seconds=%.2f poly_taker_theta=%s "
    "classification=LIVE_SETTLEMENT_LAG_RULE_RISK_NOT_ARBITRAGE",
    impl.MIN_PROFIT,
    impl.CUMULATIVE_BUDGET,
    PRICE_PACE_SECONDS,
    POLY_TAKER_THETA,
)


if __name__ == "__main__":
    impl.main()
