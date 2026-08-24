"""Fail-closed q=1 MVE live runner extended to exact 2-5 leg baskets.

This deliberately reuses the battle-tested primitives from the original 2-3
leg runner.  The only strategy expansion is the allowed authoritative MVE leg
count.  Hedge placement remains q=1 FOK on every complement leg, followed by
an immediate quote recheck before accepting the combo quote.  Any incomplete
hedge basket is flattened; ambiguous combo acknowledgement still hard-halts.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from src import sports_kp_mve_same_venue_live_q1 as base
from src.auth import KalshiClient
from src.sports_kp_execution import KalshiExecutor

SUPPORTED_LIVE_LEGS = {2, 3, 4, 5}
LIVE_MIN_LOCKED = max(base.MIN_LOCKED, base.D(os.getenv("MVE_LIVE_MIN_LOCKED", "0.03")))


def valid_live_leg_count(n: int) -> bool:
    return n in SUPPORTED_LIVE_LEGS


async def main_async() -> dict[str, Any]:
    ticker = os.getenv("KALSHI_MVE_LIVE_TICKER", "").strip()
    enabled = os.getenv("ENABLE_KALSHI_MVE_LIVE", "NO").strip().upper() == "YES"
    if not enabled:
        return {"status": "DISARMED", "order_entry": False, "reason": "ENABLE_KALSHI_MVE_LIVE!=YES"}
    if not ticker:
        return {"status": "DISARMED", "order_entry": False, "reason": "KALSHI_MVE_LIVE_TICKER missing"}

    client = KalshiClient(base_url=base.BASE)
    kexec = KalshiExecutor(client=client)
    rfq_id: str | None = None
    hedge_results: list[Any] = []
    plans: list[Any] = []
    try:
        mresp = await client.get(f"/markets/{ticker}")
        if mresp.status_code != 200:
            return {"status": "NO_TRADE", "reason": f"MVE_HTTP_{mresp.status_code}", "order_entry": False}
        mve = base._market(mresp.json() if mresp.content else {})
        if str(mve.get("status") or "").lower() not in {"active", "open"}:
            return {"status": "NO_TRADE", "reason": "MVE_NOT_OPEN", "order_entry": False}

        rfq_id, rfq, quotes = await base._fresh_rfq(client, ticker)
        legs = [dict(x) for x in (rfq.get("mve_selected_legs") or mve.get("mve_selected_legs") or []) if isinstance(x, dict)]
        if not valid_live_leg_count(len(legs)):
            return {"status": "NO_TRADE", "reason": "REQUIRE_2_TO_5_EXACT_MVE_LEGS", "rfq_id": rfq_id, "order_entry": False}

        for row in legs:
            st = str(row.get("market_ticker") or "")
            sr = await client.get(f"/markets/{st}") if st else None
            sm = base._market(sr.json() if sr is not None and sr.content else {}) if sr is not None else {}
            plan = base._make_hedge(sm, str(row.get("side") or ""))
            if plan is None:
                return {"status": "NO_TRADE", "reason": f"UNEXECUTABLE_HEDGE:{st}", "rfq_id": rfq_id, "order_entry": False}
            plans.append(plan)

        best = base._best_buy_yes_quote(quotes)
        if best is None:
            return {"status": "NO_TRADE", "reason": "NO_OPEN_BUY_YES_EQUIVALENT_RFQ_QUOTE", "rfq_id": rfq_id, "order_entry": False}
        age = base._iso_age_ms(best.get("updated_ts"))
        if age is not None and age > 2500:
            return {"status": "NO_TRADE", "reason": "RFQ_QUOTE_STALE", "quote_age_ms": age, "rfq_id": rfq_id, "order_entry": False}

        raw = best["yes_cost"] + sum((p.price for p in plans), base.ZERO)
        fees = best["fee"] + sum((p.fee for p in plans), base.ZERO)
        all_in = raw + fees
        locked = base.ONE - all_in
        if locked < LIVE_MIN_LOCKED:
            return {
                "status": "NO_TRADE", "reason": "AFTER_FEE_LOCK_BELOW_3C", "rfq_id": rfq_id,
                "all_in_cost_conservative": str(all_in), "locked_profit_conservative": str(locked),
                "min_locked_required": str(LIVE_MIN_LOCKED), "order_entry": False,
            }

        positions = await asyncio.gather(*[base._position(client, x) for x in [ticker] + [p.ticker for p in plans]])
        if any(x != base.ZERO for x in positions):
            return {"status": "NO_TRADE", "reason": "PREEXISTING_POSITION", "positions": [str(x) for x in positions], "order_entry": False}
        balance = await base._available_balance(client)
        if balance < all_in + base.D("0.10"):
            return {"status": "NO_TRADE", "reason": "INSUFFICIENT_BALANCE_BUFFER", "balance": str(balance), "needed": str(all_in + base.D('0.10')), "order_entry": False}

        hedge_results = list(await asyncio.gather(*[kexec.place_fok(p.leg) for p in plans]))
        if not all(x.filled_one for x in hedge_results):
            flats = await base._flatten_filled(kexec, plans, hedge_results)
            return {
                "status": "HEDGE_FILL_ABORT", "rfq_id": rfq_id,
                "hedges": [x.as_dict() for x in hedge_results], "flatten": flats,
                "combo_quote_accepted": False, "hard_halt_required": any(x.status == "UNKNOWN" for x in hedge_results),
            }

        freshq = await base._refresh_quote(client, best["id"])
        fresh_nb = base._d(freshq.get("no_bid_dollars"))
        fresh_status = str(freshq.get("status") or "").lower()
        fresh_age = base._iso_age_ms(freshq.get("updated_ts"))
        if fresh_status != "open" or fresh_nb is None or fresh_nb < best["no_bid"] or (fresh_age is not None and fresh_age > 2500):
            flats = await base._flatten_filled(kexec, plans, hedge_results)
            return {"status": "QUOTE_CHANGED_ABORT", "rfq_id": rfq_id, "flatten": flats, "combo_quote_accepted": False, "hard_halt_required": False}

        accept = await client.request("PUT", f"/communications/quotes/{best['id']}/accept", json={"accepted_side": "no"})
        if accept.status_code >= 300:
            flats = await base._flatten_filled(kexec, plans, hedge_results)
            return {"status": "QUOTE_ACCEPT_REJECTED", "http": accept.status_code, "rfq_id": rfq_id, "flatten": flats, "hard_halt_required": False}

        deadline = time.monotonic() + 8.0
        last_q: dict[str, Any] = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            last_q = await base._refresh_quote(client, best["id"])
            pos = await base._position(client, ticker)
            if pos == base.ONE:
                return {
                    "status": "FULLY_HEDGED_FILLED", "rfq_id": rfq_id, "quote_id": best["id"],
                    "mve_position": str(pos), "hedges": [x.as_dict() for x in hedge_results],
                    "leg_count": len(legs),
                    "all_in_cost_conservative_at_send": str(all_in), "locked_profit_conservative_at_send": str(locked),
                    "min_locked_required": str(LIVE_MIN_LOCKED),
                    "guaranteed_scalar_safe_floor": "1.00", "combo_quote_accepted": True,
                    "classification": "FILLED_NOT_SETTLED", "realized_profit": None, "hard_halt_required": False,
                }
            qstatus = str(last_q.get("status") or "").lower()
            if qstatus in {"cancelled", "canceled", "expired", "rejected"} and pos == base.ZERO:
                flats = await base._flatten_filled(kexec, plans, hedge_results)
                return {"status": "COMBO_NOT_EXECUTED_FLATTENED", "rfq_id": rfq_id, "quote_status": qstatus, "flatten": flats, "hard_halt_required": False}

        return {
            "status": "AMBIGUOUS_COMBO_AFTER_ACCEPT", "rfq_id": rfq_id, "quote_id": best["id"],
            "quote_status": last_q.get("status"), "hedges": [x.as_dict() for x in hedge_results],
            "combo_quote_accepted": True, "hard_halt_required": True, "manual_reconciliation_required": True,
        }
    finally:
        if rfq_id:
            try:
                qrows = await client.get("/communications/quotes", params={"rfq_id": rfq_id, "rfq_user_filter": "self", "limit": 10})
                accepted = False
                if qrows.status_code == 200:
                    qb = qrows.json() if qrows.content else {}
                    accepted = any(str(x.get("status") or "").lower() in {"accepted", "confirmed", "executed"} for x in (qb.get("quotes") or []) if isinstance(x, dict))
                if not accepted:
                    await client.delete(f"/communications/rfqs/{rfq_id}")
            except Exception:
                pass
        await client.close()


def main() -> None:
    print(json.dumps(asyncio.run(main_async()), separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    main()
