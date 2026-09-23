"""Turn the recorder's authenticated tape into the blindness invariant.

Reads harvested recorder log lines -- PMUS_BOOK_ROWS and PMUS_TRADE_ROWS --
and reports, per BTC15m window:

    window open -> first authenticated USABLE two-sided book
                -> first authenticated trade
                -> same market identity
                -> L5 actions with a contemporaneous book attached

A window is BLIND_AT_OPEN unless the book is observed no later than the first
trade. That is the whole question: the public path let the venue trade for
minutes before we could see a book, which is when L5 opens.

    python3 -m scripts.l5_authenticated_observation_report LOGFILE [...] \
        [--actions observer_rows.jsonl] [--json OUT]

Lines that are not tape rows are ignored, so a raw log capture can be passed
in whole. Nothing here fabricates, interpolates or backfills a price; it only
counts what the tape actually contains.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from typing import Any, Dict, Iterable, List

from src import l5_authenticated_observation as O

BOOK_PREFIX = "PMUS_BOOK_ROWS "
TRADE_PREFIX = "PMUS_TRADE_ROWS "

#: only the series under study; the recorder watches hundreds of markets
BTC15M = "cpc-btc-updown-15m-"


def _docs(lines: Iterable[str], prefix: str) -> List[dict]:
    out = []
    for line in lines:
        i = line.find(prefix)
        if i < 0:
            continue
        try:
            out.append(json.loads(line[i + len(prefix):].strip()))
        except ValueError:
            continue              # a truncated log line is skipped, not guessed
    return out


def _btc_only(by_market: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in by_market.items() if k.startswith(BTC15M)}


def build(lines: List[str], actions: List[dict]) -> Dict[str, Any]:
    books = _btc_only(O.book_states(_docs(lines, BOOK_PREFIX)))
    trades = _btc_only(O.trade_times(_docs(lines, TRADE_PREFIX)))

    by_market: Dict[str, List[dict]] = collections.defaultdict(list)
    for a in actions:
        slug = (a.get("pmus") or {}).get("slug")
        if slug:
            by_market[slug].append(a)

    windows = sorted(set(books) | set(trades))
    per = [O.evaluate(w, books, trades, by_market.get(w, [])) for w in windows]
    return {"per_window": per, "summary": O.summarize(per),
            "markets_seen": len(windows),
            "labels": ("authenticated tape only; no BBO derived from a trade, "
                       "interpolated or backfilled")}


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--actions", help="observer rows JSONL, optional")
    ap.add_argument("--json", dest="out")
    a = ap.parse_args(argv)

    lines: List[str] = []
    for p in a.logs:
        with open(p, "r", errors="replace") as fh:
            lines.extend(fh)

    actions: List[dict] = []
    if a.actions:
        with open(a.actions, "r", errors="replace") as fh:
            for line in fh:
                try:
                    actions.append(json.loads(line))
                except ValueError:
                    continue

    rep = build(lines, actions)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(rep, fh, indent=1, default=str)
    s = rep["summary"]
    print("BTC15m windows on the authenticated path: %d" % s["windows"])
    print("  median first-BOOK  latency from window open: %s s"
          % s["median_first_book_latency_s"])
    print("  median first-TRADE latency from window open: %s s"
          % s["median_first_trade_latency_s"])
    print("  blind at open: %d    clean: %d    gate: %s"
          % (s["windows_blind_at_open"], s["clean"], s["gate"]))
    for w in rep["per_window"]:
        print("  %-42s book=%-7s trade=%-7s cov=%-6s %s"
              % (w["market"], w["first_book_latency_s"],
                 w["first_trade_latency_s"], w["coverage_pct_of_window"],
                 w["verdict"]))
    return 0 if s["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
