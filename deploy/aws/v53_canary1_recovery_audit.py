"""Read-only emergency audit for the consumed V5.3 canary-1 exposure.

The live canary unexpectedly executed the V5.2 campaign path, so this diagnostic
uses the native V5.2 recovery audit against the exact durable canary state. It does
not arm recovery and cannot place/cancel orders. Account/audit evidence and current
available USD are emitted even when the expired market has no book. If an exact
one-sided residual is still open and fresh depth exists, rescue routes are computed
under the original $0.30 rescue bound and the configured $1.00 campaign-loss ceiling.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import json

from src import gemini_q10_production_executor_v5 as v5
from src import gemini_q10_v5_2_recovery as recovery52
from src.gemini_prediction_book_cache import GeminiTopCache
from src import gemini_q10_rescue_policy as rescue

ZERO = D("0")


def _row_for_outcome(outcome: str, yrow, nrow):
    return yrow if outcome == "YES" else nrow


async def main() -> None:
    recovery52.install()
    report, state, yrow, nrow = await recovery52.audit()
    account = await v5.v1._flat_snapshot()
    available_usd = None
    available_error = None
    if int(account.get("orders", 0)) == 0 and int(account.get("positions", 0)) == 0:
        try:
            available_usd, _ = await v5.v1._preflight_flat()
        except Exception as exc:
            available_error = {"type": type(exc).__name__, "message": str(exc)[:240]}
    out = {
        "classification": "READ_ONLY_V53_CANARY1_NATIVE_V52_RECOVERY_AUDIT",
        "audit": report.as_dict(),
        "account": account,
        "available_usd": None if available_usd is None else str(available_usd),
        "available_error": available_error,
        "state_status": None if not state else state.get("status"),
        "state_classification": None if not state else state.get("classification"),
        "recovery_armed": recovery52.recovery.recovery_armed(),
        "order_entry": False,
        "route_030": None,
        "route_100": None,
        "route_error": None,
    }

    if (
        int(account.get("positions", 0)) > 0
        and report.open_state
        and report.exact_residual
        and report.residual_outcome in {"YES", "NO"}
        and report.residual_quantity > ZERO
        and state
    ):
        outcome = report.residual_outcome
        row = _row_for_outcome(outcome, yrow, nrow)
        fallback_key = "yes_quote" if outcome == "YES" else "no_quote"
        fallback = D(str(state.get(fallback_key) or "0"))
        filled_price = v5._fill_price(row, fallback)
        increment = D(str(state.get("price_increment") or "0.01"))
        symbol = str(state.get("symbol") or "")
        out["filled_price"] = str(filled_price)
        cache = GeminiTopCache(symbol, stale_ms=750)
        stop = asyncio.Event()
        task = asyncio.create_task(cache.run(stop))
        try:
            try:
                snap = await cache.wait_fresh(timeout=3.0, max_age_ms=v5.BOOK_MAX_AGE_MS)
            except Exception as exc:
                out["route_error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc)[:240],
                    "classification": "NO_FRESH_BOOK_ROUTE_NOT_ATTEMPTED",
                }
            else:
                common = dict(
                    filled_outcome=outcome,
                    filled_price=filled_price,
                    quantity=report.residual_quantity,
                    yes_bid=snap.bid,
                    yes_bid_size=snap.bid_size,
                    yes_ask=snap.ask,
                    yes_ask_size=snap.ask_size,
                    increment=increment,
                )
                out["book"] = {
                    "bid": str(snap.bid),
                    "bid_size": str(snap.bid_size),
                    "ask": str(snap.ask),
                    "ask_size": str(snap.ask_size),
                    "age_ms": round(snap.age_ms, 3),
                }
                out["route_030"] = rescue.best_route_after_fill(max_loss=D("0.30"), **common).as_dict()
                out["route_100"] = rescue.best_route_after_fill(max_loss=D("1.00"), **common).as_dict()
        finally:
            stop.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    print("V53_CANARY1_RECOVERY_AUDIT=" + json.dumps(out, separators=(",", ":"), default=str), flush=True)
    print("V53_CANARY1_RECOVERY_AUDIT_COMPLETE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
