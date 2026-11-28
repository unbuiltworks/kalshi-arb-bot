"""Canonical READ-ONLY realtime mirror of new L5 activity (Phase 2E/2F).

Wraps the audited mirror collector (realtime_mirror_base, ported from
feature/l5-realtime-shadow-mirror) with two canonicalizations:

  1. Scope: BTC and ETH up/down windows at BOTH 5m and 15m durations
     (the v2 branch restricted to 15m; L5's volume is ~64% 5m).
  2. Output: every observed L5 fill is appended, as it arrives, to a JSONL
     stream in the canonical-sequence field vocabulary (window, asset,
     duration, ts, seconds_to_settlement, outcome, price, quantity,
     cumulative inventories, matched qty, pair cost, residual) so realtime
     behavior is directly comparable with the historical dataset.

On exit (or --compare on an existing stream) it prints a comparison of the
live session against the historical policy baselines from l5_behavior.json:
both-sides rate, median pair cost, size-by-price direction, and residual
share. Comparison only — nothing here trades, authenticates, or writes
anywhere but the local stream file.

    python3 -m src.polymarket_l5.realtime_mirror --minutes 30
    python3 -m src.polymarket_l5.realtime_mirror --compare data/l5/mirror/mirror_XXXX.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from . import config
from .adapters import DEFAULT_ADAPTER, VenueAdapter
from .realtime_mirror_base import DATA_API, L5_WALLET, fnum

MIRROR_DIR = config.DATA_DIR / "mirror"
BEHAVIOR = config.OUTPUT_DIR / "l5_behavior.json"
POLL_SECONDS = 10.0


def parse_window(trade: Dict[str, Any],
                 adapter: VenueAdapter = DEFAULT_ADAPTER) -> Optional[Dict[str, Any]]:
    win = adapter.parse_window(trade)
    if win is None:
        return None
    return {"asset": win.asset, "duration_minutes": win.duration_minutes,
            "window_start": win.window_start, "window_end": win.window_end,
            "slug": win.window_id}


class WindowState:
    __slots__ = ("up_qty", "up_spend", "dn_qty", "dn_spend", "seq")

    def __init__(self) -> None:
        self.up_qty = self.up_spend = self.dn_qty = self.dn_spend = 0.0
        self.seq = 0


def canonical_row(trade: Dict[str, Any], win: Dict[str, Any], st: WindowState,
                  adapter: VenueAdapter = DEFAULT_ADAPTER) -> Optional[Dict[str, Any]]:
    fill = adapter.normalize_fill(trade)
    if fill is None:
        return None
    outcome, side = fill.outcome, fill.side
    ts, price, qty, usdc = fill.timestamp, fill.price, fill.quantity, fill.dollars
    sign = 1.0 if side == "BUY" else -1.0
    if outcome == "Up":
        st.up_qty += sign * qty
        st.up_spend += sign * usdc
    else:
        st.dn_qty += sign * qty
        st.dn_spend += sign * usdc
    st.seq += 1
    matched = min(st.up_qty, st.dn_qty)
    cu = st.up_spend / st.up_qty if st.up_qty > 1e-9 else None
    cd = st.dn_spend / st.dn_qty if st.dn_qty > 1e-9 else None
    pair_cost = (cu + cd) if (cu is not None and cd is not None) else None
    residual = st.up_qty - st.dn_qty
    return {
        "schema_version": "1", "window_id": win["slug"],
        "market_slug": win["slug"], "asset": win["asset"],
        "duration_minutes": win["duration_minutes"], "trade_seq": st.seq,
        "timestamp": ts,
        "datetime_utc": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "seconds_from_window_start": ts - win["window_start"],
        "seconds_to_settlement": win["window_end"] - ts,
        "outcome": outcome, "side": side, "price": price, "quantity": qty,
        "dollars": usdc, "maker_taker": "unknown",
        "cum_up_qty": round(st.up_qty, 6), "cum_up_spend": round(st.up_spend, 6),
        "cum_down_qty": round(st.dn_qty, 6),
        "cum_down_spend": round(st.dn_spend, 6),
        "matched_qty": round(max(matched, 0.0), 6),
        "matched_pair_unit_cost": round(pair_cost, 6) if pair_cost is not None else None,
        "residual_qty_signed": round(residual, 6),
        "residual_side": "Up" if residual > 1e-9 else "Down" if residual < -1e-9 else "",
        "settlement_outcome": "", "resolution_source": "",
        "source": f"{adapter.venue}_public_activity_realtime",
        "transaction_hash": fill.transaction_id,
    }


async def run_mirror(minutes: float) -> Path:
    import httpx   # network path only; ops/shadow import this module without it
    MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = MIRROR_DIR / f"mirror_{stamp}.jsonl"
    seen: set = set()
    states: Dict[str, WindowState] = defaultdict(WindowState)
    deadline = asyncio.get_event_loop().time() + minutes * 60

    async with httpx.AsyncClient(timeout=15.0, headers={
            "User-Agent": "kalshi-arb-bot/l5-canonical-mirror (read-only)"}) as http:
        with out_path.open("a", encoding="utf-8") as sink:
            sink.write(json.dumps({"kind": "mirror_start", "wallet": L5_WALLET,
                                   "scope": "btc/eth 5m+15m", "utc": stamp}) + "\n")
            while asyncio.get_event_loop().time() < deadline:
                try:
                    resp = await http.get(f"{DATA_API}/activity", params={
                        "user": L5_WALLET, "limit": 100,
                        "sortBy": "TIMESTAMP", "sortDirection": "DESC"})
                    rows = resp.json() if resp.status_code == 200 else []
                except Exception:
                    rows = []
                for trade in sorted((r for r in rows if isinstance(r, dict)),
                                    key=lambda r: fnum(r.get("timestamp")) or 0):
                    if trade.get("type") != "TRADE":
                        continue
                    win = parse_window(trade)
                    if win is None:
                        continue
                    key = (trade.get("transactionHash"), trade.get("timestamp"),
                           trade.get("outcome"), trade.get("price"),
                           trade.get("size"))
                    if key in seen:
                        continue
                    seen.add(key)
                    row = canonical_row(trade, win, states[win["slug"]])
                    if row is not None:
                        sink.write(json.dumps(row, separators=(",", ":")) + "\n")
                        sink.flush()
                await asyncio.sleep(POLL_SECONDS)
    return out_path


def compare(stream_path: Path) -> Dict[str, Any]:
    per_window: Dict[str, Dict[str, Any]] = {}
    n_rows = 0
    for line in stream_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") == "mirror_start" or "window_id" not in row:
            continue
        per_window[row["window_id"]] = row     # last row = window end-state
        n_rows += 1
    if not per_window:
        return {"windows": 0, "rows": n_rows,
                "note": "no L5 fills observed in this session"}
    both = sum(1 for r in per_window.values()
               if r["cum_up_qty"] > 0 and r["cum_down_qty"] > 0)
    pcs = sorted(r["matched_pair_unit_cost"] for r in per_window.values()
                 if r.get("matched_pair_unit_cost") is not None)
    live = {
        "rows": n_rows,
        "windows": len(per_window),
        "both_sides_pct": round(100 * both / len(per_window), 1),
        "median_pair_cost": round(pcs[len(pcs) // 2], 4) if pcs else None,
        "median_abs_residual_qty": None,
    }
    residuals = sorted(abs(r["residual_qty_signed"]) for r in per_window.values())
    if residuals:
        live["median_abs_residual_qty"] = round(residuals[len(residuals) // 2], 2)

    hist = {}
    if BEHAVIOR.exists():
        b = json.loads(BEHAVIOR.read_text())
        hist = {
            "both_sides_pct": b.get("q1_both_sides_pct"),
            "median_pair_cost": (b.get("q2_pair_cost_distribution") or {}).get("median"),
            "median_abs_residual_qty": (b.get("q3_residual_windows") or {}).get("median_abs_qty"),
        }
    return {"live_session": live, "historical_baseline": hist,
            "note": ("live windows may be mid-flight; comparisons firm up "
                     "only for windows past settlement")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--compare", default="",
                    help="skip collection; compare an existing stream file")
    args = ap.parse_args()
    if args.compare:
        print(json.dumps(compare(Path(args.compare)), indent=1))
        return 0
    path = asyncio.run(run_mirror(args.minutes))
    print(f"mirror stream: {path}")
    print(json.dumps(compare(path), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
