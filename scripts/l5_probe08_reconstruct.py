"""Probe 08b, reconstructed from the frozen journal. Venue used only to settle.

The live sequence -- what was offered, what class it was, what was submitted
-- comes from the journal, which is hashed and unmodified. The venue read
supplies only the terminal order states and the settlement price.
"""
import json
from decimal import Decimal as D

from src.l5_expectancy_replay import Fill, InventoryWalk, UP, DOWN
from src import l5_live_execution as EXEC
from src.l5_live_execution import InventoryStore

SRC = "analysis/pmus/evidence/probe08b_sequence.json"
ALT = "analysis/pmus/evidence/probe08/probe08b_sequence.json"


def load():
    try:
        return json.load(open(ALT))
    except FileNotFoundError:
        return json.load(open(SRC))


def main():
    doc = load()
    winner = UP
    orders = doc["orders"]
    filled = [o for o in orders if o["venue_state"] == "ORDER_STATE_FILLED"]
    rested = [o for o in orders if o["venue_state"] == "ORDER_STATE_NEW"]

    print("PROBE 08b -- PRIMARY ENDPOINT")
    print()
    hdr = "%-4s %-5s %-18s %10s %7s %8s %-22s" % (
        "seq", "side", "class", "own px", "qty", "filled", "venue state")
    print(hdr); print("-" * len(hdr))
    for o in orders:
        print("%-4d %-5s %-18s %10s %7s %8s %-22s"
              % (o["seq"], o["side"], o["inventory_class"], o["own_price"],
                 o["qty"], o["cum_qty"], o["venue_state"]))
    print()
    rr = [o for o in orders if o["inventory_class"] == "REDUCE_RESIDUAL"]
    rr_filled = [o for o in rr if o["venue_state"] == "ORDER_STATE_FILLED"]
    rr_partial = [o for o in rr if D(o["cum_qty"]) > 0
                  and D(o["cum_qty"]) < D(o["qty"])]
    print("REDUCE_RESIDUAL  placed %d | partially filled %d | fully filled %d"
          % (len(rr), len(rr_partial), len(rr_filled)))
    print("                 fill rate %d/%d = %.1f%% of orders, %s/%s shares"
          % (len(rr_filled), len(rr), 100.0 * len(rr_filled) / len(rr),
             sum((D(o["cum_qty"]) for o in rr), D(0)),
             sum((D(o["qty"]) for o in rr), D(0))))
    print()

    # economics, in the unit of account: buy outcome X at X's own price
    walk = InventoryWalk(doc["market"])
    reb = D(0)
    for o in filled:
        side = UP if o["side"] == "UP" else DOWN
        walk.accept(Fill(o["l5_ts"], side, D(o["own_price"]),
                         D(o["cum_qty"]), rebate=-D(o["commission"]),
                         ref=o["venue_order_id"]))
        reb += -D(o["commission"])
    res = walk.settle(winner)
    print("CHRONOLOGICAL MATCHED / RESIDUAL  (FIFO, fills only)")
    for k in ("matched_qty", "matched_cost", "matched_redemption",
              "matched_pnl", "avg_pair_cost", "residual_qty",
              "residual_side", "residual_cost", "residual_pnl",
              "maker_rebates", "spend", "redemption", "total"):
        print("   %-20s %s" % (k, res[k]))
    print()
    print("   identity: redemption %s - spend %s + rebates %s = %s"
          % (res["redemption"], res["spend"], res["maker_rebates"],
             res["redemption"] - res["spend"] + res["maker_rebates"]))
    print()

    # exposure trajectory through the production risk function
    print("RISK EXPOSURE TRAJECTORY (production formula, venue-confirmed fills)")
    st = InventoryStore("/tmp/p08recon.json")
    st.state["positions"], st.state["leg_cost"] = {}, {}
    st.state["live_orders"] = {}
    m = doc["market"]
    peak = D(0)
    events = sorted(orders, key=lambda o: (o["l5_ts"], o["seq"]))
    for o in events:
        if o["venue_state"] == "ORDER_STATE_FILLED":
            st.apply_fill(m, o["side"], D(o["cum_qty"]), D(o["wire"]),
                          basis=EXEC.WIRE)
        else:
            # live_orders["price"] is read by risk_exposure_usd as what the
            # leg COSTS, so the venue's wire number is converted here too.
            st.state["live_orders"][o["venue_order_id"]] = {
                "market": m, "side": o["side"],
                "price": str(EXEC.own_outcome_price(
                    o["side"], D(o["wire"]), basis=EXEC.WIRE)),
                "remaining": o["qty"], "state": "LIVE_AT_VENUE"}
        r = st.risk_exposure_usd(m)
        peak = max(peak, r)
        print("   after seq %d  %-5s %-18s  risk_exposure_usd %8s"
              % (o["seq"], o["side"], o["inventory_class"], r))
    print()
    print("   PEAK risk_exposure_usd %s against the hard gate $5.00  -> %s"
          % (peak, "WITHIN" if peak <= D(5) else "BREACHED"))
    print("   the engine refused %d further REDUCE_RESIDUAL actions"
          " MAX_USD_PER_MARKET, which is its own statement that the bound bit"
          % len(doc["refused_reduce_residual_max_usd"]))


if __name__ == "__main__":
    main()
