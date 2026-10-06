"""Load the archive at its NATIVE resolution. No interpolation, ever.

One loader for both contract lengths, so BTC5m and BTC15m are handled by
identical code and can be labelled separately rather than merged.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book

COLS = ["coin", "contract_length", "market_slug", "token_id", "outcome",
        "captured_at", "market_end_at", "seconds_to_close",
        "best_bid", "best_ask", "bid_prices", "bid_sizes",
        "ask_prices", "ask_sizes", "is_winning_outcome"]


def load(parquet: str, *, contract_length: Optional[str] = None,
         coin: str = "btc", slugs: Optional[set] = None
         ) -> Tuple[Dict[str, Book], Dict[str, Dict[str, Any]]]:
    import pyarrow.parquet as pq
    d = pq.read_table(parquet, columns=COLS).to_pydict()
    books: Dict[str, Book] = {}
    wins: Dict[str, Dict[str, Any]] = {}
    for i, c in enumerate(d["coin"]):
        if c != coin:
            continue
        cl = d["contract_length"][i]
        if contract_length and cl != contract_length:
            continue
        slug = d["market_slug"][i]
        if slugs is not None and slug not in slugs:
            continue
        tok = str(d["token_id"][i])
        w = wins.setdefault(slug, {"tokens": {}, "contract_length": cl,
                                   "winner": {}})
        w["tokens"][tok] = d["outcome"][i]
        w["winner"][tok] = bool(d["is_winning_outcome"][i])
        ts = d["captured_at"][i]
        books.setdefault(tok, Book()).add(ts.timestamp(), {
            "best_bid": d["best_bid"][i], "best_ask": d["best_ask"][i],
            "bid_prices": list(d["bid_prices"][i] or []),
            "bid_sizes": list(d["bid_sizes"][i] or []),
            "ask_prices": list(d["ask_prices"][i] or []),
            "ask_sizes": list(d["ask_sizes"][i] or []),
        })
    for b in books.values():
        b.sort()
    return books, wins


def l5_fills(backfill: str, block_times: str) -> Dict[str, List[dict]]:
    """slug -> fills, each timed by its REAL block timestamp (never
    interpolated). A fill whose block has no timestamp keeps _ts None and is
    excluded downstream rather than placed on a guessed second."""
    bt = {int(k): v for k, v in
          json.load(open(block_times))["block_times"].items()}
    bf = json.load(open(backfill))
    out: Dict[str, List[dict]] = {}
    for w in bf["rows"]:
        rows = []
        for f in w["fills"]:
            g = dict(f)
            g["_ts"] = bt.get(f.get("block"))
            rows.append(g)
        out[w["market_slug"]] = rows
    return out, bt, bf


def observations(books: Dict[str, Book], tokens: List[str], start: int,
                 end: int) -> List[Dict[str, Any]]:
    """Every GENUINE archived observation in [start, end), in time order,
    merged across the two tokens. This is the event stream: BOOK UPDATE ->
    BOOK UPDATE -> ... with no bucketing and nothing synthesised."""
    ev: List[Dict[str, Any]] = []
    for tok in tokens:
        bk = books.get(tok)
        if bk is None:
            continue
        for ts, row in zip(bk.t, bk.rows):
            if start <= ts < end:
                ev.append({"t": ts, "token_id": tok, "kind": "BOOK_UPDATE",
                           "best_bid": row.get("best_bid"),
                           "best_ask": row.get("best_ask"),
                           "bid_levels": len(row.get("bid_prices") or []),
                           "top_bid_size": (
                               (row.get("bid_sizes") or [None])[0]
                               if row.get("bid_sizes") else None),
                           "row": row})
    ev.sort(key=lambda x: x["t"])
    return ev
