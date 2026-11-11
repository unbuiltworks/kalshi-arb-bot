"""Keyless READ-ONLY Gemini BTC-5m two-sided book evidence poller.

The one blocker for judging both remaining one-sided-hold fixes (the
entry-time opposite-quote check and the safe taker-completion valve) is
ask-side book state time-aligned with shadow campaigns: prints prove
which takers ARRIVED, never what liquidity was RESTING. This poller
records both sides of the public book for the nearest BTC-5m windows so
those two variants can be evaluated with zero changes to the running
supervisor. Public GETs only; no auth, no orders; append-only JSONL.

    python3 scripts/gemini_book_evidence_poller.py --minutes 120 \
        --out /state/gemini-book-evidence
"""
from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.gemini.com"


def _ctx():
    return ssl.create_default_context()


def get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"User-Agent": "book-evidence"})
    with urllib.request.urlopen(req, context=_ctx(), timeout=timeout) as r:
        return json.loads(r.read())


def nearest_windows(now: datetime, count: int = 2) -> list[str]:
    """Current + next BTC-5m window symbols (expiry stamps the ticker)."""
    base = now.replace(second=0, microsecond=0)
    base += timedelta(minutes=(5 - base.minute % 5) % 5 or 5)
    return [f"GEMI-BTC05M{base + timedelta(minutes=5*i):%y%m%d%H%M}-UP"
            for i in range(count)]


def poll_once(symbols: list[str]) -> list[dict]:
    rows = []
    for sym in symbols:
        try:
            book = get_json(f"{API}/v1/book/{sym}"
                            "?limit_bids=5&limit_asks=5")
            rows.append({"kind": "book", "t":
                         datetime.now(timezone.utc).isoformat(),
                         "symbol": sym,
                         "bids": book.get("bids"), "asks": book.get("asks"),
                         "source": "api.gemini.com/v1/book"})
        except Exception as exc:
            rows.append({"kind": "error", "symbol": sym,
                         "t": datetime.now(timezone.utc).isoformat(),
                         "err": str(exc)[:100]})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=120.0)
    ap.add_argument("--poll", type=float, default=5.0)
    ap.add_argument("--out", default="data/transfer_backtest/gemini_book_evidence")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.minutes * 60
    while time.monotonic() < deadline:
        now = datetime.now(timezone.utc)
        rows = poll_once(nearest_windows(now))
        path = out / f"book_{now:%Y%m%d}.jsonl"
        with path.open("a", encoding="utf-8") as sink:
            for row in rows:
                sink.write(json.dumps(row, separators=(",", ":")) + "\n")
        time.sleep(args.poll)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
