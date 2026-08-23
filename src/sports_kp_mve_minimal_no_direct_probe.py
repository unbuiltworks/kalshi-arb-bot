"""Read-only direct-ticker probe for the two-contract minimal MVE NO Dutch cover.

Kalshi's /events/multivariate catalog currently surfaces historical finalized MVEs
before newly-created active markets.  This probe bypasses that catalog for a small
explicit ticker set, hydrates every MVE and selected component directly, then
requests q=1 RFQs only when the MVE has no public q=1 NO ask.

Identity for any conjunction MVE and any selected component x_k:
    combo_NO + x_k = 1 - product(x_i) + x_k >= 1.

No market creation, quote acceptance, or order entry occurs here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from typing import Any, Mapping

from src.auth import KalshiClient
from src import sports_kp_mve_minimal_no_scan as minimal
from src import sports_kp_mve_same_venue_scan as scan

D = Decimal
DEFAULT_TICKERS = (
    "KXMVECROSSCATEGORY-SHARD1-S20269F2F151F69F-5126CAD8562",
    "KXMVECROSSCATEGORY-SHARD1-S2026037922EEFBB-AA8CCC11F8D",
    "KXMVECROSSCATEGORY-SHARD1-S2026037922EEFBB-5170A04850F",
    "KXMVECROSSCATEGORY-SHARD1-S202664035525BD0-BCCC0BFAE90",
    "KXMVECROSSCATEGORY-SHARD1-S202672CA07EE435-054D7EDE6EB",
    "KXMVECROSSCATEGORY-SHARD1-S202672CA07EE435-21862864C3D",
    "KXMVECROSSCATEGORY-SHARD1-S202672CA07EE435-A7CD72EFDFB",
    "KXMVECROSSCATEGORY-SHARD1-S202672CA07EE435-B818CFD4B99",
)


def _configured_tickers() -> list[str]:
    raw = os.getenv("MVE_MINIMAL_NO_DIRECT_TICKERS", "").strip()
    values = [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()] if raw else list(DEFAULT_TICKERS)
    out: list[str] = []
    seen: set[str] = set()
    for ticker in values:
        if ticker not in seen:
            out.append(ticker)
            seen.add(ticker)
        if len(out) >= 16:
            break
    return out


async def run_once() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=max(1, min(int(os.getenv("MVE_SCAN_HORIZON_HOURS", "24")), 48)))
    tickers = _configured_tickers()
    client = KalshiClient(base_url=scan.BASE)
    market_cache: dict[str, dict[str, Any]] = {}
    open_rfqs: list[str] = []
    rows: list[dict[str, Any]] = []
    try:
        async def get_market(ticker: str) -> dict[str, Any]:
            if ticker in market_cache:
                return market_cache[ticker]
            r = await client.get(f"/markets/{ticker}")
            m = scan._market(r.json() if r.status_code == 200 and r.content else {})
            market_cache[ticker] = m
            return m

        candidates: list[dict[str, Any]] = []
        for ticker in tickers:
            m = await get_market(ticker)
            legs = [dict(x) for x in (m.get("mve_selected_legs") or []) if isinstance(x, Mapping)]
            close = scan._parse_dt(
                m.get("expected_expiration_time")
                or m.get("expiration_time")
                or m.get("close_time")
            )
            status = str(m.get("status") or "").lower()
            if (
                status not in {"active", "open"}
                or len(legs) not in scan.SUPPORTED_LEG_COUNTS
                or not scan._sportsish(legs)
                or close is None
                or close <= now
                or close > horizon
            ):
                rows.append({
                    "ticker": ticker,
                    "classification": "DIRECT_NOT_ELIGIBLE",
                    "status": status,
                    "leg_count": len(legs),
                    "expected_expiration": None if close is None else close.isoformat(),
                    "order_entry": False,
                })
                continue

            best_component: dict[str, Any] | None = None
            for leg in legs:
                component_ticker = str(leg.get("market_ticker") or "")
                side = str(leg.get("side") or "").lower()
                if not component_ticker:
                    continue
                cm = await get_market(component_ticker)
                quote = minimal._selected_quote(cm, side)
                if quote is None:
                    continue
                candidate = {"market_ticker": component_ticker, "selected_side": side, **quote}
                if best_component is None or D(str(candidate["all_in"])) < D(str(best_component["all_in"])):
                    best_component = candidate
            if best_component is None:
                rows.append({"ticker": ticker, "classification": "NO_Q1_SELECTED_COMPONENT", "order_entry": False})
                continue
            candidates.append({
                "ticker": ticker,
                "title": m.get("title") or m.get("yes_sub_title"),
                "close": close,
                "leg_count": len(legs),
                "market": m,
                "best_component": best_component,
            })

        for row in candidates:
            combo_no = minimal._public_combo_no(row["market"])
            source = "PUBLIC_BOOK"
            quote_stats = None
            if combo_no is None:
                rid, quotes = await scan._quotes_for(client, row["ticker"], wait_s=1.5)
                if rid:
                    open_rfqs.append(rid)
                combo_no, quote_stats = minimal._best_rfq_combo_no(quotes)
                source = "RFQ"
            if combo_no is None:
                rows.append({
                    "ticker": row["ticker"],
                    "classification": "NO_Q1_COMBO_NO_QUOTE",
                    "quote_source": source,
                    "quote_stats": quote_stats,
                    "order_entry": False,
                })
                continue
            scored = minimal._score(
                ticker=row["ticker"],
                title=row["title"],
                close_time=row["close"].isoformat(),
                leg_count=row["leg_count"],
                component=row["best_component"],
                combo_no=combo_no,
                price_source=source,
            )
            if quote_stats is not None:
                scored["quote_stats"] = quote_stats
            rows.append(scored)

        scored_rows = [r for r in rows if "locked_profit_conservative" in r]
        scored_rows.sort(key=lambda x: D(str(x["locked_profit_conservative"])), reverse=True)
        executable = [r for r in scored_rows if r.get("classification") == "SHADOW_EXECUTABLE_MINIMAL_NO_COVER"]
        out = {
            "status": "SPORTS_MVE_MINIMAL_NO_DIRECT_V1",
            "observed_at": now.isoformat(),
            "tickers_requested": len(tickers),
            "eligible_direct_mves": len(candidates),
            "scored_covers": len(scored_rows),
            "shadow_executable": len(executable),
            "best": scored_rows[0] if scored_rows else None,
            "target_locked": "0.03",
            "identity": "combo_NO+x_k>=1",
            "catalog_bypassed": True,
            "market_creation": False,
            "quote_acceptance": False,
            "order_entry": False,
            "realized": False,
            "rows": rows,
        }
        print("SPORTS_MVE_MINIMAL_NO_DIRECT " + json.dumps(out, separators=(",", ":"), default=str), flush=True)
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
