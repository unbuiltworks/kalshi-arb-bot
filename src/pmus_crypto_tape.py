"""Bounded observation tape for the crypto reward lane.

Owner 2026-11-22: record daily-crypto books on the EXISTING sports service
in disarmed observation mode -- no new service, no new volume -- to measure
reward eligibility, quote competition and inventory risk over a full day.

One JSON line per market at most every MIN_GAP_S, in its own directory of
the bot's existing state volume (so it can never spend the sports tape's
budget):

  {"t", "slug", "b": [[px, qty], ...], "a": [[px, qty], ...],  depth, best first
   "shares": venue traded count, "obs": the lane's own decision for that
   book: quotes by rule, why not, eligibility, modelled share, block, armed}

BOUNDED: at most MAX_BYTES_PER_DAY per daily file and KEEP_DAYS files, so
the directory never exceeds MAX_BYTES_PER_DAY x KEEP_DAYS. A full file drops
rows (counted), it never grows. Recording never raises into the engine.
Nothing here is evidence of an earned reward: `obs` is MODELLED.

Replay: analysis/l5/rewards/crypto_policy_replay.py replay <file>
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from src.pmus_sports_tape import Tape

MAX_LEVELS = 30


def _f(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


ON = os.getenv("PMUS_CRYPTO_TAPE", "1").strip() != "0"
MAX_BYTES_PER_DAY = int(_f("PMUS_CRYPTO_TAPE_MAX_BYTES", "15000000"))
KEEP_DAYS = max(1, int(_f("PMUS_CRYPTO_TAPE_KEEP_DAYS", "4")))
MIN_GAP_S = max(5.0, _f("PMUS_CRYPTO_TAPE_GAP_S", "30"))


def _levels(rows: Any) -> list:
    out = []
    for r in list(rows or [])[:MAX_LEVELS]:
        if not isinstance(r, Mapping):
            continue
        px = r.get("px") if "px" in r else r.get("price")
        if isinstance(px, Mapping):
            px = px.get("value")
        q = r.get("qty") if "qty" in r else r.get("size")
        try:
            out.append([float(px), float(q)])
        except (TypeError, ValueError):
            continue
    return out


class CryptoTape(Tape):
    """The sports Tape's daily files, byte cap and pruning, with the whole
    cap available to book rows (this tape holds nothing else)."""
    BOOK_SHARE = 1.0

    def __init__(self, root: Path, *, max_bytes_per_day: Optional[int] = None,
                 keep_days: Optional[int] = None,
                 min_gap_s: Optional[float] = None, clock=time.time):
        super().__init__(root, max_bytes_per_day=MAX_BYTES_PER_DAY
                         if max_bytes_per_day is None else max_bytes_per_day,
                         keep_days=KEEP_DAYS if keep_days is None
                         else max(1, keep_days), clock=clock)
        self.min_gap = MIN_GAP_S if min_gap_s is None else float(min_gap_s)
        self._last: Dict[str, float] = {}

    def due(self, slug: str) -> bool:
        return self.clock() - self._last.get(slug, -1e18) >= self.min_gap

    def snapshot(self, slug: str, book_md: Optional[Mapping[str, Any]],
                 obs: Optional[Mapping[str, Any]] = None) -> None:
        if not self.due(slug):
            return
        now = self.clock()
        self._last[slug] = now
        try:
            md = book_md or {}
            shares = (md.get("stats") or {}).get("sharesTraded")
            rec = {"t": round(now, 3), "slug": slug,
                   "b": _levels(md.get("bids")),
                   "a": _levels(md.get("offers") if "offers" in md
                                else md.get("asks")),
                   "shares": None if shares is None else float(shares),
                   "obs": obs}
        except Exception:                                 # noqa: BLE001
            self.errors += 1
            return
        self._write(rec, now)

    def bound_bytes(self) -> int:
        """The most this directory can ever hold."""
        return self.max_bytes * self.keep_days

    def stats(self) -> Dict[str, Any]:
        return {"dir": str(self.root), "written": self.written,
                "dropped_full": self.dropped, "errors": self.errors,
                "max_bytes_per_day": self.max_bytes,
                "keep_days": self.keep_days, "gap_s": self.min_gap,
                "bound_bytes": self.bound_bytes()}
