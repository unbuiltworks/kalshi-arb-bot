"""Durable READ-ONLY Gemini L5 shadow service (OWNER 5554553793 B).

Collects the public Gemini BTC-5m book and prints for the current and
next window, and when a window expires replays it through the SAME
canonical v2 policy (modal + seeded stochastic tracks) under every
execution shell in `gemini_l5_shadow_v2.SHELLS`, strict fills only (a
print THROUGH our price). Records and cumulative per-shell summaries go
to the durable mount so restarts lose nothing. Zero orders, no
credentials, no Gemini strategy: the only knob shadowed is the
rest/cancel discipline, the identified transfer blocker.

Isolation: this service imports nothing from the PMUS probe/recorder,
reads no PMUS variable, and shares no volume with any other service.

    python3 -m src.gemini_l5_shadow_service            # runs forever
    GEMINI_SHADOW_STATE_DIR=/state/gemini-shadow
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from src import gemini_l5_shadow_v2 as gs
from src import pmus_l5_fidelity_live as fid
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5 import transfer_backtest as tb

API = "https://api.gemini.com"
STATE_ENV = "GEMINI_SHADOW_STATE_DIR"
DEFAULT_STATE_DIR = "/state/gemini-shadow"
POLL_S = 3.0
STATUS_EVERY_S = 300.0
REPORT_EVERY_S = 3600.0          # rolling 6h / 24h / cumulative reports
LIVE_SHELL = "touch_cancel_30s"  # the arm every other arm is compared to
SETTLE_GRACE_S = 20.0            # keep quoting the tape until after expiry
MAX_TAPE_ROWS = 4000             # per symbol, in memory (~3.3h at 3s)
CA_BUNDLE = "/root/.ccr/ca-bundle.crt"
LABELS = ("READ-ONLY Gemini shadow; zero orders; strict fills from public "
          "prints; settlement by mid proxy; THEORETICAL")


def _log(kind: str, **fields: Any) -> None:
    print("GEMINI_L5_SHADOW " + json.dumps({"kind": kind, **fields},
                                           default=str, sort_keys=True),
          flush=True)


def state_dir() -> Path:
    return Path(os.getenv(STATE_ENV, DEFAULT_STATE_DIR))


def default_fetch(url: str, timeout: float = 10.0) -> Any:
    ctx = (ssl.create_default_context(cafile=CA_BUNDLE)
           if Path(CA_BUNDLE).exists() else ssl.create_default_context())
    req = urllib.request.Request(url, headers={"User-Agent": "l5-shadow"})
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
        return json.loads(r.read())


class WindowTape:
    """Per-symbol public tape: bbo rows (tape row shape) + deduped prints."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.expiry = tb.ticker_expiry_epoch(tb.event_of_symbol(symbol))
        self.bbo: list[dict] = []
        self.prints: dict[int, dict] = {}
        self.replayed = False
        self.opened_at: float = 0.0

    def add_book(self, raw: Mapping[str, Any], now: float) -> Optional[dict]:
        row = gs.book_from_public(self.symbol, raw, now)
        if row["bid"] is None and row["ask"] is None:
            return None
        self.bbo.append(row)
        if len(self.bbo) > MAX_TAPE_ROWS:
            del self.bbo[:len(self.bbo) - MAX_TAPE_ROWS]
        return row

    def add_prints(self, rows: Any) -> int:
        new = 0
        for p in rows or []:
            if not isinstance(p, Mapping) or p.get("tid") is None:
                continue
            tid = int(p["tid"])
            if tid in self.prints:
                continue
            self.prints[tid] = {"timestampms": int(p.get("timestampms") or 0),
                                "price": str(p.get("price")),
                                "amount": str(p.get("amount")),
                                "type": str(p.get("type")), "tid": tid}
            new += 1
        return new

    def sorted_prints(self) -> list[dict]:
        return sorted(self.prints.values(), key=lambda x: x["timestampms"])


class ShadowService:
    def __init__(self, fetch: Callable[[str], Any] = default_fetch,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep,
                 root: Optional[Path] = None) -> None:
        self.fetch, self.clock, self.sleep = fetch, clock, sleep
        self.root = root or state_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.model = pv2.load_v2()
        self.art = fid.load()
        self.tapes: dict[str, WindowTape] = {}
        self.rows: dict[str, list[dict]] = {}      # shell:policy -> window rows
        self.errors = 0
        self.fetches = 0
        self.last_status = 0.0
        self.last_report = 0.0
        self.started_utc = _utc(self.clock())
        try:
            self.gate = gs.load_gate()
        except Exception:                             # noqa: BLE001
            self.gate = None
        self._reload_records()

    # ---- durable records ----
    def _records_path(self) -> Path:
        return self.root / "records.jsonl"

    def _reload_records(self) -> None:
        """Restore every record from the volume; a (symbol, shell, policy)
        seen twice on disk is kept once (first wins) so a restart can
        never double-count a window (OWNER 5555118026)."""
        p = self._records_path()
        self.seen: set = set()
        dupes = 0
        if not p.exists():
            _log("records_restored", windows=0, duplicates_dropped=0)
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:
                continue
            key = f"{r.get('shell')}:{r.get('policy')}"
            ident = (r.get("symbol"), key)
            if ident in self.seen:
                dupes += 1
                continue
            self.seen.add(ident)
            self.rows.setdefault(key, []).append(r)
        _log("records_restored", windows=sum(len(v) for v in self.rows.values()),
             symbols=len({i[0] for i in self.seen}), duplicates_dropped=dupes)

    def _append_record(self, rec: Mapping[str, Any]) -> None:
        with self._records_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def _append_tape(self, tape: WindowTape) -> None:
        with (self.root / "tape.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"symbol": tape.symbol, "bbo": tape.bbo,
                                "prints": tape.sorted_prints()}) + "\n")

    # ---- one poll ----
    def poll_once(self) -> dict:
        now = self.clock()
        out = {"books": 0, "prints": 0, "errors": 0}
        for sym in gs.nearest_windows(datetime.fromtimestamp(now, timezone.utc)):
            tape = self.tapes.get(sym)
            if tape is None:
                if any((sym, k) in self.seen for k in
                       (f"{n}:{p_}" for n in gs.SHELLS for p_ in gs.POLICIES)):
                    continue                  # already accounted for on disk
                tape = self.tapes[sym] = WindowTape(sym)
                tape.opened_at = now
                _log("window_open", symbol=sym, expiry_utc=_utc(tape.expiry),
                     tte_at_open_s=round((tape.expiry or now) - now, 1))
            try:
                self.fetches += 1
                book = self.fetch(f"{API}/v1/book/{sym}?limit_bids=1&limit_asks=1")
                if tape.add_book(book, now):
                    out["books"] += 1
                self.fetches += 1
                trades = self.fetch(f"{API}/v1/trades/{sym}?limit_trades=500")
                out["prints"] += tape.add_prints(trades)
            except Exception as exc:                      # noqa: BLE001
                self.errors += 1
                out["errors"] += 1
                _log("fetch_error", symbol=sym, err=str(exc)[:120])
        self._replay_expired(now)
        if now - self.last_status >= STATUS_EVERY_S:
            self.last_status = now
            _log("status", **self.status())
            self._write_summary()
        if now - self.last_report >= REPORT_EVERY_S:
            self.last_report = now
            rep = self.rolling_report(now)
            _log("rolling_report", **rep)
            try:
                (self.root / "report.json").write_text(
                    json.dumps(rep, indent=1, default=str, sort_keys=True))
            except OSError:
                pass
        return out

    # ---- rolling reports (OWNER 5554553793 B item 4) ----
    REPORT_KEYS = ("campaigns", "decisions", "orders_submitted", "first_leg_fills",
                   "campaign_first_fill_rate", "complement_orders", "complement_fills",
                   "p_complement_given_first_fill", "two_sided_fill_campaigns",
                   "matched_pairs_le_099", "pair_cost_after_fees_p25_p50_p75",
                   "median_pair_cost_after_fees", "one_sided_holds",
                   "one_sided_hold_rate", "residual_duration_s_p50",
                   "residual_outcomes", "adverse_selection_by_horizon",
                   "fees_theoretical_total", "pair_pnl_theoretical_total",
                   "hold_pnl_theoretical_total", "net_theoretical_total",
                   "capital_turn_per_day_estimate", "touch_moved_cancels",
                   "window_flow_two_sided_pct", "entry_spread_p50", "distinct_days")

    def _arm_summary(self, rows: list) -> dict:
        s = gs.summarize(rows) if rows else {}
        out = {k: s.get(k) for k in self.REPORT_KEYS}
        out["optimistic_unknown_first_fills"] = sum(
            int((r.get("optimistic_unknown") or {}).get("first_fills_at_price_only") or 0)
            for r in rows)
        out["requotes"] = sum(int(o.get("requotes") or 0) for r in rows
                              for o in r.get("orders") or [])
        return out

    def rolling_report(self, now: float) -> dict:
        windows = {"6h": 6 * 3600.0, "24h": 24 * 3600.0, "cumulative": None}
        out: dict = {"t": _utc(now), "labels": LABELS,
                     "paired_design": "every arm replays the SAME window tape with the "
                                      "same seeded v2 decisions; deltas are vs the live "
                                      f"shell {LIVE_SHELL} on identical windows",
                     "by_window": {}}
        for wname, span in windows.items():
            arms = {}
            for key, rows in sorted(self.rows.items()):
                sel = [r for r in rows
                       if span is None or float(r.get("entry_t") or 0) >= now - span]
                arms[key] = self._arm_summary(sel)
            for key, a in arms.items():
                shell, policy = key.split(":")
                base = arms.get(f"{LIVE_SHELL}:{policy}")
                if base and shell != LIVE_SHELL:
                    a["vs_live_shell"] = {
                        k: _delta(a.get(k), base.get(k)) for k in (
                            "first_leg_fills", "two_sided_fill_campaigns",
                            "one_sided_holds", "net_theoretical_total",
                            "matched_pairs_le_099")}
            out["by_window"][wname] = arms
        if self.gate:
            prim = self.gate.get("primary_arm")
            out["transfer_gate"] = {
                "primary_arm": prim,
                "verdict": gs.transfer_gate_verdict(self.rows.get(prim) or [], self.gate),
                "exploratory": {k: gs.transfer_gate_verdict(self.rows.get(k) or [], self.gate)["verdict"]
                                for k in self.gate.get("exploratory_arms") or []}}
        return out

    def _replay_expired(self, now: float) -> None:
        for sym, tape in list(self.tapes.items()):
            if tape.replayed or tape.expiry is None:
                continue
            if now < tape.expiry + SETTLE_GRACE_S:
                continue
            tape.replayed = True
            self._append_tape(tape)
            prints = tape.sorted_prints()
            summary = {}
            for name, sh in gs.SHELLS.items():
                for policy in gs.POLICIES:
                    try:
                        r = gs.replay_window(sym, tape.bbo, prints, model=self.model,
                                             art=self.art, policy=policy,
                                             mode="strict", shell={**sh, "name": name})
                        # at-price / queue-unknown fills: SEPARATE label,
                        # never counted in any strict metric (item 3)
                        ro = gs.replay_window(sym, tape.bbo, prints, model=self.model,
                                              art=self.art, policy=policy,
                                              mode="optimistic",
                                              shell={**sh, "name": name})
                    except Exception as exc:              # noqa: BLE001
                        # LIVE 21:10:23Z: a one-sided book crashed the loop
                        # and lost the in-memory tape. A replay error is a
                        # RECORD (the tape is already on disk), never a crash.
                        r = {"symbol": sym,
                             "status": "REPLAY_ERROR", "policy": policy,
                             "shell": name,
                             "err": f"{type(exc).__name__}:{str(exc)[:120]}"}
                        ro = {}
                        _log("replay_error", symbol=sym, shell=name,
                             policy=policy, err=r["err"])
                    opt_first = [f for f in ro.get("fills") or [] if f["relation"] != "complete"]
                    strict_first = [f for f in r.get("fills") or [] if f["relation"] != "complete"]
                    rec = {k: r.get(k) for k in (
                        "symbol", "status", "stop_reason", "policy", "shell",
                        "entry_t", "entry_tte_s", "entry_book", "orders",
                        "fills", "inventory", "pair_unit_cost",
                        "pair_cost_after_fees", "fees_theoretical",
                        "outcome_proxy", "pair_pnl_theoretical",
                        "hold_pnl_theoretical", "flow")}
                    rec["decisions"] = [
                        {k: d.get(k) for k in ("t", "state", "acted", "refusal",
                                               "v2_recommended", "state_n",
                                               "shadow_differs_from_executed")}
                        for d in r.get("decisions") or []]
                    rec["decisions_n"] = len(r.get("decisions") or [])
                    rec["coverage"] = ("full" if (tape.expiry or 0) - getattr(
                        tape, "opened_at", 0) >= tb.ENTRY_MAX_TTE_S else "partial")
                    rec["orders_placed"] = 0
                    rec["optimistic_unknown"] = {
                        "first_fills_at_price_only": max(0, len(opt_first) - len(strict_first)),
                        "status_optimistic": ro.get("status"),
                        "labels": "at-price/queue-unknown hypothetical fills; "
                                  "NOT strict; excluded from every metric"}
                    rec["labels"] = LABELS
                    key = f"{name}:{policy}"
                    if (sym, key) in self.seen:
                        continue                  # never a second record
                    self.seen.add((sym, key))
                    self.rows.setdefault(key, []).append(rec)
                    self._append_record(rec)
                    summary[key] = {"status": rec["status"],
                                    "fills": len(rec["fills"] or []),
                                    "orders": len(rec["orders"] or [])}
            _log("window_replayed", symbol=sym, bbo_rows=len(tape.bbo),
                 prints=len(prints), shells=summary)
            del self.tapes[sym]

    def status(self) -> dict:
        per = {}
        for key, rows in sorted(self.rows.items()):
            s = gs.summarize(rows)
            per[key] = {k: s.get(k) for k in (
                "campaigns", "orders_submitted", "first_leg_fills",
                "campaign_first_fill_rate", "complement_fills",
                "two_sided_fill_campaigns", "p_complement_given_first_fill",
                "one_sided_holds", "median_pair_cost_after_fees",
                "fees_theoretical_total", "pair_pnl_theoretical_total",
                "hold_pnl_theoretical_total", "holds_residual_won",
                "holds_residual_lost", "holds_ambiguous_excluded",
                "net_theoretical_total", "adverse_selection_30s",
                "decisions_per_campaign_p50", "touch_moved_cancels",
                "window_flow_two_sided_pct", "entry_spread_p50")}
        return {"t": _utc(self.clock()), "started_utc": self.started_utc,
                "windows_open": sorted(self.tapes), "fetches": self.fetches,
                "fetch_errors": self.errors, "orders_placed": 0,
                "labels": LABELS, "per_shell": per}

    def _write_summary(self) -> None:
        try:
            (self.root / "summary.json").write_text(
                json.dumps(self.status(), indent=1, default=str, sort_keys=True))
        except OSError:
            pass

    def run(self, until: Optional[float] = None) -> dict:
        _log("service_start", state_dir=str(self.root),
             shells=list(gs.SHELLS), policies=list(gs.POLICIES),
             model=self.model.get("_sha256_16"), labels=LABELS)
        while until is None or self.clock() < until:
            self.poll_once()
            self.sleep(POLL_S)
        return self.status()


def _delta(a: Any, b: Any) -> Optional[str]:
    try:
        return str(D(str(a)) - D(str(b)))
    except Exception:                                 # noqa: BLE001
        return None


def _utc(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def main() -> int:
    if os.getenv("GEMINI_SHADOW_ROLE", "gemini-l5-shadow") != "gemini-l5-shadow":
        print("SERVICE_ROLE_MISMATCH", flush=True)
        return 64
    ShadowService().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
