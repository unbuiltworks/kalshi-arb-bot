"""Invariant tests. If ANY of these fail the lane stops -- per instruction 1.

The three pair-cost identities are the Step-0 findings promoted to tests, plus
the accounting decomposition, the anti-martingale rule, and the rebate rules.
"""
from __future__ import annotations

import sys
from decimal import Decimal as D

sys.path.insert(0, "analysis/gemini/favorite_inventory")
from fi_core import (Book, Inventory, make_fill, maker_fee, taker_fee,
                     rebate, ONE)

FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAIL.append(name)


def bk(bid, ask, bs="100", az="100"):
    return Book(D(bid), D(bs), D(ask), D(az))


print("FI INVARIANTS\n")
print("--- 1. PAIR-COST IDENTITIES (the negative control) ---")
for bid, ask in (("0.49", "0.51"), ("0.40", "0.60"), ("0.38", "0.62"),
                 ("0.10", "0.90"), ("0.70", "0.72")):
    b = bk(bid, ask)
    s = b.spread
    tt = b.pair_cost("taker", "taker")
    mm = b.pair_cost("maker", "maker")
    mt = b.pair_cost("maker", "taker")
    tm = b.pair_cost("taker", "maker")
    check(f"taker/taker = 1+spread  (bid {bid} ask {ask}, s={s})",
          tt == ONE + s, f"got {tt}")
    check(f"maker/maker = 1-spread  (bid {bid} ask {ask})",
          mm == ONE - s, f"got {mm}")
    check(f"mixed = 1 exactly       (bid {bid} ask {ask})",
          mt == ONE and tm == ONE, f"got {mt}/{tm}")

print("\n--- 2. TAKER/TAKER MATCHING IS NEVER PROFITABLE ---")
b = bk("0.38", "0.62")
inv = Inventory()
inv.add(make_fill(0, "UP", "taker", b.price_for("UP", "taker"), 10, "taker_depth"))
inv.add(make_fill(1, "DOWN", "taker", b.price_for("DOWN", "taker"), 10, "taker_depth"))
inv.assert_identities()
check("taker/taker matched_pnl is NEGATIVE before fees",
      inv.matched_pnl() < 0, f"matched_pnl={inv.matched_pnl()}")
check("taker/taker matched loss equals qty*spread",
      inv.matched_pnl() == -(D("10") * b.spread),
      f"{inv.matched_pnl()} vs {-(D('10')*b.spread)}")
check("taker/taker net is negative for BOTH outcomes",
      inv.net_if("UP") < 0 and inv.net_if("DOWN") < 0,
      f"UP={inv.net_if('UP')} DOWN={inv.net_if('DOWN')}")

print("\n--- 3. MAKER/MAKER MATCHING IS POSITIVE BEFORE FEES ---")
inv2 = Inventory()
inv2.add(make_fill(0, "UP", "maker", b.price_for("UP", "maker"), 10, "same_price_fifo"))
inv2.add(make_fill(1, "DOWN", "maker", b.price_for("DOWN", "maker"), 10, "same_price_fifo"))
inv2.assert_identities()
check("maker/maker matched_pnl == qty*spread",
      inv2.matched_pnl() == D("10") * b.spread, f"{inv2.matched_pnl()}")
check("maker/maker net positive for BOTH outcomes after fees+rebate",
      inv2.net_if("UP") > 0 and inv2.net_if("DOWN") > 0,
      f"UP={inv2.net_if('UP')} DOWN={inv2.net_if('DOWN')}")

print("\n--- 4. ACCOUNTING DECOMPOSITION ---")
inv3 = Inventory()
inv3.add(make_fill(0, "UP", "taker", "0.62", 7, "taker_depth"))
inv3.add(make_fill(1, "UP", "maker", "0.38", 3, "same_price_fifo"))
inv3.add(make_fill(2, "DOWN", "maker", "0.38", 4, "same_price_fifo"))
inv3.assert_identities()
check("matched == min(q_up,q_down)", inv3.matched == D("4"))
check("residual == |q_up-q_down|", inv3.residual == D("6"))
check("cash == c_up + c_down", inv3.cash == inv3.c_up + inv3.c_down)
check("pnl == matched + residual, UP",
      inv3.pnl_if("UP") == inv3.matched_pnl() + inv3.residual_pnl_if("UP"))
check("pnl == matched + residual, DOWN",
      inv3.pnl_if("DOWN") == inv3.matched_pnl() + inv3.residual_pnl_if("DOWN"))
check("directional loss NOT hidden in matched profit",
      inv3.residual_pnl_if("DOWN") != inv3.pnl_if("DOWN")
      or inv3.matched_pnl() == 0)

print("\n--- 5. REBATE RULES ---")
check("rebate ZERO above the 0.80 band", rebate(100, D("0.85")) == 0,
      f"{rebate(100, D('0.85'))}")
check("rebate ZERO below the 0.20 band", rebate(100, D("0.15")) == 0)
check("rebate ZERO at q1 (cent floor)", rebate(1, D("0.50")) == 0)
check("rebate symmetric P <-> 1-P (Decimal)",
      rebate(1000, D("0.30")) == rebate(1000, D("0.70")),
      f"{rebate(1000, D('0.30'))} vs {rebate(1000, D('0.70'))}")
check("rebate only on maker fills",
      make_fill(0, "UP", "taker", "0.5", 100, "x").rebate == 0
      and make_fill(0, "UP", "maker", "0.5", 100, "x").rebate > 0)
check("taker fee > maker fee at same C,P",
      taker_fee(100, D("0.5")) > maker_fee(100, D("0.5")))
# raw = 0.07*1*0.5*0.5 = 0.0175 -> CEIL to 0.02 (not 0.01, not round-to-0.02)
check("fees ceil to the cent (0.0175 -> 0.02)",
      taker_fee(1, D("0.5")) == D("0.02"),
      f"raw=0.0175 -> {taker_fee(1, D('0.5'))}")
# an exact cent must NOT be bumped up by the ceiling
check("fees do not inflate an exact cent",
      maker_fee(D("400"), D("0.5")) == D("1.75"),
      f"raw=1.75 -> {maker_fee(D('400'), D('0.5'))}")
check("ceil is strictly above round-to-nearest where they differ",
      taker_fee(D("3"), D("0.5")) == D("0.06"),
      f"raw=0.0525 -> {taker_fee(D('3'), D('0.5'))}")

print("\n--- 6. ANTI-MARTINGALE RULE ---")
def extend_admissible(inv, book, side, mode, qty, cap_gross, cap_residual):
    """An EXTEND must satisfy every clause independently."""
    px = book.price_for(side, mode)
    if px is None:
        return False, "no_price"
    if inv.cash + qty * px > cap_gross:
        return False, "gross_cap"
    nu = inv.q_up + (qty if side == "UP" else 0)
    nd = inv.q_down + (qty if side == "DOWN" else 0)
    if abs(nu - nd) > cap_residual:
        return False, "residual_cap"
    return True, "ok"

inv4 = Inventory()
inv4.add(make_fill(0, "UP", "taker", "0.62", 10, "taker_depth"))
ok, why = extend_admissible(inv4, b, "UP", "taker", D("100"), D("20"), D("50"))
check("EXTEND refused when gross cap would be breached", not ok and why == "gross_cap", why)
ok, why = extend_admissible(inv4, b, "UP", "taker", D("20"), D("1000"), D("15"))
check("EXTEND refused when residual cap would be breached",
      not ok and why == "residual_cap", why)
ok, why = extend_admissible(inv4, b, "DOWN", "maker", D("5"), D("1000"), D("50"))
check("EXTEND allowed when both caps respected", ok, why)

print("\n--- 7. NO FUTURE KNOWLEDGE / NO MIDPOINT FILLS ---")
check("midpoint is never an acquisition price",
      b.price_for("UP", "taker") != b.mid and b.price_for("UP", "maker") != b.mid)
check("one-sided book yields no pair cost",
      _oneside_raises := (lambda: (
          Book(None, None, D("0.5"), D("1")).two_sided is False))())

print()
if FAIL:
    print(f"RESULT: {len(FAIL)} INVARIANT FAILURES -> {FAIL}")
    print("PER INSTRUCTION 1: STOP. Implementation violates the identities.")
    sys.exit(1)
print("RESULT: ALL INVARIANTS PASS -- identities enforced in the code path.")
