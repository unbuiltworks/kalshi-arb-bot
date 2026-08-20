"""Read-only de-duplicated throughput accounting for crypto capacity research.

Raw capacity checkpoints can repeat many times inside the same 15-minute market and
must not be counted as independent trades.  This wrapper listens to the existing
CRYPTO_CAPACITY_CHECKPOINT logs and records at most the *first* supported opportunity
for each tested quantity per asset/market window.  Direction flips and repeated quote
states in the same asset/window therefore cannot inflate opportunity count.

The output is modeled shadow capacity only, never realized P&L.  A daily run-rate is
withheld until at least one hour of observation has elapsed.
"""
from __future__ import annotations

from decimal import Decimal
import json
import logging
import time
from typing import Any

from src import gemini_kalshi_capacity_shadow as capacity

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_capacity_throughput_shadow")
_START_MONO = time.monotonic()
_WINDOWS_SEEN: set[tuple[str, str]] = set()
_FIRST_SUPPORTED: dict[str, dict[tuple[str, str], dict[str, Any]]] = {
    "5": {}, "10": {}, "25": {},
}


def _row_for_q(payload: dict[str, Any], q: str) -> dict[str, Any] | None:
    for row in payload.get("sizes") or []:
        if str(row.get("quantity")) == q:
            return row
    return None


def _consume_checkpoint(payload: dict[str, Any]) -> dict[str, Any]:
    asset = str(payload.get("asset") or "").upper()
    ticker = str(payload.get("kalshi_ticker") or "")
    if not asset or not ticker:
        raise ValueError("capacity checkpoint missing asset/ticker")
    key = (asset, ticker)
    _WINDOWS_SEEN.add(key)

    newly_recorded: list[str] = []
    for q in ("5", "10", "25"):
        row = _row_for_q(payload, q)
        if not row or not bool(row.get("supported")) or key in _FIRST_SUPPORTED[q]:
            continue
        profit = D(str(row.get("cushioned_profit")))
        capital = D(str(row.get("total_capital")))
        if profit <= 0 or capital <= 0:
            continue
        _FIRST_SUPPORTED[q][key] = {
            "asset": asset,
            "kalshi_ticker": ticker,
            "direction": str(payload.get("direction") or ""),
            "opened_ms": int(payload.get("opened_ms") or 0),
            "modeled_profit": str(profit),
            "modeled_capital": str(capital),
            "profit_per_contract": str(row.get("cushioned_profit_per_contract")),
        }
        newly_recorded.append(q)

    return {"window_key": key, "newly_recorded_quantities": newly_recorded}


def _sum_profit(q: str) -> D:
    return sum((D(row["modeled_profit"]) for row in _FIRST_SUPPORTED[q].values()), D("0"))


def _summary(now_mono: float | None = None) -> dict[str, Any]:
    now = time.monotonic() if now_mono is None else float(now_mono)
    elapsed_s = max(0.0, now - _START_MONO)
    elapsed_h = elapsed_s / 3600.0
    ready = elapsed_s >= 3600.0
    modeled = {q: _sum_profit(q) for q in ("5", "10", "25")}
    daily = {
        q: (str(modeled[q] * D("24") / D(str(elapsed_h))) if ready and elapsed_h > 0 else None)
        for q in ("5", "10", "25")
    }
    return {
        "dedupe_rule": "FIRST_SUPPORTED_ONLY_ONE_TRADE_PER_ASSET_MARKET_WINDOW_PER_SIZE",
        "unique_asset_windows_seen": len(_WINDOWS_SEEN),
        "q5_unique_supported_windows": len(_FIRST_SUPPORTED["5"]),
        "q10_unique_supported_windows": len(_FIRST_SUPPORTED["10"]),
        "q25_unique_supported_windows": len(_FIRST_SUPPORTED["25"]),
        "q5_first_supported_modeled_profit_sum": str(modeled["5"]),
        "q10_first_supported_modeled_profit_sum": str(modeled["10"]),
        "q25_first_supported_modeled_profit_sum": str(modeled["25"]),
        "observed_seconds": round(elapsed_s, 3),
        "daily_projection_ready": ready,
        "q5_shadow_modeled_daily_projection": daily["5"],
        "q10_shadow_modeled_daily_projection": daily["10"],
        "q25_shadow_modeled_daily_projection": daily["25"],
        "projection_interpretation": "READ_ONLY_MODELED_FIRST_SUPPORTED_CAPACITY_NOT_EXECUTED_OR_REALIZED_PNL",
        "risk_gates_changed": False,
        "trade_authorized": False,
        "read_only": True,
        "order_entry": False,
    }


class _CapacityCheckpointHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            prefix = "CRYPTO_CAPACITY_CHECKPOINT "
            if not message.startswith(prefix):
                return
            payload = json.loads(message[len(prefix):])
            consumed = _consume_checkpoint(payload)
            logger.info(
                "CRYPTO_CAPACITY_THROUGHPUT_SUMMARY %s",
                json.dumps({**_summary(), "latest": {
                    "asset": consumed["window_key"][0],
                    "kalshi_ticker": consumed["window_key"][1],
                    "newly_recorded_quantities": consumed["newly_recorded_quantities"],
                }}, separators=(",", ":"), sort_keys=True),
            )
        except Exception:
            logger.exception("CRYPTO_CAPACITY_THROUGHPUT_HANDLER_ERROR")


def install() -> None:
    capacity.logger.addHandler(_CapacityCheckpointHandler())
    logger.warning(
        "CRYPTO_CAPACITY_THROUGHPUT_INSTALLED dedupe=one_first_supported_per_asset_market_window_per_size "
        "daily_projection_min_observation_s=3600 trade_authorized=false order_entry=false"
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    install()
    capacity.main()


if __name__ == "__main__":
    main()
