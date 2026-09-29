"""Exact replay of a live window from its persisted `native_book_depth`.

WHY THIS EXISTS. R6's 18:17:32->18:30 counterfactual could not be answered:
the probe saw authenticated full depth at 14ms and threw it away, and the
recorder's raw tape sits on another service's volume. `6683b0b3` now
journals `native_book_depth` -- THE ladder object each decision was taken
against -- so a live window becomes replayable from its own journal, with
no second service and no reconstruction.

WHAT IT REPLAYS. The frozen engine, unchanged, over the real recorded
sequence: `native_tick.book` supplies the BBO row, `native_book_depth`
supplies the ladder for the same `t`. Decisions come out of
`pmus_l5_native_engine`; nothing here re-implements them.

HONEST LIMIT, stated rather than buried: a replay answers "what would the
engine have decided, given what it saw". It does NOT prove a counterfactual
order would have FILLED -- our own resting order changes the queue. Fills
are therefore modelled by the same `ReplayVenue(queue_aware)` the frozen
parity arm uses, and counterfactual fills are labelled MODELLED, never
realized.

READ-ONLY. No network, no orders, no arming.
"""
from __future__ import annotations

import json
from decimal import Decimal as D
from typing import Any, Dict, Iterator, List, Mapping, Optional

from src import pmus_l5_native_engine as E
from src import pmus_native_parity as P


DEPTH_KIND = "native_book_depth"
TICK_KIND = "native_tick"


def journal_rows(path: str) -> Iterator[Dict[str, Any]]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _payload(rec: Mapping[str, Any]) -> Mapping[str, Any]:
    """Journal rows are {kind, t, payload} or flat; accept both."""
    p = rec.get("payload")
    return p if isinstance(p, Mapping) else rec


def _ladder(rec: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """A depth row back into the {bids, offers} shape the walk consumes.

    Levels were journalled as strings so Decimal round-trips exactly.
    """
    p = _payload(rec)
    bids = [[D(str(a)), D(str(b))] for a, b in (p.get("bids") or [])]
    offers = [[D(str(a)), D(str(b))] for a, b in (p.get("offers") or [])]
    if not bids and not offers:
        return None
    return {"kind": "book", "t": p.get("t"), "bids": bids, "offers": offers}


def build_sequence(path: str) -> List[Dict[str, Any]]:
    """The recorded window as rows the frozen walk understands.

    Depth is attached to the tick it was recorded with: both rows carry the
    same `t`, which is the book timestamp the decision used.
    """
    depth_by_t: Dict[Any, Dict[str, Any]] = {}
    ticks: List[Dict[str, Any]] = []
    for rec in journal_rows(path):
        kind = rec.get("kind")
        if kind == DEPTH_KIND:
            lad = _ladder(rec)
            if lad is not None:
                depth_by_t[_payload(rec).get("t")] = lad
        elif kind == TICK_KIND:
            ticks.append(_payload(rec))

    rows: List[Dict[str, Any]] = []
    for tk in ticks:
        t = tk.get("t")
        lad = depth_by_t.get(t)
        if lad is not None:
            rows.append(lad)
        bk = tk.get("book") or {}
        rows.append({"kind": "bbo", "t": t,
                     "state": bk.get("state") or "MARKET_STATE_OPEN",
                     "bid": bk.get("bid"), "ask": bk.get("ask"),
                     "bid_qty": bk.get("bid_qty"),
                     "ask_qty": bk.get("ask_qty")})
    return rows


def coverage(path: str) -> Dict[str, Any]:
    """Can this window be replayed exactly, and where is depth missing?

    A tick with no depth row is NOT silently walked as if the book were
    empty -- it is counted, so a partial recording is visible instead of
    being mistaken for a faithful replay.
    """
    n_depth = n_tick = 0
    matched = 0
    depth_t = set()
    for rec in journal_rows(path):
        k = rec.get("kind")
        if k == DEPTH_KIND:
            n_depth += 1
            depth_t.add(_payload(rec).get("t"))
        elif k == TICK_KIND:
            n_tick += 1
    for rec in journal_rows(path):
        if rec.get("kind") == TICK_KIND and _payload(rec).get("t") in depth_t:
            matched += 1
    return {"ticks": n_tick, "depth_rows": n_depth,
            "ticks_with_depth": matched,
            "ticks_without_depth": n_tick - matched,
            "exact_replay_possible": n_tick > 0 and matched == n_tick,
            "labels": ("a tick without depth cannot be replayed exactly; "
                       "the frozen engine's own `if self.ladder else []` "
                       "guard then decides no completion, which is the "
                       "honest answer, not a reconstruction")}


def replay(path: str, *, winner: str, clip=D(10)) -> Dict[str, Any]:
    rows = build_sequence(path)
    res = P.replay_window(rows, winner=winner, clip=clip)
    return {"coverage": coverage(path),
            "clip": str(clip),
            "settled": res,
            "net": P.net_of(res),
            "labels": ("counterfactual fills are MODELLED by the frozen "
                       "queue-aware venue, never realized")}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit("usage: l5_depth_replay.py <journal.jsonl> [winner]")
    p = sys.argv[1]
    print(json.dumps(coverage(p), indent=2, default=str))
    if len(sys.argv) > 2:
        print(json.dumps(replay(p, winner=sys.argv[2]), indent=2, default=str))
