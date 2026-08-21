"""Production combo supervisor v7: use expected sports resolution for horizon.

Kalshi documents ``expected_expiration_time`` as the forecasted time the outcome
will be known; sports ``close_time`` may intentionally be days later to allow
for rescheduling.  The combo engine's short-duration horizon therefore uses
expected_expiration_time first and falls back to close/latest expiration only
when the forecasted field is unavailable.

All v6 request throttling, diagnostics, depth corrections, action-only alerts,
and fail-closed q=1 execution remain unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src import sports_kp_live_combo_supervisor_v6 as v6

base = v6.base
generator = v6.generator
logger = v6.logger


def _resolution_time(market: dict[str, Any]):
    expected = generator._parse_dt(market.get("expected_expiration_time"))
    if expected is not None:
        v6._DIAG["resolution_expected_expiration"] += 1
        return expected
    close = generator._parse_dt(market.get("close_time"))
    if close is not None:
        v6._DIAG["resolution_fallback_close"] += 1
        return close
    latest = generator._parse_dt(market.get("latest_expiration_time") or market.get("expiration_time"))
    if latest is not None:
        v6._DIAG["resolution_fallback_latest"] += 1
    return latest


def _short_horizon_leg(
    market: dict[str, Any], event_ticker: str, side: str, horizon: datetime
):
    side = side.lower()
    v6._DIAG["markets_examined"] += 1
    if side not in {"yes", "no"}:
        v6._DIAG["reject_bad_side"] += 1
        return None
    status = str(market.get("status") or "").lower()
    if status not in {"active", "open"}:
        v6._DIAG["reject_status"] += 1
        return None
    v6._DIAG["status_open"] += 1

    resolution = _resolution_time(market)
    now = datetime.now(timezone.utc)
    if resolution is None:
        v6._DIAG["reject_no_resolution_time"] += 1
        return None
    if resolution <= now:
        v6._DIAG["reject_closed_time"] += 1
        return None
    if resolution > horizon:
        v6._DIAG["reject_beyond_horizon"] += 1
        return None
    v6._DIAG["within_horizon"] += 1

    ticker = str(market.get("ticker") or "")
    if not ticker:
        v6._DIAG["reject_no_ticker"] += 1
        return None

    if side == "yes":
        hedge_side = "no"
        price = v6._derived_no_ask(market)
        depth = generator._d(market.get("yes_bid_size_fp") or market.get("yes_bid_size"))
    else:
        hedge_side = "yes"
        price = v6._derived_yes_ask(market)
        depth = generator._d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))

    if price is None:
        v6._DIAG["reject_no_price"] += 1
        return None
    if not (generator.ZERO < price < generator.ONE):
        v6._DIAG["reject_nonmarketable_price"] += 1
        return None
    v6._DIAG["marketable_price"] += 1
    if depth is None:
        v6._DIAG["reject_no_depth"] += 1
        return None
    if depth < generator.ONE:
        v6._DIAG["reject_depth_lt_q1"] += 1
        return None
    v6._DIAG["eligible_legs"] += 1

    return generator.Leg(
        event_ticker,
        ticker,
        side,
        hedge_side,
        price,
        generator._fee(price),
        depth,
        resolution,
    )


generator._leg_from_market = _short_horizon_leg

run = base.run
main = base.main


if __name__ == "__main__":
    main()
