"""Accounting-grade ledger for the L5 full history.

Builds a complete cashflow ledger from every activity type Polymarket reports
for the address (TRADE, SPLIT, MERGE, REDEEM, REWARD, MAKER_REBATE,
TAKER_REBATE), reconstructs each short-crypto window chronologically, and
decomposes P&L into exactly-additive components:

    net_trading_cash(market) = redeems + sells - buys          (cash truth)
    pair_locked   = matched_qty * (1 - wavg_up_cost - wavg_down_cost)
    residual_and_timing = net_trading_cash - pair_locked       (by definition)

Incentive income (REWARD / MAKER_REBATE / TAKER_REBATE) has no conditionId, so
it is a separate dated global stream — never mixed into trading P&L.

Reconciliation: derived per-market cash P&L is compared with Polymarket's own
closed-position rows (cashPnl / realizedPnl) for every market the positions
endpoint covers. Differences are reported, not hidden.

Sources: raw caches ``activity`` (full payloads) and ``activity_gap`` (slim
backfill rows). Dedup across overlapping harvests is a multiset union keyed on
(type, tx hash, market, outcome, side, size, price, ts): within one cached
response duplicates are legitimate distinct fills; across responses the same
key is the same fill seen twice.

Read-only research. No network. Nothing here can execute anywhere.

    python -m src.polymarket_l5.ledger
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import config
from .cache import RawCache, coerce_items

OUT_JSON = config.OUTPUT_DIR / "l5_ledger.json"
OUT_WINDOWS = config.OUTPUT_DIR / "l5_ledger_windows.csv.gz"

SLUG_RE = re.compile(r"^([a-z]+)-updown-(\d+)m-(\d+)$")
INCOME_TYPES = ("REWARD", "MAKER_REBATE", "TAKER_REBATE")
QTY_EPS = config.QTY_EPS


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _key(item: Dict[str, Any]) -> bytes:
    raw = "|".join((
        str(item.get("type", "")),
        str(item.get("transactionHash", "")),
        str(item.get("conditionId", "")),
        str(item.get("outcomeIndex", "")),
        str(item.get("side", "")),
        f"{_num(item.get('size')):.6f}",
        f"{_num(item.get('price')):.6f}",
        str(int(_num(item.get("timestamp")))),
    ))
    return hashlib.blake2b(raw.encode(), digest_size=16).digest()


def stream_activity(cache: RawCache, tags=("activity", "activity_gap")) -> Iterator[Dict[str, Any]]:
    """Every unique activity item across all harvests (multiset union)."""
    processed: Dict[bytes, int] = {}
    for tag in tags:
        for record in cache.records(tag):
            if record.get("status") != 200:
                continue
            batch_count: Dict[bytes, int] = {}
            for item in coerce_items(record.get("payload")):
                k = _key(item)
                batch_count[k] = batch_count.get(k, 0) + 1
                if batch_count[k] > processed.get(k, 0):
                    processed[k] = batch_count[k]
                    yield item


class WindowAgg:
    """Chronological per-market (== per-window for updown markets) state."""

    __slots__ = ("condition_id", "slug", "asset", "horizon_m", "window_start",
                 "fills", "redeem_usdc", "redeem_qty", "redeem_n",
                 "types_seen")

    def __init__(self, condition_id: str) -> None:
        self.condition_id = condition_id
        self.slug = ""
        self.asset = ""
        self.horizon_m = 0
        self.window_start = 0
        self.fills: List[Tuple[int, int, int, float, float, float]] = []
        # (ts, outcome_index, +1 buy/-1 sell, size, price, usdc)
        self.redeem_usdc = 0.0
        self.redeem_qty = 0.0
        self.redeem_n = 0
        self.types_seen: set = set()


def build_ledger(cache: Optional[RawCache] = None) -> Dict[str, Any]:
    cache = cache or RawCache(config.RAW_DIR)
    markets: Dict[str, WindowAgg] = {}
    income_rows: List[Tuple[int, str, float]] = []   # (ts, type, usdc)
    unknown_types: Dict[str, int] = defaultdict(int)
    counts: Dict[str, int] = defaultdict(int)

    for item in stream_activity(cache):
        typ = item.get("type", "")
        counts[typ] += 1
        ts = int(_num(item.get("timestamp")))
        usdc = _num(item.get("usdcSize"))
        if typ in INCOME_TYPES:
            income_rows.append((ts, typ, usdc))
            continue
        cid = item.get("conditionId") or ""
        if not cid:
            unknown_types[f"{typ}:no_condition"] += 1
            continue
        agg = markets.get(cid)
        if agg is None:
            agg = markets[cid] = WindowAgg(cid)
        agg.types_seen.add(typ)
        slug = item.get("slug") or ""
        if slug and not agg.slug:
            agg.slug = slug
            m = SLUG_RE.match(slug)
            if m:
                agg.asset = m.group(1).upper()
                agg.horizon_m = int(m.group(2))
                agg.window_start = int(m.group(3))
        if typ == "TRADE":
            side = item.get("side", "")
            sign = 1 if side == "BUY" else -1
            size = _num(item.get("size"))
            price = _num(item.get("price"))
            if usdc == 0.0:
                usdc = size * price
            agg.fills.append((ts, int(_num(item.get("outcomeIndex"))),
                              sign, size, price, usdc))
        elif typ == "REDEEM":
            agg.redeem_usdc += usdc
            agg.redeem_qty += _num(item.get("size"))
            agg.redeem_n += 1
        elif typ in ("SPLIT", "MERGE", "CONVERSION"):
            # Mint/burn a full Up+Down pair for $1/pair. SPLIT pays cash out,
            # MERGE receives it. Neither appears in the harvests seen so far;
            # if the backfill surfaces them they land in both the cashflow and
            # both sides' inventory here.
            sign = -1 if typ == "SPLIT" else 1
            size = _num(item.get("size"))
            if usdc == 0.0:
                usdc = size
            agg.fills.append((ts, 0, 1 if typ == "SPLIT" else -1,
                              size, 0.5, usdc / 2))
            agg.fills.append((ts, 1, 1 if typ == "SPLIT" else -1,
                              size, 0.5, usdc / 2))
        else:
            unknown_types[typ] += 1

    window_rows = [_finalize(agg) for agg in markets.values()]
    window_rows.sort(key=lambda r: (r["window_start"] or 0, r["condition_id"]))
    return {
        "counts": dict(counts),
        "unknown_types": dict(unknown_types),
        "income_rows": sorted(income_rows),
        "windows": window_rows,
    }


def _finalize(agg: WindowAgg) -> Dict[str, Any]:
    fills = sorted(agg.fills)
    buy_qty = [0.0, 0.0]
    buy_cost = [0.0, 0.0]
    sell_qty = [0.0, 0.0]
    sell_proceeds = [0.0, 0.0]
    first_fill = None
    first_opposite_ts = None
    first_idx = None
    last_ts = None
    for ts, oi, sign, size, price, usdc in fills:
        oi = 0 if oi == 0 else 1
        if sign > 0:
            buy_qty[oi] += size
            buy_cost[oi] += usdc
        else:
            sell_qty[oi] += size
            sell_proceeds[oi] += usdc
        if first_fill is None:
            first_fill = (ts, oi, sign, size, price)
            first_idx = oi
        elif first_opposite_ts is None and oi != first_idx and sign > 0:
            first_opposite_ts = ts
        last_ts = ts

    buys = buy_cost[0] + buy_cost[1]
    sells = sell_proceeds[0] + sell_proceeds[1]
    net_trading_cash = agg.redeem_usdc + sells - buys
    matched = min(buy_qty[0], buy_qty[1])
    cu = buy_cost[0] / buy_qty[0] if buy_qty[0] > QTY_EPS else None
    cd = buy_cost[1] / buy_qty[1] if buy_qty[1] > QTY_EPS else None
    pair_cost = (cu + cd) if (cu is not None and cd is not None) else None
    pair_locked = matched * (1.0 - pair_cost) if pair_cost is not None else 0.0
    residual = net_trading_cash - pair_locked

    pos_after = [buy_qty[i] - sell_qty[i] for i in (0, 1)]
    # Shares that neither sold nor redeemed: either expired worthless (cash
    # already accounts for that) or a not-yet-redeemed win (cash understates).
    unredeemed = max(0.0, pos_after[0] + pos_after[1] - agg.redeem_qty)

    window_end = (agg.window_start + agg.horizon_m * 60) if agg.window_start else None
    ff = first_fill or (None, None, None, None, None)
    return {
        "condition_id": agg.condition_id,
        "slug": agg.slug,
        "asset": agg.asset,
        "horizon_m": agg.horizon_m,
        "window_start": agg.window_start,
        "is_updown": bool(agg.window_start),
        "n_fills": len(fills),
        "buy_usdc": round(buys, 6),
        "sell_usdc": round(sells, 6),
        "redeem_usdc": round(agg.redeem_usdc, 6),
        "redeem_qty": round(agg.redeem_qty, 6),
        "net_trading_cash": round(net_trading_cash, 6),
        "buy_up_qty": round(buy_qty[0], 6),
        "buy_down_qty": round(buy_qty[1], 6),
        "matched_qty": round(matched, 6),
        "wavg_up_cost": round(cu, 6) if cu is not None else None,
        "wavg_down_cost": round(cd, 6) if cd is not None else None,
        "pair_cost": round(pair_cost, 6) if pair_cost is not None else None,
        "pair_locked": round(pair_locked, 6),
        "residual_and_timing": round(residual, 6),
        "unredeemed_qty": round(unredeemed, 6),
        "first_ts": ff[0],
        "first_outcome_index": ff[1],
        "first_is_buy": (ff[2] == 1) if ff[2] is not None else None,
        "first_size": round(ff[3], 6) if ff[3] is not None else None,
        "first_price": round(ff[4], 6) if ff[4] is not None else None,
        "first_tte_s": (window_end - ff[0]) if (window_end and ff[0]) else None,
        "opposite_gap_s": (first_opposite_ts - ff[0])
        if (first_opposite_ts and ff[0]) else None,
        "last_ts": last_ts,
    }


# ---------------------------------------------------------------------------
# Reconciliation against Polymarket's own position rows
# ---------------------------------------------------------------------------

def load_positions(cache: RawCache) -> Dict[str, Dict[str, float]]:
    """Per-market sums of Polymarket's own cashPnl/realizedPnl."""
    per_market: Dict[str, Dict[str, float]] = {}
    seen: set = set()
    for tag in ("positions_closed", "positions_all", "positions_redeemable"):
        for record in cache.records(tag):
            if record.get("status") != 200:
                continue
            for item in coerce_items(record.get("payload")):
                cid = item.get("conditionId") or ""
                asset = item.get("asset") or ""
                if not cid or (cid, asset) in seen:
                    continue
                seen.add((cid, asset))
                agg = per_market.setdefault(
                    cid, {"cashPnl": 0.0, "realizedPnl": 0.0,
                          "totalBought": 0.0, "currentValue": 0.0, "rows": 0,
                          "terminal": True})
                agg["cashPnl"] += _num(item.get("cashPnl"))
                agg["realizedPnl"] += _num(item.get("realizedPnl"))
                agg["totalBought"] += _num(item.get("totalBought"))
                agg["currentValue"] += _num(item.get("currentValue"))
                agg["rows"] += 1
                cur = _num(item.get("curPrice"))
                if 0.001 < cur < 0.999:
                    agg["terminal"] = False
    return per_market


def reconcile(windows: List[Dict[str, Any]], positions: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    diffs: List[float] = []
    matched_n = nonterminal_skipped = 0
    ours_sum = theirs_sum = 0.0
    worst: List[Tuple[float, str]] = []
    for w in windows:
        p = positions.get(w["condition_id"])
        if p is None:
            continue
        if not p["terminal"]:
            # A live mark (curPrice strictly inside (0,1)) is an opinion, not
            # a cashflow — reconcile only settled markets.
            nonterminal_skipped += 1
            continue
        # Our cash truth ignores unredeemed winners; Polymarket's cashPnl marks
        # remaining shares at curPrice. Add their currentValue to our cash so
        # both sides measure the same thing.
        ours = w["net_trading_cash"] + p["currentValue"]
        theirs = p["cashPnl"]
        d = ours - theirs
        diffs.append(d)
        matched_n += 1
        ours_sum += ours
        theirs_sum += theirs
        worst.append((abs(d), w["condition_id"]))
    worst.sort(reverse=True)
    abs_sorted = sorted(abs(d) for d in diffs)

    def pct_within(cents: float) -> float:
        return round(100 * sum(1 for d in diffs if abs(d) <= cents) / len(diffs), 1) if diffs else 0.0

    return {
        "markets_matched": matched_n,
        "markets_nonterminal_skipped": nonterminal_skipped,
        "markets_unmatched_ours": sum(1 for w in windows if w["condition_id"] not in positions),
        "our_cash_plus_marks_sum": round(ours_sum, 2),
        "their_cashpnl_sum": round(theirs_sum, 2),
        "sum_abs_diff": round(sum(abs(d) for d in diffs), 2),
        "median_abs_diff": round(abs_sorted[len(abs_sorted) // 2], 6) if abs_sorted else None,
        "pct_within_1c": pct_within(0.01),
        "pct_within_10c": pct_within(0.10),
        "pct_within_1usd": pct_within(1.00),
        "worst_5": [{"abs_diff": round(a, 4), "condition_id": c} for a, c in worst[:5]],
    }


def main() -> int:
    cache = RawCache(config.RAW_DIR)
    ledger = build_ledger(cache)
    windows = ledger["windows"]
    positions = load_positions(cache)
    recon = reconcile(windows, positions)

    updown = [w for w in windows if w["is_updown"]]
    other = [w for w in windows if not w["is_updown"]]
    income_total = defaultdict(float)
    for _ts, typ, usdc in ledger["income_rows"]:
        income_total[typ] += usdc

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "activity_counts": ledger["counts"],
        "unknown_types": ledger["unknown_types"],
        "n_markets": len(windows),
        "n_updown_windows": len(updown),
        "n_other_markets": len(other),
        "trading": {
            "buy_usdc": round(sum(w["buy_usdc"] for w in windows), 2),
            "sell_usdc": round(sum(w["sell_usdc"] for w in windows), 2),
            "redeem_usdc": round(sum(w["redeem_usdc"] for w in windows), 2),
            "net_trading_cash": round(sum(w["net_trading_cash"] for w in windows), 2),
            "pair_locked": round(sum(w["pair_locked"] for w in updown), 2),
            "residual_and_timing": round(sum(w["residual_and_timing"] for w in updown), 2),
            "unredeemed_qty": round(sum(w["unredeemed_qty"] for w in windows), 2),
            "other_markets_net_cash": round(sum(w["net_trading_cash"] for w in other), 2),
        },
        "income": {k: round(v, 2) for k, v in sorted(income_total.items())},
        "income_total": round(sum(income_total.values()), 2),
        "reconciliation": recon,
    }
    summary["grand_total_cash"] = round(
        summary["trading"]["net_trading_cash"] + summary["income_total"], 2)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with OUT_JSON.open("w") as fh:
        json.dump({**summary,
                   "income_rows": [
                       {"ts": ts, "type": t, "usdc": round(u, 4)}
                       for ts, t, u in ledger["income_rows"]]}, fh, indent=1)

    cols = list(windows[0].keys()) if windows else []
    with gzip.open(OUT_WINDOWS, "wt", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(windows)

    print(json.dumps(summary, indent=1))
    print(f"wrote {OUT_JSON} and {OUT_WINDOWS} ({len(windows)} markets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
