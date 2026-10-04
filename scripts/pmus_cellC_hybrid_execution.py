"""P1 — hybrid maker->taker execution on FROZEN Cell C. Execution only.

Cell C's decisions, timestamps, signals, quantities, inventory rules and
reversal logic are untouched. For every action it actually took, this asks:
could it have rested as a maker first, and what would the fallback have
cost?

Fill discipline is the established one. NO TOUCH = FILL: a resting order
fills only once its price IS the touch, with queue-ahead taken from the
depth displayed at that moment, depleted only by trades consuming our side,
and zeroed by a print strictly through our price.

The waiting grid comes from the tape's own resolution (median book
inter-arrival 0.101 s, median our-side trade inter-arrival 0.075 s, p90
0.924 s). The WHOLE frontier is reported; no timeout is selected here.
"""
from __future__ import annotations
import bisect, collections, json, os, statistics, sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util                                           # noqa: E402
from src import pmus_fees as FEES                               # noqa: E402

_s = importlib.util.spec_from_file_location(
    "dca", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "pmus_favorite_dca.py"))
DCA = importlib.util.module_from_spec(_s)
_s.loader.exec_module(DCA)
ONE = D(1)

#: cancel RTT p90 (0.612 s) + one median book inter-arrival (0.101 s)
CANCEL_LIFECYCLE_S = D("0.713")
WAITS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0)


def book_at(books, bt, t):
    j = bisect.bisect_left(bt, t) - 1
    while j >= 0 and (books[j]["bid"] is None or books[j]["ask"] is None):
        j -= 1
    return books[j] if j >= 0 else None


def prices(side, is_buy, bid, ask):
    """(taker price, maker price) in the OUTCOME's own economic terms.

    BUY  UP   : taker lifts the UP ask; maker joins the UP bid.
    BUY  DOWN : taker lifts the DOWN ask (= 1 - UP bid); maker joins the
                DOWN bid (= 1 - UP ask).
    SELL UP   : taker hits the UP bid;  maker offers at the UP ask.
    SELL DOWN : taker hits the DOWN bid (= 1 - UP ask); maker offers at the
                DOWN ask (= 1 - UP bid).
    """
    if side == "UP":
        return (ask, bid) if is_buy else (bid, ask)
    return ((ONE - bid, ONE - ask) if is_buy else (ONE - ask, ONE - bid))


def maker_level(side, is_buy, bid, ask):
    """Our resting order expressed in the single long-side book:
    (up_price_level, is_offer_side)."""
    if side == "UP":
        return (bid, False) if is_buy else (ask, True)
    # a DOWN bid is an UP offer; a DOWN offer is an UP bid
    return (ask, True) if is_buy else (bid, False)


def evidenced_fill(books, bt, trades, tt, t0, up_px, is_offer, want, horizon):
    """Seconds to an evidenced fill of `want` shares, or None."""
    queue = None
    filled = D(0)
    i = bisect.bisect_right(tt, t0)
    while i < len(trades) and trades[i]["t"] <= t0 + horizon:
        tr = trades[i]; i += 1
        b = book_at(books, bt, tr["t"])
        if b is not None and queue is None:
            if is_offer and D(b["ask"]) == up_px and b["ask_qty"] is not None:
                queue = D(b["ask_qty"])
            elif (not is_offer) and D(b["bid"]) == up_px \
                    and b["bid_qty"] is not None:
                queue = D(b["bid_qty"])
        if queue is None:
            continue
        if tr.get("dir") != ("buy" if is_offer else "sell"):
            continue
        px, qty = D(tr["px"]), D(tr["qty"])
        through = (px > up_px) if is_offer else (px < up_px)
        if through:
            queue = D(0)
            continue
        if px != up_px:
            continue
        if queue > 0:
            eaten = min(queue, qty); queue -= eaten; qty -= eaten
        if qty <= 0:
            continue
        filled += min(want - filled, qty)
        if filled >= want:
            return tr["t"] - t0
    return None


def main() -> int:
    by = DCA.load(DCA.TAPE)
    d = json.load(open("analysis/l5/favorite_dca/replay_summary.json"))
    rows = []
    for slug in sorted(d["windows"]):
        w = by[slug]
        books, trades = w["book"], w["trade"]
        bt = [b["t"] for b in books]
        tt = [t["t"] for t in trades]
        c = d["windows"][slug]["CELL_C_reversal_only"]
        # episode boundaries give ENTRY vs EXTEND without touching strategy
        entry_ts = {e["t_entry"] for e in c["episodes"]}
        acts = []
        for a in c["allocation_log"]:
            acts.append({"t": a["t"], "side": a["side"], "is_buy": True,
                         "sh": D(a["sh"]),
                         "cls": "ENTRY" if a["t"] in entry_ts else "EXTEND"})
        for e in c["exit_log"]:
            for l in e["legs"]:
                acts.append({"t": l["t"], "side": e["side_exited"],
                             "is_buy": False, "sh": D(l["sh"]),
                             "cls": "EXIT"})
        for a in acts:
            b = book_at(books, bt, a["t"])
            if b is None:
                continue
            bid, ask = D(b["bid"]), D(b["ask"])
            tk, mk = prices(a["side"], a["is_buy"], bid, ask)
            if not (D("0.01") <= tk <= D("0.99")
                    and D("0.01") <= mk <= D("0.99")):
                continue
            up_px, is_offer = maker_level(a["side"], a["is_buy"], bid, ask)
            ttf = evidenced_fill(books, bt, trades, tt, a["t"], up_px,
                                 is_offer, a["sh"], max(WAITS))
            r = {"slug": slug, "t": a["t"], "cls": a["cls"],
                 "side": a["side"], "is_buy": a["is_buy"],
                 "sh": float(a["sh"]),
                 "taker_px": float(tk), "maker_px": float(mk),
                 "spread_captured": float(abs(tk - mk)),
                 "taker_fee": float(FEES.taker_fee(tk, a["sh"])),
                 "maker_rebate_modelled": float(
                     FEES.maker_rebate(mk, a["sh"])),
                 "time_to_fill": ttf, "fallback": {}}
            for W in WAITS:
                if ttf is not None and ttf <= W:
                    r["fallback"][str(W)] = {"filled_maker": True}
                    continue
                t2 = a["t"] + W + float(CANCEL_LIFECYCLE_S)
                b2 = book_at(books, bt, t2)
                if b2 is None:
                    r["fallback"][str(W)] = {"filled_maker": False,
                                             "no_state": True}
                    continue
                tk2, _ = prices(a["side"], a["is_buy"],
                                D(b2["bid"]), D(b2["ask"]))
                r["fallback"][str(W)] = {
                    "filled_maker": False,
                    "taker_px_after": float(tk2),
                    "adverse": float((tk2 - tk) if a["is_buy"]
                                     else (tk - tk2)),
                    "fee_after": float(FEES.taker_fee(tk2, a["sh"]))}
            rows.append(r)
    json.dump(rows, open("/tmp/claude-0/-home-user-kalshi-arb-bot/"
                         "671883cb-e673-5581-ae31-5d97cf169fd1/scratchpad/"
                         "hybrid.json", "w"))
    c = collections.Counter(r["cls"] for r in rows)
    print(json.dumps({"actions": len(rows), "by_class": dict(c),
                      "with_evidenced_fill_within_60s":
                      sum(1 for r in rows if r["time_to_fill"] is not None)},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
