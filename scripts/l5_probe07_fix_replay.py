"""The two Probe 07 execution fixes, replayed. Mechanics only, no strategy.

Probe 07 lost ten actions to two defects that had nothing to do with what to
trade:

  seq 20  l5_price 0.2600000021 -> wired 0.7399999979, venue echoed 0.73,
          the exact-echo gate refused. SUBMIT_OUTCOME_UNKNOWN.
  seq 22+ nine MAKER actions refused ORDER_STATE_UNCERTAIN, all of them
          AFTER seq 20, because a refusal that never posted an order left
          the session quarantined.

This replays what each fix, and both, would have made SUBMITTABLE. It does
not claim a single fill. A submitted maker order rests; whether it fills is
the venue's answer and Probe 07 cannot supply it. Nothing here is mixed with
the Adapted treatment results.
"""
import json
from decimal import Decimal as D, ROUND_FLOOR

LAM = D("0.04518752824220514525")
TICK = D("0.01")
QINC = D("0.01")
CAP = D("5")
MAX_LIVE = 8

ACTIONS = "analysis/pmus/evidence/probe07_actions.jsonl"
ORDERS = "analysis/pmus/evidence/probe07_orders.json"


def floor_to(x: D, step: D) -> D:
    return (x / step).to_integral_value(rounding=ROUND_FLOOR) * step


def risk(up_q, up_c, dn_q, dn_c) -> D:
    at_zero = -up_c + (dn_q - dn_c)      # DOWN settles 1
    at_one = (up_q - up_c) - dn_c        # UP settles 1
    worst = max(-at_zero, -at_one)
    return worst if worst > 0 else D(0)


def main():
    acts = [json.loads(l) for l in open(ACTIONS)]
    orders = {o["id"]: o for o in json.load(open(ORDERS))["orders"]}

    lost = [a for a in acts
            if a["refused_reason"] in ("ORDER_STATE_UNCERTAIN",
                                       "SUBMIT_OUTCOME_UNKNOWN")]
    print("PROBE 07 -- EXECUTION FIX REPLAY (mechanics only)")
    print("lost actions: %d  (%d ORDER_STATE_UNCERTAIN, %d SUBMIT_OUTCOME_UNKNOWN)"
          % (len(lost),
             sum(1 for a in lost if a["refused_reason"] == "ORDER_STATE_UNCERTAIN"),
             sum(1 for a in lost if a["refused_reason"] == "SUBMIT_OUTCOME_UNKNOWN")))
    print("every one is our_mode MAKER: %s"
          % all(a["our_mode"] == "MAKER" for a in lost))
    print()

    # what each lost action would have become
    print("%-4s %-5s %-13s %-10s %8s %8s %9s"
          % ("seq", "side", "l5_price", "tick px", "l5_size", "qty", "off-tick"))
    for a in lost:
        px = D(a["l5_price"])
        tick_px = floor_to(px, TICK)
        qty = floor_to(LAM * D(a["l5_size"]), QINC)
        print("%-4d %-5s %-13s %-10s %8s %8s %9s"
              % (a["log_seq"], a["side"], a["l5_price"], tick_px,
                 a["l5_size"], qty, "YES" if tick_px != px else "no"))
    print()

    # ---- gate replay: which of the ten clear EVERY remaining gate
    # inventory from the nine real fills, keyed by the action that made them
    real = {}
    for a in acts:
        if a.get("venue_order"):
            o = orders[a["venue_order"]]
            wire = D(o["avgPx"]); q = D(str(o["cumQuantity"]))
            if o["intent"] == "ORDER_INTENT_BUY_SHORT":
                real[a["log_seq"]] = ("DOWN", D(1) - wire, q)
            else:
                real[a["log_seq"]] = ("UP", wire, q)

    for label, fix_tick, fix_terminal in (
            ("fix 1 only  (tick quantization)", True, False),
            ("fix 2 only  (terminal-known-no-order)", False, True),
            ("both fixes", True, True)):
        up_q = up_c = dn_q = dn_c = D(0)
        live = 0
        quarantined = False
        recovered, blocked = [], []
        for a in sorted(acts, key=lambda x: x["log_seq"]):
            seq = a["log_seq"]
            if seq in real:                      # a real fill, always booked
                side, px, q = real[seq]
                if side == "UP":
                    up_q += q; up_c += px * q
                else:
                    dn_q += q; dn_c += px * q
                continue
            if a["refused_reason"] == "MAKER_ONLY_NO_TAKER":
                continue                         # strategy, not mechanics
            px = D(a["l5_price"])
            tick_px = floor_to(px, TICK)
            qty = floor_to(LAM * D(a["l5_size"]), QINC)
            off_tick = tick_px != px
            if a["refused_reason"] == "SUBMIT_OUTCOME_UNKNOWN":
                if off_tick and not fix_tick:
                    blocked.append((seq, "STILL_OFF_TICK"))
                    if not fix_terminal:
                        quarantined = True
                    continue
            elif quarantined:
                blocked.append((seq, "ORDER_STATE_UNCERTAIN"))
                continue
            if qty < QINC:
                blocked.append((seq, "QTY_BELOW_INCREMENT")); continue
            live += 1          # counted, reported separately: slot pressure
                               # depends on when orders terminate, which a
                               # replay of orders never placed cannot know
            # the gate charges an outstanding order as if it filled in full
            nu_q, nu_c, nd_q, nd_c = up_q, up_c, dn_q, dn_c
            if a["side"] == "UP":
                nu_q += qty; nu_c += tick_px * qty
            else:
                nd_q += qty; nd_c += tick_px * qty
            r = risk(nu_q, nu_c, nd_q, nd_c)
            if r > CAP:
                blocked.append((seq, "RISK_EXPOSURE_%s" % r)); continue
            up_q, up_c, dn_q, dn_c = nu_q, nu_c, nd_q, nd_c
            recovered.append((seq, a["side"], str(tick_px), str(qty), str(r)))
        print("%-38s submittable %d/10   blocked %d   (price/qty/risk/identity)"
              % (label, len(recovered), len(blocked)))
        for s, why in blocked:
            print("      seq %-3d blocked %s" % (s, why))
        if recovered:
            print("      peak worst-case exposure with all of them live: %s"
                  % max(D(x[4]) for x in recovered))
        print()
    # slot pressure, measured rather than assumed
    import datetime as dt
    iv = []
    for o in orders.values():
        a = dt.datetime.fromisoformat(o["insertTime"].replace("Z", "+00:00")).timestamp()
        b = dt.datetime.fromisoformat(o["lastTransactTime"].replace("Z", "+00:00")).timestamp()
        iv.append((a, b, "real"))
    w_end = max(b for _, b, _ in iv)
    for a in acts:
        if a["refused_reason"] in ("ORDER_STATE_UNCERTAIN", "SUBMIT_OUTCOME_UNKNOWN"):
            iv.append((float(a["l5_ts"]), w_end, "recovered"))
    ev = sorted([(a, 1) for a, _, _ in iv] + [(b, -1) for _, b, _ in iv])
    cur = peak = 0
    for _, d in ev:
        cur += d
        peak = max(peak, cur)
    print("SLOT PRESSURE, both bounds, because the truth is between them:")
    print("   upper bound  -- no order ever terminates: 9 real + 10 recovered")
    print("                  = 19 slots wanted against max_live_orders %d,"
          % MAX_LIVE)
    print("                  so the last few would wait, not be lost.")
    print("   interval      -- real orders over their own insert..lastTransact,")
    print("                  recovered ones resting to window end, decision")
    print("                  time proxied by L5's action timestamp:")
    print("                  peak concurrent %d against %d -> %s"
          % (peak, MAX_LIVE,
             "WOULD BIND" if peak > MAX_LIVE else "does not bind"))
    print("   the $5 risk cap is not the constraint on either bound.")
    print()
    print("NOT CLAIMED: that any recovered order would have FILLED. A maker")
    print("order rests. Probe 07 has no evidence for the fill rate of orders")
    print("it never placed.")


if __name__ == "__main__":
    main()
