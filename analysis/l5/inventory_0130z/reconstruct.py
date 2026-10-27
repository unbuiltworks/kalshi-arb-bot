#!/usr/bin/env python3
"""0130z: inventory after every fill, and what an L5-style policy would do.

Fills are the venue's own L5_LIVE_LIFECYCLE terminal rows for
cpc-btc-updown-15m-2026-10-27-0130z. DOWN `avg_px` is quoted in WIRE terms,
so own cost is 1 - wire; UP's wire and own price coincide. The reconstruction
is checked against the settlement line rather than asserted.
"""
from decimal import Decimal as D

SETTLED = {"up_qty": D("3.58"), "up_cost": D("2.398400"),
           "dn_qty": D("3.58"), "dn_cost": D("1.002600"),
           "matched_qty": D("3.58"), "matched_cost": D("3.401000"),
           "settlement_pnl": D("0.179000"), "yes_value": D("1.0000")}

# (utc, coid, side, qty, avg_px as the venue reported it)
FILLS = [("01:31:35", "l5f-13fb9f6c", "UP",   D("1.78"), D("0.68")),
         ("01:37:05", "l5f-36efeba0", "DOWN", D("1.78"), D("0.73")),
         ("01:38:40", "l5f-dd74d584", "UP",   D("1.80"), D("0.66")),
         ("01:38:54", "l5f-76051b9a", "DOWN", D("1.80"), D("0.71"))]


def own(side, wire):
    """DOWN is quoted in UP terms on this book; UP's own price is the wire."""
    return wire if side == "UP" else D(1) - wire


def main():
    up = dn = up_c = dn_c = D(0)
    print("%-9s %-5s %5s %6s %6s | %5s %5s %7s %9s %8s"
          % ("utc", "side", "qty", "wire", "own",
             "UP", "DOWN", "matched", "residual", "cum cost"))
    rounds = []
    for utc, _coid, side, qty, wire in FILLS:
        o = own(side, wire)
        if side == "UP":
            up += qty; up_c += qty * o
        else:
            dn += qty; dn_c += qty * o
        matched = min(up, dn)
        resid = up - dn
        rs = "flat" if resid == 0 else "%+.2f %s" % (abs(resid),
                                                     "UP" if resid > 0 else "DN")
        print("%-9s %-5s %5s %6s %6s | %5s %5s %7s %9s %8s"
              % (utc, side, qty, wire, o, up, dn, matched, rs, up_c + dn_c))
        if resid == 0:
            rounds.append((utc, qty, o))
    print()
    # the two rounds, each priced on its OWN pair rather than the blend
    print("round 1  UP 1.78 @ 0.68 + DOWN 1.78 @ 0.27 = pair %.4f"
          % (D("0.68") + D("0.27")))
    print("round 2  UP 1.80 @ 0.66 + DOWN 1.80 @ 0.29 = pair %.4f"
          % (D("0.66") + D("0.29")))
    print()
    cost = up_c + dn_c
    print("reconstructed  up_cost %.6f  dn_cost %.6f  total %.6f"
          % (up_c, dn_c, cost))
    print("settlement     up_cost %.6f  dn_cost %.6f  total %.6f"
          % (SETTLED["up_cost"], SETTLED["dn_cost"], SETTLED["matched_cost"]))
    ok = (up_c == SETTLED["up_cost"] and dn_c == SETTLED["dn_cost"]
          and cost == SETTLED["matched_cost"])
    print("RECONCILES: %s" % ok)
    pnl = min(up, dn) * SETTLED["yes_value"] - cost
    print("net %.6f vs venue %.6f  -> %s"
          % (pnl, SETTLED["settlement_pnl"], pnl == SETTLED["settlement_pnl"]))


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------
# THE ONE DECISION POINT, and what each policy does with it.
#
# 01:32:00, holding UP 1.78 @ own 0.68, residual +1.78, book bid 0.50/ask
# 0.51. The engine logged COMPLEMENT_PAIR_ABOVE_CAP: pair 1.1500 against the
# 0.96 ceiling, complement_price 0.4700. It refused and waited 330 s.
# --------------------------------------------------------------------------
def counterfactual():
    held_q, held_p = D("1.78"), D("0.68")
    dn_now = D("0.47")          # complement own price at 01:32, refused
    dn_later = D("0.27")        # complement own price at 01:37:05, taken
    ext_p = D("0.50")           # UP maker at the 01:32 touch

    print("\n--- 01:32:00, residual +1.78 UP @ 0.68, book 0.50/0.51 ---")

    a = held_q * (D(1) - (held_p + dn_later))
    b = held_q * (D(1) - (held_p + dn_now))
    print("STRICT 0.96, waited  : pair %.4f  net %+.4f   (what we did)"
          % (held_p + dn_later, a))
    print("complete now at 0.47 : pair %.4f  net %+.4f   (ceiling refused)"
          % (held_p + dn_now, b))
    print("  value of the ceiling plus patience, round 1: %+.4f" % (a - b))

    # L5's demonstrated action here. In his 1-5 share bucket (n=6,459) he
    # EXTENDs 0.713 and complements 0.027, so this is his modal move.
    cap = D("3")                        # ABS_RESIDUAL_CAP_CONTRACTS
    ext_q = cap - held_q                # all the cap still allows
    by_cash = D("3.00") / (D(1) + ext_p)   # env_per_round / (1 + up_px)
    print("\nL5-style EXTEND, sized by whichever binds:")
    print("  residual cap allows %.2f ; per-round cash allows %.2f -> %.2f"
          % (ext_q, by_cash, min(ext_q, by_cash)))
    ext_q = min(ext_q, by_cash)
    q = held_q + ext_q
    cost = held_q * held_p + ext_q * ext_p
    print("  UP %.2f @ avg %.4f, then DOWN %.2f @ %.4f"
          % (q, cost / q, q, dn_later))
    net = q - (cost + q * dn_later)
    print("  pair %.4f  net %+.4f   (%+.4f vs what we did)"
          % (cost / q + dn_later, net, net - a))


if __name__ != "__main__":
    pass
