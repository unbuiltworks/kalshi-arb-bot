"""Replay recorded PMUS BTC15m windows through the trade-through observer.

READ-ONLY. Reads committed book/trade tapes, writes one JSON summary to
stdout and nothing else. No venue request, no credential, no ledger.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import sys
from decimal import Decimal as D

from src import l5_two_sided_shadow as S

DECIDE_AT_S = float(os.getenv("TT_DECIDE_AT_S", "300"))
#: "first" walks the book in TIME ORDER and places at the FIRST snapshot
#: whose pair clears the maker-aware fee floor. "fixed" uses the snapshot at
#: DECIDE_AT_S. Never "best": choosing the best moment in hindsight is not an
#: executable rule and flatters every result it touches.
PLACEMENT = os.getenv("TT_PLACEMENT", "fixed")
QTY = D(os.getenv("TT_QTY", "5"))
TAPES = os.getenv("TT_TAPES", "data/l5_exit_cf")


def window_start(slug: str) -> float:
    p = slug.split("cpc-btc-updown-15m-")[1]
    d = dt.datetime.strptime(p, "%Y-%m-%d-%H%Mz").replace(
        tzinfo=dt.timezone.utc)
    return d.timestamp()


def settle_up_from_tape(trades) -> bool:
    """The venue's own resolution is authoritative and is NOT in these tapes;
    the final print is used only to SCORE the one-sided case and is labelled
    as such wherever it appears."""
    return D(str(trades[-1]["px"])) >= D("0.5")


def main() -> int:
    reports = []
    for bf in sorted(glob.glob(os.path.join(TAPES, "*.book.jsonl"))):
        slug = os.path.basename(bf).split(".")[0]
        tf = bf.replace(".book.", ".trades.")
        if not os.path.exists(tf):
            continue
        book = [json.loads(l) for l in open(bf)]
        trades = [json.loads(l) for l in open(tf)]
        if not trades:
            continue
        t0 = window_start(slug)
        up = settle_up_from_tape(trades)
        ok_rows = [r for r in book
                   if r.get("status") == "OK" and r.get("bid")
                   and r.get("ask") and r["t"] <= t0 + 900 - 180]
        if PLACEMENT == "first":
            from src import l5_two_sided_probe as TSP
            dec = None
            for r in ok_rows:                      # TIME ORDER, no hindsight
                b, a = D(r["bid"]), D(r["ask"])
                if a <= b:
                    continue
                if TSP.fee_floor(b, D(1) - a)["ok"]:
                    dec = r
                    break
        else:
            dec = next((r for r in ok_rows
                        if r["t"] >= t0 + DECIDE_AT_S), None)
        for depth in S.TT_DEPTHS:
            ob = S.TradeThroughObserver(slug, depth, QTY, window_start=t0)
            if dec is None:
                ob.refusal = "NO_OK_BOOK_AT_DECISION"
            else:
                ob.place(D(dec["bid"]), D(dec["ask"]),
                         S._d(dec.get("bid_qty")), S._d(dec.get("ask_qty")),
                         dec["t"])
                ob.consume(trades, ob.deadline() or (t0 + 900))
            reports.append(ob.report(up))
    print(json.dumps({"source": TAPES, "decide_at_s": DECIDE_AT_S,
                      "placement": PLACEMENT,
                      "qty_per_leg": str(QTY),
                      "windows": reports,
                      "summary": S.aggregate(reports)},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
