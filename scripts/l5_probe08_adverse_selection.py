"""What the repair side cost Probe 08 by waiting. Measurement only.

No rule is proposed here and none is implied. A pair-cost ceiling and a
price-chasing rule are both explicitly out of scope; this file exists to put
a number on the thing Probe 08's primary endpoint pointed at -- that the
repair orders which filled were the expensive ones.

TIMEBASE, stated because it is a limitation. The recovery's order table does
not carry the venue's insertTime / lastTransactTime, so every interval here
is measured on L5's OWN action timestamps -- the moment the action that
produced each order was decided. That is a lower bound on rest duration and
it is not the venue's clock.

PRICE MOVEMENT is likewise measured on L5's successive prices for the repair
side, not on a book mid. It is what the frozen evidence holds.
"""
import json
from decimal import Decimal as D

SRC = "analysis/pmus/evidence/probe08/probe08b_sequence.json"


def main():
    doc = json.load(open(SRC))
    orders = sorted(doc["orders"], key=lambda o: o["seq"])
    repairs = [o for o in orders if o["inventory_class"] == "REDUCE_RESIDUAL"]
    opens = [o for o in orders if o["inventory_class"] == "FLAT_OPEN"]
    filled = [o for o in repairs if o["venue_state"] == "ORDER_STATE_FILLED"]
    unfilled = [o for o in repairs if o["venue_state"] != "ORDER_STATE_FILLED"]
    t0 = min(o["l5_ts"] for o in repairs)

    print("PROBE 08b -- REPAIR-SIDE ADVERSE SELECTION")
    print("timebase: L5 action timestamps, a LOWER BOUND on rest duration")
    print()
    h = ("%-4s %8s %7s %9s %-12s %s"
         % ("seq", "offer px", "qty", "t-t0 (s)", "outcome", "note"))
    print(h); print("-" * (len(h) + 12))
    for o in repairs:
        print("%-4d %8s %7s %9d %-12s %s"
              % (o["seq"], o["own_price"], o["qty"], o["l5_ts"] - t0,
                 "FILLED" if o in filled else "never filled",
                 "" if o in filled else "rested to window end"))
    print()

    cheapest_offer = min(D(o["own_price"]) for o in repairs)
    first_offer = D(repairs[0]["own_price"])
    first_fill = D(filled[0]["own_price"]) if filled else None
    last_fill = D(filled[-1]["own_price"]) if filled else None
    print("  first repair OFFERED          %s at t0" % first_offer)
    print("  first repair FILLED           %s at t0+%ds"
          % (first_fill, filled[0]["l5_ts"] - t0))
    print("  last  repair FILLED           %s at t0+%ds"
          % (last_fill, filled[-1]["l5_ts"] - t0))
    print("  price paid MINUS price first offered   %+s" % (first_fill - first_offer))
    print("  cheapest offer never filled   %s" % cheapest_offer)
    print("  offers that filled            %s"
          % ", ".join(str(D(o["own_price"])) for o in filled))
    print("  offers that did NOT fill      %s"
          % ", ".join(str(D(o["own_price"])) for o in unfilled))
    print("  every unfilled offer is cheaper than every filled one: %s"
          % (max(D(o["own_price"]) for o in unfilled)
             < min(D(o["own_price"]) for o in filled)))
    print()
    print("  L5 repair-side price while our orders rested:")
    print("     %s" % " -> ".join(str(D(o["own_price"])) for o in repairs))
    print("     moved %+s over %ds"
          % (D(repairs[-1]["own_price"]) - D(repairs[0]["own_price"]),
             repairs[-1]["l5_ts"] - t0))
    print()

    # pair cost created by each repair fill, FIFO against the open legs
    print("PAIR COST CREATED BY EACH REPAIR FILL (FIFO)")
    queue = [[D(o["qty"]), D(o["own_price"]), o["seq"]] for o in opens]
    total_q, total_c = D(0), D(0)
    for o in filled:
        left = D(o["cum_qty"])
        px = D(o["own_price"])
        while left > 0 and queue:
            lot = queue[0]
            take = lot[0] if lot[0] <= left else left
            pair = px + lot[1]
            total_q += take
            total_c += pair * take
            print("   seq %d  %s of UP@%s  x  DOWN@%s (seq %d)  pair cost %s"
                  % (o["seq"], take, px, lot[1], lot[2], pair))
            lot[0] -= take
            left -= take
            if lot[0] == 0:
                queue.pop(0)
        if left > 0:
            print("   seq %d  %s of UP@%s  unmatched -> residual"
                  % (o["seq"], left, px))
    print("   weighted average pair cost  %s" % (total_c / total_q))
    print()

    print("WHAT THE UNFILLED OFFERS WOULD HAVE PAIRED AT, had they filled")
    print("(counterfactual pricing only -- NOT a claim that they would have)")
    q2 = [[D(o["qty"]), D(o["own_price"]), o["seq"]] for o in opens]
    for o in unfilled:
        left = D(o["qty"])
        px = D(o["own_price"])
        while left > 0 and q2:
            lot = q2[0]
            take = lot[0] if lot[0] <= left else left
            print("   seq %d  %s of UP@%s  x  DOWN@%s  would pair at %s"
                  % (o["seq"], take, px, lot[1], px + lot[1]))
            lot[0] -= take
            left -= take
            if lot[0] == 0:
                q2.pop(0)
    print()
    print("  ADVERSE SELECTION, first pair: filled at %s, the first offer"
          % (D(filled[0]["own_price"]) + D(opens[0]["own_price"])))
    print("  would have paired at %s -- a difference of %s per share."
          % (D(unfilled[0]["own_price"]) + D(opens[0]["own_price"]),
             (D(filled[0]["own_price"]) - D(unfilled[0]["own_price"]))))


if __name__ == "__main__":
    main()
