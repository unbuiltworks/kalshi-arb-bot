"""Read-only ETH5 front-of-queue census.

This collector answers a narrower question than the live V3 probe: given the
visible Gemini order book, what maker prices would be needed to gain price
priority on BOTH outcomes, and how much after-fee pair edge survives there?

It uses only public Gemini event/book endpoints. No credentials, balances,
account endpoints, order entry, cancellation, or realized P&L are used.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D, ROUND_CEILING
import json
from pathlib import Path
import time
from typing import Any, Mapping

import httpx

from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_l5_threshold_shadow import discover
from src.gemini_short_crypto_dynamic_complement_q1_v3 import edge_spending_pair_model

REST = "https://api.gemini.com"
ONE = D("1")
Q = D("1")
RAW_GATE = D("0.05")
FINAL_EDGE_FLOOR = D("0.02")
MAX_STRESS_TICKS = 4


def _d(value: Any) -> D | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _level(raw: Any) -> tuple[D, D] | None:
    if not isinstance(raw, Mapping):
        return None
    price = _d(raw.get("price"))
    qty = _d(raw.get("amount") if raw.get("amount") is not None else raw.get("quantity"))
    if price is None or qty is None or not (D("0") < price < ONE) or qty <= 0:
        return None
    return price, qty


def normalized_levels(book: Mapping[str, Any]) -> tuple[list[tuple[D, D]], list[tuple[D, D]]]:
    bids = [x for x in (_level(row) for row in (book.get("bids") or [])) if x]
    asks = [x for x in (_level(row) for row in (book.get("asks") or [])) if x]
    bids.sort(key=lambda z: z[0], reverse=True)
    asks.sort(key=lambda z: z[0])
    return bids, asks


def pair_edge(yes_price: D, no_price: D) -> D:
    yes_fee = gemini_predictions_maker_fee(yes_price, Q)
    no_fee = gemini_predictions_maker_fee(no_price, Q)
    return ONE - yes_price - no_price - yes_fee - no_fee


def _ceil_ticks(delta: D, tick: D) -> int:
    if delta <= 0:
        return 0
    return int((delta / tick).to_integral_value(rounding=ROUND_CEILING))


def _queue_ahead(levels: list[tuple[D, D]], quote: D) -> D:
    """Visible price-time quantity ahead of a new buy at ``quote``.

    Better-priced bids are ahead, and existing size already resting at our exact
    price is ahead. A quote strictly above the visible best bid therefore has
    zero visible quantity ahead at the snapshot instant.
    """
    total = D("0")
    for price, qty in levels:
        if price > quote:
            total += qty
        elif price == quote:
            total += qty
    return total


def _no_bid_levels_from_yes_asks(asks: list[tuple[D, D]]) -> list[tuple[D, D]]:
    rows = [(ONE - price, qty) for price, qty in asks]
    rows.sort(key=lambda z: z[0], reverse=True)
    return rows


def _serialize_levels(levels: list[tuple[D, D]]) -> list[dict[str, str]]:
    return [{"price": str(price), "quantity": str(qty)} for price, qty in levels]


def score_book(*, ticker: str, symbol: str, seconds_to_expiry: float, tick: D, book: Mapping[str, Any], observed_at: str | None = None) -> dict[str, Any]:
    bids, asks = normalized_levels(book)
    if not bids or not asks:
        raise RuntimeError("incomplete ETH5 book")
    yes_bid, yes_bid_qty = bids[0]
    yes_ask, yes_ask_qty = asks[0]
    if not (D("0") < yes_bid < yes_ask < ONE):
        raise RuntimeError("invalid ETH5 book")
    if tick <= 0:
        raise RuntimeError("invalid ETH5 tick")

    no_bids = _no_bid_levels_from_yes_asks(asks)
    no_bid, no_bid_qty = no_bids[0]
    no_ask = ONE - yes_bid

    v3 = edge_spending_pair_model(yes_bid, yes_ask)
    v3_yes = D(str(v3["yes_quote"]))
    v3_no = D(str(v3["no_quote"]))
    raw_edge = D(str(v3["raw_modeled_edge"]))
    v3_edge = D(str(v3["modeled_edge"]))

    # Price priority on a displayed limit book: one whole tick above the current
    # best bid is an empty, better price level unless it would cross the ask.
    yes_front = yes_bid + tick
    no_front = no_bid + tick
    yes_front_post_only = yes_front < yes_ask
    no_front_post_only = no_front < no_ask
    front_post_only = yes_front_post_only and no_front_post_only
    front_edge = pair_edge(yes_front, no_front) if front_post_only else None

    yes_ticks_to_front = _ceil_ticks(yes_front - v3_yes, tick) if yes_front_post_only else None
    no_ticks_to_front = _ceil_ticks(no_front - v3_no, tick) if no_front_post_only else None

    # Visible queue ahead at the V3 prices. For NO buys, YES asks transform into
    # NO bids at 1-price with the same displayed quantity.
    yes_queue_ahead_v3 = _queue_ahead(bids, v3_yes)
    no_queue_ahead_v3 = _queue_ahead(no_bids, v3_no)

    stress: list[dict[str, Any]] = []
    max_extra = -1
    for k in range(0, MAX_STRESS_TICKS + 1):
        yp = v3_yes + tick * k
        np = v3_no + tick * k
        post_only = yp < yes_ask and np < no_ask
        edge = pair_edge(yp, np) if post_only else None
        survives = bool(edge is not None and edge >= FINAL_EDGE_FLOOR)
        if survives:
            max_extra = k
        stress.append({
            "extra_ticks_each_from_v3": k,
            "yes_price": str(yp),
            "no_price": str(np),
            "post_only": post_only,
            "edge_after_maker_fees": None if edge is None else str(edge),
            "survives_2c_floor": survives,
        })

    return {
        "type": "GEMINI_ETH5_FRONT_QUEUE_SNAPSHOT_V1",
        "classification": "READ_ONLY_PUBLIC_BOOK_CENSUS_NOT_REALIZED",
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
        "symbol": symbol,
        "seconds_to_expiry": round(seconds_to_expiry, 3),
        "price_increment": str(tick),
        "yes_best_bid": str(yes_bid),
        "yes_best_bid_qty": str(yes_bid_qty),
        "yes_best_ask": str(yes_ask),
        "yes_best_ask_qty": str(yes_ask_qty),
        "no_best_bid": str(no_bid),
        "no_best_bid_qty": str(no_bid_qty),
        "no_best_ask": str(no_ask),
        "raw_modeled_edge": str(raw_edge),
        "raw_gate_5c": raw_edge >= RAW_GATE,
        "v3_yes_quote": str(v3_yes),
        "v3_no_quote": str(v3_no),
        "v3_modeled_edge": str(v3_edge),
        "visible_yes_queue_ahead_at_v3": str(yes_queue_ahead_v3),
        "visible_no_queue_ahead_at_v3": str(no_queue_ahead_v3),
        "yes_front_quote": str(yes_front),
        "no_front_quote": str(no_front),
        "yes_ticks_from_v3_to_front": yes_ticks_to_front,
        "no_ticks_from_v3_to_front": no_ticks_to_front,
        "front_post_only": front_post_only,
        "front_edge_after_maker_fees": None if front_edge is None else str(front_edge),
        "front_survives_2c_floor": bool(front_edge is not None and front_edge >= FINAL_EDGE_FLOOR),
        "max_symmetric_extra_ticks_each_from_v3_preserving_2c": max_extra,
        "stress": stress,
        "bids": _serialize_levels(bids),
        "asks": _serialize_levels(asks),
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
    }


def _quantile(values: list[D], fraction: D) -> D | None:
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    idx = int(((len(vals) - 1) * fraction).to_integral_value(rounding="ROUND_HALF_UP"))
    return vals[max(0, min(idx, len(vals) - 1))]


def summarize_rows(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    snapshots = [r for r in rows if r.get("type") == "GEMINI_ETH5_FRONT_QUEUE_SNAPSHOT_V1"]
    candidates = [r for r in snapshots if bool(r.get("raw_gate_5c"))]
    front = [r for r in candidates if bool(r.get("front_survives_2c_floor"))]

    front_edges = [D(str(r["front_edge_after_maker_fees"])) for r in candidates if r.get("front_edge_after_maker_fees") is not None]
    yes_queue = [D(str(r.get("visible_yes_queue_ahead_at_v3") or "0")) for r in candidates]
    no_queue = [D(str(r.get("visible_no_queue_ahead_at_v3") or "0")) for r in candidates]
    extra_ticks = [D(str(r.get("max_symmetric_extra_ticks_each_from_v3_preserving_2c") or 0)) for r in candidates]

    by_ticker: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in snapshots:
        by_ticker[str(row.get("ticker") or "UNKNOWN")].append(row)

    windows = []
    for ticker, group in sorted(by_ticker.items()):
        cands = [r for r in group if bool(r.get("raw_gate_5c"))]
        feasible = [r for r in cands if bool(r.get("front_survives_2c_floor"))]
        raw_edges = [D(str(r.get("raw_modeled_edge") or "0")) for r in cands]
        f_edges = [D(str(r["front_edge_after_maker_fees"])) for r in cands if r.get("front_edge_after_maker_fees") is not None]
        windows.append({
            "ticker": ticker,
            "snapshots": len(group),
            "qualifier_snapshots": len(cands),
            "front_feasible_2c_snapshots": len(feasible),
            "front_feasible_2c_pct_of_qualifiers": 0.0 if not cands else round(len(feasible) / len(cands), 4),
            "max_raw_edge": None if not raw_edges else str(max(raw_edges)),
            "max_front_edge": None if not f_edges else str(max(f_edges)),
        })

    def fmt(v: D | None) -> str | None:
        return None if v is None else str(v)

    return {
        "type": "GEMINI_ETH5_FRONT_QUEUE_CENSUS_SUMMARY_V1",
        "classification": "READ_ONLY_PUBLIC_BOOK_CENSUS_NOT_REALIZED",
        "order_entry": False,
        "credentials_loaded": False,
        "realized_pnl": None,
        "snapshots": len(snapshots),
        "qualifier_snapshots_raw_edge_ge_5c": len(candidates),
        "front_feasible_2c_snapshots": len(front),
        "front_feasible_2c_pct_of_qualifiers": 0.0 if not candidates else round(len(front) / len(candidates), 4),
        "front_edge_p10": fmt(_quantile(front_edges, D("0.10"))),
        "front_edge_median": fmt(_quantile(front_edges, D("0.50"))),
        "front_edge_p90": fmt(_quantile(front_edges, D("0.90"))),
        "visible_yes_queue_ahead_at_v3_median": fmt(_quantile(yes_queue, D("0.50"))),
        "visible_no_queue_ahead_at_v3_median": fmt(_quantile(no_queue, D("0.50"))),
        "max_symmetric_extra_ticks_each_from_v3_preserving_2c_median": fmt(_quantile(extra_ticks, D("0.50"))),
        "windows_seen": len(windows),
        "windows_with_qualifier": sum(1 for w in windows if w["qualifier_snapshots"] > 0),
        "windows_with_front_feasible_2c": sum(1 for w in windows if w["front_feasible_2c_snapshots"] > 0),
        "windows": windows,
    }


def summarize_file(path: Path) -> dict[str, Any]:
    rows: list[Mapping[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, Mapping):
                rows.append(row)
    return summarize_rows(rows)


async def capture(*, seconds: float, poll_s: float, output: Path, summary_output: Path | None = None) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if summary_output:
        summary_output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, seconds)

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-eth5-front-queue-census/1.0"}) as http:
        with output.open("a", encoding="utf-8") as fh:
            while time.monotonic() < deadline:
                started = time.monotonic()
                try:
                    events = await discover(http)
                    event = events.get("ETH05M")
                    if event is not None:
                        response = await http.get(
                            f"{REST}/v1/book/{event.symbol}",
                            params={"limit_bids": 20, "limit_asks": 20},
                            headers={"Cache-Control": "no-cache"},
                        )
                        response.raise_for_status()
                        book = response.json()
                        if not isinstance(book, Mapping):
                            raise RuntimeError("invalid Gemini book payload")
                        row = score_book(
                            ticker=event.ticker,
                            symbol=event.symbol,
                            seconds_to_expiry=event.seconds_to_expiry,
                            tick=event.price_increment,
                            book=book,
                        )
                        fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
                        fh.flush()
                except Exception as exc:
                    row = {
                        "type": "GEMINI_ETH5_FRONT_QUEUE_CENSUS_ERROR",
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:240],
                        "order_entry": False,
                        "credentials_loaded": False,
                    }
                    fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                    fh.flush()
                elapsed = time.monotonic() - started
                await asyncio.sleep(max(0.05, poll_s - elapsed))

    summary = summarize_file(output)
    summary["observed_at"] = datetime.now(timezone.utc).isoformat()
    summary["seconds_requested"] = seconds
    summary["poll_s"] = poll_s
    if summary_output:
        summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, separators=(",", ":"), default=str), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=86400.0)
    parser.add_argument("--poll-s", type=float, default=5.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()
    if args.summarize_only:
        result = summarize_file(args.output)
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        return
    asyncio.run(capture(seconds=args.seconds, poll_s=args.poll_s, output=args.output, summary_output=args.summary_output))


if __name__ == "__main__":
    main()
