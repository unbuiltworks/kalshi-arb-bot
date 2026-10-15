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
        dec = next((r for r in book
                    if r["t"] >= t0 + DECIDE_AT_S
                    and r.get("status") == "OK"
                    and r.get("bid") and r.get("ask")), None)
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
                      "qty_per_leg": str(QTY),
                      "windows": reports,
                      "summary": S.aggregate(reports)},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
