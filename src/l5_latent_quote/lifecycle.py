"""Shadow order lifecycle and defensible-fill evidence.

No touch = fill. No invented queue. If the queue cannot be established the fill
is UNKNOWN, never assumed. Repricing is explicit: CANCELLED then PLACED_SHADOW.
"""
from __future__ import annotations
import itertools
from . import config as C

_seq = itertools.count(1)
PLACED, RESTING, PARTIAL, FULL = "PLACED_SHADOW", "RESTING", "PARTIAL_SHADOW_FILL", "FULL_SHADOW_FILL"
CANCELLED, EXPIRED, REPRICED, UNKNOWN = "CANCELLED", "EXPIRED", "REPRICED", "UNKNOWN_FILL"


def place(now, slug, model, arm, side, q):
    return {"oid": f"SH{next(_seq):07d}", "events": [(now, PLACED)], "state": RESTING,
            "slug": slug, "model": model, "arm": arm, "side": side,
            "price": q["price"], "size": q["size"], "queue_ahead_at_place": q["queue_ahead"],
            "bid_at_place": q["bid_at_place"], "ask_at_place": q["ask_at_place"],
            "placed_t": now, "filled_q": 0.0, "vol_at_level": 0.0, "vol_through": 0.0,
            "first_fill_t": None, "full_fill_t": None, "evidence": None,
            "px_at_fill_bid": None, "px_at_fill_ask": None}


def _same_side_consumed(o, tr):
    """A taker SELL of our outcome executes against resting BIDS, i.e. against us."""
    return tr["up"] == (o["side"] == "up") and (not tr["buy"])


def observe(o, now, trades, bk, markout_s=30.0):
    """Advance one resting shadow order against subsequent evidence."""
    if o["state"] in (FULL, CANCELLED, EXPIRED, REPRICED):
        return o
    for tr in trades:
        if tr["t"] <= o["placed_t"] or not _same_side_consumed(o, tr):
            continue
        if tr["p"] < o["price"] - 1e-9:
            o["vol_through"] += tr["q"]
        elif abs(tr["p"] - o["price"]) < 1e-9:
            o["vol_at_level"] += tr["q"]

    ev = None
    if o["vol_through"] > 0:
        ev = "TRADE_THROUGH"
    elif o["queue_ahead_at_place"] is None:
        ev = None
    elif o["vol_at_level"] > o["queue_ahead_at_place"] + 1e-9:
        ev = "QUEUE_CONSUMED"

    if ev:
        avail = (o["vol_at_level"] - o["queue_ahead_at_place"]) if ev == "QUEUE_CONSUMED" else o["size"]
        got = max(0.0, min(o["size"], avail))
        if got > o["filled_q"]:
            o["filled_q"] = got
            if o["first_fill_t"] is None:
                o["first_fill_t"] = now
                b = bk["bids"][0]["p"] if bk["bids"] else None
                a = bk["asks"][0]["p"] if bk["asks"] else None
                o["px_at_fill_bid"], o["px_at_fill_ask"] = b, a
            o["evidence"] = ev
            o["state"] = FULL if o["filled_q"] >= o["size"] - 1e-9 else PARTIAL
            o["events"].append((now, o["state"]))
            if o["state"] == FULL:
                o["full_fill_t"] = now
    elif o["vol_at_level"] > 0 and o["state"] == RESTING:
        # same-price prints that do NOT exhaust the observed queue are NOT a fill
        o["evidence"] = UNKNOWN

    if o["state"] in (RESTING, PARTIAL) and now - o["placed_t"] > C.MAX_REST_S:
        o["state"] = EXPIRED
        o["events"].append((now, EXPIRED))
    return o


def cancel_for_reprice(o, now):
    """Explicit cancel. No silent repricing: both events are preserved."""
    if o["state"] in (RESTING, PARTIAL):
        o["state"] = REPRICED
        o["events"].append((now, REPRICED))
    return o
