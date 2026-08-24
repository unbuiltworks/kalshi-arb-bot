"""Read-only exact-fee rescore for recent minimal-NO sports MVE Dutch covers.

Uses Kalshi's current quadratic fee semantics at centicent precision and live
series fee metadata.  MVE fees use the current published KXMVE schedule:
taker multiplier 1, maker multiplier 2.  No orders, quote acceptance, or market
creation occur here.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_CEILING
import json
from typing import Any, Mapping

from src.auth import KalshiClient
from src import sports_kp_mve_minimal_no_direct_probe as direct
from src import sports_kp_mve_minimal_no_scan as minimal
from src import sports_kp_mve_same_venue_scan as scan

D = Decimal
ONE = D("1")
CENTICENT = D("0.0001")
TAKER_RATE = D("0.07")
MAKER_RATE = D("0.0175")
TARGET = D("0.03")
MVE_TAKER_MULTIPLIER = D("1")
MVE_MAKER_MULTIPLIER = D("2")


def _ceil_centicent(value: Decimal) -> Decimal:
    return (value / CENTICENT).to_integral_value(rounding=ROUND_CEILING) * CENTICENT


def quadratic_fee(price: Decimal, multiplier: Decimal, *, maker: bool) -> Decimal:
    if price <= 0 or price >= 1 or multiplier <= 0:
        return D("0")
    rate = MAKER_RATE if maker else TAKER_RATE
    return _ceil_centicent(multiplier * rate * price * (ONE - price))


def _series_from_ticker(ticker: str) -> str:
    return str(ticker or "").split("-", 1)[0]


def _selected_bid(market: Mapping[str, Any], selected_side: str) -> Decimal | None:
    side = str(selected_side or "").lower()
    if side == "yes":
        return scan._d(market.get("yes_bid_dollars"))
    if side == "no":
        return scan._d(market.get("no_bid_dollars"))
    return None


def _mve_maker_target(component_price: Decimal, component_fee: Decimal) -> Decimal | None:
    """Highest combo-NO maker price that still leaves >=3c after maker fee."""
    best: Decimal | None = None
    # MVE price grid observed at .001 in RFQ quotes; scan conservatively at .001.
    p = D("0.001")
    while p < ONE:
        total = component_price + component_fee + p + quadratic_fee(p, MVE_MAKER_MULTIPLIER, maker=True)
        if ONE - total >= TARGET:
            best = p
        p += D("0.001")
    return best


async def run_once() -> dict[str, Any]:
    client = KalshiClient(base_url=scan.BASE)
    open_rfqs: list[str] = []
    series_cache: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    try:
        async def series_meta(series: str) -> dict[str, Any]:
            if series in series_cache:
                return series_cache[series]
            r = await client.get(f"/series/{series}")
            body = r.json() if r.status_code == 200 and r.content else {}
            meta = body.get("series") if isinstance(body, Mapping) and isinstance(body.get("series"), Mapping) else body
            meta = dict(meta) if isinstance(meta, Mapping) else {}
            series_cache[series] = meta
            return meta

        for ticker in direct._configured_tickers():
            mr = await client.get(f"/markets/{ticker}")
            mve = scan._market(mr.json() if mr.status_code == 200 and mr.content else {})
            legs = [dict(x) for x in (mve.get("mve_selected_legs") or []) if isinstance(x, Mapping)]
            if str(mve.get("status") or "").lower() not in {"active", "open"} or not legs:
                continue

            rid, quotes = await scan._quotes_for(client, ticker, wait_s=1.5)
            if rid:
                open_rfqs.append(rid)
            combo_no, qstats = minimal._best_rfq_combo_no(quotes)
            if combo_no is None:
                continue
            combo_px = D(str(combo_no["price"]))
            combo_taker_fee = quadratic_fee(combo_px, MVE_TAKER_MULTIPLIER, maker=False)

            components: list[dict[str, Any]] = []
            for leg in legs:
                ct = str(leg.get("market_ticker") or "")
                side = str(leg.get("side") or "").lower()
                if not ct:
                    continue
                cr = await client.get(f"/markets/{ct}")
                cm = scan._market(cr.json() if cr.status_code == 200 and cr.content else {})
                askq = minimal._selected_quote(cm, side)
                if askq is None:
                    continue
                ask = D(str(askq["price"]))
                bid = _selected_bid(cm, side)
                series = _series_from_ticker(ct)
                meta = await series_meta(series)
                mult = D(str(meta.get("fee_multiplier") if meta.get("fee_multiplier") is not None else "1"))
                ftype = str(meta.get("fee_type") or "quadratic")
                taker_fee = quadratic_fee(ask, mult, maker=False)
                maker_fee_at_bid = None
                if bid is not None and D("0") < bid < ONE:
                    maker_mult = mult if ftype == "quadratic_with_maker_fees" else D("0")
                    maker_fee_at_bid = quadratic_fee(bid, maker_mult, maker=True)
                components.append({
                    "ticker": ct, "side": side, "series": series,
                    "fee_type": ftype, "fee_multiplier": str(mult),
                    "ask": ask, "bid": bid,
                    "taker_fee": taker_fee, "maker_fee_at_bid": maker_fee_at_bid,
                })

            if not components:
                continue
            # Minimal identity permits ANY selected leg; choose cheapest exact taker
            # and maker scenarios independently.
            taker_comp = min(components, key=lambda x: x["ask"] + x["taker_fee"])
            taker_total = taker_comp["ask"] + taker_comp["taker_fee"] + combo_px + combo_taker_fee
            maker_candidates = [x for x in components if x["bid"] is not None and x["maker_fee_at_bid"] is not None]
            maker_comp = min(maker_candidates, key=lambda x: x["bid"] + x["maker_fee_at_bid"]) if maker_candidates else None
            maker_component_total = None
            maker_component_locked = None
            mve_maker_target = None
            if maker_comp is not None:
                maker_component_total = maker_comp["bid"] + maker_comp["maker_fee_at_bid"] + combo_px + combo_taker_fee
                maker_component_locked = ONE - maker_component_total
                mve_maker_target = _mve_maker_target(maker_comp["bid"], maker_comp["maker_fee_at_bid"])

            rows.append({
                "ticker": ticker,
                "title": mve.get("title") or mve.get("yes_sub_title"),
                "combo_no_rfq": str(combo_px),
                "combo_taker_fee_exact": str(combo_taker_fee),
                "quote_stats": qstats,
                "best_taker_component": {k: (str(v) if isinstance(v, Decimal) else v) for k,v in taker_comp.items()},
                "exact_taker_all_in": str(taker_total),
                "exact_taker_locked": str(ONE - taker_total),
                "best_maker_component": None if maker_comp is None else {k: (str(v) if isinstance(v, Decimal) else v) for k,v in maker_comp.items()},
                "component_maker_plus_mve_taker_all_in": None if maker_component_total is None else str(maker_component_total),
                "component_maker_plus_mve_taker_locked": None if maker_component_locked is None else str(maker_component_locked),
                "max_mve_no_maker_price_for_3c_with_component_maker": None if mve_maker_target is None else str(mve_maker_target),
                "mve_rfq_minus_required_maker_price": None if mve_maker_target is None else str(combo_px - mve_maker_target),
                "classification": "EXACT_FEE_SHADOW_EXECUTABLE" if ONE - taker_total >= TARGET else "EXACT_FEE_SHADOW_NO_TRADE",
                "market_creation": False,
                "quote_acceptance": False,
                "order_entry": False,
            })

        rows.sort(key=lambda r: D(str(r["exact_taker_locked"])), reverse=True)
        out = {
            "status": "SPORTS_MVE_EXACT_FEE_PROBE_V1",
            "scored": len(rows),
            "shadow_executable": sum(r["classification"] == "EXACT_FEE_SHADOW_EXECUTABLE" for r in rows),
            "best": rows[0] if rows else None,
            "fee_rounding": "CENTICENT",
            "mve_taker_multiplier": str(MVE_TAKER_MULTIPLIER),
            "mve_maker_multiplier": str(MVE_MAKER_MULTIPLIER),
            "target_locked": str(TARGET),
            "market_creation": False,
            "quote_acceptance": False,
            "order_entry": False,
            "realized": False,
            "rows": rows,
        }
        print("SPORTS_MVE_EXACT_FEE_PROBE " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
        return out
    finally:
        for rid in open_rfqs:
            try:
                await client.delete(f"/communications/rfqs/{rid}")
            except Exception:
                pass
        await client.close()


if __name__ == "__main__":
    asyncio.run(run_once())
