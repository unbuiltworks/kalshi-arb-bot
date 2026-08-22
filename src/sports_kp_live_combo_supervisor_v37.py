"""Production combo supervisor v37: same-market YES+NO Dutch-book shadow scan.

Scans the same open Kalshi sports component markets used by the MVE manufacturer
and tests the simplest exact-partition identity on one binary market:

    YES + NO = $1 at settlement.

For q=1, a candidate is shadow-executable only when current YES ask + NO ask +
conservative taker fees on both sides leaves >= $0.03 locked profit. Because both
legs are the exact same Kalshi market, there is no cross-venue rule-equivalence
problem. This layer is read-only: no order entry, no market creation, no RFQ.

Kalshi market-list responses may omit explicit ask fields. In that case the
executable ask is derived from the opposite best bid using binary complement
book semantics: YES ask = 1 - NO bid, NO ask = 1 - YES bid; corresponding ask
depth is the opposite bid depth.
"""
from __future__ import annotations

from decimal import Decimal
import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v36 as v36
from src import sports_kp_mve_combo_generator as g
from src.auth import KalshiClient

D = Decimal
ONE = D("1")
logger = v36.logger


def _d(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:
        return None


def _ask_and_depth(m: dict[str, Any], side: str) -> tuple[Decimal | None, Decimal | None, str]:
    side = side.lower()
    if side == "yes":
        ask = _d(m.get("yes_ask_dollars"))
        depth = _d(m.get("yes_ask_size_fp") or m.get("yes_ask_size"))
        if ask is not None and depth is not None:
            return ask, depth, "explicit_ask"
        opp_bid = _d(m.get("no_bid_dollars"))
        opp_depth = _d(m.get("no_bid_size_fp") or m.get("no_bid_size"))
        if opp_bid is not None and opp_depth is not None:
            return ONE - opp_bid, opp_depth, "derived_from_no_bid"
    else:
        ask = _d(m.get("no_ask_dollars"))
        depth = _d(m.get("no_ask_size_fp") or m.get("no_ask_size"))
        if ask is not None and depth is not None:
            return ask, depth, "explicit_ask"
        opp_bid = _d(m.get("yes_bid_dollars"))
        opp_depth = _d(m.get("yes_bid_size_fp") or m.get("yes_bid_size"))
        if opp_bid is not None and opp_depth is not None:
            return ONE - opp_bid, opp_depth, "derived_from_yes_bid"
    return None, None, "missing"


async def _same_market_probe() -> None:
    client = KalshiClient(base_url=g.BASE)
    rows: list[dict[str, Any]] = []
    events_scanned = 0
    markets_scanned = 0
    seen_events: set[str] = set()
    try:
        collections = [c for c in await g._open_collections(client)
                       if g._sportsish(c.get("collection_ticker"), c.get("title"), c.get("description"), c.get("series_ticker"))]
        for c in collections:
            associated = [dict(x) for x in (c.get("associated_events") or []) if isinstance(x, dict)]
            if not associated:
                associated = [{"ticker": x} for x in (c.get("associated_event_tickers") or [])]
            for erow in associated:
                if events_scanned >= 48:
                    break
                event = str(erow.get("ticker") or "")
                if not event or event in seen_events:
                    continue
                seen_events.add(event)
                events_scanned += 1
                try:
                    markets = await g._event_markets(client, event)
                except Exception:
                    continue
                for m in markets:
                    markets_scanned += 1
                    if str(m.get("status") or "").lower() not in {"active", "open"}:
                        continue
                    ticker = str(m.get("ticker") or "")
                    ya, yd, ysrc = _ask_and_depth(m, "yes")
                    na, nd, nsrc = _ask_and_depth(m, "no")
                    if not ticker or ya is None or na is None or yd is None or nd is None:
                        continue
                    if not (D("0") < ya < ONE and D("0") < na < ONE) or yd < ONE or nd < ONE:
                        continue
                    yf = g._fee(ya)
                    nf = g._fee(na)
                    all_in = ya + na + yf + nf
                    locked = ONE - all_in
                    rows.append({
                        "ticker": ticker,
                        "event_ticker": event,
                        "title": m.get("title") or m.get("subtitle") or m.get("yes_sub_title"),
                        "yes_ask": str(ya),
                        "no_ask": str(na),
                        "yes_depth_fp": str(yd),
                        "no_depth_fp": str(nd),
                        "yes_price_source": ysrc,
                        "no_price_source": nsrc,
                        "yes_fee": str(yf),
                        "no_fee": str(nf),
                        "all_in_cost": str(all_in),
                        "locked_profit": str(locked),
                        "classification": "SAME_MARKET_SHADOW_EXECUTABLE" if locked >= D("0.03") else "SAME_MARKET_SHADOW_NO_TRADE",
                    })
            if events_scanned >= 48:
                break
        rows.sort(key=lambda r: D(r["locked_profit"]), reverse=True)
        logger.warning("SPORTS_SAME_MARKET_YES_NO_SHADOW %s", json.dumps({
            "events_scanned": events_scanned,
            "markets_scanned": markets_scanned,
            "priced_binary_markets": len(rows),
            "shadow_executable": sum(r["classification"] == "SAME_MARKET_SHADOW_EXECUTABLE" for r in rows),
            "best": rows[0] if rows else None,
            "identity": "YES+NO=1 on exact same binary Kalshi market",
            "live_threshold": "0.03",
            "market_creation": False,
            "rfq": False,
            "order_entry": False,
        }, separators=(",", ":"), default=str))
    except Exception as exc:
        logger.warning("SPORTS_SAME_MARKET_YES_NO_SHADOW_ERROR %s", json.dumps({
            "error": type(exc).__name__, "detail": str(exc)[:180], "order_entry": False,
        }, separators=(",", ":")))
    finally:
        await client.close()


async def run() -> None:
    await _same_market_probe()
    await v36.run()


def main() -> None:
    import asyncio
    import logging
    import os
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
