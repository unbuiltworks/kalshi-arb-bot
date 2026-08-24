"""Read-only stratified settlement-risk telemetry for 15-minute Gemini/Kalshi crypto.

Wraps the existing opportunity-settlement audit and adds disagreement counts by asset,
direction, and exact matrix cell (profit floor x boundary distance x revalidation delay).
No order transport or account state is used.
"""
from __future__ import annotations

from collections import defaultdict
import json
import logging
import sys
from typing import Any

from src import gemini_kalshi_opportunity_settlement_audit_shadow as audit
from src.gemini_kalshi_opportunity_settlement_recent_patch import install as install_recent_settlements

logger = logging.getLogger("kalshi_arb.gemini_kalshi_opportunity_settlement_risk_shadow")

_ORIGINAL_EMIT = audit._emit_settled_audits
_ASSET: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
_ASSET_DIRECTION: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
_ASSET_CELL: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))


def configure_shadow_logging() -> None:
    """Keep normal read-only telemetry on stdout so Railway severity stays accurate.

    Python's default logging handler writes to stderr. Railway classifies stderr lines as
    error severity even when the record itself says INFO, which makes a healthy shadow
    scanner look unhealthy. Configure the root handler before the imported watcher main
    has a chance to install its default handler; later non-force basicConfig calls are
    then harmless no-ops. Real WARNING/ERROR records retain their logging level.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        stream=sys.stdout,
        force=True,
    )


def _bump(bucket: defaultdict[str, int], agrees: bool) -> None:
    bucket["settled"] += 1
    bucket["compatible"] += int(agrees)
    bucket["disagreements"] += int(not agrees)


def _snapshot(table: dict[str, defaultdict[str, int]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, counts in sorted(table.items()):
        settled = int(counts.get("settled", 0))
        disagreements = int(counts.get("disagreements", 0))
        out[key] = {
            "settled": settled,
            "compatible": int(counts.get("compatible", 0)),
            "disagreements": disagreements,
            "observed_disagreement_rate": (disagreements / settled) if settled else None,
        }
    return out


def _emit_with_risk(rows: list[Any]) -> None:
    by_ticker = {str(row.kalshi_ticker): row for row in rows}

    # Capture strata before the base audit removes settled candidates from memory.
    observed: list[tuple[str, str, tuple[str, ...], bool]] = []
    for key, candidate in list(audit._PENDING.items()):
        if key in audit._SETTLED_KEYS:
            continue
        settled = by_ticker.get(str(candidate["kalshi_ticker"]))
        if settled is None:
            continue
        observed.append((
            str(candidate["asset"]),
            str(candidate["direction"]),
            tuple(sorted(candidate["passing_cells"])),
            bool(settled.agrees),
        ))

    _ORIGINAL_EMIT(rows)

    for asset, direction, cells, agrees in observed:
        _bump(_ASSET[asset], agrees)
        _bump(_ASSET_DIRECTION[f"{asset}|{direction}"], agrees)
        for cell in cells:
            _bump(_ASSET_CELL[f"{asset}|{cell}"], agrees)

    if observed:
        logger.info(
            "CRYPTO_SETTLEMENT_RISK_STRATA %s",
            json.dumps({
                "newly_settled_candidates": len(observed),
                "by_asset": _snapshot(_ASSET),
                "by_asset_direction": _snapshot(_ASSET_DIRECTION),
                "by_asset_cell": _snapshot(_ASSET_CELL),
                "interpretation": "OBSERVED_POST_SETTLEMENT_COUNTERFACTUAL_RISK_NOT_REALIZED_PNL",
                "live_gate_recommendation": "DO_NOT_ARM_FROM_THIS_TELEMETRY_ALONE",
                "trade_authorized": False,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str),
        )


def main() -> None:
    configure_shadow_logging()
    install_recent_settlements()
    audit._emit_settled_audits = _emit_with_risk
    logger.info(
        "CRYPTO_SETTLEMENT_RISK_STRATA_ENABLED dimensions=asset,asset_direction,asset_cell "
        "settlement_source=gemini_recently_settled expiry_compat_fixed=true "
        "counterfactual_only=true trade_authorized=false read_only=true order_entry=false"
    )
    audit.main()


if __name__ == "__main__":
    main()
