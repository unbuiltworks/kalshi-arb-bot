"""Answer the blindness question from the RAW TAPE, on the recorder itself.

    window opens -> first authenticated two-sided book -> first authenticated
    trade -> same market identity -> coverage through the window

WHY NOT THE LOG. The book tape I added prints the recorder's IN-MEMORY state
on its status cadence, which turned out to be a lossy view of it: emissions
are ~60-110s apart, each carried ONE new state per market, and `book_updates`
sat at 1 for a live market whose price was visibly moving. Whatever that is,
it is not the venue's update rate, so a latency measured off it would be a
measurement of our own cadence.

The raw tape has no such problem. The recorder appends EVERY websocket frame
verbatim, `{"kind":"ws","t":<our receipt>,"msg":<frame>}`, before it touches
any in-memory state. Replaying those frames through the recorder's OWN
`apply_events` -- passing each row's receipt as `now` -- reconstructs the book
exactly as it arrived, with the true receipt timestamp on every update. Same
parser, so the reconstruction cannot drift from what the recorder believed.

This runs WHERE THE VOLUME IS, prints one line, and touches nothing: it opens
the tape read-only, builds its own state dict, and shares nothing with the
running recorder.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src import l5_authenticated_observation as O
from src import pmus_ws_evidence_recorder as R

BTC15M = "cpc-btc-updown-15m-"

#: when the authenticated book tape went live; windows before this were only
#: ever seen over the defective public path and are reported separately
DEFAULT_SINCE = "2026-09-23T22:30:00+00:00"


def _epoch(v: Any) -> Optional[float]:
    return O._epoch(v)


def tape_rows(root: Path, max_age_s: Optional[float] = None,
              now: Optional[float] = None) -> Iterable[dict]:
    """Every raw ws row, oldest first. A malformed line is skipped."""
    now = time.time() if now is None else now
    for part in R.tape_parts(root):
        if max_age_s is not None:
            try:
                if now - part.stat().st_mtime > max_age_s:
                    continue
            except OSError:
                continue
        try:
            with part.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue          # truncated tail: skipped, not guessed
                    if row.get("kind") == "ws":
                        yield row
        except OSError:
            continue


def replay(rows: Iterable[dict]) -> Dict[str, Any]:
    """Rebuild market state through the recorder's own parser.

    Each frame is applied with ITS OWN receipt time, so every book state and
    trade carries the instant it actually arrived rather than the instant we
    happened to look.
    """
    state: Dict[str, Any] = {}
    applied = skipped = 0
    for row in rows:
        t = _epoch(row.get("t"))
        msg = row.get("msg")
        if t is None or msg is None:
            skipped += 1
            continue
        try:
            R.apply_events(state, msg, t)
            applied += 1
        except Exception:                                      # noqa: BLE001
            skipped += 1
    return {"state": state, "frames_applied": applied,
            "frames_skipped": skipped}


def book_states(s: Dict[str, Any]) -> List[dict]:
    """bbo_history -> the shape the invariant reads.

    Status is a property of the STATE, not of when we looked at it: a
    two-sided quote is OK. Emission-time staleness does not apply to a
    replay, so it is deliberately not reproduced here.
    """
    out = []
    for h in (s.get("bbo_history") or ()):
        bid, ask = h.get("bid"), h.get("ask")
        if bid is not None and ask is not None:
            status = "OK"
        elif bid is None and ask is None:
            status = "NO_BOOK"
        else:
            status = "ONE_SIDED"
        out.append({"t": h.get("t"), "bid": bid, "ask": ask,
                    "bid_qty": h.get("bid_qty"), "ask_qty": h.get("ask_qty"),
                    "status": status})
    out.sort(key=lambda r: r["t"])
    return out


def build(state: Dict[str, Any], since: Optional[str] = DEFAULT_SINCE
          ) -> Dict[str, Any]:
    books, trades = {}, {}
    for slug, s in state.items():
        if not str(slug).startswith(BTC15M):
            continue
        books[slug] = book_states(s)
        ts = []
        for tr in (s.get("trades") or ()):
            t = _epoch(tr.get("trade_time"))
            if t is None:
                t = _epoch(tr.get("t"))
            if t is not None:
                ts.append(t)
        trades[slug] = sorted(ts)

    cut = _epoch(since) if since else None
    prospective, superseded = [], []
    for slug in sorted(set(books) | set(trades)):
        row = O.evaluate(slug, books, trades, [])
        ws = O.window_start_epoch(slug)
        if cut is not None and (ws is None or ws < cut):
            superseded.append(row)
        else:
            prospective.append(row)
    # The gate THIS script answers is the blindness one. L5 action
    # attachment is scored on the observer side, where the actions are; a
    # window here cannot be "clean" for want of actions the recorder never
    # sees, so requiring them would report a failure that is not one.
    live = [w for w in prospective if w["verdict"] != O.NOT_LIVE]
    blind = [w for w in live if w["verdict"] == O.BLIND]
    seen_first = [w for w in live
                  if w["book_observed_before_first_trade"] is True]
    return {"authenticated_from": since,
            "per_window": prospective,
            "summary": O.summarize(prospective),
            "superseded_summary": O.summarize(superseded),
            "btc15m_markets_in_tape": len(books),
            "blindness": {
                "live_windows": len(live),
                "blind_at_open": len(blind),
                "book_before_first_trade": len(seen_first),
                "gate": ("PASS" if (live and not blind) else "FAIL"),
                "meaning": ("PASS means that in every window observed live, "
                            "the authenticated two-sided book was seen no "
                            "later than the first authenticated trade -- we "
                            "are not blind while L5 opens."),
            }}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.getenv("PMUS_WS_ROOT",
                                                "/state/pmus-ws"))
    ap.add_argument("--max-age-s", type=float, default=None,
                    help="skip tape parts older than this")
    ap.add_argument("--since", default=DEFAULT_SINCE)
    ap.add_argument("--json", dest="out")
    a = ap.parse_args(argv)

    rep = replay(tape_rows(Path(a.root), a.max_age_s))
    out = build(rep["state"], a.since)
    out["frames_applied"] = rep["frames_applied"]
    out["frames_skipped"] = rep["frames_skipped"]
    out["labels"] = ("replayed from the RAW authenticated tape through the "
                     "recorder's own parser; every timestamp is our true "
                     "receipt of that frame. Nothing interpolated, backfilled "
                     "or derived from a trade.")
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(out, fh, indent=1, default=str)
    print("L5_AUTH_OBSERVATION " + json.dumps(
        out, separators=(",", ":"), default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
