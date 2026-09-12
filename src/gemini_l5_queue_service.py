"""Queue-aware READ-ONLY Gemini L5 shadow service.

This isolated wrapper keeps the canonical Gemini shadow untouched while adding
25-level depth and a sequential same-tape queue experiment. The experiment
uses the existing Gemini transfer initializer to choose the opening side, then
compares touch / 1 populated level back / 2 populated levels back for that
first leg and, only after a strict first-leg trade-through, the opposite-side
fee-safe complement levels on the then-current book.

No credentials, order transport, or live execution path are present.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal as D
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
            "YES": q.populated_price_arms(depth, True, tb.TICK, max_back=QUEUE_BACK_LEVELS),
            "NO": q.populated_price_arms(depth, False, tb.TICK, max_back=QUEUE_BACK_LEVELS),
        }
        row["queue_semantics"] = q.SEMANTICS
        self.bbo.append(row)
        if len(self.bbo) > base.MAX_TAPE_ROWS:
            del self.bbo[:len(self.bbo) - base.MAX_TAPE_ROWS]
        return row


class QueueShadowService(base.ShadowService):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.queue_arm_records: list[dict] = []
        self.queue_scored_symbols: set[str] = set()
        p = self.root / "queue_arm_records.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("symbol") and rec["symbol"] not in self.queue_scored_symbols:
                    self.queue_arm_records.append(rec)
                    self.queue_scored_symbols.add(rec["symbol"])
        base._log("queue_arm_records_restored", windows=len(self.queue_arm_records))

    def _append_queue_arm_record(self, rec: Mapping[str, Any]) -> None:
        self.queue_arm_records.append(dict(rec))
        self.queue_scored_symbols.add(str(rec.get("symbol")))
        try:
            with (self.root / "queue_arm_records.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass

    @staticmethod
    def _strict_fill(prints, side_yes: bool, price: D,
                     start_s: float, end_s: float) -> Optional[dict]:
        px = D(str(price))
        for p in prints or []:
            ts = int(p.get("timestampms") or 0) / 1000.0
            if ts <= start_s or ts > end_s:
                continue
            try:
                pp = D(str(p.get("price")))
            except Exception:
                continue
            typ = str(p.get("type") or "").lower()
            hit = (side_yes and typ == "sell" and pp < px) or (
                (not side_yes) and typ == "buy" and pp > D("1") - px)
            if hit:
                return {"t": ts, "timestampms": int(p.get("timestampms") or 0),
                        "print_price": str(pp), "print_type": typ,
                        "quoted_price": str(px), "tid": p.get("tid")}
        return None

    @staticmethod
    def _row_at_or_after(rows, t: float) -> Optional[Mapping[str, Any]]:
        after = [r for r in rows if float(r.get("t") or 0) >= t and r.get("depth")]
        if after:
            return min(after, key=lambda r: float(r.get("t") or 0))
        before = [r for r in rows if float(r.get("t") or 0) <= t and r.get("depth")]
        return max(before, key=lambda r: float(r.get("t") or 0)) if before else None

    def _evaluate_sequential_queue(self, sym: str, tape: QueueWindowTape) -> dict:
        rows = sorted([r for r in tape.bbo if r.get("depth")],
                      key=lambda r: float(r.get("t") or 0))
        prints = tape.sorted_prints()
        expiry = float(tape.expiry or 0)
        entry = None
        open_side = None
        first_arms = []

        for r in rows:
            t = float(r.get("t") or 0)
            tte = expiry - t
            if not (tb.MIN_TTE_S <= tte <= tb.ENTRY_MAX_TTE_S):
                continue
            try:
                chosen = tb.choose_open_side(D(str(r.get("bid"))), D(str(r.get("bid_sz"))),
                                             D(str(r.get("ask"))), D(str(r.get("ask_sz"))))
            except Exception:
                chosen = None
            if not chosen:
                continue
            side = str(chosen["side"])
            arms = (r.get("queue_arms") or {}).get(side) or []
            arms = [a for a in arms
                    if tb.MIN_PRICE <= D(str(a["price"])) <= tb.MAX_PRICE]
            if arms:
                entry, open_side, first_arms = r, side, arms
                break

        if entry is None:
            return {"symbol": sym, "status": "NO_QUEUE_ENTRY_COVERAGE",
                    "orders_placed": 0, "semantics": q.SEMANTICS}

        entry_t = float(entry.get("t") or 0)
        first_end = min(entry_t + tb.FIRST_WAIT_S,
                        expiry - tb.STOP_BEFORE_EXPIRY_S)
        side_yes = open_side == "YES"
        out = {"symbol": sym, "status": "SCORED", "opening_side": open_side,
               "entry_t": entry_t, "entry_tte_s": round(expiry - entry_t, 3),
               "entry_spread": entry.get("spread_depth"),
               "entry_flow_60s": entry.get("flow_60s"),
               "entry_movement_60s": entry.get("movement_60s"),
               "arms": [], "orders_placed": 0, "semantics": q.SEMANTICS}

        for fa in first_arms[:QUEUE_BACK_LEVELS + 1]:
            first_px = D(str(fa["price"]))
            ff = self._strict_fill(prints, side_yes, first_px, entry_t, first_end)
            arm = {"entry_arm": fa.get("arm"),
                   "populated_levels_back": int(fa.get("populated_levels_back") or 0),
                   "opening_side": open_side, "first_price": str(first_px),
                   "first_queue": fa.get("queue"), "first_fill": ff,
                   "complements": []}
            if ff is None:
                arm["status"] = "NO_FIRST_FILL"
                out["arms"].append(arm)
                continue

            comp_row = self._row_at_or_after(rows, ff["t"])
            if comp_row is None:
                arm["status"] = "NO_COMPLEMENT_BOOK"
                out["arms"].append(arm)
                continue
            comp_yes = not side_yes
            candidates = q.fee_safe_price_arms(
                comp_row.get("depth") or {}, comp_yes, tb.TICK, first_px,
                tb.maker_fee, tb.MIN_EDGE, max_back=QUEUE_BACK_LEVELS)
            candidates = [c for c in candidates
                          if tb.MIN_PRICE <= D(str(c["price"])) <= tb.MAX_PRICE]
            comp_end = expiry - tb.STOP_BEFORE_EXPIRY_S
            for cand in candidates:
                cp = D(str(cand["price"]))
                cf = (self._strict_fill(prints, comp_yes, cp, ff["t"], comp_end)
                      if cand.get("fee_safe") else None)
                arm["complements"].append({**cand,
                    "side": "YES" if comp_yes else "NO",
                    "quote_t": float(comp_row.get("t") or ff["t"]),
                    "flow_60s": comp_row.get("flow_60s"),
                    "movement_60s": comp_row.get("movement_60s"),
                    "strict_fill": cf,
                    "completed": cf is not None,
                    "completion_latency_s": (round(cf["t"] - ff["t"], 3)
                                               if cf is not None else None)})
            arm["status"] = ("COMPLEMENTED" if any(c["completed"] for c in arm["complements"])
                             else "RESIDUAL")
            out["arms"].append(arm)
        return out

    def queue_arm_summary(self) -> dict:
        out: dict[str, Any] = {"windows": len(self.queue_arm_records), "entry_arms": {}}
        for back in range(QUEUE_BACK_LEVELS + 1):
            key = "touch" if back == 0 else f"{back}_populated_level_back"
            arms = [a for rec in self.queue_arm_records for a in rec.get("arms") or []
                    if int(a.get("populated_levels_back") or 0) == back]
            first = [a for a in arms if a.get("first_fill")]
            comp: dict[str, Any] = {}
            for cb in range(QUEUE_BACK_LEVELS + 1):
                ckey = "touch" if cb == 0 else f"{cb}_populated_level_back"
                cands = [c for a in first for c in a.get("complements") or []
                         if int(c.get("populated_levels_back") or 0) == cb]
                safe = [c for c in cands if c.get("fee_safe")]
                done = [c for c in safe if c.get("completed")]
                lats = sorted(float(c["completion_latency_s"]) for c in done
                              if c.get("completion_latency_s") is not None)
                comp[ckey] = {"fee_safe_opportunities": len(safe),
                              "strict_completions": len(done),
                              "completion_rate_pct": (round(100.0 * len(done) / len(safe), 1)
                                                      if safe else None),
                              "completion_latency_s_p50": (lats[len(lats)//2] if lats else None)}
            out["entry_arms"][key] = {
                "windows": len(arms), "first_fill_windows": len(first),
                "first_fill_rate_pct": (round(100.0 * len(first) / len(arms), 1)
                                        if arms else None),
                "residual_windows": sum(a.get("status") == "RESIDUAL" for a in arms),
                "complemented_windows": sum(a.get("status") == "COMPLEMENTED" for a in arms),
                "complements": comp}
        return out

    def _replay_expired(self, now: float) -> None:
        for sym, tape in list(self.tapes.items()):
            if (sym in self.queue_scored_symbols or tape.replayed or tape.expiry is None
                    or now < tape.expiry + base.SETTLE_GRACE_S):
                continue
            try:
                rec = self._evaluate_sequential_queue(sym, tape)
            except Exception as exc:
                rec = {"symbol": sym, "status": "QUEUE_ARM_ERROR",
                       "err": f"{type(exc).__name__}:{str(exc)[:160]}",
                       "orders_placed": 0, "semantics": q.SEMANTICS}
            rec["labels"] = LABELS
            self._append_queue_arm_record(rec)
            base._log("queue_arm_window", result=rec,
                      cumulative=self.queue_arm_summary())
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
                book = self.fetch(f"{base.API}/v1/book/{sym}?limit_bids={DEPTH_LEVELS}&limit_asks={DEPTH_LEVELS}")
                row = tape.add_book(book, now)
                if row:
                    out["books"] += 1
                self.fetches += 1
                trades = self.fetch(f"{base.API}/v1/trades/{sym}?limit_trades=500")
                out["prints"] += tape.add_prints(trades)
                if row:
                    row["flow_60s"] = q.recent_flow(tape.sorted_prints(), int(now * 1000), window_s=60)
                    row["movement_60s"] = q.movement(tape.bbo, now, window_s=60)
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
            rep["queue_collection"] = {"depth_levels": DEPTH_LEVELS,
                "arms": ["touch", "1_populated_level_back", "2_populated_level_back"],
                "sequential_queue_arm_summary": self.queue_arm_summary(),
                "semantics": q.SEMANTICS, "orders_placed": 0}
            base._log("queue_rolling_report", **rep)
            try:
                (self.root / "report.json").write_text(json.dumps(rep, indent=1, default=str, sort_keys=True))
            except OSError:
                pass
        return out

    def status(self) -> dict:
        out = super().status()
        out["queue_collection"] = {"depth_levels": DEPTH_LEVELS,
            "arms": ["touch", "1_populated_level_back", "2_populated_level_back"],
            "sequential_queue_arm_summary": self.queue_arm_summary(),
            "semantics": q.SEMANTICS, "orders_placed": 0}
        out["labels"] = LABELS
        return out


def main() -> int:
    if os.getenv("GEMINI_SHADOW_ROLE", "gemini-l5-shadow") != "gemini-l5-shadow":
        print("SERVICE_ROLE_MISMATCH", flush=True)
        return 64
    svc = QueueShadowService()
    try:
        srv = base.start_report_server(lambda: svc.rolling_report(time.time()))
        base._log("queue_report_server", port=srv.server_address[1], paths=["/rolling", "/health"])
    except Exception as exc:
        base._log("queue_report_server_error", err=f"{type(exc).__name__}:{str(exc)[:160]}")
    svc.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
