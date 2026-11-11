"""Fetch historical public Polymarket sports trades for the transfer
backtest (read-only). Writes data/transfer_backtest/sports_trades.jsonl.gz
with one row per market: metadata + complete trade prints."""
from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.polymarket_l5 import sports_transfer_backtest as sb  # noqa: E402

OUT = Path("data/transfer_backtest/sports_trades.jsonl.gz")
TAGS = ["mlb", "nfl", "cfb", "tennis", "epl", "soccer", "wnba"]
END_MIN = "2026-08-31T00:00:00Z"
END_MAX = "2026-09-02T14:00:00Z"
KINDS = {"moneyline", "spread", "spreads", "totals", "total"}
MIN_VOLUME = 5000.0
MAX_MARKETS_PER_TAG = 18


def main() -> int:
    done = set()
    if OUT.exists():
        for line in gzip.open(OUT, "rt", encoding="utf-8"):
            try:
                done.add(json.loads(line)["conditionId"])
            except (json.JSONDecodeError, KeyError):
                continue
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "at", encoding="utf-8") as sink:
        for tag in TAGS:
            try:
                events = sb.fetch_closed_events(tag, END_MIN, END_MAX)
            except Exception as exc:
                print(json.dumps({"tag": tag, "error": str(exc)[:120]}))
                continue
            picks = []
            for ev in events:
                for m in ev.get("markets", []):
                    kind = (m.get("sportsMarketType") or "").lower()
                    vol = float(m.get("volume") or 0)
                    if kind in KINDS and vol >= MIN_VOLUME:
                        picks.append((vol, ev, m))
            picks.sort(key=lambda x: -x[0])
            picks = picks[:MAX_MARKETS_PER_TAG]
            fetched = 0
            for vol, ev, m in picks:
                cid = m.get("conditionId")
                if not cid or cid in done:
                    continue
                try:
                    trades = sb.fetch_market_trades(cid)
                except Exception as exc:
                    print(json.dumps({"cid": cid[:14], "error": str(exc)[:120]}))
                    continue
                sink.write(json.dumps({
                    "tag": tag, "event_title": ev.get("title"),
                    "event_start": ev.get("startDate"),
                    "game_start": m.get("gameStartTime"),
                    "question": m.get("question"),
                    "sportsMarketType": m.get("sportsMarketType"),
                    "volume": vol, "conditionId": cid,
                    "closed_time": m.get("closedTime"),
                    "maker_fee_bps": m.get("makerBaseFee"),
                    "taker_fee_bps": m.get("takerBaseFee"),
                    "outcomes": m.get("outcomes"),
                    "trades": trades}, separators=(",", ":")) + "\n")
                done.add(cid)
                fetched += 1
                time.sleep(0.2)
            print(json.dumps({"tag": tag, "events": len(events),
                              "markets_fetched": fetched}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
