"""Bounded coalescing scheduler for sports K/P exact Kalshi synchronizations.

The live sports service intentionally caps exact Kalshi depth=1 synchronization at
4 requests/second.  This helper preserves that global cap while retaining the most
recent Polymarket-triggered key instead of forcing callers to drop every trigger
that arrives during the 250 ms pacing window.

This module has no network or order-entry code.  It is a scheduling primitive only;
the existing <=100 ms book-age, <=50 ms skew, >=3c after-fee, rule-certification,
and final revalidation gates remain authoritative at the caller.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class ExactSyncCoalescer:
    min_interval_seconds: float = 0.25
    _last_dispatch: float = field(default=float("-inf"), init=False)
    _pending: OrderedDict[str, None] = field(default_factory=OrderedDict, init=False)

    def offer(self, key: str) -> None:
        """Keep one pending copy of a key and make the newest offer newest priority."""
        if key in self._pending:
            self._pending.move_to_end(key)
        else:
            self._pending[key] = None

    def due(self, now: float) -> bool:
        return bool(self._pending) and now - self._last_dispatch >= self.min_interval_seconds

    def pop_due(self, now: float) -> str | None:
        """Dispatch at most one key when the unchanged global pacing window allows it."""
        if not self.due(now):
            return None
        key, _ = self._pending.popitem(last=True)
        self._last_dispatch = now
        return key

    @property
    def pending_count(self) -> int:
        return len(self._pending)
