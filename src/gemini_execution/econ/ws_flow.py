"""FLOW FROM THE MAINTAINED SOCKET, because the polled tape may not decide.

WHY THIS MODULE EXISTS, AND IT IS NOT A PERFORMANCE ARGUMENT. The entry gates
were reading `habitat.public_flow`, a polled REST read whose own docstring says:

    "EVIDENCE ONLY: no price, size or decision is taken from this."

Gating an entry on it takes a decision from it. That is the same invariant
class as `metadata.contract_depth`'s "no caller may price, size or gate
anything on it", which already turned a depth gate into a quote gate on this
branch. Both labels exist for the same reason: a separately polled read has its
own clock, and deciding on it means deciding on a view that was never
consistent with the book the controller decided on.

It was also empirically closed. On GEMI-BTC05M2609201320-UP the gate refused
50 of 50 entries with "only 0 prints in the window" -- the polled tape is thin
or empty EARLY in a window, which is exactly when BTC5m ENTRY runs.

WHAT THE SOCKET GIVES THAT THE POLL CANNOT. `l5_shadow.gemini_ws` maintains the
book from per-order deltas and records every `type == "trade"` event with a
millisecond venue timestamp. Crucially `prints_since(t0)` returns **None**, not
`[]`, when the socket's continuous view began after `t0`: "we saw nothing" stays
distinguishable from "we were not watching". A reversibility gate lives or dies
on that distinction -- an empty list read as "no flow" would refuse precisely
the markets we had just failed to observe.

THE SIDE TRANSLATION, IN ONE PLACE. Gemini names the side of the RESTING order
that was hit, so `resting_side == "bid"` means someone SOLD into a bid and the
AGGRESSOR was a sell. Getting this backwards inverts every alternation and
imbalance feature computed downstream, so the mapping lives here and nowhere
else, exactly as `hypothetical.normalize` keeps its own translation single.

FAIL CLOSED, AND NO FALLBACK TO THE POLLED TAPE. When coverage is unvouched
this returns `vouched: False` with a reason and NO prints. It does not quietly
fall back to `public_flow`: falling back would reintroduce the very source the
module exists to stop deciding on, and would do it invisibly. A gate that
cannot be evaluated must read UNKNOWN and refuse, which is what the caller does.

READ ONLY. The public market-data socket takes no credentials and carries no
order path.
"""
from __future__ import annotations

import time
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

SCHEMA = "gemini_econ_ws_flow/v1"

#: `resting_side` -> the AGGRESSOR's direction
AGGRESSOR_OF = {"bid": "sell", "buy": "sell", "ask": "buy", "sell": "buy"}

LABELS = (
    "Flow from the MAINTAINED websocket, not the polled REST tape. The polled "
    "tape is labelled EVIDENCE ONLY and may not be decided on.",
    "UNVOUCHED is not EMPTY. When the socket cannot vouch for the interval "
    "this returns no prints and vouched=False; it never falls back to the "
    "polled tape.",
)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def aggressor_side(trade: Mapping[str, Any]) -> Optional[str]:
    """Which direction the TAKER traded in. None when the venue did not say.

    None is returned rather than a guess: an unknown direction excluded from
    the alternation count is honest, while a defaulted one silently biases it.
    """
    rs = str(trade.get("resting_side") or "").strip().lower()
    return AGGRESSOR_OF.get(rs)


def normalize(trades: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Socket trade records into the shape the feature extractor reads.

    It speaks `side`/`qty`; the socket speaks `resting_side`/`size`. The
    translation is here and nowhere else so the two cannot drift.
    """
    out: List[Dict[str, Any]] = []
    for t in trades or []:
        side = aggressor_side(t)
        q = _dec(t.get("size"))
        p = _dec(t.get("price"))
        out.append({
            "side": side,                  # None stays None; never defaulted
            "qty": None if q is None else str(q),
            "price": None if p is None else str(p),
            "t": t.get("t"),
            "venue_ms": t.get("venue_ms"),
            "resting_side": t.get("resting_side"),
            "source": "GEMINI_WS",
        })
    return out


class WsFlowSource:
    """One socket per watched symbol, and flow windows read off it.

    The feed is injected-or-created so a test can drive this against a fake
    book with no network at all. A source that can only be exercised against a
    live venue is a source that does not get exercised.
    """

    def __init__(self, feed: Any = None) -> None:
        self._feed = feed
        self._watch_started: Dict[str, float] = {}

    @property
    def feed(self) -> Any:
        if self._feed is None:
            from src.l5_shadow.gemini_ws import GeminiFeed
            self._feed = GeminiFeed()
        return self._feed

    def watch(self, symbol: str) -> None:
        if symbol not in self._watch_started:
            self.feed.watch(symbol)
            self._watch_started[symbol] = time.time()

    def drop(self, symbol: str) -> None:
        """Stop following an expired window so sockets do not accumulate."""
        self._watch_started.pop(symbol, None)
        try:
            self.feed.drop(symbol)
        except Exception:                                        # noqa: BLE001
            pass

    def flow(self, symbol: str, *, window_s: float,
             now: Optional[float] = None) -> Dict[str, Any]:
        """Prints in the trailing `window_s`, or an explicit refusal."""
        t = time.time() if now is None else now
        t0 = t - float(window_s)
        book = getattr(self.feed, "_books", {}).get(symbol)
        base: Dict[str, Any] = {"schema": SCHEMA, "symbol": symbol,
                                "window_s": window_s, "labels": list(LABELS),
                                "source": "GEMINI_WS"}
        if book is None:
            base.update(vouched=False, prints=None,
                        why="no socket is watching %s yet" % symbol)
            return base
        raw = book.prints_since(t0)
        if raw is None:
            base.update(
                vouched=False, prints=None,
                why=("the socket cannot vouch for the last %.1fs: its "
                     "continuous view began after that point, so an empty "
                     "list here would assert nobody traded when the truth is "
                     "that we were not watching" % window_s),
                ready=getattr(book, "ready", None),
                ready_since=getattr(book, "ready_since", None))
            return base
        rows = normalize(raw)
        known = [r for r in rows if r["side"] in ("buy", "sell")]
        base.update(vouched=True, prints=rows, n_prints=len(rows),
                    n_directional=len(known),
                    undirected=len(rows) - len(known), why=None)
        return base
