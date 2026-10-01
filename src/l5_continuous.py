"""V1-V4 shadow variants. REPLAY/SHADOW ONLY -- no order path exists here.

`src/l5_two_sided.py` is the FROZEN benchmark (`49799e99`) and is not touched.
Each variant is a subclass whose entire difference is a visible override, so
attribution is structural rather than a matter of trust:

    TwoSided (frozen)
      +-- V1ExtendOnly            allow_extend forced True
            +-- V2NoBasisGate     the basis-improvement gate removed
                  +-- V3Continuous  explicit post-fill replenishment of the
                  |                 filled side AT ITS PREVIOUS LEVEL, with the
                  |                 untouched opposite quote preserved
                  +-- V4Readjust    same, but the replenishment price is
                                    RE-CHOSEN from the current book

WHAT NO VARIANT DOES: cancel a valid unfilled quote to chase a better touch.
That is the HOLD_OPEN_EXTEND finding and it is preserved everywhere. The V3/V4
contrast is only about the price of the REPLACEMENT quote on the side that just
filled -- which is the one thing L5's fill data can speak to, since a fill
removes his quote and forces a replenishment, whereas cancellation of an
unfilled order is not observable at all.

clip and residual_cap are NOT changed by any variant. If the cap binds, that is
reported, not engineered away.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Optional

from src.l5_two_sided import ARM_REPLICATION, Leg, TwoSided

V1 = "V1_EXTEND_ONLY"
V2 = "V2_EXTEND_NO_BASIS_GATE"
V3 = "V3_CONTINUOUS_TWO_SIDED"
V4 = "V4_CONTINUOUS_PLUS_READJUST"
VARIANTS = (V1, V2, V3, V4)


class V1ExtendOnly(TwoSided):
    """V1: heavy-side acquisition is permitted. Nothing else changes."""

    variant = V1

    def __init__(self, **kw) -> None:
        kw["allow_extend"] = True                 # the ONLY difference
        kw.setdefault("arm", ARM_REPLICATION)
        super().__init__(**kw)


class V2NoBasisGate(V1ExtendOnly):
    """V2: V1 without the basis-improvement requirement on an extend.

    L5 worsens his own same-side basis on 37.44% of his extends, so the gate
    cannot describe him. The residual cap and every other gate remain.
    """

    variant = V2

    #: consulted by `desired()` only through this flag, so the override is
    #: visible and nothing else in the frozen logic moves.
    require_basis_improvement = False

    def desired(self, side: str) -> Optional[D]:
        defic = self.deficient()
        if defic is not None and side != defic and self.qty[side] > 0:
            # Re-implement ONLY the heavy-side branch, minus the basis gate.
            own = self.own_touch(side)
            if own is None:
                return None
            if abs(self.residual()) + self.clip > self.residual_cap:
                self._block("extend_breaches_residual_cap")
                return None
            return own
        return super().desired(side)


class V3Continuous(V2NoBasisGate):
    """V3: the filled side is replenished AT THE LEVEL IT HELD.

    WHERE THE HOOK LIVES, and why it matters. The frozen `on_trade` nulls a
    fully-filled leg AFTER calling `_book_fill`, then calls `reconcile_quotes`.
    So a replenishment written inside `_book_fill` is destroyed, and the price
    ends up re-derived -- which made V3 and V4 behaviourally identical in the
    first implementation. An attribution test caught it. The hook therefore
    lives in `reconcile_quotes`, where it survives.

    A FINDING THIS EXPOSED: the frozen engine ALREADY replenishes both sides
    immediately at a RE-CHOSEN price, because `reconcile_quotes` runs at the end
    of every `on_trade`. So "continuous two-sided replenishment with readjust" is
    not a new mechanism at all -- it is what V1/V2 already do. The genuinely new
    mechanism is V3's LEVEL PERSISTENCE: replenishing at the price the filled leg
    held, which preserves the level rather than chasing the current touch.
    """

    variant = V3
    readjust_on_replenish = False

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.replenishes = 0
        self._filled_px: dict = {}

    def _replenish_price(self, side: str) -> Optional[D]:
        """V3 keeps the level the filled leg held."""
        return self._filled_px.get(side)

    def _book_fill(self, side, qty, px, *, maker, evidence) -> None:
        super()._book_fill(side, qty, px, maker=maker, evidence=evidence)
        self._filled_px[side] = D(str(px))

    def reconcile_quotes(self) -> None:
        """Replenish a side that just filled at its own policy price, leaving any
        untouched valid leg -- and its queue position -- strictly alone."""
        for side in ("up", "down"):
            if self.legs[side] is not None or side not in self._filled_px:
                continue
            want = self._replenish_price(side)
            if want is None:
                continue
            if self.desired(side) is None:
                self._block("replenish_blocked_by_gate")
                self._filled_px.pop(side, None)
                continue
            shown = self.displayed_at(side, want)
            if shown is None:
                self._block("replenish_queue_position_unknowable")
                continue
            self.legs[side] = Leg(side, want, self.clip, shown, self.now,
                                  maker=True, cycle=self.cycle)
            self.places += 1
            self.replenishes += 1
            self.ahead_at_placement.append(shown)
            self._filled_px.pop(side, None)
        super().reconcile_quotes()

    def report(self, *, winner: Optional[str] = None) -> dict:
        out = super().report(winner=winner)
        out["variant"] = self.variant
        out["replenishes"] = self.replenishes
        out["readjust_on_replenish"] = self.readjust_on_replenish
        return out


class V4Readjust(V3Continuous):
    """V4: the replenishment price is RE-CHOSEN from the current book.

    Evidence: 91.02% of L5's per-outcome price levels yield exactly one fill
    before the price moves, median level life 5s, and 26.75% of level changes
    return to a previously used level. A fill removes the quote, so choosing the
    replacement price is a decision his data CAN speak to. Cancelling an
    unfilled quote is not, and V4 still never does it.
    """

    variant = V4
    readjust_on_replenish = True

    def _replenish_price(self, side: str) -> Optional[D]:
        """Re-choose from the current book. NOTE: this reproduces what the frozen
        engine's own `reconcile_quotes` already does, so V4 is expected to be
        BEHAVIOURALLY EQUIVALENT to V2 -- asserted by test, and reported rather
        than disguised as a distinct mechanism."""
        return self.desired(side)


BY_NAME = {V1: V1ExtendOnly, V2: V2NoBasisGate,
           V3: V3Continuous, V4: V4Readjust}


def build(name: str, *, clip: D = D(1), **kw) -> TwoSided:
    """`kw` exists so an attribution TEST can hold a frozen knob (e.g. the
    reactive take) equal across variants. The comparison runner passes none of
    it, so every variant runs on the frozen defaults."""
    if name not in BY_NAME:
        raise ValueError(f"unknown variant {name!r}")
    return BY_NAME[name](clip=clip, **kw)
