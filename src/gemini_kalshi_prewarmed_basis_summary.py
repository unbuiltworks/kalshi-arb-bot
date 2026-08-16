"""Cumulative summaries for the prewarmed prospective-basis research shadow.

This wrapper adds no market-data or REST calls. It observes episode-close state already
held by ``gemini_kalshi_prewarmed_basis_shadow`` and keeps cumulative counts / apparent
quote-lock profit by asset for the execution-relevant cohorts:

- raw >=3c threshold episodes
- 2bp held throughout + 50ms / 100ms
- 5bp held throughout + 50ms / 100ms

The sums are research quote-lock economics, not realized P&L. Settlement remains
INDEX_MISMATCH_RESEARCH_ONLY and no order authorization is added.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import json
import logging

from src import gemini_kalshi_prewarmed_basis_shadow as basis

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_prewarmed_basis_summary")
_BASE_CLOSE = basis._close_missing_with_basis

# asset -> cohort -> [episodes, apparent_profit]
_COUNTS: dict[str, dict[str, list]] = defaultdict(
    lambda: defaultdict(lambda: [0, D("0")])
)
_TOTAL_CLOSES = 0
LOG_EVERY_CLOSES = 25


def _qualifies(state, duration_ms: int, threshold: str, survival_ms: int) -> bool:
    if state is None or duration_ms < survival_ms:
        return False
    if threshold == "2bp":
        return bool(state.all_events_gate_2bp)
    if threshold == "5bp":
        return bool(state.all_events_gate_5bp)
    raise ValueError(threshold)


def _record(asset: str, cohort: str, profit: Decimal) -> None:
    row = _COUNTS[asset][cohort]
    row[0] += 1
    row[1] += D(profit)


def summary_payload() -> dict:
    by_asset = {}
    totals: dict[str, list] = defaultdict(lambda: [0, D("0")])
    for asset, cohorts in sorted(_COUNTS.items()):
        by_asset[asset] = {}
        for cohort, (episodes, profit) in sorted(cohorts.items()):
            by_asset[asset][cohort] = {
                "episodes": int(episodes),
                "apparent_locked_quote_profit": str(profit),
            }
            totals[cohort][0] += int(episodes)
            totals[cohort][1] += D(profit)
    return {
        "episode_closes_observed": _TOTAL_CLOSES,
        "by_asset": by_asset,
        "totals": {
            cohort: {
                "episodes": int(values[0]),
                "apparent_locked_quote_profit": str(values[1]),
            }
            for cohort, values in sorted(totals.items())
        },
        "profit_interpretation": "APPARENT_QUOTE_LOCK_PROFIT_NOT_REALIZED_PNL",
        "basis_proxy": "GEMINI_PUBLIC_SPOT_BOOK_MID",
        "official_live_kaiko": False,
        "settlement_status": basis.hot.SETTLEMENT_STATUS,
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }


def _summarizing_close(*, active, qualifying, now_ms, asset, pair) -> None:
    global _TOTAL_CLOSES
    closing = [direction for direction in active if direction not in qualifying]
    survivor_seen = False
    for direction in closing:
        ep = active.get(direction)
        if ep is None:
            continue
        state = basis._BASIS_EPISODES.get((asset, direction))
        duration_ms = max(0, int(now_ms) - int(ep.start_ms))
        profit = D(ep.best_profit)
        _TOTAL_CLOSES += 1
        _record(asset, "raw_threshold", profit)
        for threshold in ("2bp", "5bp"):
            for survival_ms in (50, 100):
                if _qualifies(state, duration_ms, threshold, survival_ms):
                    cohort = f"{threshold}_{survival_ms}ms"
                    _record(asset, cohort, profit)
                    survivor_seen = True

    # Let the proven whole-episode annotator emit its detailed record and perform the
    # actual close after we have read (but not mutated) its basis state.
    _BASE_CLOSE(
        active=active,
        qualifying=qualifying,
        now_ms=now_ms,
        asset=asset,
        pair=pair,
    )

    if closing and (survivor_seen or _TOTAL_CLOSES % LOG_EVERY_CLOSES == 0):
        logger.info(
            "CRYPTO_HOT_BASIS_COHORT_SUMMARY %s",
            json.dumps(summary_payload(), separators=(",", ":")),
        )


def install() -> None:
    basis.install()
    basis.hot._close_missing_episodes = _summarizing_close


def main() -> None:
    install()
    logger.info(
        "CRYPTO_HOT_BASIS_COHORT_SUMMARY_ENABLED no_additional_venue_calls=true read_only=true order_entry=false"
    )
    basis.prewarmed.main()


if __name__ == "__main__":
    main()
