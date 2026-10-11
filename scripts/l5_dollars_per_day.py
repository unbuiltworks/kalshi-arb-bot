"""Lane 6 -- turn any candidate edge into dollars/day, and work BACKWARD
from the $480/day target.

  $/day = opportunities/day  x  causal fill probability  x  net EV/fill
                              x  executable size

This module does NOT generate hypotheses and does not claim profit. It
prices one, and it states what would have to be true. Every input is
named and sourced, and an input we have not measured is passed as None and
reported as UNMEASURED rather than defaulted to something convenient.

MEASURED CONSTANTS from this programme's own evidence (sourced, not
assumed):
  * BTC5m windows per day            288  (one per 5 minutes, 24h)
  * taker fee                        0.07*q*p*(1-p), cent-rounded,
                                     RECONCILED against our own charged
                                     fills in cohort 1
  * maker fee                        0.0175*q*p*(1-p) -- published, and
                                     NOT reconciled: we have never had a
                                     maker fill on this venue
  * measured ack latency p90         0.197 s (five live fills, 134-197 ms)
  * complement reach, underdog       45.9% of 61 independent windows
  * complement reach, favored        50.8% of 61 independent windows
  * mean gain per completed lock     +$0.1252 to +$0.3134 depending on the
                                     first-leg population
  * mean loss per failed first leg   -$0.3186 to -$0.7610, same populations
"""
from __future__ import annotations

import json
from decimal import Decimal as D
from typing import Any, Dict, Optional

TARGET_PER_DAY = D("480")
TARGET_PER_WINDOW = D("5")
BTC5M_WINDOWS_PER_DAY = 288
SIZES = (1, 5, 10, 25, 50, 100)


def dollars_per_day(opportunities_per_day: Optional[float],
                    fill_probability: Optional[float],
                    net_ev_per_fill: Optional[D],
                    size: int) -> Optional[D]:
    if None in (opportunities_per_day, fill_probability) \
            or net_ev_per_fill is None:
        return None
    return (D(str(opportunities_per_day)) * D(str(fill_probability))
            * net_ev_per_fill * D(size))


def required_edge(opportunities_per_day: float, fill_probability: float,
                  size: int, target: D = TARGET_PER_DAY) -> Optional[D]:
    """What net EV per filled contract would $480/day demand?"""
    den = D(str(opportunities_per_day)) * D(str(fill_probability)) * D(size)
    if den <= 0:
        return None
    return (target / den).quantize(D("0.000001"))


def price(name: str, *, opportunities_per_day: Optional[float],
          fill_probability: Optional[float],
          net_ev_per_fill: Optional[D],
          capacity_by_size: Optional[Dict[int, bool]] = None,
          evidence: str = "", unmeasured: Optional[list] = None
          ) -> Dict[str, Any]:
    rows = {}
    for s in SIZES:
        ok = True if capacity_by_size is None else capacity_by_size.get(s)
        d = dollars_per_day(opportunities_per_day, fill_probability,
                            net_ev_per_fill, s)
        rows["q%d" % s] = {
            "capacity_supported": ok,
            "dollars_per_day": (str(d.quantize(D("0.01")))
                                if d is not None else None),
            "meets_480": (bool(d >= TARGET_PER_DAY)
                          if (d is not None and ok) else None),
            "required_net_ev_per_fill_for_480": (
                str(required_edge(opportunities_per_day, fill_probability, s))
                if (opportunities_per_day and fill_probability) else None),
        }
    return {"hypothesis": name, "evidence": evidence,
            "opportunities_per_day": opportunities_per_day,
            "causal_fill_probability": fill_probability,
            "net_ev_per_fill": (str(net_ev_per_fill)
                                if net_ev_per_fill is not None else None),
            "UNMEASURED_INPUTS": unmeasured or [],
            "by_size": rows}


def backward_from_target() -> Dict[str, Any]:
    """What would have to be true, at Gemini BTC5m's actual cadence?"""
    out = {}
    for opp_frac, label in ((1.0, "every_window"), (0.459, "underdog_reach"),
                            (0.10, "one_in_ten_windows")):
        opp = BTC5M_WINDOWS_PER_DAY * opp_frac
        out[label] = {"opportunities_per_day": round(opp, 1)}
        for fp in (1.0, 0.5, 0.25):
            out[label]["fill_%d%%" % int(fp * 100)] = {
                "q%d" % s: str(required_edge(opp, fp, s)) for s in SIZES}
    return out


if __name__ == "__main__":
    print(json.dumps({
        "target_per_day": str(TARGET_PER_DAY),
        "target_per_window": str(TARGET_PER_WINDOW),
        "btc5m_windows_per_day": BTC5M_WINDOWS_PER_DAY,
        "required_net_ev_per_fill": backward_from_target(),
        "note": ("per-fill edge required to reach $480/day. Compare against "
                 "the measured mean gain per completed lock, +$0.1252 to "
                 "+$0.3134, and remember the SAME strategy loses $0.3186 to "
                 "$0.7610 on each first leg that never completes."),
    }, indent=2))
