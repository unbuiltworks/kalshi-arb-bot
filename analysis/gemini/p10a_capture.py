"""P10-A: capture trades and books together, with the clock on both.

    python3 analysis/gemini/p10a_capture.py <out.jsonl> <minutes> [n_symbols]

WHY. P9-D showed a contract's `/v1/trades` feed cannot be decoded on its own.
Deciding which side a print belongs to needs a book from the same moment, and
"the same moment" has to be MEASURED, not assumed: if the book we hold is two
seconds stale and the price moved, the classification is noise wearing a
uniform.

So every record carries its own timing. For each poll we write the request
time, the response time and the round trip; for each trade we write the
venue's `tid`, `timestampms`, `type`, `price` and `amount` verbatim. Trades are
de-duplicated by `tid`, so a print appears once however many polls see it, and
the book rows around it are recoverable by timestamp.

NOTHING IS CLASSIFIED HERE. This file only records. Deciding what a row means
is `p10b_decode.py`'s job, and it is kept separate so the capture cannot be
quietly shaped by the hypothesis it is meant to test.

READ-ONLY: two public GETs, a global rate limit, no credential, no order path.
"""
import json
import os
import sys
import threading
import time
import urllib.request

BASE = "https://api.gemini.com"
RATE = 4.5
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


def timed_get(path, tries=2):
    """Returns (payload, t_sent, t_recv). The two clocks are the point."""
    for i in range(tries):
        _slot()
        t0 = time.time()
        try:
            with urllib.request.urlopen(BASE + path, timeout=15) as r:
                body = json.load(r)
            return body, t0, time.time()
        except Exception:                                      # noqa: BLE001
            if i == tries - 1:
                return None, t0, time.time()
    return None, 0.0, 0.0


CATEGORIES = ("Crypto", "Weather", "Politics", "Economics")


def discover(n_symbols):
    """The busiest live contract in each category, ROUND ROBIN.

    Round robin rather than a ranked list, because the point is to sample the
    venue's different flow regimes. A ranked list fills every slot from
    whichever category happens to be busiest -- which is exactly what the first
    two captures of 2026-09-22 did, giving four crypto symbols and no coverage
    of the families whose tapes show the strongest mirror signature. The
    per-category candidate counts are recorded with the slate so a silently
    empty category cannot repeat that quietly.
    """
    per_cat, counts = [], {}
    for cat in CATEGORIES:
        d, _a, _b = timed_get(
            f"/v1/prediction-markets/events?limit=60&category={cat}", tries=3)
        rows = [e for e in ((d or {}).get("data") or [])
                if str(e.get("status")) == "active"]
        rows.sort(key=lambda e: -float(e.get("volume24h") or 0))
        cand = []
        for e in rows:
            for c in (e.get("contracts") or []):
                if c.get("instrumentSymbol") and c.get("status") == "active":
                    cand.append({
                        "ticker": e.get("ticker"),
                        "symbol": c["instrumentSymbol"],
                        "category": e.get("category"),
                        "subcategory": (e.get("subcategory") or {}).get("name"),
                        "expiry": c.get("expiryDate"),
                        "start": c.get("effectiveDate")})
        per_cat.append(cand)
        counts[cat] = len(cand)
    out, i = [], 0
    while len(out) < n_symbols and any(len(c) > i for c in per_cat):
        for cand in per_cat:
            if i < len(cand):
                out.append(cand[i])
            if len(out) >= n_symbols:
                break
        i += 1
    return out[:n_symbols], counts


def capture(out_path, minutes, n_symbols=4, rediscover_s=300):
    """Records to `<out_path>.partial` and renames on completion.

    WHY THE RENAME. A capture appends for the better part of an hour. If it
    writes straight into the repository, the working tree is dirty for the
    whole run and every commit carries a half-finished file. `.partial` is
    gitignored, so a running capture is invisible to git and the finished file
    appears once, complete.
    """
    part = out_path + ".partial"
    res = _capture_to(part, minutes, n_symbols, rediscover_s)
    os.replace(part, out_path)
    return res


def _capture_to(out_path, minutes, n_symbols=4, rediscover_s=300):
    end = time.time() + minutes * 60
    seen = set()
    syms = []
    last_disc = 0.0
    n_book = n_trade = 0
    with open(out_path, "a") as fh:
        while time.time() < end:
            if time.time() - last_disc > rediscover_s:
                syms, counts = discover(n_symbols)
                last_disc = time.time()
                fh.write(json.dumps({"kind": "slate", "t": time.time(),
                                     "candidates_per_category": counts,
                                     "symbols": [s["symbol"] for s in syms]}) + "\n")
            for s in syms:
                if time.time() >= end:
                    break
                ev, t0, t1 = timed_get(
                    f"/v1/prediction-markets/events/{s['ticker']}")
                for c in ((ev or {}).get("contracts") or []):
                    if c.get("instrumentSymbol") != s["symbol"]:
                        continue
                    pr = c.get("prices") or {}
                    fh.write(json.dumps({
                        "kind": "book", "symbol": s["symbol"],
                        "category": s["category"], "subcategory": s["subcategory"],
                        "t_sent": t0, "t_recv": t1, "rtt": t1 - t0,
                        "bid": pr.get("bestBid"), "ask": pr.get("bestAsk"),
                        "last": pr.get("lastTradePrice"),
                        "buy_yes": (pr.get("buy") or {}).get("yes"),
                        "buy_no": (pr.get("buy") or {}).get("no"),
                        "sell_yes": (pr.get("sell") or {}).get("yes"),
                        "sell_no": (pr.get("sell") or {}).get("no"),
                        "expiry": c.get("expiryDate"),
                        "start": c.get("effectiveDate"),
                        "resolution": c.get("resolutionSide")}) + "\n")
                    n_book += 1
                tp, t2, t3 = timed_get(f"/v1/trades/{s['symbol']}?limit_trades=50")
                if isinstance(tp, list):
                    for t in tp:
                        key = (s["symbol"], t.get("tid"))
                        if key in seen:
                            continue
                        seen.add(key)
                        fh.write(json.dumps({
                            "kind": "trade", "symbol": s["symbol"],
                            "category": s["category"],
                            "subcategory": s["subcategory"],
                            "tid": t.get("tid"), "t": t.get("timestamp"),
                            "tms": t.get("timestampms"),
                            "price": t.get("price"), "amount": t.get("amount"),
                            "type": t.get("type"),
                            "t_sent": t2, "t_recv": t3, "rtt": t3 - t2,
                            "first_seen": time.time()}) + "\n")
                        n_trade += 1
                fh.flush()
    return {"book_rows": n_book, "trade_rows": n_trade}


if __name__ == "__main__":
    out = sys.argv[1]
    mins = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    r = capture(out, mins, n)
    print(json.dumps(r), file=sys.stderr)
