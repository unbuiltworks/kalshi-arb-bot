"""Runner for the 5m coverage recorder. READ ONLY, PUBLIC ENDPOINTS ONLY.

There is no order verb, no credential read and no signing path in this module
or in anything it imports — `recorder` is stdlib-only and this file imports
nothing from the execution tree. It cannot be armed, because there is nothing
here to arm.

Durability: every sample is appended to a per-window JSONL under --out-dir as
it is taken, so a restart loses at most the current second rather than the
window. Digests are written beside the raw tape. Nothing is held only in RAM.

Fail-soft by design: one bad read must not end a 24/7 collection, so every
venue call is wrapped and a failure becomes a null-with-reason sample rather
than an exception. The service restart policy is the backstop, not the plan.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from src.l5_5m_coverage import recorder as R

L5 = "l5zn1bwom8etsk"
GAMMA = "https://gamma-api.polymarket.com/markets?slug={slug}"
CLOB = "https://clob.polymarket.com/book?token_id={tid}"
ACTIVITY = ("https://data-api.polymarket.com/activity"
            "?user={user}&limit=100&type=TRADE")
UA = {"User-Agent": "l5-5m-coverage/1.0 (read-only research collector)"}


def _get(url: str, timeout: float = 4.0) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers=UA, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.loads(fh.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError, TimeoutError):
        return None            # a failed read is a null sample, never a crash


def book_for(slug: str, cache: Dict[str, Any]) -> tuple:
    """(book, source_ts). Both None when the venue does not serve it."""
    meta = cache.get(slug)
    if meta is None:
        m = _get(GAMMA.format(slug=slug))
        meta = (m or [None])[0] if isinstance(m, list) else None
        cache[slug] = meta or {}
    if not meta:
        return None, None
    try:
        tids = json.loads(meta.get("clobTokenIds") or "[]")
    except ValueError:
        return None, None
    if not tids:
        return None, None
    b = _get(CLOB.format(tid=tids[0]))
    if not isinstance(b, dict):
        return None, None
    bids, asks = b.get("bids") or [], b.get("asks") or []
    top_b = max(bids, key=lambda x: float(x["price"])) if bids else None
    top_a = min(asks, key=lambda x: float(x["price"])) if asks else None
    ts = b.get("timestamp")
    return ({
        "up_bid": top_b and top_b.get("price"),
        "up_ask": top_a and top_a.get("price"),
        "up_bid_size": top_b and top_b.get("size"),
        "up_ask_size": top_a and top_a.get("size"),
    }, (float(ts) / 1000.0 if ts else None))


def l5_fills(seen: List[List[tuple]]) -> List[tuple]:
    """L5's recent fills, multiset-union deduped across overlapping polls."""
    a = _get(ACTIVITY.format(user=L5))
    resp = []
    for r in (a or []):
        resp.append((str(r.get("conditionId")), int(r.get("timestamp") or 0),
                     str(r.get("side")), str(r.get("outcome")),
                     float(r.get("price") or 0), float(r.get("size") or 0),
                     float(r.get("usdcSize") or 0)))
    seen.append(resp)
    del seen[:-8]                       # keep a short overlap window
    return R.dedupe_fills(seen)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-5m")
    ap.add_argument("--hours", type=float, default=0.0)     # 0 == continuous
    a = ap.parse_args(argv)
    os.makedirs(a.out_dir, exist_ok=True)
    deadline = time.time() + a.hours * 3600 if a.hours else None

    open_w: Dict[tuple, Dict[str, Any]] = {}
    digests: List[Dict[str, Any]] = []
    meta_cache: Dict[str, Any] = {}
    fill_pages: List[List[tuple]] = []
    last_report = 0.0

    while deadline is None or time.time() < deadline:
        now = time.time()
        # ROLL EVERY ITERATION, never on a discovery tick: that fusion is what
        # cost the Gemini collector 82s of every window.
        for asset in R.ASSETS:
            for k in (0, 1):
                st = R.window_start(now, k)
                if now >= R.due_at(st) and (asset, st) not in open_w:
                    open_w[(asset, st)] = {"samples": [], "hist": []}
        fills = l5_fills(fill_pages)
        for (asset, st), w in list(open_w.items()):
            if now > st + R.WINDOW_S:
                d = R.digest(asset, st, w["samples"])
                digests.append(d)
                with open(os.path.join(a.out_dir, "digests.jsonl"), "a") as fh:
                    fh.write(json.dumps(d) + "\n")
                del open_w[(asset, st)]
                continue
            sl = R.slug(asset, st)
            book, src = book_for(sl, meta_cache)
            recent = [f for f in fills if st - R.PRE_ROLL_S <= f[1] <= st + R.WINDOW_S]
            up = sum(f[5] for f in recent if f[3] == "Up" and f[2] == "BUY")
            dn = sum(f[5] for f in recent if f[3] != "Up" and f[2] == "BUY")
            s = R.sample(
                asset=asset, start=st, now=now, book=book, t_source=src,
                inv={"up": up, "dn": dn,
                     "cost_up": sum(f[6] for f in recent if f[3] == "Up" and f[2] == "BUY"),
                     "cost_dn": sum(f[6] for f in recent if f[3] != "Up" and f[2] == "BUY")},
                flow={"l5_fill": bool([f for f in recent if now - 1 <= f[1] <= now]),
                      "buy_qty": sum(f[5] for f in recent if f[2] == "BUY"),
                      "sell_qty": sum(f[5] for f in recent if f[2] == "SELL")},
                history=w["hist"])
            w["samples"].append(s)
            w["hist"].append({"t": now, "mid": s["mid"],
                              "attainable_pair_cost": s["attainable_pair_cost"]})
            del w["hist"][:-120]
            with open(os.path.join(a.out_dir, f"{sl}.jsonl"), "a") as fh:
                fh.write(json.dumps(s) + "\n")
        if now - last_report > 300:
            print("L5_5M_COVERAGE " + json.dumps(R.run_digest(digests)), flush=True)
            last_report = now
        time.sleep(max(0.0, R.SAMPLE_S - (time.time() - now)))
    print("L5_5M_COVERAGE " + json.dumps(R.run_digest(digests)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
