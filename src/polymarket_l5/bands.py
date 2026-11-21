"""Price-band discriminator study for L5 (audit-specified, 2026-08-31).

Question: are his cheap-side buys (2-5c) smart optionality, cheap completion
legs, or just residue of a broad ladder? For every buy fill, bucketed by
purchase price:

  * matched vs directional attribution (pro-rata within each market side:
    matched_qty/side_qty of every fill on that side is "matched", the rest
    "directional") — no fill-level matching fiction, just the neutral split;
  * settlement-scored directional P&L per band, with qty-weighted realized
    win rate vs the break-even rate (= qty-weighted avg price);
  * pro-rata share of the market's locked pair value credited to the band;
  * the size curve: qty per fill by price band (is 'more shares as price
    falls' real?).

Winner attribution: the outcome with redeem_qty > 0 won (he never sells, so
winning shares are redeemed). Markets with no redeem at all have unknown
winners (recent/unredeemed) and are excluded from win-rate math but counted.

Also reports the pair-cost TAIL (not just medians) by completion-gap bucket,
because cross-time volatility harvesting would live in the tail: pairs at
24c total exist only if pair_cost distributions have real mass far below $1.

Offline research on committed raw caches. No execution anywhere.

    python -m src.polymarket_l5.bands
"""
from __future__ import annotations

import csv
import gzip
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .cache import RawCache
from .ledger import stream_activity, _num

OUT_JSON = config.OUTPUT_DIR / "l5_price_bands.json"
IN_WINDOWS = config.OUTPUT_DIR / "l5_ledger_windows.csv.gz"

BANDS = (
    ("le2c", 0.0, 0.02),
    ("3_5c", 0.02, 0.05),
    ("6_10c", 0.05, 0.10),
    ("11_20c", 0.10, 0.20),
    ("21_35c", 0.20, 0.35),
    ("36_65c", 0.35, 0.65),
    ("66_90c", 0.65, 0.90),
    ("gt90c", 0.90, 1.01),
)


def band_of(price: float) -> str:
    for name, lo, hi in BANDS:
        if lo < price <= hi or (lo == 0.0 and price <= hi):
            return name
    return "gt90c"


class Side:
    __slots__ = ("qty", "cost", "fills")

    def __init__(self) -> None:
        self.qty = 0.0
        self.cost = 0.0
        self.fills: List[Tuple[float, float]] = []  # (price, size)


def main() -> int:
    cache = RawCache(config.RAW_DIR)
    # market -> {outcome_index: Side}, plus redeem qty per outcome
    sides: Dict[str, Dict[int, Side]] = defaultdict(lambda: defaultdict(Side))
    redeems: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))

    for item in stream_activity(cache):
        typ = item.get("type", "")
        cid = item.get("conditionId") or ""
        if not cid:
            continue
        oi = 0 if int(_num(item.get("outcomeIndex"), 0)) == 0 else 1
        if typ == "TRADE" and item.get("side") == "BUY":
            size = _num(item.get("size"))
            price = _num(item.get("price"))
            usdc = _num(item.get("usdcSize")) or size * price
            s = sides[cid][oi]
            s.qty += size
            s.cost += usdc
            s.fills.append((price, size))
        elif typ == "REDEEM":
            redeems[cid][oi] += _num(item.get("size"))

    agg: Dict[str, Dict[str, float]] = {
        name: defaultdict(float) for name, _, _ in BANDS}
    unknown_winner_markets = 0
    known_winner_markets = 0

    for cid, per_side in sides.items():
        q0 = per_side[0].qty
        q1 = per_side[1].qty
        matched = min(q0, q1)
        c0 = per_side[0].cost / q0 if q0 > 0 else 0.0
        c1 = per_side[1].cost / q1 if q1 > 0 else 0.0
        pair_cost = c0 + c1
        pair_locked_per_share = (1.0 - pair_cost) if (q0 > 0 and q1 > 0) else 0.0

        r = redeems.get(cid, {})
        if r.get(0, 0.0) > 1e-9 and r.get(1, 0.0) > 1e-9:
            winner: Optional[int] = None  # both redeemed: negative-risk oddity
        elif r.get(0, 0.0) > 1e-9:
            winner = 0
        elif r.get(1, 0.0) > 1e-9:
            winner = 1
        else:
            winner = None
        if winner is None:
            unknown_winner_markets += 1
        else:
            known_winner_markets += 1

        for oi in (0, 1):
            s = per_side[oi]
            if s.qty <= 0:
                continue
            mfrac = matched / s.qty if s.qty > 0 else 0.0
            for price, size in s.fills:
                b = agg[band_of(price)]
                cost = price * size
                b["qty"] += size
                b["cost"] += cost
                b["n_fills"] += 1
                b["matched_qty"] += size * mfrac
                # half the pair edge credited to each leg, pro-rata
                b["pair_locked_credit"] += size * mfrac * pair_locked_per_share / 2.0
                dir_qty = size * (1.0 - mfrac)
                b["directional_qty"] += dir_qty
                b["directional_cost"] += cost * (1.0 - mfrac)
                if winner is not None:
                    b["dir_qty_known"] += dir_qty
                    b["dir_cost_known"] += cost * (1.0 - mfrac)
                    if oi == winner:
                        b["dir_win_qty"] += dir_qty
                        b["dir_payout"] += dir_qty * 1.0

    rows = []
    for name, lo, hi in BANDS:
        b = agg[name]
        if b["qty"] <= 0:
            continue
        avg_price = b["cost"] / b["qty"]
        dirq = b["dir_qty_known"]
        win_rate = (b["dir_win_qty"] / dirq) if dirq > 0 else None
        breakeven = (b["dir_cost_known"] / dirq) if dirq > 0 else None
        dir_pnl = b["dir_payout"] - b["dir_cost_known"]
        rows.append({
            "band": name,
            "n_fills": int(b["n_fills"]),
            "qty": round(b["qty"], 1),
            "cost_usdc": round(b["cost"], 2),
            "avg_price": round(avg_price, 4),
            "avg_fill_size": round(b["qty"] / b["n_fills"], 2),
            "matched_pct": round(100 * b["matched_qty"] / b["qty"], 1),
            "pair_locked_credit": round(b["pair_locked_credit"], 2),
            "directional_qty_known": round(dirq, 1),
            "dir_win_rate": round(win_rate, 4) if win_rate is not None else None,
            "dir_breakeven_rate": round(breakeven, 4) if breakeven is not None else None,
            "dir_edge_per_share": round(win_rate - breakeven, 4)
            if (win_rate is not None and breakeven is not None) else None,
            "dir_settlement_pnl": round(dir_pnl, 2),
        })

    # pair-cost tail by completion gap, from the ledger windows artifact
    tail: Dict[str, Any] = {}
    if IN_WINDOWS.exists():
        buckets = defaultdict(list)
        with gzip.open(IN_WINDOWS, "rt", newline="") as fh:
            for row in csv.DictReader(fh):
                pc = row.get("pair_cost")
                gap = row.get("opposite_gap_s")
                if pc in ("", None, "None") or gap in ("", None, "None"):
                    continue
                pc_f, gap_f = float(pc), float(gap)
                if gap_f <= 2:
                    k = "le2s"
                elif gap_f <= 30:
                    k = "2_30s"
                elif gap_f <= 120:
                    k = "30_120s"
                else:
                    k = "gt120s"
                buckets[k].append(pc_f)
                buckets["all"].append(pc_f)
        for k, pcs in buckets.items():
            pcs.sort()
            n = len(pcs)
            tail[k] = {
                "n": n,
                "p10": round(pcs[int(0.10 * n)], 4),
                "p25": round(pcs[int(0.25 * n)], 4),
                "median": round(pcs[n // 2], 4),
                "pct_lt_090": round(100 * sum(1 for p in pcs if p < 0.90) / n, 1),
                "pct_lt_075": round(100 * sum(1 for p in pcs if p < 0.75) / n, 1),
                "pct_lt_050": round(100 * sum(1 for p in pcs if p < 0.50) / n, 1),
            }

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": ("pro-rata matched attribution per market side; winner from "
                   "redeem records; unknown-winner markets excluded from "
                   "win-rate math"),
        "markets_known_winner": known_winner_markets,
        "markets_unknown_winner": unknown_winner_markets,
        "bands": rows,
        "pair_cost_tail_by_gap": tail,
    }
    OUT_JSON.write_text(json.dumps(out, indent=1))

    print(f"{'band':<8}{'fills':>8}{'qty':>10}{'avg_px':>8}{'sz/fill':>8}"
          f"{'match%':>8}{'pairCr$':>9}{'win%':>7}{'be%':>7}{'edge':>8}{'dirP&L$':>9}")
    for r in rows:
        f = lambda v, s: format(v, s) if v is not None else "     -"
        print(f"{r['band']:<8}{r['n_fills']:>8}{r['qty']:>10.0f}{r['avg_price']:>8.3f}"
              f"{r['avg_fill_size']:>8.1f}{r['matched_pct']:>8.1f}"
              f"{r['pair_locked_credit']:>9.0f}"
              f"{f(r['dir_win_rate'], '>7.3f')}{f(r['dir_breakeven_rate'], '>7.3f')}"
              f"{f(r['dir_edge_per_share'], '>8.4f')}{r['dir_settlement_pnl']:>9.0f}")
    print("\npair-cost tail by gap bucket:")
    print(json.dumps(tail, indent=1))
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
