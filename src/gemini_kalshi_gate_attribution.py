"""Read-only attribution for crypto live gate rejections.

Installs a logging observer on the existing micro-live executor logger. It never
changes market data, gate values, execution state, or order flow. Only the 50 ms
revalidation point is counted so 50/100 ms diagnostics do not double count one plan.
"""
from __future__ import annotations

from collections import Counter
import json
import logging
import time
from typing import Any

logger = logging.getLogger("kalshi_arb.crypto_gate_attribution")
_INSTALLED = False


def classify_gate(payload: dict[str, Any]) -> str:
    """Return the first material reason a 50 ms plan was not executable."""
    if bool(payload.get("strict_micro_live_executable_20bp")):
        return "EXECUTABLE"
    if not bool(payload.get("same_window")):
        return "WINDOW_MISMATCH"
    if not bool(payload.get("same_original_episode")):
        return "EPISODE_SURVIVAL"
    if not bool(payload.get("gemini_price_ok")) or not bool(payload.get("kalshi_price_ok")):
        return "PRICE_MOVED"
    if not bool(payload.get("gemini_depth_ok")) or not bool(payload.get("kalshi_depth_ok")):
        return "DEPTH_GONE"
    if not bool(payload.get("post_activation_books")):
        return "PRE_ACTIVATION_BOOK"
    current_skew = payload.get("current_quote_skew_ms")
    if current_skew is not None and int(current_skew) > 50:
        return "QUOTE_SKEW"
    g_age = payload.get("gemini_age_ms")
    k_age = payload.get("kalshi_age_ms")
    if (g_age is not None and int(g_age) > 250) or (k_age is not None and int(k_age) > 250):
        return "BOOK_STALE"
    if not bool(payload.get("strict_fresh_250ms_and_50ms_skew")):
        return "FRESHNESS_OTHER"
    if not bool(payload.get("entry_double_boundary_20bp")):
        return "BOUNDARY_ENTRY"
    if not bool(payload.get("double_boundary_20bp_now")):
        return "BOUNDARY_NOW"
    if not bool(payload.get("double_boundary_20bp_held_through_episode")):
        return "BOUNDARY_EPISODE"
    return "OTHER_GATE"


class _GateHandler(logging.Handler):
    def __init__(self, emit_every_s: float = 10.0) -> None:
        super().__init__(level=logging.INFO)
        self.counts: Counter[str] = Counter()
        self.by_asset: dict[str, Counter[str]] = {}
        self.last_emit = time.monotonic()
        self.emit_every_s = emit_every_s

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            prefix = "CRYPTO_MICRO_LIVE_EXECUTOR_GATE "
            if not message.startswith(prefix):
                return
            payload = json.loads(message[len(prefix):])
            if int(payload.get("delay_ms") or 0) != 50:
                return
            reason = classify_gate(payload)
            asset = str(payload.get("asset") or "UNKNOWN").upper()
            self.counts[reason] += 1
            self.by_asset.setdefault(asset, Counter())[reason] += 1
            now = time.monotonic()
            if now - self.last_emit >= self.emit_every_s:
                self.last_emit = now
                summary = {
                    "sample":"50ms_revalidation",
                    "total":sum(self.counts.values()),
                    "counts":dict(self.counts),
                    "by_asset":{a: dict(c) for a, c in sorted(self.by_asset.items())},
                    "risk_gates_changed":False,
                    "order_entry":False,
                }
                logger.info("CRYPTO_GATE_ATTRIBUTION_SUMMARY %s", json.dumps(summary, separators=(",", ":"), sort_keys=True))
        except Exception:
            # Telemetry must never interfere with execution.
            return


def install(executor_logger: logging.Logger) -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    handler = _GateHandler()
    executor_logger.addHandler(handler)
    _INSTALLED = True
    logger.info("CRYPTO_GATE_ATTRIBUTION_INSTALLED sample=50ms risk_gates_changed=false order_entry=false")
