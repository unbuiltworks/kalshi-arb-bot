"""READ-ONLY Polymarket US live microstructure sampler (public gateway).

Polls BBO + top-of-book for live MLB markets on gateway.polymarket.us
(keyless, well under the 20 req/s public cap) and appends JSONL rows.
Measures: spread, depth at touch, BBO update cadence, lastTradePx moves,
and sharesTraded deltas (public taker-volume proxy). No orders, no auth.
"""
from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CA = "/root/.ccr/ca-bundle.crt"
G = "https://gateway.polymarket.us"
OUT = Path("data/transfer_backtest/pmus_live_sample.jsonl")


def get(path, ctx):
    req = urllib.request.Request(G + path, headers={"User-Agent": "research"})
    with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
        return json.loads(r.read())


def main(minutes: float, poll_s: float) -> int:
    ctx = ssl.create_default_context(cafile=CA)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + minutes * 60
    with OUT.open("a", encoding="utf-8") as sink:
        while time.time() < deadline:
            try:
                ev = get("/v2/leagues/mlb/events", ctx)
                evs = ev if isinstance(ev, list) else ev.get("events") or []
                slugs = []
                for e in evs:
                    if not e.get("live"):
                        continue
                    for m in e.get("markets", []):
                        if m.get("slug", "").startswith(("aec-", "a5c-")):
                            slugs.append(m["slug"])
                for slug in slugs[:6]:
                    try:
                        bbo = get(f"/v1/markets/{slug}/bbo", ctx)["marketData"]
                        book = get(f"/v1/markets/{slug}/book", ctx)["marketData"]
                        bids = book.get("bids") or []
                        asks = book.get("asks") or []
                        row = {
                            "t": datetime.now(timezone.utc).isoformat(),
                            "slug": slug,
                            "bid": (bbo.get("bestBid") or {}).get("value"),
                            "bid_qty": bids[0]["qty"] if bids else None,
                            "ask": (bbo.get("bestAsk") or {}).get("value"),
                            "ask_qty": asks[0]["qty"] if asks else None,
                            "last": (bbo.get("lastTradePx") or {}).get("value"),
                            "shares_traded": bbo.get("sharesTraded"),
                            "open_interest": bbo.get("openInterest"),
                        }
                        sink.write(json.dumps(row, separators=(",", ":")) + "\n")
                    except Exception as exc:
                        sink.write(json.dumps({"slug": slug,
                                               "err": str(exc)[:80]}) + "\n")
                sink.flush()
            except Exception as exc:
                sink.write(json.dumps({"err": str(exc)[:80]}) + "\n")
            time.sleep(poll_s)
    return 0


if __name__ == "__main__":
    mins = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    poll = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    raise SystemExit(main(mins, poll))
