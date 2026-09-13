"""Queue-aware READ-ONLY Gemini L5 shadow service.

This isolated service keeps the canonical Gemini L5 behavioral replay intact
while collecting 25 levels of public depth. At expiry it anchors execution
counterfactuals to canonical decisions and also runs a larger-action-budget
multi-fill inventory-path replay to test repeated accumulation/rebalancing.

No credentials, order transport, or live execution path are present.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from src import gemini_l5_complement_cf as ccf
from src import gemini_l5_inventory_cf as invcf
from src import gemini_l5_queue_shadow as q
from src import gemini_l5_shadow_service as base
from src import gemini_l5_shadow_v2 as gs
from src.polymarket_l5 import transfer_backtest as tb

DEPTH_LEVELS = 25
QUEUE_BACK_LEVELS = 2
POST_FILL_ATTRIBUTION_VERSION = 2
LABELS = (
    "READ-ONLY Gemini queue-aware + multi-fill inventory shadow; zero orders; "
    "public depth/prints only; counterfactuals are anchored to canonical L5 state; "
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
            "YES": q.populated_price_arms(depth, True, tb.TICK,
                                           max_back=QUEUE_BACK_LEVELS),
            "NO": q.populated_price_arms(depth, False, tb.TICK,
                                          max_back=QUEUE_BACK_LEVELS),
        }
        row["queue_semantics"] = q.SEMANTICS
        self.bbo.append(row)
        if len(self.bbo) > base.MAX_TAPE_ROWS:
            del self.bbo[:len(self.bbo) - base.MAX_TAPE_ROWS]
        return row


class QueueShadowService(base.ShadowService):
    """Canonical shadow plus queue/complement/multi-fill counterfactuals."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cf_records: list[dict] = []
        self.cf_scored_symbols: set[str] = set()
        self.inventory_records: list[dict] = []
        self.inventory_scored_symbols: set[str] = set()
        self._restore_records(self.root / "complement_cf_records.jsonl",
                              self.cf_records, self.cf_scored_symbols)
        self._restore_records(self.root / "inventory_cf_records.jsonl",
                              self.inventory_records, self.inventory_scored_symbols)
        base._log("counterfactual_records_restored",
                  complement_windows=len(self.cf_records),
                  inventory_windows=len(self.inventory_records),
                  post_fill_attribution_version=POST_FILL_ATTRIBUTION_VERSION)

    @staticmethod
    def _restore_records(path, records: list[dict], symbols: set[str]) -> None:
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            sym = rec.get("symbol")
            if sym and sym not in symbols:
                records.append(rec)
                symbols.add(sym)

    def _append_record(self, filename: str, rec: Mapping[str, Any],
                       records: list[dict], symbols: set[str]) -> None:
        records.append(dict(rec))
        symbols.add(str(rec.get("symbol")))
        try:
            with (self.root / filename).open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass

    def _replay_expired(self, now: float) -> None:
        # Counterfactuals first, while the queue-enriched tape is still in memory.
        # Canonical base replay then records its untouched evaluator rows.
        for sym, tape in list(self.tapes.items()):
            if tape.replayed or tape.expiry is None or now < tape.expiry + base.SETTLE_GRACE_S:
                continue
            if sym not in self.cf_scored_symbols:
                try:
                    rec = ccf.evaluate(sym, tape.bbo, tape.sorted_prints(),
                                       model=self.model, art=self.art)
                except Exception as exc:
                    rec = {"symbol": sym, "status": "COMPLEMENT_CF_ERROR",
                           "err": f"{type(exc).__name__}:{str(exc)[:180]}",
                           "orders_placed": 0, "labels": LABELS}
                rec["orders_placed"] = 0
                rec["post_fill_attribution_version"] = POST_FILL_ATTRIBUTION_VERSION
                self._append_record("complement_cf_records.jsonl", rec,
                                    self.cf_records, self.cf_scored_symbols)
                base._log("complement_cf_window", result=rec,
                          cumulative=ccf.summarize(self.cf_records))
            if sym not in self.inventory_scored_symbols:
                try:
                    irec = invcf.evaluate(sym, tape.bbo, tape.sorted_prints(),
                                          model=self.model, art=self.art)
                except Exception as exc:
                    irec = {"symbol": sym, "status": "INVENTORY_CF_ERROR",
                            "err": f"{type(exc).__name__}:{str(exc)[:180]}",
                            "orders_placed": 0, "labels": LABELS}
                irec["orders_placed"] = 0
                irec["post_fill_attribution_version"] = POST_FILL_ATTRIBUTION_VERSION
                self._append_record("inventory_cf_records.jsonl", irec,
                                    self.inventory_records, self.inventory_scored_symbols)
                base._log("inventory_cf_window", result=irec,
                          cumulative=invcf.summarize(self.inventory_records))
        super()._replay_expired(now)

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
            except Exception as exc:
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
            rep["queue_collection"] = self._research_summary()
            base._log("queue_rolling_report", **rep)
            try:
                (self.root / "report.json").write_text(
                    json.dumps(rep, indent=1, default=str, sort_keys=True))
            except OSError:
                pass
        return out

    def _research_summary(self) -> dict:
        return {
            "depth_levels": DEPTH_LEVELS,
            "counterfactual": "canonical complement decisions + 64-action multi-fill inventory path",
            "post_fill_attribution_version": POST_FILL_ATTRIBUTION_VERSION,
            "complement_counterfactual_summary": ccf.summarize(self.cf_records),
            "multi_fill_inventory_summary": invcf.summarize(self.inventory_records),
            "semantics": q.SEMANTICS,
            "orders_placed": 0,
        }

    def status(self) -> dict:
        out = super().status()
        out["queue_collection"] = self._research_summary()
        out["labels"] = LABELS
        return out


def main() -> int:
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
