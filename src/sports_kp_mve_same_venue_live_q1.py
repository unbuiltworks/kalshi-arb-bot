"""Fail-closed q=1 live runner for one exact same-Kalshi MVE synthetic arb.

This runner NEVER discovers a market to trade on its own.  The caller must set
an exact KALSHI_MVE_LIVE_TICKER and ENABLE_KALSHI_MVE_LIVE=YES.  It rebuilds a
fresh q=1 RFQ + exact component hedge plan, requires >= $0.02 locked profit
using conservative whole-cent taker fees, checks zero pre-existing positions
and balance, FOKs the liquid single hedges first, then accepts the still-open
MVE quote.  If the single hedges do not all fill, any fills are flattened and
the RFQ is never accepted.  If the combo quote is definitively not executed,
the single hedges are flattened. Ambiguous combo acknowledgement hard-halts
without guessing.

The intended Railway deployment is one-shot with restartPolicy=NEVER and a
watch pattern restricted to this file.  Leave ENABLE_KALSHI_MVE_LIVE=NO except
for an explicitly authorized q=1 attempt.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json
import os
import time
from typing import Any

from src.auth import KalshiClient
from src.sports_kp_execution import ExecutionLeg, KalshiExecutor

BASE = "https://external-api.kalshi.com/trade-api/v2"
D = Decimal
ONE = D("1")
ZERO = D("0")
CENT = D("0.01")
MIN_LOCKED = D("0.02")


@dataclass(frozen=True)
class HedgePlan:
    ticker: str
    selected_side: str
    hedge_side: str
    price: Decimal
    depth: Decimal
    fee: Decimal
    leg: ExecutionLeg


def _d(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:
        return None


def _ceil_cent(v: Decimal) -> Decimal:
    return (v / CENT).to_integral_value(rounding=ROUND_CEILING) * CENT


def _fee(p: Decimal) -> Decimal:
    if p <= 0 or p >= 1:
        return ZERO
    return _ceil_cent(D("0.07") * p * (ONE - p))


def _market(body: Any) -> dict[str, Any]:
    if isinstance(body, dict) and isinstance(body.get("market"), dict):
        return dict(body["market"])
    return dict(body) if isinstance(body, dict) else {}


def _iso_age_ms(v: Any) -> int | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - dt).total_seconds() * 1000))
    except Exception:
        return None


def _make_hedge(market: dict[str, Any], selected: str) -> HedgePlan | None:
    ticker = str(market.get("ticker") or "")
    selected = selected.lower()
    hedge = "no" if selected == "yes" else "yes" if selected == "no" else ""
    if hedge == "yes":
        px = _d(market.get("yes_ask_dollars")); depth = _d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
        yes_limit = px
        native = "YES"
    elif hedge == "no":
        px = _d(market.get("no_ask_dollars")); depth = _d(market.get("no_ask_size_fp") or market.get("no_ask_size"))
        yes_limit = ONE - px if px is not None else None
        native = "NO"
    else:
        return None
    if (
        not ticker or px is None or yes_limit is None or depth is None
        or not (ZERO < px < ONE) or depth < ONE
        or str(market.get("status") or "").lower() not in {"active", "open"}
    ):
        return None
    leg = ExecutionLeg(
        venue="kalshi",
        market_id=ticker,
        normalized_outcome=native,
        native_outcome=native,
        raw_outcome_price=px,
        yes_book_limit=yes_limit,
        source_side="CONTRACT",
    )
    return HedgePlan(ticker, selected, hedge, px, depth, _fee(px), leg)


async def _position(client: KalshiClient, ticker: str) -> Decimal:
    r = await client.get("/portfolio/positions", params={"ticker": ticker, "limit": 10})
    r.raise_for_status()
    for row in r.json().get("market_positions") or []:
        if str(row.get("ticker") or "") == ticker:
            return D(str(row.get("position_fp") or "0"))
    return ZERO


async def _available_balance(client: KalshiClient) -> Decimal:
    r = await client.get("/portfolio/balance", params={"subaccount": 0})
    r.raise_for_status()
    body = r.json() if r.content else {}
    if body.get("balance_dollars") is not None:
        return D(str(body["balance_dollars"]))
    # Stable API returns integer cents for `balance`.
    return D(str(body.get("balance") or "0")) / D("100")


async def _fresh_rfq(client: KalshiClient, ticker: str) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    create = await client.post("/communications/rfqs", json={
        "market_ticker": ticker, "contracts": 1, "rest_remainder": False, "replace_existing": True,
    })
    if create.status_code >= 300:
        raise RuntimeError(f"RFQ_CREATE_HTTP_{create.status_code}")
    body = create.json() if create.content else {}
    rid = str(body.get("id") or (body.get("rfq") or {}).get("id") or "") if isinstance(body, dict) else ""
    if not rid:
        raise RuntimeError("RFQ_ID_MISSING")
    rr = await client.get(f"/communications/rfqs/{rid}")
    rr.raise_for_status()
    rb = rr.json() if rr.content else {}
    rfq = dict(rb.get("rfq") or {}) if isinstance(rb, dict) else {}
    quotes: list[dict[str, Any]] = []
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        await asyncio.sleep(0.25)
        qr = await client.get("/communications/quotes", params={"rfq_id": rid, "rfq_user_filter": "self", "limit": 100})
        if qr.status_code != 200:
            continue
        qb = qr.json() if qr.content else {}
        rows = qb.get("quotes") or [] if isinstance(qb, dict) else []
        quotes = [dict(x) for x in rows if isinstance(x, dict)]
        if quotes and time.monotonic() + 0.5 >= deadline:
            break
    return rid, rfq, quotes


def _best_buy_yes_quote(quotes: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for q in quotes:
        if str(q.get("status") or "").lower() not in {"", "open"}:
            continue
        nb = _d(q.get("no_bid_dollars"))
        if nb is None or not (ZERO < nb < ONE):
            continue
        cost = ONE - nb
        row = {"id": str(q.get("id") or ""), "no_bid": nb, "yes_cost": cost, "fee": _fee(cost), "updated_ts": q.get("updated_ts")}
        if not row["id"]:
            continue
        if best is None or cost < best["yes_cost"]:
            best = row
    return best


async def _refresh_quote(client: KalshiClient, quote_id: str) -> dict[str, Any]:
    r = await client.get(f"/communications/quotes/{quote_id}")
    if r.status_code != 200:
        return {}
    body = r.json() if r.content else {}
    return dict(body.get("quote") or {}) if isinstance(body, dict) else {}


async def _flatten_filled(kexec: KalshiExecutor, plans: list[HedgePlan], results: list[Any]) -> list[dict[str, Any]]:
    outs: list[dict[str, Any]] = []
    for plan, result in zip(plans, results):
        if getattr(result, "filled_one", False):
            flat = await kexec.emergency_flatten(plan.leg)
            outs.append(flat.as_dict())
    return outs


async def main_async() -> dict[str, Any]:
    ticker = os.getenv("KALSHI_MVE_LIVE_TICKER", "").strip()
    enabled = os.getenv("ENABLE_KALSHI_MVE_LIVE", "NO").strip().upper() == "YES"
    if not enabled:
        return {"status": "DISARMED", "order_entry": False, "reason": "ENABLE_KALSHI_MVE_LIVE!=YES"}
    if not ticker:
        return {"status": "DISARMED", "order_entry": False, "reason": "KALSHI_MVE_LIVE_TICKER missing"}

    client = KalshiClient(base_url=BASE)
    kexec = KalshiExecutor(client=client)
    rfq_id: str | None = None
    hedge_results: list[Any] = []
    plans: list[HedgePlan] = []
    try:
        mresp = await client.get(f"/markets/{ticker}")
        if mresp.status_code != 200:
            return {"status": "NO_TRADE", "reason": f"MVE_HTTP_{mresp.status_code}", "order_entry": False}
        mve = _market(mresp.json() if mresp.content else {})
        if str(mve.get("status") or "").lower() not in {"active", "open"}:
            return {"status": "NO_TRADE", "reason": "MVE_NOT_OPEN", "order_entry": False}

        rfq_id, rfq, quotes = await _fresh_rfq(client, ticker)
        legs = [dict(x) for x in (rfq.get("mve_selected_legs") or mve.get("mve_selected_legs") or []) if isinstance(x, dict)]
        if len(legs) not in {2, 3}:
            return {"status": "NO_TRADE", "reason": "REQUIRE_2_OR_3_EXACT_MVE_LEGS", "rfq_id": rfq_id, "order_entry": False}

        for row in legs:
            st = str(row.get("market_ticker") or "")
            sr = await client.get(f"/markets/{st}") if st else None
            sm = _market(sr.json() if sr is not None and sr.content else {}) if sr is not None else {}
            plan = _make_hedge(sm, str(row.get("side") or ""))
            if plan is None:
                return {"status": "NO_TRADE", "reason": f"UNEXECUTABLE_HEDGE:{st}", "rfq_id": rfq_id, "order_entry": False}
            plans.append(plan)

        best = _best_buy_yes_quote(quotes)
        if best is None:
            return {"status": "NO_TRADE", "reason": "NO_OPEN_BUY_YES_EQUIVALENT_RFQ_QUOTE", "rfq_id": rfq_id, "order_entry": False}
        age = _iso_age_ms(best.get("updated_ts"))
        if age is not None and age > 2500:
            return {"status": "NO_TRADE", "reason": "RFQ_QUOTE_STALE", "quote_age_ms": age, "rfq_id": rfq_id, "order_entry": False}

        raw = best["yes_cost"] + sum((p.price for p in plans), ZERO)
        fees = best["fee"] + sum((p.fee for p in plans), ZERO)
        all_in = raw + fees
        locked = ONE - all_in
        if locked < MIN_LOCKED:
            return {
                "status": "NO_TRADE", "reason": "AFTER_FEE_LOCK_BELOW_2C", "rfq_id": rfq_id,
                "all_in_cost_conservative": str(all_in), "locked_profit_conservative": str(locked), "order_entry": False,
            }

        # The live attempt requires a clean starting state. This also blocks an
        # accidental redeploy from duplicating an unsettled prior success.
        positions = await asyncio.gather(*[_position(client, x) for x in [ticker] + [p.ticker for p in plans]])
        if any(x != ZERO for x in positions):
            return {"status": "NO_TRADE", "reason": "PREEXISTING_POSITION", "positions": [str(x) for x in positions], "order_entry": False}
        balance = await _available_balance(client)
        if balance < all_in + D("0.10"):
            return {"status": "NO_TRADE", "reason": "INSUFFICIENT_BALANCE_BUFFER", "balance": str(balance), "needed": str(all_in + D('0.10')), "order_entry": False}

        # Mutation starts here. FOK all liquid singles first. Temporary exposure,
        # if any, is only in the underlying markets and is bounded to q=1 each.
        hedge_results = list(await asyncio.gather(*[kexec.place_fok(p.leg) for p in plans]))
        if not all(x.filled_one for x in hedge_results):
            flats = await _flatten_filled(kexec, plans, hedge_results)
            return {
                "status": "HEDGE_FILL_ABORT", "rfq_id": rfq_id,
                "hedges": [x.as_dict() for x in hedge_results], "flatten": flats,
                "combo_quote_accepted": False, "hard_halt_required": any(x.status == "UNKNOWN" for x in hedge_results),
            }

        # Recheck the exact quote immediately before acceptance; never accept a
        # changed/worse/stale quote after the singles are filled.
        freshq = await _refresh_quote(client, best["id"])
        fresh_nb = _d(freshq.get("no_bid_dollars"))
        fresh_status = str(freshq.get("status") or "").lower()
        fresh_age = _iso_age_ms(freshq.get("updated_ts"))
        if fresh_status != "open" or fresh_nb is None or fresh_nb < best["no_bid"] or (fresh_age is not None and fresh_age > 2500):
            flats = await _flatten_filled(kexec, plans, hedge_results)
            return {"status": "QUOTE_CHANGED_ABORT", "rfq_id": rfq_id, "flatten": flats, "combo_quote_accepted": False, "hard_halt_required": False}

        accept = await client.request("PUT", f"/communications/quotes/{best['id']}/accept", json={"accepted_side": "no"})
        if accept.status_code >= 300:
            flats = await _flatten_filled(kexec, plans, hedge_results)
            return {"status": "QUOTE_ACCEPT_REJECTED", "http": accept.status_code, "rfq_id": rfq_id, "flatten": flats, "hard_halt_required": False}

        # HVM timing is <=3s maker confirmation + 1s execution timer. Allow
        # extra API propagation time, reconciling both quote state and position.
        deadline = time.monotonic() + 8.0
        last_q: dict[str, Any] = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            last_q = await _refresh_quote(client, best["id"])
            pos = await _position(client, ticker)
            if pos == ONE:
                return {
                    "status": "FULLY_HEDGED_FILLED", "rfq_id": rfq_id, "quote_id": best["id"],
                    "mve_position": str(pos), "hedges": [x.as_dict() for x in hedge_results],
                    "all_in_cost_conservative_at_send": str(all_in), "locked_profit_conservative_at_send": str(locked),
                    "guaranteed_scalar_safe_floor": "1.00", "combo_quote_accepted": True,
                    "classification": "FILLED_NOT_SETTLED", "realized_profit": None, "hard_halt_required": False,
                }
            qstatus = str(last_q.get("status") or "").lower()
            if qstatus in {"cancelled", "canceled", "expired", "rejected"} and pos == ZERO:
                flats = await _flatten_filled(kexec, plans, hedge_results)
                return {"status": "COMBO_NOT_EXECUTED_FLATTENED", "rfq_id": rfq_id, "quote_status": qstatus, "flatten": flats, "hard_halt_required": False}

        # Do not guess after an acknowledged quote acceptance. Keeping the q=1
        # singles is safer than flattening them if the combo may have executed
        # but position propagation is ambiguous.
        return {
            "status": "AMBIGUOUS_COMBO_AFTER_ACCEPT", "rfq_id": rfq_id, "quote_id": best["id"],
            "quote_status": last_q.get("status"), "hedges": [x.as_dict() for x in hedge_results],
            "combo_quote_accepted": True, "hard_halt_required": True, "manual_reconciliation_required": True,
        }
    finally:
        # Cancel an unaccepted/open RFQ. After acceptance, Kalshi owns the HVM
        # lifecycle and deletion may be invalid; failure here is harmless.
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
