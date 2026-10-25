"""What a PMUS maker fill is worth, using only MEASURED inputs.

Every number here comes from a measurement committed in this repo or from the
venue's published schedule. Nothing is modelled:

  spread            measured on the venue-native PMUS window tape
  P(fill)           analysis/l5/pmus_fill_model.json -- 965 of our own orders
  adverse selection analysis/l5/ADVERSE_SELECTION_MEASUREMENT_20261024.md
                    -- 956 modelled fills, 475 live windows, adverse_rate
                    0.9163, -$0.0827 per filled contract
  maker rebate      src/pmus_fees.py, the published schedule

The question is the simplest possible one, and it does not need a forecast:
a resting maker order captures some of the spread and accrues a rebate, and
pays adverse selection. Which is bigger?
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..")))

from src import pmus_fees
from src.gemini_execution.pmus_execution_model import CalibratedFillModel

HERE = os.path.dirname(os.path.abspath(__file__))

#: the measured adverse selection, per FILLED contract
ADVERSE_PER_FILLED_CONTRACT = D("0.0827")
ADVERSE_RATE = D("0.9163")
ADVERSE_SOURCE = "analysis/l5/ADVERSE_SELECTION_MEASUREMENT_20261024.md"


def main():
    ws = json.load(open(os.path.join(HERE, "pmus_windows.json")))
    spreads, mids = [], []
    for w in ws:
        for fr in w["bbo"]:
            yb, nb = D(fr[1]), D(fr[3])
            spreads.append(float(D(1) - yb - nb))
            mids.append(float((yb + (D(1) - nb)) / D(2)))
    spread = D(str(round(statistics.median(spreads), 4)))
    model = CalibratedFillModel.load()
    p_fill = D(str(round(model.p_fill(leg="first_leg", rest_s=30.0) or 0.0, 4)))

    print(f"PMUS venue-native tape: {len(ws)} windows, {len(spreads)} book frames")
    print(f"  median spread            {spread}")
    print(f"  median |mid-0.50|        {round(statistics.median([abs(m-0.5) for m in mids]),4)}")
    print(f"  P(fill by 30s), measured {p_fill}   (965 real orders)")
    print(f"  adverse selection        -{ADVERSE_PER_FILLED_CONTRACT} per filled "
          f"contract, {ADVERSE_RATE} of fills adverse")
    print(f"    source: {ADVERSE_SOURCE}")

    print("\nWHAT A RESTING MAKER ORDER CAPTURES, per filled contract")
    half = (spread / D(2)).quantize(D("0.0001"))
    print(f"  half the spread, if we rest at the touch and the mid is fair   "
          f"+{half}")
    rows = []
    for px in ("0.30", "0.50", "0.70"):
        rb = pmus_fees.maker_rebate(D(px), D(1))
        rows.append((px, rb))
    print("  maker rebate accrued (published schedule, a CREDIT):")
    for px, rb in rows:
        print(f"      at p={px}   +{rb}")
    rebate = max(rb for _, rb in rows)
    print(f"  taker fee paid                                                  "
          f"0 (maker never crosses)")

    gross = half + rebate
    net = gross - ADVERSE_PER_FILLED_CONTRACT
    print("\nWHAT IT PAYS")
    print(f"  adverse selection, MEASURED                                   "
          f"-{ADVERSE_PER_FILLED_CONTRACT}")
    print("\nNET PER FILLED CONTRACT")
    print(f"  capture {gross}  -  adverse {ADVERSE_PER_FILLED_CONTRACT}  "
          f"=  {net}")
    ratio = (ADVERSE_PER_FILLED_CONTRACT / gross) if gross > 0 else None
    print(f"  adverse selection is {round(float(ratio),1)}x everything a maker "
          f"fill can capture")

    print("\nAND WHY SELECTIVITY CANNOT CLOSE THAT GAP")
    print("  98.0% of the measured fills came from a SWEEP -- a print consuming")
    print("  our entire level. We do not get filled and then suffer an adverse")
    print("  move; the adverse move IS the fill. Resting more selectively")
    print("  changes WHICH sweeps reach us, not the fact that a sweep is what")
    print("  reaches us. To overturn this a selection rule would have to find a")
    print(f"  subset whose adverse selection is below {gross} per contract --")
    print(f"  a {round(float(ratio),1)}x improvement -- and the one such rule")
    print("  already pre-registered and tested (ADVERSE_EXTEND_RESULT.md) failed")
    print("  on HOLDOUT, missing recoveries 45:1 against losses prevented.")

    out = os.path.join(HERE, "inventory", "maker_economics_measured.json")
    json.dump(dict(windows=len(ws), median_spread=str(spread),
                   p_fill_30s=str(p_fill),
                   half_spread=str(half), max_rebate=str(rebate),
                   gross_capture=str(gross),
                   adverse_per_filled_contract=str(ADVERSE_PER_FILLED_CONTRACT),
                   adverse_rate=str(ADVERSE_RATE),
                   net_per_filled_contract=str(net),
                   adverse_multiple_of_capture=round(float(ratio), 2),
                   sources=[ADVERSE_SOURCE, "analysis/l5/pmus_fill_model.json",
                            "src/pmus_fees.py (docs.polymarket.us/fees)"]),
              open(out, "w"), indent=1)
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
