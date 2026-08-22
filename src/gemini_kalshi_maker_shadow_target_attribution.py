"""Read-only target attribution for the existing crypto maker shadow.

The base maker shadow intentionally mixes several required-profit cohorts in its
aggregate fill counters. That is useful for research, but it can make a high fill
count look more relevant to the live >=3c lane than it really is. This wrapper adds
per-target fill/profit/stale-hedge counters without changing quote generation, queue
consumption, fill inference, fee math, settlement classification, or order capability.

No order transport is added. All outputs remain INDEX_MISMATCH_RESEARCH_ONLY.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src import gemini_kalshi_maker_shadow as base

D = Decimal
_INSTALLED = False
_TARGET_STATS: dict[int, dict[str, dict[str, Decimal | int]]] = {}


def _target_from_key(key: str) -> str | None:
    try:
        _direction, _policy, target = str(key).rsplit("|", 2)
        return str(D(target))
    except Exception:
        return None


def _bucket(stats: Any, target: str) -> dict[str, Decimal | int]:
    by_target = _TARGET_STATS.setdefault(id(stats), {})
    return by_target.setdefault(
        target,
        {"fills": 0, "profit": D("0"), "stale_hedges": 0},
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_process_trade = base._process_trade
    original_payload = base.MakerStats.payload

    def process_trade_with_target_attribution(**kwargs):
        stats = kwargs["stats"]
        events = original_process_trade(**kwargs)
        for row in events:
            event = str(row.get("event") or "")
            target = row.get("target_profit")
            if target is None:
                target = _target_from_key(str(row.get("key") or ""))
            if target is None:
                continue
            target_s = str(D(str(target)))
            bucket = _bucket(stats, target_s)
            if event == "SIMULATED_FILL":
                bucket["fills"] = int(bucket["fills"]) + 1
                bucket["profit"] = D(str(bucket["profit"])) + D(
                    str(row.get("simulated_fill_profit") or "0")
                )
            elif event == "FILL_HEDGE_UNAVAILABLE":
                bucket["stale_hedges"] = int(bucket["stale_hedges"]) + 1
        return events

    def payload_with_target_attribution(self, active_quotes: int, queue_size: int):
        payload = original_payload(self, active_quotes, queue_size)
        rows = _TARGET_STATS.get(id(self), {})
        by_target = {
            target: {
                "fills": int(row["fills"]),
                "profit": str(row["profit"]),
                "stale_hedges": int(row["stale_hedges"]),
            }
            for target, row in sorted(rows.items(), key=lambda item: D(item[0]))
        }
        live_floor = D("0.03")
        payload["by_target"] = by_target
        payload["fills_at_or_above_3c_target"] = sum(
            int(row["fills"]) for target, row in rows.items() if D(target) >= live_floor
        )
        payload["stale_hedges_at_or_above_3c_target"] = sum(
            int(row["stale_hedges"]) for target, row in rows.items() if D(target) >= live_floor
        )
        payload["profit_at_or_above_3c_target"] = str(sum(
            (D(str(row["profit"])) for target, row in rows.items() if D(target) >= live_floor),
            D("0"),
        ))
        payload["target_attribution"] = "DIAGNOSTIC_ONLY"
        payload["live_floor_reference"] = "0.03"
        payload["risk_gates_changed"] = False
        payload["execution_eligible"] = False
        payload["read_only"] = True
        payload["order_entry"] = False
        return payload

    base._process_trade = process_trade_with_target_attribution
    base.MakerStats.payload = payload_with_target_attribution
    _INSTALLED = True


def main() -> None:
    # Install before importing the Railway service module so its imported collect_all
    # executes against the patched base module. The service still owns its resting-
    # liveness/stress sidecars and CLI, including max-resting-feed-age-ms.
    install()
    from src import gemini_kalshi_maker_shadow_service as service
    service.main()


if __name__ == "__main__":
    main()
