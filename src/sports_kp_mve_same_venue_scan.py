"""Bounded read-only scanner for same-Kalshi MVE synthetic arbitrage.

For current active 2-5 leg MVE markets, this scanner uses authoritative
``mve_selected_legs`` and prices the complement of every selected single leg.
It requests q=1 RFQs only for a bounded shortlist, observes maker quotes, then
deletes the RFQs. It NEVER accepts a quote and NEVER submits an order.

Coverage identity for selected-leg settlement values x_i in [0,1]:
    product(x_i) + sum(1 - x_i) >= 1.
Thus long MVE + long complement of each selected single has a $1 minimum
settlement floor even if a component settles scalar. The identity holds for
all n >= 2; this production scanner intentionally caps n at 5 to bound hedge
count, fees, latency, and temporary execution exposure.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import json
import os
from typing import Any

from src.auth import KalshiClient

BASE = "https://external-api.kalshi.com/trade-api/v2"
D = Decimal
ONE = D("1")
CENT = D("0.01")
SUPPORTED_LEG_COUNTS = {2, 3, 4, 5}
SPORT_HINTS = (
    "NFL", "MLB", "NBA", "WNBA", "NHL", "NCAAF", "NCAAB", "ATP", "WTA",
    "PGA", "UFC", "SOCCER", "GAME", "FOOTBALL", "BASEBALL", "TENNIS",
    "HITS", "HOMER", "TOTALBASE", "STRIKEOUT", "PASS", "RUSH", "RECEIV",
)


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
        return D("0")
    return _ceil_cent(D("0.07") * p * (ONE - p))


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _market(body: Any) -> dict[str, Any]:
    if isinstance(body, dict) and isinstance(body.get("market"), dict):
        return dict(body["market"])
    return dict(body) if isinstance(body, dict) else {}


def _hedge_quote(m: dict[str, Any], selected: str) -> dict[str, Any]:
    selected = selected.lower()
    hedge = "no" if selected == "yes" else "yes" if selected == "no" else ""
    if hedge == "yes":
        px = _d(m.get("yes_ask_dollars")); depth = _d(m.get("yes_ask_size_fp") or m.get("yes_ask_size"))
    elif hedge == "no":
        px = _d(m.get("no_ask_dollars")); depth = _d(m.get("no_ask_size_fp") or m.get("no_ask_size"))
    else:
        px = depth = None
    ok = bool(px is not None and D("0") < px < ONE and depth is not None and depth >= ONE and str(m.get("status") or "").lower() in {"active", "open"})
    return {
        "hedge_side": hedge,
        "price": px,
        "depth": depth,
        "fee": _fee(px) if px is not None else None,
        "ok": ok,
    }


def _sportsish(legs: list[dict[str, Any]]) -> bool:
    text = " ".join(str(x.get("market_ticker") or "") + " " + str(x.get("event_ticker") or "") for x in legs).upper()
    return any(h in text for h in SPORT_HINTS)


async def _quotes_for(client: KalshiClient, ticker: str, wait_s: float = 1.75) -> tuple[str | None, list[dict[str, Any]]]:
    resp = await client.post("/communications/rfqs", json={
        "market_ticker": ticker, "contracts": 1, "rest_remainder": False, "replace_existing": True,
    })
    if resp.status_code >= 300:
        return None, []
    body = resp.json() if resp.content else {}
    rfq_id = str(body.get("id") or (body.get("rfq") or {}).get("id") or "") if isinstance(body, dict) else ""
    if not rfq_id:
        return None, []
    deadline = asyncio.get_running_loop().time() + wait_s
    quotes: list[dict[str, Any]] = []
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.25)
        q = await client.get("/communications/quotes", params={"rfq_id": rfq_id, "rfq_user_filter": "self", "limit": 100})
        if q.status_code == 200:
            qb = q.json() if q.content else {}
            rows = qb.get("quotes") or [] if isinstance(qb, dict) else []
            quotes = [dict(x) for x in rows if isinstance(x, dict)]
    return rfq_id, quotes


async def main_async() -> dict[str, Any]:
    max_rfqs = max(1, min(int(os.getenv("MVE_SCAN_MAX_RFQS", "12")), 30))
    horizon_h = max(1, min(int(os.getenv("MVE_SCAN_HORIZON_HOURS", "24")), 48))
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=horizon_h)
    client = KalshiClient(base_url=BASE)
    open_rfqs: list[str] = []
    try:
        raw: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(8):
            params: dict[str, Any] = {"limit": 200, "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            r = await client.get("/events/multivariate", params=params)
            if r.status_code != 200:
                break
            body = r.json() if r.content else {}
            for event in body.get("events") or []:
                if not isinstance(event, dict):
                    continue
                for m in event.get("markets") or []:
                    if not isinstance(m, dict):
                        continue
                    legs = [dict(x) for x in (m.get("mve_selected_legs") or []) if isinstance(x, dict)]
                    if len(legs) not in SUPPORTED_LEG_COUNTS or not _sportsish(legs):
                        continue
                    close = _parse_dt(m.get("close_time") or m.get("expected_expiration_time"))
                    status = str(m.get("status") or "").lower()
                    if status not in {"active", "open"} or close is None or close < now or close > horizon:
                        continue
                    raw.append({
                        "ticker": str(m.get("ticker") or ""),
                        "title": m.get("title") or m.get("yes_sub_title") or event.get("title"),
                        "close_time": close.isoformat(),
                        "legs": legs,
                    })
            cursor = str(body.get("cursor") or "") if isinstance(body, dict) else ""
            if not cursor:
                break

        by_ticker = {x["ticker"]: x for x in raw if x["ticker"]}
        candidates = sorted(by_ticker.values(), key=lambda x: (x["close_time"], len(x["legs"])))
        prequalified: list[dict[str, Any]] = []
        for c in candidates[:500]:
            hedge_rows: list[dict[str, Any]] = []
            ok = True
            for leg in c["legs"]:
                st = str(leg.get("market_ticker") or "")
                selected = str(leg.get("side") or "").lower()
                sr = await client.get(f"/markets/{st}") if st else None
                sm = _market(sr.json() if sr is not None and sr.content else {}) if sr is not None else {}
                h = _hedge_quote(sm, selected)
                hrow = {
                    "market_ticker": st,
                    "selected_side": selected,
                    "hedge_side": h["hedge_side"],
                    "hedge_ask_dollars": None if h["price"] is None else str(h["price"]),
                    "hedge_depth_fp": None if h["depth"] is None else str(h["depth"]),
                    "conservative_taker_fee": None if h["fee"] is None else str(h["fee"]),
                    "status": sm.get("status"),
                }
                hedge_rows.append(hrow)
                ok = ok and bool(h["ok"])
            if not ok:
                continue
            hedge_raw = sum((D(x["hedge_ask_dollars"]) for x in hedge_rows), D("0"))
            hedge_fees = sum((D(x["conservative_taker_fee"]) for x in hedge_rows), D("0"))
            if hedge_raw + hedge_fees >= D("0.98"):
                continue
            prequalified.append({**c, "single_hedges": hedge_rows, "single_cost_with_fees": str(hedge_raw + hedge_fees)})
            if len(prequalified) >= max_rfqs:
                break

        scored: list[dict[str, Any]] = []
        for c in prequalified:
            rfq_id, quotes = await _quotes_for(client, c["ticker"])
            if rfq_id:
                open_rfqs.append(rfq_id)
            best: dict[str, Any] | None = None
            for q in quotes:
                if str(q.get("status") or "").lower() not in {"", "open"}:
                    continue
                nb = _d(q.get("no_bid_dollars"))
                if nb is None or not (D("0") < nb < ONE):
                    continue
                combo_cost = ONE - nb
                row = {
                    "quote_id": q.get("id"),
                    "no_bid_dollars": str(nb),
                    "combo_yes_equivalent": str(combo_cost),
                    "combo_fee_conservative": str(_fee(combo_cost)),
                    "updated_ts": q.get("updated_ts"),
                }
                if best is None or D(row["combo_yes_equivalent"]) < D(best["combo_yes_equivalent"]):
                    best = row
            if best is None:
                scored.append({**c, "rfq_id": rfq_id, "quote_count": len(quotes), "classification": "NO_QUOTE"})
                continue
            singles = D(c["single_cost_with_fees"])
            combo_all = D(best["combo_yes_equivalent"]) + D(best["combo_fee_conservative"])
            all_in = singles + combo_all
            locked = ONE - all_in
            scored.append({
                **c,
                "rfq_id": rfq_id,
                "quote_count": len(quotes),
                "best_quote": best,
                "all_in_cost_conservative": str(all_in),
                "locked_profit_conservative": str(locked),
                "classification": "EXECUTABLE_CANDIDATE" if locked >= D("0.02") else "NO_TRADE",
            })

        scored.sort(key=lambda x: D(x.get("locked_profit_conservative") or "-9"), reverse=True)
        return {
            "status": "KALSHI_MVE_SAME_VENUE_SCAN",
            "observed_at": now.isoformat(),
            "horizon_hours": horizon_h,
            "supported_leg_counts": sorted(SUPPORTED_LEG_COUNTS),
            "active_short_duration_mves": len(candidates),
            "active_by_leg_count": {str(n): sum(len(x["legs"]) == n for x in candidates) for n in sorted(SUPPORTED_LEG_COUNTS)},
            "prequalified_for_rfq": len(prequalified),
            "rfqs_requested": len([x for x in scored if x.get("rfq_id")]),
            "executable_candidates": sum(x.get("classification") == "EXECUTABLE_CANDIDATE" for x in scored),
            "ranked": scored[:max_rfqs],
            "threshold": "0.02",
            "coverage_identity": "product(x_i)+sum(1-x_i)>=1 for x_i in [0,1], n>=2",
            "quote_acceptance": False,
            "order_entry": False,
            "funds_at_risk": False,
        }
    finally:
        for rid in open_rfqs:
            try:
                await client.delete(f"/communications/rfqs/{rid}")
            except Exception:
                pass
        await client.close()


def main() -> None:
    print(json.dumps(asyncio.run(main_async()), separators=(",", ":"), default=str), flush=True)


if __name__ == "__main__":
    main()
