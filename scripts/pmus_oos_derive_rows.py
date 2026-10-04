"""Derive book/trade rows from harvested raw WS frames, using the RECORDER's
own folding code.

WHY THIS STEP EXISTS. The development tape's `book`/`trade` rows were produced
by `scripts/pmus_railway_log_to_tape.py` from the recorder's PRINTED
`PMUS_BOOK_ROWS` / `PMUS_TRADE_ROWS` lines. Those printed rows are a SAMPLE,
drained from the recorder's in-process history and capped per emission; the
earlier log harvest in this programme also truncated at 501 entries per query
and so manufactured 45-second gaps. The volume's raw tape is the complete
record of the same frames, so the rows are derived from it here instead.

NOTHING IS REIMPLEMENTED. `apply_events` is the recorder's own pure fold from a
raw frame to per-market state; `bbo_history` entries and `trades` entries are
exactly the records the printed rows were built from, field for field. This
module only (a) replays frames in timestamp order and (b) drains the two ring
buffers by identity as they grow, which is what the recorder's logger does via
`_BOOK_LOG_SEEN`. Draining incrementally matters: `apply_events` keeps the last
2000 book states and 500 trades per market, so reading the buffers only at the
end would silently drop a busy window's early history.

HOW THIS DIFFERS FROM DEVELOPMENT, STATED PLAINLY. Development rows are a
sampled drain of this same history; OOS rows here are the complete drain. The
schema, the fold and the field semantics are identical, and the already
established completeness gate still judges every window. But the two samples
are not produced by byte-identical pipelines, and that is a real difference
which belongs in the result rather than in a footnote.

The frozen model, its coefficients, the EV gate, the controller and the fee
treatment are untouched by this file.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pmus_ws_evidence_recorder import (                          # noqa
    apply_events, book_status, _walk_market_events)


def _epoch(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return dt.datetime.fromisoformat(
            str(v).replace("Z", "+00:00")).timestamp()
    except Exception:                                          # noqa: BLE001
        return None


def derive(src: str, dst: str) -> dict:
    """Replay `src` (raw ws frames) and write dev-schema book/trade rows.

    ONLY the markets a frame actually touches are drained, and only the TAIL of
    their histories is examined. The first version rescanned every market's full
    2000-entry history on every frame against a growing `seen` set, which is
    fine for the 9 markets of a calibration slice and quadratic for the ~200 of
    a full harvest. `apply_events` appends in timestamp order and trims only
    from the front, so walking back from the end while the timestamp exceeds the
    last one emitted finds exactly the new entries, in O(new rows) per frame.
    """
    state: dict = {}
    last_book: dict = {}
    last_trade: dict = {}
    frames = books = trades = skipped = 0
    out = open(dst, "w")

    def drain(slug, s, key, lastmap, emit):
        seq = s.get(key) or []
        prev = lastmap.get(slug)
        fresh = []
        for i in range(len(seq) - 1, -1, -1):
            t = seq[i].get("t")
            if prev is not None and t is not None and t <= prev:
                break
            fresh.append(seq[i])
        n = 0
        for x in reversed(fresh):
            emit(slug, x)
            n += 1
        if fresh:
            newest = fresh[0].get("t")
            if newest is not None:
                lastmap[slug] = newest
        return n

    def emit_book(slug, h):
        out.write(json.dumps({
            "t": h.get("t"), "bid": h.get("bid"), "ask": h.get("ask"),
            "bid_qty": h.get("bid_qty"), "ask_qty": h.get("ask_qty"),
            "ask_book_missing": h.get("ask_book_missing"),
            "status": book_status(
                {"bid": h.get("bid"), "ask": h.get("ask"), "t": h.get("t")},
                h.get("t"), True),
            "slug": slug, "kind": "book"}, default=str) + "\n")

    def emit_trade(slug, x):
        out.write(json.dumps({
            "t": x.get("t"), "px": x.get("px"), "qty": x.get("qty"),
            "trade_time": x.get("trade_time"),
            "bid_at_trade": x.get("bid_at_trade"),
            "ask_at_trade": x.get("ask_at_trade"),
            "dir": x.get("dir"),
            "slug": slug, "kind": "trade"}, default=str) + "\n")

    with open(src) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:                                  # noqa: BLE001
                continue
            if r.get("kind") != "ws":
                continue
            now = _epoch(r.get("t"))
            if now is None:
                skipped += 1
                continue
            frames += 1
            msg = r.get("msg")
            # which markets this frame concerns, read structurally by the
            # recorder's own walker rather than guessed
            touched = []
            evs: list = []
            _walk_market_events(msg, evs)
            for ev in evs:
                if ev.get("slug") and ev["slug"] not in touched:
                    touched.append(ev["slug"])
            apply_events(state, msg, now)
            for slug in touched:
                s = state.get(slug)
                if not s:
                    continue
                books += drain(slug, s, "bbo_history", last_book, emit_book)
                trades += drain(slug, s, "trades", last_trade, emit_trade)
    out.close()
    return {"ws_frames": frames, "book_rows": books, "trade_rows": trades,
            "frames_without_timestamp": skipped, "slugs": len(state)}


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: pmus_oos_derive_rows.py <raw ws jsonl> <out jsonl>",
              file=sys.stderr)
        return 2
    print(json.dumps(derive(sys.argv[1], sys.argv[2]), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
