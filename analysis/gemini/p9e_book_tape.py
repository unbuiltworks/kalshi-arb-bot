"""Paired book+tape recorder -- the input a decodable tape needs.

    python3 analysis/gemini/p9e_book_tape.py <out.jsonl> <minutes> [category ...]

WHY THIS EXISTS. P9-D found that a contract's `/v1/trades` feed cannot be
decoded on its own: for most slate families, 62-100% of the distinct prices a
contract prints have their MIRROR also present and 42-100% of contracts print
across both halves of [0,1], which a single outcome's price does not do. A row
therefore cannot be assigned to a side -- and every downstream quantity
(complement availability, pair cost, markout, maker-fill simulation) needs that
assignment.

The book settles it: `bestBid`/`bestAsk` are published in the CONTRACT's own
space, so a print recorded next to a contemporaneous book can be assigned by
which reading lies inside it. `lastTradePrice` cannot do this job -- it appears
to echo the most recent tape row whatever space that row is in, so comparing
the two is circular.

READ-ONLY. Two public GETs, a global rate limit, no credential, no order path.
Trades are de-duplicated by the venue's own `tid`, so a print is recorded once
however many polls see it.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "https://api.gemini.com"
RATE = 4.0
_lock = threading.Lock()
_next = [0.0]


def _slot():
    with _lock:
        now = time.time()
        t = max(now, _next[0])
        _next[0] = t + 1.0 / RATE
    d = t - time.time()
    if d > 0:
        time.sleep(d)


def get(path, tries=2):
    for i in range(tries):
        _slot()
        try:
            with urllib.request.urlopen(BASE + path, timeout=20) as r:
                return json.load(r)
        except Exception:                                      # noqa: BLE001
            if i == tries - 1:
                return None
    return None


def slate(categories):
    out = []
    for c in categories:
        d = get(f"/v1/prediction-markets/events?limit=100&category={c}")
        for e in ((d or {}).get("data") or []):
            if float(e.get("volume") or 0) <= 0:
                continue
            out.append(e)
    return out


def record(out_path, minutes, categories):
    events = slate(categories)
    syms = []
    for e in events:
        for c in (e.get("contracts") or []):
            if c.get("instrumentSymbol") and c.get("status") == "active":
                syms.append((e.get("ticker"), c["instrumentSymbol"],
                             e.get("category"),
                             (e.get("subcategory") or {}).get("name")))
    print(f"recording {len(syms)} contracts for {minutes} min", file=sys.stderr)
    seen_tids = set()
    end = time.time() + minutes * 60
    n_book = n_trade = 0
    with open(out_path, "a") as fh:
        while time.time() < end:
            for ticker, sym, cat, sub in syms:
                ev = get(f"/v1/prediction-markets/events/{ticker}")
                cs = (ev or {}).get("contracts") or []
                now = time.time()
                for c in cs:
                    if c.get("instrumentSymbol") != sym:
                        continue
                    pr = c.get("prices") or {}
                    fh.write(json.dumps({
                        "kind": "book", "t": now, "symbol": sym,
                        "category": cat, "subcategory": sub,
                        "bid": pr.get("bestBid"), "ask": pr.get("bestAsk"),
                        "last": pr.get("lastTradePrice"),
                        "expiry": c.get("expiryDate"),
                        "start": c.get("effectiveDate"),
                        "resolution": c.get("resolutionSide")}) + "\n")
                    n_book += 1
                tp = get(f"/v1/trades/{sym}?limit_trades=50")
                if isinstance(tp, list):
                    for t in tp:
                        tid = t.get("tid")
                        if tid in seen_tids:
                            continue
                        seen_tids.add(tid)
                        fh.write(json.dumps({
                            "kind": "trade", "symbol": sym, "tid": tid,
                            "t": t.get("timestamp"), "tms": t.get("timestampms"),
                            "price": t.get("price"), "amount": t.get("amount"),
                            "type": t.get("type")}) + "\n")
                        n_trade += 1
                fh.flush()
    print(f"book rows {n_book}, trade rows {n_trade}", file=sys.stderr)


if __name__ == "__main__":
    out = sys.argv[1]
    mins = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    cats = sys.argv[3:] or ["Crypto", "Sports", "Politics", "Economics"]
    record(out, mins, cats)
