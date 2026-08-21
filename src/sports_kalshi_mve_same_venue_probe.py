"""Read-only q=1 same-Kalshi MVE synthetic-arbitrage probe.

For one exact Kalshi MVE, request a fresh market-maker RFQ and use the RFQ's
authoritative ``mve_selected_legs`` to price the state-covering portfolio:

    long MVE conjunction + long complement of every underlying selected leg

This remains state-covering even when an underlying settles to a scalar value.
If selected-leg settlement values are x_i in [0,1], the MVE pays product(x_i)
and each opposite single pays (1-x_i), so total payout is
product(x_i) + sum(1-x_i) >= 1.  This follows directly from the MVE product
settlement rule and uses the exact component market tickers returned by Kalshi.

This module is PRE-TRADE ONLY.  It creates an RFQ (which risks no capital),
reads quotes and market metadata, and never accepts a quote or submits an order.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_CEILING
import json
import os
from typing import Any

from src.auth import KalshiClient

BASE = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_MVE = "KXMVECROSSCATEGORY-SHARD1-S20268C8F3EDA15F-692042E5C77"
D = Decimal
ONE = D("1")
CENT = D("0.01")


def _d(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return D(str(value))
    except Exception:
        return None


def _ceil_cent(value: Decimal) -> Decimal:
    # Deliberately conservative for q=1 validation. Kalshi's published q=1 fee
    # table displays whole-cent fees, so whole-cent ceiling avoids declaring an
    # arb that depends on more favorable sub-cent accounting.
    return (value / CENT).to_integral_value(rounding=ROUND_CEILING) * CENT


def _taker_fee(price: Decimal, *, multiplier: Decimal = ONE) -> Decimal:
    if price <= 0 or price >= 1:
        return D("0")
    return _ceil_cent(multiplier * D("0.07") * price * (ONE - price))


def _market(body: Any) -> dict[str, Any]:
    if isinstance(body, dict) and isinstance(body.get("market"), dict):
        return dict(body["market"])
    return dict(body) if isinstance(body, dict) else {}


def _single_buy_cost(market: dict[str, Any], wanted: str) -> tuple[Decimal | None, Decimal | None]:
    wanted = wanted.lower()
    if wanted == "yes":
        px = _d(market.get("yes_ask_dollars"))
        depth = _d(market.get("yes_ask_size_fp") or market.get("yes_ask_size"))
    elif wanted == "no":
        px = _d(market.get("no_ask_dollars"))
        depth = _d(market.get("no_ask_size_fp") or market.get("no_ask_size"))
    else:
        return None, None
    return px, depth


async def main_async() -> dict[str, Any]:
    ticker = os.getenv("KALSHI_MVE_PROBE_TICKER", DEFAULT_MVE).strip() or DEFAULT_MVE
    client = KalshiClient(base_url=BASE)
    try:
        mresp = await client.get(f"/markets/{ticker}")
        mbody = mresp.json() if mresp.content else {}
        mve_market = _market(mbody)

        create = await client.post(
            "/communications/rfqs",
            json={
                "market_ticker": ticker,
                "contracts": 1,
                "rest_remainder": False,
                "replace_existing": True,
            },
        )
        create_body = create.json() if create.content else {}
        rfq_id = ""
        if isinstance(create_body, dict):
            rfq_id = str(create_body.get("id") or (create_body.get("rfq") or {}).get("id") or "")

        rfq: dict[str, Any] = {}
        quotes: list[dict[str, Any]] = []
        if create.status_code < 300 and rfq_id:
            rresp = await client.get(f"/communications/rfqs/{rfq_id}")
            if rresp.status_code == 200:
                rb = rresp.json() if rresp.content else {}
                rfq = dict(rb.get("rfq") or {}) if isinstance(rb, dict) else {}
            for _ in range(20):
                await asyncio.sleep(0.25)
                qresp = await client.get(
                    "/communications/quotes",
                    params={"rfq_id": rfq_id, "rfq_user_filter": "self", "limit": 100},
                )
                if qresp.status_code != 200:
                    continue
                qb = qresp.json() if qresp.content else {}
                rows = qb.get("quotes") or [] if isinstance(qb, dict) else []
                quotes = [dict(x) for x in rows if isinstance(x, dict)]
                if quotes:
                    await asyncio.sleep(0.5)
                    qresp2 = await client.get(
                        "/communications/quotes",
                        params={"rfq_id": rfq_id, "rfq_user_filter": "self", "limit": 100},
                    )
                    if qresp2.status_code == 200:
                        qb2 = qresp2.json() if qresp2.content else {}
                        rows2 = qb2.get("quotes") or [] if isinstance(qb2, dict) else []
                        quotes = [dict(x) for x in rows2 if isinstance(x, dict)]
                    break

        legs = rfq.get("mve_selected_legs") or mve_market.get("mve_selected_legs") or []
        legs = [dict(x) for x in legs if isinstance(x, dict)]
        single_rows: list[dict[str, Any]] = []
        all_singles_tradeable = True
        for leg in legs:
            st = str(leg.get("market_ticker") or "")
            selected = str(leg.get("side") or "").lower()
            complement = "no" if selected == "yes" else "yes" if selected == "no" else ""
            sresp = await client.get(f"/markets/{st}") if st else None
            sm = _market(sresp.json() if sresp is not None and sresp.content else {}) if sresp is not None else {}
            px, depth = _single_buy_cost(sm, complement)
            fee = _taker_fee(px) if px is not None else None
            ok = bool(
                st and complement and px is not None and D("0") < px < ONE
                and depth is not None and depth >= ONE
                and str(sm.get("status") or "").lower() in {"active", "open"}
            )
            all_singles_tradeable = all_singles_tradeable and ok
            single_rows.append({
                "market_ticker": st,
                "selected_side": selected,
                "hedge_side": complement,
                "status": sm.get("status"),
                "hedge_ask_dollars": None if px is None else str(px),
                "hedge_depth_fp": None if depth is None else str(depth),
                "conservative_taker_fee": None if fee is None else str(fee),
                "tradeable_q1": ok,
            })

        # A quote maker's NO bid lets the RFQ requester sell NO, which is the
        # same directional exposure as buying YES. Effective YES cost = 1-NO bid.
        # No quote is accepted here.
        quote_options: list[dict[str, Any]] = []
        for q in quotes:
            if str(q.get("status") or "").lower() not in {"open", ""}:
                continue
            nb = _d(q.get("no_bid_dollars"))
            if nb is None or nb <= 0 or nb >= 1:
                continue
            combo_yes = ONE - nb
            quote_options.append({
                "quote_id": q.get("id"),
                "no_bid_dollars": str(nb),
                "requester_buy_yes_equivalent": str(combo_yes),
                "combo_conservative_taker_fee": str(_taker_fee(combo_yes)),
                "status": q.get("status"),
                "updated_ts": q.get("updated_ts"),
            })
        quote_options.sort(key=lambda x: D(x["requester_buy_yes_equivalent"]))

        best = quote_options[0] if quote_options else None
        raw_cost = fees = all_in = locked = None
        if best and len(single_rows) in {2, 3} and all_singles_tradeable:
            combo_cost = D(best["requester_buy_yes_equivalent"])
            single_prices = [D(x["hedge_ask_dollars"]) for x in single_rows]
            single_fees = [D(x["conservative_taker_fee"]) for x in single_rows]
            raw_cost = combo_cost + sum(single_prices, D("0"))
            fees = D(best["combo_conservative_taker_fee"]) + sum(single_fees, D("0"))
            all_in = raw_cost + fees
            locked = ONE - all_in

        executable = bool(
            locked is not None and locked >= D("0.02")
            and len(legs) in {2, 3}
            and all_singles_tradeable
            and str(mve_market.get("status") or "").lower() in {"active", "open"}
        )
        return {
            "status": "KALSHI_MVE_SAME_VENUE_Q1_PROBE",
            "market_ticker": ticker,
            "market_status": mve_market.get("status"),
            "market_title": mve_market.get("title") or mve_market.get("yes_sub_title"),
            "rfq_http_status": create.status_code,
            "rfq_id": rfq_id or None,
            "authoritative_leg_count": len(legs),
            "mve_selected_legs": legs,
            "single_hedges": single_rows,
            "quote_count": len(quotes),
            "best_quote": best,
            "raw_portfolio_cost": None if raw_cost is None else str(raw_cost),
            "conservative_fees": None if fees is None else str(fees),
            "all_in_cost": None if all_in is None else str(all_in),
            "guaranteed_scalar_safe_floor": "1.00" if len(legs) in {2, 3} else None,
            "locked_profit_conservative": None if locked is None else str(locked),
            "threshold": "0.02",
            "classification": "EXECUTABLE_CANDIDATE" if executable else "NO_TRADE",
            "coverage_identity": "product(x_i)+sum(1-x_i)>=1 for x_i in [0,1]",
            "quote_acceptance": False,
            "order_entry": False,
            "funds_at_risk": False,
        }
    finally:
        await client.close()


def main() -> None:
    print(json.dumps(asyncio.run(main_async()), separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    main()
