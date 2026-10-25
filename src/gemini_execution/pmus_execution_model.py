"""PMUS fees and PMUS fill probability for the inventory engine.

THIS MODULE EXISTS BECAUSE THE FIRST VERSION OF THE ENGINE WAS WRONG ON ALL
THREE. It was built against `analysis/profit_search/replay_b.py`, whose
constants are Kalshi's, and it invented a fill probability from displayed
depth. On PMUS, each of those is a material error, and they do not cancel:

  1. NO PER-ORDER FEE. replay_b charges PER_ORDER = $0.01 on every order.
     The published PMUS schedule (docs.polymarket.us/fees, reproduced row for
     row in `src/pmus_fees.py`) has no such charge. Carrying it taxed every
     decision and produced the "~$293/day in fees at L5's cadence" figure,
     which is WITHDRAWN -- it was an artefact of a fee PMUS does not levy.

  2. THE MAKER REBATE IS A CREDIT, NOT A NEGATIVE FEE. `pmus_fees` says so
     explicitly and never subtracts it from a cost. The engine was netting
     theta = -0.0125 into its cost basis, which flatters every maker
     candidate's EV with income the venue has not credited. Strategy
     selection here EXCLUDES it; `maker_rebate_accrual` reports it separately
     so it can be reported but never spent.

  3. ROUNDING IS BANKER'S, NOT CEIL. replay_b rounds fees up to the cent.
     PMUS rounds half-even at 1e-4, which is what reproduces all 99 published
     rows.

  4. P(FILL) IS CALIBRATED, NOT MODELLED -- and it REFUSES past its horizon.
     The engine's own `maker_fill_prob` returned ~0.05 for a clip at the touch.
     The measured figure from 965 real submitted orders, with cancels treated
     as CENSORED rather than as permanent no-fills, is **0.43 at 30 s** --
     roughly nine times higher, and biased in the direction that suppresses
     maker trading. A shadow carrying that error cannot be trusted about
     fills, which is precisely what the live campaign demonstrated.

     Past 32.2 s there is NO DATA -- not sparse data, none. This provider
     returns None there, and the engine turns None into WAIT rather than an
     extrapolated number. L5's median complement gap is 47 s, beyond our
     entire observed range, so the horizon that matters for his behaviour is
     one only a live run that rests longer can measure.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal as D
from typing import Any, Mapping, Optional, Sequence

from src import pmus_fees

LABEL = "PMUS_EXECUTION_MODEL_V1"

#: the calibrated report committed at analysis/l5/pmus_fill_model.json
_DEFAULT_CALIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "..", "analysis", "l5", "pmus_fill_model.json")

FIRST_LEG = "first_leg"
COMPLEMENT = "complement"


# ---------------------------------------------------------------------- fees
def taker_fee(qty: D, wire_px: D, when: Optional[dt.datetime] = None) -> D:
    """PMUS taker fee in dollars. No per-order component."""
    if qty <= 0 or wire_px <= 0 or wire_px >= 1:
        return D(0)
    return pmus_fees.taker_fee(wire_px, qty, when=when)


def maker_fee(qty: D, wire_px: D, when: Optional[dt.datetime] = None) -> D:
    """PMUS maker fee for STRATEGY SELECTION: zero.

    A maker order pays nothing and ACCRUES a rebate. The accrual is income
    only once the venue credits it, so it is not netted into cost here. Use
    `maker_rebate_accrual` to report it.
    """
    return D(0)


def maker_rebate_accrual(qty: D, wire_px: D,
                         when: Optional[dt.datetime] = None) -> D:
    """The rebate a maker fill accrues, as a POSITIVE credit, reported never
    netted."""
    if qty <= 0 or wire_px <= 0 or wire_px >= 1:
        return D(0)
    return pmus_fees.maker_rebate(wire_px, qty, when=when)


# ------------------------------------------------------------- P(fill by t)
class CalibratedFillModel:
    """P(fill by t) from our own real outcomes, refusing past its horizon."""

    def __init__(self, report: Mapping[str, Any]):
        self.horizon_s = float(report.get("estimable_horizon_s") or 0.0)
        self.legs = report.get("legs") or {}
        self.n_orders = sum(int((self.legs.get(k) or {}).get("n_orders") or 0)
                            for k in self.legs)
        self.source = report.get("source") or "analysis/l5/pmus_fill_model.json"

    @classmethod
    def load(cls, path: str = _DEFAULT_CALIB) -> "CalibratedFillModel":
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def _curve(self, leg: str) -> Sequence[Mapping[str, float]]:
        g = self.legs.get(leg) or {}
        return g.get("curve") or []

    def p_fill(self, *, leg: str, rest_s: float) -> Optional[float]:
        """P(this order fills within `rest_s` seconds of resting.

        None means UNESTIMABLE -- beyond the longest rest we have ever
        observed. The engine must treat None as a refusal, not as zero and not
        as the last observed value.
        """
        if rest_s <= 0:
            return 0.0
        if rest_s > self.horizon_s:
            return None
        curve = self._curve(leg)
        best = 0.0
        for row in curve:
            t = float(row.get("t") or row.get("t_s") or 0.0)
            if t <= rest_s:
                p = row.get("p_fill_by_t")
                if p is not None:
                    best = float(p)
            else:
                break
        if not curve:
            g = self.legs.get(leg) or {}
            pf = (g.get("p_fill") or {}).get("30")
            if pf is None:
                return None
            # scale the single calibrated point linearly inside the horizon;
            # conservative, and never applied past it.
            return float(pf) * min(1.0, rest_s / 30.0)
        return best

    def leg_for(self, *, resid_before: D, side: str) -> str:
        """A buy that reduces signed exposure is a COMPLEMENT; one that opens
        or extends is a FIRST LEG. Those are the two relations the model was
        calibrated on."""
        if resid_before == 0:
            return FIRST_LEG
        reduces = (resid_before > 0 and side == "DOWN") or \
                  (resid_before < 0 and side == "UP")
        return COMPLEMENT if reduces else FIRST_LEG

    # --- the venue's fee schedule, so one object answers both questions the
    # engine asks of a venue: what does this cost, and will it fill.
    def taker_fee(self, qty: D, wire_px: D,
                  when: Optional[dt.datetime] = None) -> D:
        return taker_fee(qty, wire_px, when)

    def maker_fee(self, qty: D, wire_px: D,
                  when: Optional[dt.datetime] = None) -> D:
        return maker_fee(qty, wire_px, when)

    def maker_rebate_accrual(self, qty: D, wire_px: D,
                             when: Optional[dt.datetime] = None) -> D:
        return maker_rebate_accrual(qty, wire_px, when)

    def describe(self) -> dict:
        return {"label": LABEL, "source": self.source,
                "estimable_horizon_s": self.horizon_s,
                "orders_calibrated_on": self.n_orders,
                "p_fill_30s": {k: (self.legs.get(k) or {}).get("p_fill", {}).get("30")
                               for k in self.legs},
                "note": "cancels CENSORED, not counted as permanent no-fills; "
                        "None past the horizon rather than an extrapolation"}


def pmus_config(base=None, **kw):
    """A Config wired to PMUS's published fees and our own measured fills.

    `maker_rest_s` and `order_ttl_s` are capped to the calibrated horizon, so
    no decision is ever priced on a rest time we have never actually observed.
    """
    from src.gemini_execution.inventory_engine import Config
    model = CalibratedFillModel.load()
    fields = dict(venue=model,
                  maker_rest_s=min(30.0, model.horizon_s),
                  order_ttl_s=min(30.0, model.horizon_s))
    if base is not None:
        from dataclasses import asdict
        merged = {k: v for k, v in asdict(base).items() if k != "venue"}
        merged.update(fields)
        merged.update(kw)
        merged["venue"] = model
        return Config(**merged)
    fields.update(kw)
    return Config(**fields)
