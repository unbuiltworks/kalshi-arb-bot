"""SHADOW ONLY: what the residual cap is actually buying, cap by cap.

OWNER GO (this cycle): "Replay CONTROL = 6 units, SHADOW 8 / 10 / 12, plus
an UNBOUNDED arm for DIAGNOSTIC PURPOSES ONLY... the shadow inventory must
evolve independently for each arm. Every subsequent risk/economic decision
must use that arm's own hypothetical inventory, not production inventory."

That last sentence is the whole design. A cap arm is not a filter over
production's decisions; it is a separate inventory history. Two consequences
that a filter would miss, and that this module models:

  * a counterfactual EXTEND that fills MOVES the arm's residual, so the
    arm's own cap adjudicates every later decision;
  * and a bigger cap is not monotonically "more extends". An arm that
    spends its headroom early can REFUSE an extend production actually
    filled, because by then the ARM's inventory is over ITS cap. Arms can
    therefore have FEWER fills than production, not only more.

Live cap stays 6. UNBOUNDED exists to measure the full suppressed action
mass and is not a candidate for anything.

ACCOUNTING. Terminal P&L is decomposed the way the owner reports it:

    total = matched-pair P&L + directional residual P&L
    matched m   = min(up, dn),  pays exactly $1 per pair
    residual r  = |up - dn| on the surplus side

and the arm's delta is always quoted against the SAME market's confirmed
venue realized number, never against another arm.

The critical structural fact this exposes: extra inventory on the side
already held CANNOT become matched unless the opposite side later exceeds
the ORIGINAL held quantity, because matched = min(up, dn). Adding to the
larger side moves nothing but residual. That is arithmetic, not a modelling
choice, and it is what P3 is really asking about.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Any, Mapping, Optional

from src import l5_band_semantics as bs

CONTROL_CAP = 6                      # live R5FT rung; unchanged by anything here
CAP_ARMS = (6, 8, 10, 12)
UNBOUNDED = 10 ** 9                  # diagnostic only, never a live candidate
BAND_MODES = ("current", "entry_only")
FILL_RULES = ("TAPE_SUPPORTED", "FILL_ASSUMED")

# P3 outcome classes for each extra EXTEND the arm takes
CLASS_A = "A_later_opposite_fill_created_matched_inventory"
CLASS_B_THEN_C = "B_opposite_opportunity_appeared_no_supported_fill_then_C"
CLASS_C = "C_remained_directional_through_settlement"
CLASS_D = "D_evidence_ends_before_outcome"


@dataclass
class ArmInventory:
    """One arm's own position. Never reads production inventory again once
    it has diverged."""
    up: D = D("0")
    dn: D = D("0")
    cost_up: D = D("0")
    cost_dn: D = D("0")
    unit: Optional[D] = None
    peak_residual_units: D = D("0")
    peak_residual_usd: D = D("0")
    peak_unmatched_usd: D = D("0")
    fills: list = field(default_factory=list)

    @property
    def spent(self) -> D:
        return self.cost_up + self.cost_dn

    @property
    def residual(self) -> D:
        return self.up - self.dn

    @property
    def matched(self) -> D:
        return min(self.up, self.dn)

    @property
    def surplus_side(self) -> Optional[str]:
        if self.up > self.dn:
            return "up"
        if self.dn > self.up:
            return "dn"
        return None

    def avg(self, side: str) -> D:
        q = self.up if side == "up" else self.dn
        c = self.cost_up if side == "up" else self.cost_dn
        return (c / q) if q > 0 else D("0")

    def residual_units(self) -> D:
        if not self.unit or self.unit == 0:
            return D("0")
        return abs(self.residual) / self.unit

    def unmatched_usd(self) -> D:
        s = self.surplus_side
        return abs(self.residual) * self.avg(s) if s else D("0")

    def add(self, side: str, qty: D, px: D, *, label: str = "",
            t_utc: str = "") -> None:
        if self.unit is None and qty > 0:
            self.unit = qty
        if side == "up":
            self.up += qty
            self.cost_up += qty * px
        else:
            self.dn += qty
            self.cost_dn += qty * px
        self.fills.append({"side": side, "qty": str(qty), "px": str(px),
                           "label": label, "t_utc": t_utc})
        self.peak_residual_units = max(self.peak_residual_units,
                                       self.residual_units())
        self.peak_residual_usd = max(self.peak_residual_usd,
                                     abs(self.residual))
        self.peak_unmatched_usd = max(self.peak_unmatched_usd,
                                      self.unmatched_usd())

    def cap_allows_extend(self, add_qty: D, cap: int) -> bool:
        """Production's own rule, on THIS arm's inventory, EXACTLY.

        The live shell reads

            relation == "extend" and abs(inv.behavioral_residual()) >= cap

        -- the residual BEFORE the extend, with `>=`. So production permits
        an extend that OVERSHOOTS the cap, and only refuses the next one.
        An earlier version here tested the position the extend would create
        with `<`, which is a tighter rule: under it the CONTROL arm refused
        a production extend that really happened, which is a self-
        consistency failure, not a finding. `add_qty` is accepted and
        deliberately unused so callers read the same way as the live code.
        """
        del add_qty                      # production does not look at it
        if not self.unit or self.unit == 0:
            return True
        return abs(self.residual / self.unit) < cap


def decompose_pnl(inv: ArmInventory, winner: Optional[str]) -> Optional[dict]:
    """matched-pair P&L and directional residual P&L, and their sum checked
    against payout - spend. None when the venue outcome is unknown -- an
    unsettled market contributes nothing and is never counted as zero."""
    if winner is None:
        return None
    m = inv.matched
    matched_pnl = m * (D("1") - (inv.avg("up") + inv.avg("dn")))
    s = inv.surplus_side
    r = abs(inv.residual)
    if s is None:
        residual_pnl = D("0")
    else:
        residual_pnl = (r * (D("1") - inv.avg(s)) if s == winner
                        else -r * inv.avg(s))
    payout = inv.up if winner == "up" else inv.dn
    total = payout - inv.spent
    return {"matched_pairs": str(m),
            "matched_pair_pnl": str(matched_pnl.quantize(D("0.0001"))),
            "residual_qty": str(r), "residual_side": s,
            "directional_residual_pnl": str(residual_pnl.quantize(D("0.0001"))),
            "total_pnl": str(total.quantize(D("0.0001"))),
            "decomposition_closes": abs(
                (matched_pnl + residual_pnl) - total) <= D("0.0001")}


def quantiles(xs: list, ps=(0.5, 0.75, 0.9)) -> dict:
    """Plain order statistics, no interpolation -- with samples this small
    an interpolated p90 invents a value no market produced."""
    if not xs:
        return {f"p{int(p * 100)}": None for p in ps} | {"max": None, "n": 0}
    s = sorted(xs)
    out = {f"p{int(p * 100)}": str(s[min(int(p * len(s)), len(s) - 1)])
           for p in ps}
    out["max"] = str(s[-1])
    out["n"] = len(s)
    return out
