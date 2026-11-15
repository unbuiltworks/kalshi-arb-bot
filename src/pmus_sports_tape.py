"""Lightweight market + execution tape for the sports two-sided bot.

OWNER 2026-11-15: "record order-book depth, quote age, market movement, and
fill outcomes going forward" so entry rules can be tested CHRONOLOGICALLY
out of sample instead of fitted to the 77 markets that exposed the losses.

What it writes (JSON lines, one file per UTC day, inside the bot's existing
state volume -- no new service):
  {"k":"book", t, slug, bids:[top 3 levels as served], asks:[...]}
      throttled to one line per market per BOOK_MIN_GAP_S, on change only
  {"k":"ev", t, kind, ...}  the bot's own ORDER / FILL / pull / cancel /
      recovery / pair events (the same payloads it already logs)
Bounded: MAX_BYTES_PER_DAY per file, KEEP_DAYS files. Recording never
raises into trading: every failure is swallowed and counted.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

EVENT_KINDS = frozenset({
    "ORDER", "FILL", "PAIR_LOCKED", "EXIT_LONE_LEG", "reactive_pull",
    "jump_pause", "order_closed_by_venue", "RECOVERY_DECISION",
    "RECOVERY_RESOLVED", "open_ev_reject", "market_closed", "SELECTED",
    "released_flat_market", "daily_loss_stop", "HALT_NEW_EXPOSURE",
    "shared_halt", "INVENTORY_RECONCILE", "XV_PAPER_ENTRY",
    "XV_PAPER_RESULT", "XV_PAPER_SUMMARY", "XV_PAPER_MARKETS",
    "intl_guard_pull", "intl_guard_block"})


def book_levels(msg: Any, out: Optional[list] = None, slug_hint: str = ""
                ) -> List[Tuple[str, list, list]]:
    """(slug, top-3 bid levels, top-3 ask levels) per book event, levels
    kept exactly as the venue served them (schema-agnostic)."""
    if out is None:
        out = []
    if isinstance(msg, list):
        for item in msg:
            book_levels(item, out, slug_hint)
        return out
    if not isinstance(msg, Mapping):
        return out
    slug = str(msg.get("marketSlug") or msg.get("market_slug")
               or msg.get("slug") or slug_hint or "")
    bids = msg.get("bids")
    asks = msg.get("offers") if "offers" in msg else msg.get("asks")
    if slug and (isinstance(bids, list) or isinstance(asks, list)):
        out.append((slug, list(bids or [])[:3], list(asks or [])[:3]))
    for v in msg.values():
        if isinstance(v, (list, Mapping)):
            book_levels(v, out, slug)
    return out


class Tape:
    def __init__(self, root: Path, *, max_bytes_per_day: int = 40_000_000,
                 keep_days: int = 7, book_min_gap_s: float = 1.0,
                 clock=time.time):
        self.root = Path(root)
        self.max_bytes = int(max_bytes_per_day)
        self.keep_days = int(keep_days)
        self.gap = float(book_min_gap_s)
        self.clock = clock
        self._lock = threading.Lock()
        self._last_book: Dict[str, Tuple[float, str]] = {}
        self.written = 0
        self.dropped = 0
        self.errors = 0

    def _path(self, now: float) -> Path:
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d")
        return self.root / f"tape_{day}.jsonl"

    def _write(self, rec: Mapping[str, Any], now: float) -> None:
        try:
            line = json.dumps(rec, default=str, separators=(",", ":")) + "\n"
            p = self._path(now)
            with self._lock:
                self.root.mkdir(parents=True, exist_ok=True)
                size = p.stat().st_size if p.exists() else 0
                if size + len(line) > self.max_bytes:
                    self.dropped += 1
                    return
                new_file = size == 0
                with open(p, "a", encoding="utf-8") as fh:
                    fh.write(line)
                self.written += 1
                if new_file:
                    self._prune()
        except Exception:                                 # noqa: BLE001
            self.errors += 1

    def _prune(self) -> None:
        files = sorted(self.root.glob("tape_*.jsonl"))
        for old in files[:-self.keep_days] if self.keep_days > 0 else []:
            try:
                old.unlink()
            except Exception:                             # noqa: BLE001
                self.errors += 1

    def book(self, msg: Any) -> None:
        """Called with every raw WS frame (cheap when nothing changed)."""
        now = self.clock()
        try:
            levels = book_levels(msg)
        except Exception:                                 # noqa: BLE001
            self.errors += 1
            return
        for slug, bids, asks in levels:
            sig = json.dumps([bids, asks], default=str, sort_keys=True)
            last = self._last_book.get(slug)
            if last and (last[1] == sig or now - last[0] < self.gap):
                continue
            self._last_book[slug] = (now, sig)
            self._write({"k": "book", "t": round(now, 3), "slug": slug,
                         "bids": bids, "asks": asks}, now)

    def event(self, kind: str, payload: Mapping[str, Any]) -> None:
        if kind not in EVENT_KINDS:
            return
        now = self.clock()
        self._write({"k": "ev", "t": round(now, 3), "kind": kind,
                     **payload}, now)

    def stats(self) -> Dict[str, int]:
        return {"written": self.written, "dropped": self.dropped,
                "errors": self.errors}
