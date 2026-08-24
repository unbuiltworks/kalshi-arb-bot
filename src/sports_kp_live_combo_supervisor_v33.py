"""Production combo supervisor v33: replay already-created custom MVE tickers.

Creation allowance is finite, but RFQ repricing of an existing custom ticker does
not require another MVE create. This layer augments the current v32/v31 stack with
a bounded replay pool seeded from MVE_REPLAY_TICKERS and automatically populated
from newly generated rows in-process.

Replay is read-only until the unchanged supervisor sees a >=3c conservative q=1
candidate and hands it to the existing fail-closed executor. No creation cap,
execution threshold, depth, freshness, capital, one-attempt, flatten, hard-halt,
or settlement semantics are weakened.
"""
from __future__ import annotations

from decimal import Decimal
import json
import os
from typing import Any

from src import sports_kp_live_combo_supervisor_v32 as v32
from src import sports_kp_live_combo_supervisor_v2 as base2
from src import sports_kp_mve_same_venue_scan as scan
from src.auth import KalshiClient

D = Decimal
ONE = D("1")
_REPLAY: set[str] = {x.strip() for x in os.getenv("MVE_REPLAY_TICKERS", "").split(",") if x.strip()}
_ORIGINAL_GENERATE = base2.generate_mves
_ORIGINAL_SCAN = base2.scan_existing_mves


async def _generate_with_replay_capture() -> dict[str, Any]:
    result = await _ORIGINAL_GENERATE()
    if isinstance(result, dict):
        for row in result.get("ranked") or []:
            if isinstance(row, dict) and row.get("ticker"):
                _REPLAY.add(str(row["ticker"]))
    return result


async def _score_replay_ticker(client: KalshiClient, ticker: str) -> dict[str, Any] | None:
    mr = await client.get(f"/markets/{ticker}")
    if mr.status_code != 200:
        return None
    m = scan._market(mr.json() if mr.content else {})
    if str(m.get("status") or "").lower() not in {"active", "open"}:
        return None
    legs = [dict(x) for x in (m.get("mve_selected_legs") or []) if isinstance(x, dict)]
    if len(legs) not in scan.SUPPORTED_LEG_COUNTS or not scan._sportsish(legs):
        return None

    hedge_rows: list[dict[str, Any]] = []
    hedge_all = D("0")
    for leg in legs:
        st = str(leg.get("market_ticker") or "")
        selected = str(leg.get("side") or "").lower()
        sr = await client.get(f"/markets/{st}") if st else None
        sm = scan._market(sr.json() if sr is not None and sr.content else {}) if sr is not None else {}
        h = scan._hedge_quote(sm, selected)
        if not h.get("ok"):
            return None
        px = D(str(h["price"])); fee = D(str(h["fee"])); depth = D(str(h["depth"]))
        hedge_all += px + fee
        hedge_rows.append({
            "market_ticker": st,
            "selected_side": selected,
            "hedge_side": h["hedge_side"],
            "hedge_ask_dollars": str(px),
            "hedge_depth_fp": str(depth),
            "conservative_taker_fee": str(fee),
        })
    if hedge_all >= D("0.99"):
        return None

    rfq_id, quotes = await scan._quotes_for(client, ticker)
    try:
        best: dict[str, Any] | None = None
        for q in quotes:
            if str(q.get("status") or "").lower() not in {"", "open"}:
                continue
            nb = scan._d(q.get("no_bid_dollars"))
            if nb is None or not (D("0") < nb < ONE):
                continue
            combo_cost = ONE - nb
            row = {
                "quote_id": str(q.get("id") or ""),
                "combo_yes_equivalent": str(combo_cost),
                "combo_fee_conservative": str(scan._fee(combo_cost)),
                "updated_ts": q.get("updated_ts"),
            }
            if row["quote_id"] and (best is None or D(row["combo_yes_equivalent"]) < D(best["combo_yes_equivalent"])):
                best = row
        if best is None:
            return {
                "ticker": ticker, "legs": legs, "generated": True, "replayed": True,
                "quote_count": len(quotes), "classification": "NO_QUOTE",
            }
        all_in = hedge_all + D(best["combo_yes_equivalent"]) + D(best["combo_fee_conservative"])
        locked = ONE - all_in
        return {
            "ticker": ticker,
            "title": m.get("title") or m.get("yes_sub_title"),
            "close_time": m.get("close_time") or m.get("expected_expiration_time"),
            "legs": legs,
            "single_hedges": hedge_rows,
            "single_cost_with_fees": str(hedge_all),
            "best_quote": best,
            "quote_count": len(quotes),
            "generated": True,
            "replayed": True,
            "all_in_cost_conservative": str(all_in),
            "locked_profit_conservative": str(locked),
            "classification": "EXECUTABLE_CANDIDATE" if locked >= D("0.03") else "NO_TRADE",
        }
    finally:
        if rfq_id:
            try:
                await client.delete(f"/communications/rfqs/{rfq_id}")
            except Exception:
                pass


async def _scan_with_replay() -> dict[str, Any]:
    listed = await _ORIGINAL_SCAN()
    max_replay = max(1, min(int(os.getenv("MVE_REPLAY_MAX_RFQS_PER_CYCLE", "6")), 12))
    rows: list[dict[str, Any]] = []
    client = KalshiClient(base_url=scan.BASE)
    try:
        for ticker in sorted(_REPLAY)[:max_replay]:
            try:
                row = await _score_replay_ticker(client, ticker)
            except Exception:
                row = None
            if row:
                rows.append(row)
    finally:
        await client.close()

    combined: dict[str, dict[str, Any]] = {}
    for row in list(listed.get("ranked") or []) + rows:
        if not isinstance(row, dict) or not row.get("ticker"):
            continue
        t = str(row["ticker"])
        try:
            edge = D(str(row.get("locked_profit_conservative") or "-9"))
            old = D(str(combined.get(t, {}).get("locked_profit_conservative") or "-9"))
        except Exception:
            edge, old = D("-9"), D("-9")
        if t not in combined or edge > old:
            combined[t] = row
    ranked = sorted(combined.values(), key=lambda r: D(str(r.get("locked_profit_conservative") or "-9")), reverse=True)
    listed["ranked"] = ranked
    listed["executable_candidates"] = sum(r.get("classification") == "EXECUTABLE_CANDIDATE" for r in ranked)
    listed["replay_pool_size"] = len(_REPLAY)
    listed["replay_rfqs_scored"] = len(rows)
    listed["replay_executable_candidates"] = sum(r.get("classification") == "EXECUTABLE_CANDIDATE" for r in rows)
    listed["replay_best"] = rows and max(rows, key=lambda r: D(str(r.get("locked_profit_conservative") or "-9"))) or None
    v32.v31.logger.warning("SPORTS_MVE_REPLAY_HEARTBEAT %s", json.dumps({
        "pool_size": len(_REPLAY),
        "rfqs_scored": len(rows),
        "executable_candidates": listed["replay_executable_candidates"],
        "best": listed["replay_best"],
        "new_market_creation": False,
        "live_threshold": "0.03",
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return listed


base2.generate_mves = _generate_with_replay_capture
base2.scan_existing_mves = _scan_with_replay

run = v32.run
main = v32.main

if __name__ == "__main__":
    main()
