"""Queue-aware READ-ONLY Gemini L5 shadow service.

This is an isolated wrapper around the canonical Gemini shadow service. It
changes only the public-data collection layer:
- fetches 25 bid/ask levels instead of top-of-book only
- retains bounded depth on every BBO row
- records touch / 1-populated-level-back / 2-populated-level-back queue arms
- records 60s public-print flow and 60s mid movement

Replay/fill semantics remain the canonical strict trade-through shadow. No
credentials, order transport, or live execution path are added here.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from src import gemini_l5_queue_shadow as q
from src import gemini_l5_shadow_service as base
from src import gemini_l5_shadow_v2 as gs
from src.polymarket_l5 import transfer_backtest as tb

DEPTH_LEVELS = 25
QUEUE_BACK_LEVELS = 2
LABELS = (
    "READ-ONLY Gemini queue-aware shadow; zero orders; public depth/prints only; "
    "strict trade-through is hypothetical maker-fill evidence, not a venue fill; "
    "all P&L remains THEORETICAL"
)


class QueueWindowTape(base.WindowTape):
    """Canonical window tape plus bounded Gemini depth/queue geometry."""

    def add_book(self, raw: Mapping[str, Any], now: float) -> Optional[dict]:
        row = gs.book_from_public(self.symbol, raw, now)
        if row["bid"] is None and row["ask"] is None:
            return None

        depth = q.retain_book(raw, max_levels=DEPTH_LEVELS)
        spr = q.spread(depth)
        row["depth"] = depth
        row["depth_levels"] = DEPTH_LEVELS
        row["spread_depth"] = str(spr) if spr is not None else None
        row["queue_arms"] = {
            "YES": q.populated_price_arms(
                depth, True, tb.TICK, max_back=QUEUE_BACK_LEVELS),
            "NO": q.populated_price_arms(
                depth, False, tb.TICK, max_back=QUEUE_BACK_LEVELS),
        }
        row["queue_semantics"] = q.SEMANTICS

        self.bbo.append(row)
        if len(self.bbo) > base.MAX_TAPE_ROWS:
            del self.bbo[:len(self.bbo) - base.MAX_TAPE_ROWS]
        return row


class QueueShadowService(base.ShadowService):
    """Canonical shadow with queue-aware collection; execution remains absent."""

    def poll_once(self) -> dict:
        now = self.clock()
        out = {"books": 0, "prints": 0, "errors": 0}
        for sym in gs.nearest_windows(datetime.fromtimestamp(now, timezone.utc)):
            tape = self.tapes.get(sym)
            if tape is None:
                if any((sym, k) in self.seen for k in
                       (f"{n}:{p_}" for n in gs.SHELLS for p_ in gs.POLICIES)):
                    continue
                tape = self.tapes[sym] = QueueWindowTape(sym)
                tape.opened_at = now
                base._log("queue_window_open", symbol=sym,
                          expiry_utc=base._utc(tape.expiry),
                          tte_at_open_s=round((tape.expiry or now) - now, 1),
                          depth_levels=DEPTH_LEVELS)
            try:
                self.fetches += 1
                book = self.fetch(
                    f"{base.API}/v1/book/{sym}?limit_bids={DEPTH_LEVELS}"
                    f"&limit_asks={DEPTH_LEVELS}")
                row = tape.add_book(book, now)
                if row:
                    out["books"] += 1

                self.fetches += 1
                trades = self.fetch(f"{base.API}/v1/trades/{sym}?limit_trades=500")
                out["prints"] += tape.add_prints(trades)

                if row:
                    row["flow_60s"] = q.recent_flow(
                        tape.sorted_prints(), int(now * 1000), window_s=60)
                    row["movement_60s"] = q.movement(
                        tape.bbo, now, window_s=60)
            except Exception as exc:  # venue/network/data; never place orders
                self.errors += 1
                out["errors"] += 1
                base._log("queue_fetch_error", symbol=sym,
                          err=f"{type(exc).__name__}:{str(exc)[:120]}")

        self._replay_expired(now)
        if now - self.last_status >= base.STATUS_EVERY_S:
            self.last_status = now
            base._log("queue_status", **self.status())
            self._write_summary()
        if now - self.last_report >= base.REPORT_EVERY_S:
            self.last_report = now
            rep = self.rolling_report(now)
            rep["queue_collection"] = {
                "depth_levels": DEPTH_LEVELS,
                "arms": ["touch", "1_populated_level_back", "2_populated_level_back"],
                "semantics": q.SEMANTICS,
                "orders_placed": 0,
            }
            base._log("queue_rolling_report", **rep)
            try:
                (self.root / "report.json").write_text(
                    json.dumps(rep, indent=1, default=str, sort_keys=True))
            except OSError:
                pass
        return out

    def status(self) -> dict:
        out = super().status()
        out["queue_collection"] = {
            "depth_levels": DEPTH_LEVELS,
            "arms": ["touch", "1_populated_level_back", "2_populated_level_back"],
            "semantics": q.SEMANTICS,
            "orders_placed": 0,
        }
        out["labels"] = LABELS
        return out


def main() -> int:
    # Preserve the existing fail-closed service role contract.
    if os.getenv("GEMINI_SHADOW_ROLE", "gemini-l5-shadow") != "gemini-l5-shadow":
        print("SERVICE_ROLE_MISMATCH", flush=True)
        return 64
    svc = QueueShadowService()
    try:
        srv = base.start_report_server(lambda: svc.rolling_report(time.time()))
        base._log("queue_report_server", port=srv.server_address[1],
                  paths=["/rolling", "/health"])
    except Exception as exc:
        base._log("queue_report_server_error",
                  err=f"{type(exc).__name__}:{str(exc)[:160]}")
    svc.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
