#!/usr/bin/env python3
"""Does a 5-minute clock give more completed inventory cycles per hour?

VENUE: Gemini. This is the ONLY venue in this repository where both a
5-minute and a 15-minute BTC up/down clock exist with book AND print tapes,
so it is the only place the question can be asked from data rather than
assumed. PMUS does not list a 5-minute up/down market at all -- the
`btc-updown-5m` slugs in `analysis/l5/breadth/universe_scan.json` come from
`gamma-api.polymarket.com`, which is Polymarket INTERNATIONAL, not
`api.polymarket.us`. So this comparison is structural evidence about the
clock, not a tradeable PMUS lane.

GEMINI PAIR MECHANICS. Gemini publishes ONE signed instrument per window,
so a matched pair is not two instruments: it is buying the instrument at the
bid and selling it at the ask. The pair therefore costs exactly

    pair_cost = bid + (1 - ask) = 1 - spread

and a completed cycle needs BOTH sides to fill -- a counterparty selling
into our bid and another buying from our ask, inside the same window.

WHAT COUNTS AS A FILL. A print at or through our quoted price, on the
correct side. That is still an UPPER BOUND: queue position is unobservable,
so a print at our price might have filled someone ahead of us. It is never
treated as realized anything. Prints are the venue's own trade tape.

FEES. Gemini maker fee is 0.0175*C*P*(1-P) and the maker rebate is
0.021*C*P*(1-P), so the pair is fee-POSITIVE by 0.0035*C*P*(1-P) IF the
rebate is credited. At P=0.50 that is +8.75 bp of notional per side. The
rebate rounds DOWN daily, so below roughly q=1 contract it credits $0.00 --
which is why five real live fills earned nothing. Both are shown; neither is
assumed.
"""
from __future__ import annotations

import collections
import gzip
import json
import pathlib
from decimal import Decimal as D

ROOT = pathlib.Path(__file__).resolve().parents[3]
BBO = ("data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz",
       "data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz")
PRINTS = ("data/transfer_backtest/gemini_prints_btc05m.jsonl.gz",
          "data/transfer_backtest/gemini_prints_15m_tail.jsonl.gz")


def clock_of(symbol: str):
    """BTC05M / BTC15M from the symbol, not from the filename.

    The files are not cleanly separated -- `gemini_prints_15m_tail` carries
    BTC05M symbols -- so routing on the filename would mix the two clocks
    and the whole comparison would be meaningless.
    """
    if "BTC05M" in symbol:
        return "5m"
    if "BTC15M" in symbol:
        return "15m"
    return None


def main() -> int:
    # ---- books, per window
    books = collections.defaultdict(list)      # (clock, window) -> samples
    for rel in BBO:
        with gzip.open(ROOT / rel, "rt") as fh:
            for line in fh:
                r = json.loads(line)
                c = clock_of(r["symbol"])
                if c is None:
                    continue
                books[(c, r["symbol"])].append(
                    (r["t"], D(r["bid"]), D(r["ask"])))

    # ---- prints, per window
    prints = collections.defaultdict(list)
    for rel in PRINTS:
        with gzip.open(ROOT / rel, "rt") as fh:
            for line in fh:
                blk = json.loads(line)
                c = clock_of(blk["symbol"])
                if c is None:
                    continue
                for p in blk.get("rows") or []:
                    prints[(c, blk["symbol"])].append(
                        (float(p["timestampms"]) / 1000.0,
                         D(str(p["price"])), D(str(p["amount"])),
                         str(p["type"])))

    out = {}
    for clock, wmin in (("5m", 300.0), ("15m", 900.0)):
        wins = [k for k in books if k[0] == clock]
        spreads, costs = [], []
        two_sided = 0
        with_prints = 0
        cycles_total = 0
        for k in wins:
            b = sorted(books[k])
            # the window's median touch, as the quote we would have joined
            sp = sorted((a - bd) for _, bd, a in b if a > bd)
            if not sp:
                continue
            med_sp = sp[len(sp) // 2]
            spreads.append(med_sp)
            costs.append(D("1") - med_sp)
            pr = prints.get(k) or []
            if pr:
                with_prints += 1
            # a completed cycle needs a SELL into our bid and a BUY from our
            # ask, both inside this window. Quote at the median touch.
            med_bid = sorted(bd for _, bd, _ in b)[len(b) // 2]
            med_ask = med_bid + med_sp
            sells = [p for p in pr if p[3] == "sell" and p[1] <= med_bid]
            buys = [p for p in pr if p[3] == "buy" and p[1] >= med_ask]
            if sells and buys:
                two_sided += 1
                cycles_total += min(len(sells), len(buys))

        cs = sorted(costs)
        ss = sorted(spreads)
        n = len(costs)
        out[clock] = {
            "windows_with_a_book": n,
            "windows_with_any_print": with_prints,
            "windows_with_both_sides_printed": two_sided,
            "two_sided_rate": (round(two_sided / n, 4) if n else None),
            "median_spread": str(ss[len(ss) // 2]) if ss else None,
            "median_attainable_pair_cost": str(cs[len(cs) // 2]) if cs else None,
            "share_pair_cost_at_or_under_0_96": (
                round(sum(1 for c in costs if c <= D("0.96")) / n, 4)
                if n else None),
            "upper_bound_cycles_per_window": (
                round(cycles_total / n, 3) if n else None),
            "upper_bound_cycles_per_hour": (
                round(cycles_total / n * (3600.0 / wmin), 3) if n else None),
            "windows_per_hour_if_every_one_traded": int(3600 / wmin),
        }

    print(json.dumps({
        "venue": "Gemini",
        "by_clock": out,
        "labels": {
            "cycles": ("UPPER BOUND. A print at our price is not our fill: "
                       "queue position is unobservable. Nothing here is "
                       "submitted, filled, settled or realized."),
            "pmus_5m": ("PMUS lists no 5-minute up/down market. The "
                        "btc-updown-5m slugs in the breadth scan are "
                        "gamma-api.polymarket.com, i.e. Polymarket "
                        "International, a different venue."),
        },
    }, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
