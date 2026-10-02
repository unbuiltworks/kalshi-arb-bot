"""L5 SETTINGS AUDIT A3 -- UP/DOWN / YES-NO semantic consistency tests.

This lane has had NO-side accounting bugs before, so the transformation from
Gemini wire data to economic outcome is ASSERTED here, not assumed.

Wire facts established by probe earlier in this lane:
  * a BTC05M window lists exactly ONE instrument, "...-UP". No DOWN symbol
    exists on the wire (measured: 204,867 bbo / 692 print rows, all -UP).
  * a trade carries p, q and m where m = buyer_is_maker, naming the PASSIVE
    side: m=True  -> the buyer was the maker, so the TAKER SOLD into the bid;
                     a passive BUY is the counterparty.
            m=False -> the seller was the maker, so the TAKER BOUGHT from the
                     ask; a passive SELL is the counterparty.
  * depth "bids"/"asks" are quoted in UP terms.

Economic identities under test:
  I1  holding UP settles 1 if UP resolves true, else 0.
  I2  the economic complement of +1 UP is -1 UP (selling it back), NOT a
      separate DOWN instrument.
  I3  pair_cost for buy@p_buy then sell@p_sell is p_buy + (1 - p_sell),
      which is < 1 exactly when p_sell > p_buy.
  I4  matched P&L per contract = p_sell - p_buy = 1 - pair_cost.
  I5  a passive BUY fills only on m=True prints; a passive SELL only on
      m=False prints. Mixing them double-counts every print.
  I6  settlement of a matched pair is path-independent: it pays the same
      whether UP settles 0 or 1.
  I7  directional residual is exposed to settlement; matched is not.
  I8  the maker rebate formula 0.30*0.07*C*P*(1-P) is symmetric in P about
      0.50 and rounds DOWN to the cent.
"""
from __future__ import annotations

import math
import sys
from decimal import Decimal as D, ROUND_FLOOR

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAIL.append(name)


def pair_cost(p_buy, p_sell):
    return p_buy + (1.0 - p_sell)


def matched_pnl(p_buy, p_sell, qty=1.0):
    return qty * (p_sell - p_buy)


def settle_value(inv_long, inv_short, settles_true):
    """Cash from settlement for a long/short UP position."""
    payoff = 1.0 if settles_true else 0.0
    return inv_long * payoff - inv_short * payoff


def rebate_float(C, P):
    """NAIVE float implementation. Kept to DEMONSTRATE a real hazard: with
    floor-to-cent, float representation breaks the P <-> 1-P symmetry of the
    formula at cent boundaries (measured: C=1000 gives 4.40 at P=0.3 but 4.41
    at P=0.7, which are the same quantity). Any production rebate accrual
    must use Decimal."""
    raw = 0.30 * 0.07 * C * P * (1.0 - P)
    return math.floor(raw * 100.0) / 100.0


def rebate(C, P):
    """CORRECT implementation: exact decimal arithmetic, floor to the cent."""
    C, P = D(str(C)), D(str(P))
    raw = D("0.30") * D("0.07") * C * P * (D("1") - P)
    return (raw * 100).to_integral_value(rounding=ROUND_FLOOR) / 100


def passive_side_for(buyer_is_maker):
    return "buy" if buyer_is_maker is True else "sell"


def main():
    print("A3  UP/DOWN SEMANTIC CONSISTENCY TESTS")
    print()
    # I3 / I4
    check("I3 pair_cost < 1 iff p_sell > p_buy",
          pair_cost(0.30, 0.40) < 1.0 and pair_cost(0.40, 0.30) > 1.0
          and abs(pair_cost(0.35, 0.35) - 1.0) < 1e-12,
          f"{pair_cost(0.30,0.40)=} {pair_cost(0.40,0.30)=}")
    check("I4 matched pnl == 1 - pair_cost",
          all(abs(matched_pnl(b, s) - (1.0 - pair_cost(b, s))) < 1e-12
              for b, s in ((0.1, 0.9), (0.3, 0.4), (0.45, 0.55), (0.8, 0.2))))
    # I6 path independence
    ok = True
    for b, s in ((0.30, 0.40), (0.10, 0.95), (0.60, 0.61)):
        for truth in (True, False):
            cash = -b + s + settle_value(1.0, 1.0, truth)
            if abs(cash - matched_pnl(b, s)) > 1e-12:
                ok = False
    check("I6 matched pair settlement is path-independent", ok)
    # I7 residual IS exposed
    long_only_true = -0.30 + settle_value(1.0, 0.0, True)
    long_only_false = -0.30 + settle_value(1.0, 0.0, False)
    check("I7 directional residual IS settlement-exposed",
          abs(long_only_true - long_only_false) > 0.5,
          f"{long_only_true=} {long_only_false=}")
    # I5 passive-side mapping
    check("I5 buyer_is_maker=True -> passive BUY",
          passive_side_for(True) == "buy")
    check("I5 buyer_is_maker=False -> passive SELL",
          passive_side_for(False) == "sell")
    check("I5 a print is passive on exactly one side",
          passive_side_for(True) != passive_side_for(False))
    # I8 rebate symmetry + floor
    check("I8 rebate symmetric about P=0.50 (Decimal)",
          rebate(1000, 0.3) == rebate(1000, 0.7),
          f"{rebate(1000,0.3)=} {rebate(1000,0.7)=}")
    check("I8b FLOAT implementation BREAKS that symmetry (hazard confirmed)",
          rebate_float(1000, 0.3) != rebate_float(1000, 0.7),
          f"float gave {rebate_float(1000,0.3)} vs {rebate_float(1000,0.7)}")
    check("I8 rebate rounds DOWN to the cent (q1 at P=0.5 pays ZERO)",
          rebate(1, 0.5) == 0,
          f"raw={0.30*0.07*1*0.25:.6f} -> {rebate(1,0.5)}")
    check("I8 max per-contract rebate is 0.021*0.25 = $0.00525",
          abs(0.30 * 0.07 * 0.25 - 0.00525) < 1e-12)

    # MUTATION TESTS: each deliberately-wrong variant MUST be caught.
    print("\n  mutation tests (a wrong implementation must FAIL these):")

    def bad_pair_cost(p_buy, p_sell):      # forgets the complement
        return p_buy + p_sell
    check("M1 catches pair_cost = p_buy + p_sell",
          not (bad_pair_cost(0.30, 0.40) < 1.0) or
          abs(bad_pair_cost(0.3, 0.4) - pair_cost(0.3, 0.4)) > 1e-9,
          "the wrong formula was NOT distinguished")

    def bad_matched(p_buy, p_sell, qty=1.0):   # sign flip
        return qty * (p_buy - p_sell)
    check("M2 catches sign-flipped matched pnl",
          abs(bad_matched(0.3, 0.4) - matched_pnl(0.3, 0.4)) > 1e-9)

    def bad_passive(buyer_is_maker):           # inverted side mapping
        return "sell" if buyer_is_maker is True else "buy"
    check("M3 catches inverted passive-side mapping",
          bad_passive(True) != passive_side_for(True))

    def bad_rebate(C, P):                      # rounds to NEAREST, not down
        return round(0.30 * 0.07 * C * P * (1.0 - P), 2)
    # C=3 at P=0.5 gives raw 0.01575: floor -> 0.01, nearest -> 0.02.
    # C=2 does NOT discriminate (both 0.01), so it would be a vacuous test.
    check("M4 catches round-to-nearest rebate (discriminating case C=3)",
          abs(float(rebate(3, 0.5)) - bad_rebate(3, 0.5)) > 1e-9,
          f"nearest={bad_rebate(3,0.5)} down={float(rebate(3,0.5))}")

    def bad_settle(inv_long, inv_short, truth):  # ignores the short leg
        return inv_long * (1.0 if truth else 0.0)
    check("M5 catches settlement that ignores the short leg",
          bad_settle(1, 1, True) != settle_value(1, 1, True))

    print()
    if FAIL:
        print(f"RESULT: {len(FAIL)} FAILED -> {FAIL}")
        return 1
    print("RESULT: ALL SEMANTIC AND MUTATION TESTS PASS")
    print()
    print("CONSEQUENCE FOR THE AUDIT: the UP-only wire representation is")
    print("economically consistent. There is no DOWN instrument to mis-handle")
    print("on BTC05M, so the historical NO-side bug class cannot arise here in")
    print("the form it did on a two-instrument venue. The complement is a SELL")
    print("of the same instrument, which a2_inventory_complement.py implements.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
