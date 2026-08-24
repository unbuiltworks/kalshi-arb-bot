"""Production combo supervisor v36: complete 2x2 MVE partition shadow scanner.

For two fixed binary component markets A and B, the four conjunctions
A/B = YY, YN, NY, NN are mutually exclusive and exhaustive when both component
markets resolve normally. Buying one YES contract on every conjunction therefore
pays exactly $1 in every normal outcome. This layer searches the already-created
custom-MVE replay pool for complete four-corner partitions, obtains fresh q=1 RFQ
quotes for all four corners, and reports conservative after-fee partition cost.

This is SHADOW ONLY. It does not create markets, accept quotes, submit orders, or
change the existing live q=1 executor. A future live path must independently prove
MVE/component void/cancel settlement preserves exhaustiveness, revalidate all four
quotes/depth atomically, reserve capital for every leg, and enforce a profit floor.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v35 as v35
from src import sports_kp_live_combo_supervisor_v33 as v33
from src import sports_kp_mve_same_venue_scan as scan
from src.auth import KalshiClient

D = Decimal
ONE = D("1")
logger = v35.logger


def _corner_key(legs: list[dict[str, Any]]) -> tuple[tuple[str, str], tuple[str, str]] | None:
    """Return fixed component pair + side corner for a clean two-leg binary MVE."""
    if len(legs) != 2:
        return None
    norm: list[tuple[str, str]] = []
    for leg in legs:
        mt = str(leg.get("market_ticker") or "")
        side = str(leg.get("side") or "").lower()
        if not mt or side not in {"yes", "no"}:
            return None
        norm.append((mt, side))
    norm.sort(key=lambda x: x[0])
    if norm[0][0] == norm[1][0]:
        return None
    return ((norm[0][0], norm[1][0]), (norm[0][1], norm[1][1]))


async def _fresh_combo_yes(client: KalshiClient, ticker: str) -> dict[str, Any] | None:
    """Fresh q=1 combo YES equivalent from an RFQ; no order entry."""
    rid, quotes = await scan._quotes_for(client, ticker)
    try:
        best: dict[str, Any] | None = None
        for q in quotes:
            if str(q.get("status") or "").lower() not in {"", "open"}:
                continue
            nb = scan._d(q.get("no_bid_dollars"))
            if nb is None or not (D("0") < nb < ONE):
                continue
            px = ONE - nb
            row = {
                "ticker": ticker,
                "quote_id": str(q.get("id") or ""),
                "price": px,
                "fee": scan._fee(px),
                "updated_ts": q.get("updated_ts"),
            }
            if row["quote_id"] and (best is None or px < best["price"]):
                best = row
        return best
    finally:
        if rid:
            try:
                await client.delete(f"/communications/rfqs/{rid}")
            except Exception:
                pass


async def _partition_shadow_probe() -> None:
    client = KalshiClient(base_url=scan.BASE)
    groups: dict[tuple[str, str], dict[tuple[str, str], str]] = defaultdict(dict)
    market_meta: dict[str, dict[str, Any]] = {}
    try:
        for ticker in sorted(v33._REPLAY):
            try:
                r = await client.get(f"/markets/{ticker}")
                if r.status_code != 200:
                    continue
                m = scan._market(r.json() if r.content else {})
                if str(m.get("status") or "").lower() not in {"active", "open"}:
                    continue
                legs = [dict(x) for x in (m.get("mve_selected_legs") or []) if isinstance(x, dict)]
                ck = _corner_key(legs)
                if ck is None:
                    continue
                pair, corner = ck
                groups[pair][corner] = ticker
                market_meta[ticker] = {"title": m.get("title") or m.get("yes_sub_title"), "legs": legs}
            except Exception:
                continue

        required = {("yes", "yes"), ("yes", "no"), ("no", "yes"), ("no", "no")}
        complete = [(pair, corners) for pair, corners in groups.items() if required.issubset(corners)]
        results: list[dict[str, Any]] = []
        for pair, corners in complete[:4]:
            quotes: list[dict[str, Any]] = []
            failed = False
            for corner in sorted(required):
                q = await _fresh_combo_yes(client, corners[corner])
                if q is None:
                    failed = True
                    break
                quotes.append({
                    "corner": "/".join(corner),
                    "ticker": q["ticker"],
                    "price": str(q["price"]),
                    "fee": str(q["fee"]),
                    "updated_ts": q["updated_ts"],
                })
            if failed or len(quotes) != 4:
                continue
            total = sum((D(q["price"]) + D(q["fee"]) for q in quotes), D("0"))
            locked = ONE - total
            results.append({
                "component_markets": list(pair),
                "corners": quotes,
                "all_in_partition_cost": str(total),
                "locked_profit": str(locked),
                "classification": "PARTITION_SHADOW_EXECUTABLE" if locked >= D("0.03") else "PARTITION_SHADOW_NO_TRADE",
                "normal_outcome_payout_floor": "1.00",
                "void_cancel_rules_certified": False,
                "order_entry": False,
            })
        results.sort(key=lambda r: D(r["locked_profit"]), reverse=True)
        logger.warning("SPORTS_MVE_V36_PARTITION_SHADOW %s", json.dumps({
            "replay_pool_size": len(v33._REPLAY),
            "two_leg_component_pairs_seen": len(groups),
            "complete_four_corner_partitions": len(complete),
            "partitions_fresh_scored": len(results),
            "shadow_executable": sum(r["classification"] == "PARTITION_SHADOW_EXECUTABLE" for r in results),
            "best": results[0] if results else None,
            "math_identity": "YY+YN+NY+NN=1 for two normally-resolving binary components",
            "void_cancel_rule_gate": "REQUIRED_BEFORE_LIVE",
            "market_creation": False,
            "order_entry": False,
        }, separators=(",", ":"), default=str))
    except Exception as exc:
        logger.warning("SPORTS_MVE_V36_PARTITION_SHADOW_ERROR %s", json.dumps({
            "error": type(exc).__name__, "detail": str(exc)[:180], "order_entry": False,
        }, separators=(",", ":")))
    finally:
        await client.close()


async def run() -> None:
    await _partition_shadow_probe()
    await v35.run()


def main() -> None:
    import asyncio
    import logging
    import os
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
