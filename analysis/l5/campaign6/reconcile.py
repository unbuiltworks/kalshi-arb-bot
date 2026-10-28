#!/usr/bin/env python3
"""Re-derive Campaign 6 from the tape alone, with no Railway access.

This exists so the six completed pairs, the zero campaign residual and the
+$1.5908 realized net can be checked independently of the engine's own
counters -- and so the two places where the engine and the tape DISAGREE are
visible rather than buried:

  * the 12th fill, booked through the requote cancel race, never produced a
    positive L5_LIVE_LIFECYCLE delta, so the engine's fill_events and
    contracts_filled undercount it;
  * one completion closed a pair ABOVE $1.00. That is a defensive loss, not
    negative matched surplus, and is reported on its own line.

Own vs wire: a matched UP/DOWN pair redeems exactly $1.00, so pair cost is
the sum of the two OWN prices. The venue quotes DOWN on the wire as
(1 - own), which is why every DOWN fill is converted before it is summed.
"""
import json
import pathlib
from decimal import Decimal as D

HERE = pathlib.Path(__file__).resolve().parent
ONE = D("1.00")


def load(name):
    with (HERE / name).open() as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def own_price(side, wire):
    w = D(str(wire))
    return w if side == "UP" else ONE - w


def main():
    submits = {s["coid"]: s for s in load("tape_submits.jsonl")}
    fills = load("tape_fills.jsonl")
    cash = load("tape_cash_moves.jsonl")

    # Join each fill to its order to recover side and window.
    legs = []
    for f in fills:
        s = submits.get(f["coid"])
        if s is None:
            raise SystemExit("fill with no submit row: %s" % f["coid"])
        legs.append({
            "utc": f["utc"], "coid": f["coid"], "window": s["window"],
            "side": s["side"], "qty": D(str(f["qty"])),
            "own": own_price(s["side"], f["wire_px"]),
            "wire": D(str(f["wire_px"])),
            "requested_own": D(str(s["own_px"])),
        })
    legs.sort(key=lambda r: r["utc"])

    windows, order = {}, []
    for leg in legs:
        w = leg["window"]
        if w not in windows:
            windows[w] = {"lots": {"UP": [], "DOWN": []}, "pairs": [],
                          "fills": 0, "contracts": D(0)}
            order.append(w)
        st = windows[w]
        st["fills"] += 1
        st["contracts"] += leg["qty"]
        st["lots"][leg["side"]].append([leg["qty"], leg["own"], leg["coid"]])

        # FIFO-match against the opposite side as soon as both exist. This
        # mirrors the venue merging a matched pair to cash on match.
        other = "DOWN" if leg["side"] == "UP" else "UP"
        while st["lots"]["UP"] and st["lots"]["DOWN"]:
            up, dn = st["lots"]["UP"][0], st["lots"]["DOWN"][0]
            q = min(up[0], dn[0])
            cost = up[1] + dn[1]
            st["pairs"].append({"qty": q, "pair_cost": cost,
                                "up_own": up[1], "dn_own": dn[1],
                                "pnl": q * (ONE - cost),
                                "up_coid": up[2], "dn_coid": dn[2]})
            up[0] -= q
            dn[0] -= q
            if up[0] == 0:
                st["lots"]["UP"].pop(0)
            if dn[0] == 0:
                st["lots"]["DOWN"].pop(0)

    print("=" * 74)
    print("CAMPAIGN 6 -- re-derived from tape (no Railway)")
    print("=" * 74)
    tot_surplus = tot_defensive = D(0)
    tot_fills = 0
    tot_contracts = D(0)
    for w in order:
        st = windows[w]
        surplus = sum((p["pnl"] for p in st["pairs"]
                       if p["pair_cost"] <= ONE), D(0))
        defensive = sum((p["pnl"] for p in st["pairs"]
                         if p["pair_cost"] > ONE), D(0))
        resid = {s: sum((l[0] for l in st["lots"][s]), D(0))
                 for s in ("UP", "DOWN")}
        tot_surplus += surplus
        tot_defensive += defensive
        tot_fills += st["fills"]
        tot_contracts += st["contracts"]
        print("\n%s  fills=%d  contracts=%s  pairs=%d"
              % (w, st["fills"], st["contracts"], len(st["pairs"])))
        for p in st["pairs"]:
            tag = "DEFENSIVE" if p["pair_cost"] > ONE else "matched"
            print("    qty %-5s  UP %-6s + DOWN %-6s = %-6s  %+8.4f  %s"
                  % (p["qty"], p["up_own"], p["dn_own"], p["pair_cost"],
                     p["pnl"], tag))
        print("    surplus %+8.4f   defensive %+8.4f   residual UP=%s DOWN=%s"
              % (surplus, defensive, resid["UP"], resid["DOWN"]))

    net = tot_surplus + tot_defensive
    print("\n" + "-" * 74)
    print("matched-pair surplus   %+10.4f" % tot_surplus)
    print("defensive losses       %+10.4f" % tot_defensive)
    print("re-derived net         %+10.4f" % net)

    first = D(str(cash[0]["available_cash_usd"]))
    last = D(str(cash[-1]["available_cash_usd"]))
    print("cash first -> last     %s -> %s  (%+0.4f)"
          % (first, last, last - first))
    agree = (last - first) == net
    print("cash agrees with pairs %s" % ("YES" if agree else "NO"))
    print("fills %d, contracts %s" % (tot_fills, tot_contracts))
    print("-" * 74)
    return 0 if agree else 1


if __name__ == "__main__":
    raise SystemExit(main())
