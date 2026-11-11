"""P9-D: which Gemini family, if any, can pay a sequential inventory trader?

    python3 analysis/gemini/p9d_sequential.py <pull.json> [spreads.json]

CONTROLLED BY THE P8 AUDIT (ed4573b). Consequences, all of them deliberate:

  * RAW TRADES. The mirrored-twin deletion is OFF. The audit found its stated
    mechanism falsified -- 1,474 of 1,476 candidate pairs share an aggressor
    side and every re-pulled pair carries distinct `tid`s -- so nothing here
    deletes a row the venue reported.
  * `tid` and `timestampms` are carried through and written into the event
    log, because the audit's finding was only possible once they existed.
  * NO CAPTURE THINNING. The old `capture` parameter took every Nth qualifying
    print, which is phase-locked and produced one arbitrary sample per level.
    No result below uses it. Queue position is instead handled honestly: a
    fill requires a print AT OR THROUGH our resting level, and that is stated
    as the evidence tier rather than discounted by a made-up fraction.

EVERY FILL BELOW IS HYPOTHETICAL. Tier AT_LEVEL: a print at or through a
price we are modelled as resting at. The venue's 56 CONFIRMED maker fills are
a separate corpus and are never mixed into these totals.

WHAT IS BEING TESTED. P9-F quoted once and left the quote resting for the
contract's whole life, which guarantees adverse fills as the price drifts away
-- a stale quote is not what a maker does. Here the quote is RE-CENTRED on
every print, with one print of latency, and after a first fill the position is
managed with information available at that instant only:

    S   static symmetric quote, left resting          (the P9-F baseline)
    A0  re-centred symmetric quote, no inventory logic
    A1  A0 + stop quoting the side we are already long
    A2  A1 + skew the needed side by `skew` to get paired
    A3  A2 + cross to complete when the resulting pair cost is acceptable

NO REBATE TERM ANYWHERE IN SELECTION. Rebates are computed afterwards, from a
frozen policy, and reported on their own line.
"""
import json
import os
import sys
import datetime as _dt
from collections import defaultdict
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

YES, NO = "YES", "NO"
TIER = "AT_LEVEL_HYPOTHETICAL"


def iso(v):
    try:
        return _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:                                          # noqa: BLE001
        return None


def family(c):
    return (c.get("category") or "?", c.get("subcategory") or "?")


def prints_of(c):
    """RAW, ordered, four-or-six field rows kept as they came."""
    return sorted((list(r) for r in (c.get("prints") or [])),
                  key=lambda r: (int(r[0]), str(r[1])))


def won_of(c):
    r = str(c.get("resolution") or "").strip().lower()
    return YES if r in ("yes", "up", "true") else (
        NO if r in ("no", "down", "false") else None)


def expiry_s(c):
    e = iso(c.get("expiry"))
    return None if e is None else int(e.timestamp())


# --------------------------------------------------------------------------
# the sequential maker
# --------------------------------------------------------------------------
def simulate(c, *, spread, arm, skew=F(0), comp_max=F(1), max_legs=2,
             log=None):
    """One contract, one arm. Returns the position and an event list.

    THE CAUSAL RULE, and it is the whole design: the quote acting on print k
    was derived from print k-1. We never place a quote using the price of the
    trade that fills it.
    """
    pr = prints_of(c)
    if len(pr) < 2:
        return None
    half = spread / 2
    exp = expiry_s(c)
    yes_px, no_px = [], []
    events = []
    first_fill_t = None
    first_fill_side = None
    second_fill_t = None
    completed_by_cross = False

    prev = F(str(pr[0][2]))
    for row in pr[1:]:
        t, side, px_s = int(row[0]), str(row[1]), str(row[2])
        px = F(px_s)
        ny, nn = len(yes_px), len(no_px)
        long_yes, long_no = ny > nn, nn > ny

        if arm == "S":
            # the P9-F baseline: quotes never move off the FIRST print
            bid, ask = F(str(pr[0][2])) - half, F(str(pr[0][2])) + half
        else:
            bid, ask = prev - half, prev + half

        # inventory-aware quoting
        quote_yes = quote_no = True
        if arm in ("A1", "A2", "A3"):
            quote_yes = not long_yes          # do not add to the long side
            quote_no = not long_no
        if arm in ("A2", "A3") and skew:
            if long_yes:                      # we need NO: pay up for it
                ask = ask - skew
            elif long_no:                     # we need YES: pay up for it
                bid = bid + skew

        filled = None
        if (quote_yes and side == "sell" and px <= bid
                and F(0) < bid < F(1) and ny + nn < max_legs):
            yes_px.append(bid)
            filled = (YES, bid)
        elif (quote_no and side == "buy" and px >= ask
              and F(0) < ask < F(1) and ny + nn < max_legs):
            no_px.append(F(1) - ask)
            filled = (NO, F(1) - ask)

        if filled:
            ev = {"t": t, "leg": filled[0], "price": str(filled[1]),
                  "print_price": px_s, "aggressor": side, "tier": TIER,
                  "tid": (row[4] if len(row) > 4 else None),
                  "tms": (row[5] if len(row) > 5 else None),
                  "tte_s": (None if exp is None else exp - t)}
            events.append(ev)
            if first_fill_t is None:
                first_fill_t, first_fill_side = t, filled[0]
            elif second_fill_t is None:
                second_fill_t = t

        # A3 may cross to finish a pair, using only the price just printed
        if (arm == "A3" and not filled and len(yes_px) != len(no_px)):
            if len(yes_px) > len(no_px):
                cost = F(1) - (px - half)          # lift the NO ask
                pair = (sum(yes_px, F(0)) / len(yes_px)) + cost
                if pair <= comp_max and F(0) < cost < F(1):
                    no_px.append(cost)
                    completed_by_cross = True
                    if second_fill_t is None:
                        second_fill_t = t
                    events.append({"t": t, "leg": NO, "price": str(cost),
                                   "print_price": px_s, "aggressor": "CROSS",
                                   "tier": "TAKER_HYPOTHETICAL", "tid": None,
                                   "tms": None,
                                   "tte_s": (None if exp is None else exp - t)})
            else:
                cost = px + half                    # lift the YES ask
                pair = (sum(no_px, F(0)) / len(no_px)) + cost
                if pair <= comp_max and F(0) < cost < F(1):
                    yes_px.append(cost)
                    completed_by_cross = True
                    if second_fill_t is None:
                        second_fill_t = t
                    events.append({"t": t, "leg": YES, "price": str(cost),
                                   "print_price": px_s, "aggressor": "CROSS",
                                   "tier": "TAKER_HYPOTHETICAL", "tid": None,
                                   "tms": None,
                                   "tte_s": (None if exp is None else exp - t)})
        prev = px

    y, n = len(yes_px), len(no_px)
    if y + n == 0:
        return {"symbol": c["symbol"], "family": family(c), "fills": 0,
                "events": events}
    m, r = min(y, n), abs(y - n)
    dom = None if y == n else (YES if y > n else NO)
    ay = (sum(yes_px, F(0)) / y) if y else F(0)
    an = (sum(no_px, F(0)) / n) if n else F(0)
    ad = ay if dom == YES else (an if dom == NO else F(0))
    spend = sum(yes_px, F(0)) + sum(no_px, F(0))
    won = won_of(c)
    out = {"symbol": c["symbol"], "family": family(c), "fills": y + n,
           "yes": y, "no": n, "matched_qty": m, "residual_qty": r,
           "spend": spend, "pair_cost": (ay + an) if m else None,
           "first_fill_t": first_fill_t, "first_fill_side": first_fill_side,
           "second_fill_t": second_fill_t, "crossed": completed_by_cross,
           "settled": won is not None, "events": events,
           "first_tte": (events[0]["tte_s"] if events else None),
           "second_tte": next((e["tte_s"] for e in events[1:]), None)}
    if won is None:
        return out
    matched = F(m) * (F(1) - ay - an)
    residual = F(r) * ((F(1) if dom == won else F(0)) - ad)
    out.update({"matched_pnl": matched, "residual_pnl": residual,
                "net": matched + residual, "won": won, "dominant": dom})
    return out
