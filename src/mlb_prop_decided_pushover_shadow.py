"""Pushover wrapper for the read-only MLB decided-prop shadow.

The underlying scanner remains unchanged and has no order-entry path. This wrapper
listens only for FINAL_STAT_STABLE_RESEARCH_ONLY signals and sends a human-readable
notification when a venue still offers the already-known winning side below its
$1 settlement value after the scanner's conservative fee bound.

These are settlement-lag signals, not cross-venue arbitrage. Threshold-reached
in-game research signals are deliberately never alerted here.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from src import mlb_prop_decided_shadow as decided
from src.sports_pushover_alerts import SportsPushoverAlerts

logger = logging.getLogger("kalshi_arb.mlb_prop_decided_pushover_shadow")

FAMILY_LABELS = {
    "baseball_player_hits": "hits",
    "baseball_player_hits_runs_rbis": "hits+runs+RBIs",
    "baseball_player_total_bases": "total bases",
    "baseball_player_strikeouts": "strikeouts",
}


class _FinalSignalHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.sink = SportsPushoverAlerts()
        self.sent: set[str] = set()

    async def _send_edge(self, row: dict[str, Any], edge: dict[str, Any]) -> None:
        venue = str(edge.get("venue") or "").title()
        outcome = str(edge.get("outcome") or "")
        player = str(row.get("player") or "Unknown player")
        threshold = str(row.get("threshold") or "?")
        family = FAMILY_LABELS.get(str(row.get("family") or ""), str(row.get("family") or "prop"))
        ask = str(edge.get("winner_ask") or "?")
        fee = str(edge.get("fee_upper_bound") or "?")
        profit = str(edge.get("known_winner_profit") or "?")
        statistic = str(row.get("official_statistic") or "?")
        game_pk = str(row.get("mlb_game_pk") or "?")
        key = "|".join((str(row.get("poly_slug")), str(row.get("kalshi_ticker")), venue, outcome))
        if key in self.sent:
            return
        title = f"MLB FINAL EDGE — {player} {threshold}+ {family}"[:250]
        message = "\n".join((
            f"Official MLB game state: FINAL (game {game_pk})",
            f"Official stat: {statistic} → known outcome {outcome}",
            f"Venue: {venue}",
            f"Winner ask: ${ask} + fee cap ${fee}",
            f"Modeled profit: +${profit}/contract if final settlement is unchanged",
            "Evidence held stable ≥60s / ≥3 observations.",
            "SETTLEMENT-LAG SIGNAL — NOT CROSS-VENUE ARBITRAGE",
            "Read-only scanner; no order was submitted.",
        ))
        try:
            request_id = await self.sink.send(title=title, message=message[:1024])
        except Exception as exc:
            logger.warning("DECIDED_MLB_PROP_PUSHOVER_ERROR %s", type(exc).__name__)
            return
        self.sent.add(key)
        logger.info(
            "DECIDED_MLB_PROP_PUSHOVER_SENT %s",
            json.dumps({"key": key, "request_id": request_id, "venue": venue, "outcome": outcome}, separators=(",", ":")),
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            prefix = "DECIDED_MLB_PROP_SIGNAL "
            if not message.startswith(prefix):
                return
            row = json.loads(message[len(prefix):])
            if row.get("decision_state") != "FINAL_STAT_STABLE_RESEARCH_ONLY":
                return
            edges = row.get("known_winner_edges") or []
            if not edges or not self.sink.configured:
                return
            loop = asyncio.get_running_loop()
            for edge in edges:
                if isinstance(edge, dict):
                    loop.create_task(self._send_edge(row, edge))
        except Exception:
            # Notification instrumentation must never stop the underlying scanner.
            logger.exception("DECIDED_MLB_PROP_PUSHOVER_HANDLER_ERROR")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    decided.logger.addHandler(_FinalSignalHandler())
    logger.info(
        "DECIDED_MLB_PROP_PUSHOVER_READY configured=%s read_only=true order_entry=false final_only=true",
        SportsPushoverAlerts().configured,
    )
    asyncio.run(decided.run_forever())


if __name__ == "__main__":
    main()
