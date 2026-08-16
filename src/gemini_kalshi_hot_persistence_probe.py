"""Read-only persistence milestones for prewarmed Gemini/Kalshi quote locks.

A displayed episode that eventually lasts 100 ms is not necessarily executable after
a confirmation delay. This wrapper logs the *current* fee-adjusted quote economics
when an episode first survives 10/25/50/100 ms, so research can measure how much edge
remains after waiting instead of using eventual duration as hindsight.

Includes the initial 500 ms startup-snapshot quarantine. No order-entry path.
"""
from __future__ import annotations

import json

from src import gemini_kalshi_predictive_prewarmed_quarantine as quarantine

base = quarantine.base
hot = quarantine.hot
_original_update = hot._update_episodes
MILESTONES_MS = (10, 25, 50, 100)


def _update_episodes_with_persistence(
    *,
    active,
    qualifying,
    now_ms: int,
    asset: str,
    pair,
    trigger_venue: str,
) -> None:
    _original_update(
        active=active,
        qualifying=qualifying,
        now_ms=now_ms,
        asset=asset,
        pair=pair,
        trigger_venue=trigger_venue,
    )

    for direction, (q, candidate) in qualifying.items():
        ep = active.get(direction)
        if ep is None:
            continue
        logged = getattr(ep, "persistence_milestones_logged", None)
        if logged is None:
            logged = set()
            setattr(ep, "persistence_milestones_logged", logged)
        age_ms = max(0, now_ms - ep.start_ms)
        for milestone_ms in MILESTONES_MS:
            if milestone_ms in logged or age_ms < milestone_ms:
                continue
            logged.add(milestone_ms)
            hot.logger.info(
                "CRYPTO_HOT_PERSISTENCE_MILESTONE %s",
                json.dumps({
                    "asset": asset,
                    "direction": direction,
                    "episode_start_ms": ep.start_ms,
                    "milestone_ms": milestone_ms,
                    "observed_age_ms": age_ms,
                    "positive_events_so_far": ep.events,
                    "trigger_venue": trigger_venue,
                    "gemini_symbol": pair.gemini_symbol,
                    "kalshi_ticker": pair.kalshi_ticker,
                    **hot._candidate_payload(q, candidate),
                    "best_profit_so_far": str(ep.best_profit),
                    "best_capital_so_far": str(ep.best_capital),
                    "best_q_so_far": ep.best_q,
                    "settlement_status": hot.SETTLEMENT_STATUS,
                    "trade_authorized": False,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")),
            )


hot._update_episodes = _update_episodes_with_persistence


def main() -> None:
    quarantine.main()


if __name__ == "__main__":
    main()
