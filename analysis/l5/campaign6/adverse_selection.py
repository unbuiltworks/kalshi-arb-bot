#!/usr/bin/env python3
"""How far the achievable pair moved between opening and completing.

THE QUESTION. The 0.96 opening ceiling is checked when a plan is BUILT. A
maker opening leg then rests, and it fills when someone takes it -- which is
most likely exactly when the market is moving through it. By the time it
fills, the complement can cost something quite different from what the plan
projected, and the ceiling has no say any more.

Campaign 6's 1600z third pair is the worked case: plan pair cost 0.9500,
realised 1.0500. The DOWN leg rested at own 0.41 while UP ran 0.56 -> 0.66 in
about 35 seconds, so it was taken BECAUSE the complement was running away.
No gate failed; the exposure is structural.

WHAT THIS MEASURES. For each completed pair, the complement price available
when the opening leg was submitted, against the complement price actually
paid. Positive drift is adverse. Both tails are reported: Campaign 6's
favourable tail is far bigger than its adverse one, and saying only the
latter would misread the risk.

The complement-at-open price is reconstructed the way the engine quotes:
`depth_ticks` behind the touch on the other side, from the book snapshot the
pre-submit record carries. Own terms throughout -- a DOWN own price is
(1 - wire).
"""
import json
import pathlib
from decimal import Decimal as D

HERE = pathlib.Path(__file__).resolve().parent
ONE = D("1.00")
TICK = D("0.01")
DEPTH_TICKS = 2          # L5_TS_DEPTH_TICKS as deployed for Campaign 6


def load(name):
    with (HERE / name).open() as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def own_price(side, wire):
    w = D(str(wire))
    return w if side == "UP" else ONE - w


def complement_own_at(side_to_place, bid, ask):
    """What the engine would have quoted for `side_to_place` on this book."""
    if bid in (None, "") or ask in (None, ""):
        return None
    bid, ask = D(str(bid)), D(str(ask))
    touch = bid if side_to_place == "UP" else ONE - ask
    return touch - DEPTH_TICKS * TICK


def main():
    submits = {s["coid"]: s for s in load("tape_submits.jsonl")}
    fills = {f["coid"]: f for f in load("tape_fills.jsonl")}

    legs = []
    for coid, f in fills.items():
        s = submits[coid]
        legs.append({"coid": coid, "utc": f["utc"], "window": s["window"],
                     "side": s["side"], "qty": D(str(f["qty"])),
                     "own": own_price(s["side"], f["wire_px"]),
                     "bid": s["book_bid"], "ask": s["book_ask"],
                     "submit_utc": s["utc"]})
    legs.sort(key=lambda r: r["utc"])

    # Pair them the way the window did: FIFO against the opposite side.
    lots = {}
    pairs = []
    for leg in legs:
        w = leg["window"]
        lots.setdefault(w, {"UP": [], "DOWN": []})
        lots[w][leg["side"]].append(leg)
        while lots[w]["UP"] and lots[w]["DOWN"]:
            up, dn = lots[w]["UP"].pop(0), lots[w]["DOWN"].pop(0)
            opening, complement = sorted((up, dn), key=lambda r: r["utc"])
            pairs.append({"window": w, "opening": opening,
                          "complement": complement})

    print("=" * 78)
    print("OPENING ADVERSE SELECTION -- Campaign 6, %d completed pairs"
          % len(pairs))
    print("=" * 78)
    print("%-7s %-5s %-7s %-9s %-9s %-9s %-8s %-7s" %
          ("window", "open", "own", "comp@open", "comp paid", "realised",
           "drift", "wait_s"))
    drifts = []
    for p in pairs:
        o, c = p["opening"], p["complement"]
        at_open = complement_own_at(c["side"], o["bid"], o["ask"])
        realised = o["own"] + c["own"]
        planned = None if at_open is None else o["own"] + at_open
        drift = None if planned is None else realised - planned
        if drift is not None:
            drifts.append((drift, p, realised, planned))
        wait = (_secs(c["utc"]) - _secs(o["utc"]))
        print("%-7s %-5s %-7s %-9s %-9s %-9s %-8s %-7.1f" %
              (p["window"], o["side"], o["own"],
               "n/a" if at_open is None else at_open, c["own"], realised,
               "n/a" if drift is None else ("%+0.4f" % drift), wait))

    if not drifts:
        return 1
    print("-" * 78)
    adverse = [d for d, *_ in drifts if d > 0]
    favourable = [d for d, *_ in drifts if d < 0]
    worst = max(drifts, key=lambda t: t[0])
    print("pairs measured            %d" % len(drifts))
    print("adverse (drift > 0)       %d   worst %+0.4f"
          % (len(adverse), max(adverse) if adverse else D(0)))
    print("favourable (drift < 0)    %d   best  %+0.4f"
          % (len(favourable), min(favourable) if favourable else D(0)))
    print("mean drift                %+0.4f"
          % (sum(d for d, *_ in drifts) / D(len(drifts))))
    print("pairs realised above 1.00 %d"
          % sum(1 for _d_, _p, r, _pl in drifts if r > ONE))
    print("worst case                %s %s: planned %s, realised %s (%+0.4f)"
          % (worst[1]["window"], worst[1]["opening"]["side"],
             worst[3], worst[2], worst[0]))
    print("-" * 78)
    print("READING: the adverse tail is thin but it is the one that crosses")
    print("$1.00, and crossing $1.00 is what turns a pair into a defensive")
    print("loss. The favourable tail is larger and is where the campaign's")
    print("edge came from -- a completion that gets CHEAPER while it waits.")
    print("Neither tail is evidence the repricing policy caused either one.")
    return 0


def _secs(utc):
    hh, mm, ss = utc[11:19].split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(ss)


if __name__ == "__main__":
    raise SystemExit(main())
