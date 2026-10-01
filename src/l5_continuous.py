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
    """V1: heavy-side acquisition is permitted. Nothing else changes.

    Also carries the RESIDUAL-CAP OBSERVER shared by every variant. The cap is
    NOT altered -- it is instrumented, because the preregistration predicts it
    becomes the binding constraint once extension is allowed, and that
    prediction has to be measurable rather than argued. Every counter below is
    written AFTER decisions are made and is read by no rule, so it cannot change
    behaviour; a test pins that.
    """

    variant = V1

    def __init__(self, **kw) -> None:
        kw["allow_extend"] = True                 # the ONLY difference
        kw.setdefault("arm", ARM_REPLICATION)
        super().__init__(**kw)
        self.cap = {
            "acquisitions_attempted": 0,
            "blocked_by_cap": 0,
            "blocked_up": 0, "blocked_down": 0,
            "seconds_cap_bound": 0.0,
            "entries_into_cap": 0, "exits_from_cap": 0,
            "residual_at_first_bind": None,
            "matched_before_first_bind": None,
            "matched_after_first_bind": None,
            "touch_available_while_cap_bound": 0,
            "reopened_by_opposite_fill": 0,
        }
        self._cap_bound = False
        self._cap_last_t = None

    # ------------------------------------------------ decision-neutral probe
    def _cap_would_block(self, side: str) -> bool:
        """Pure predicate, mirroring the frozen gate. Reads nothing new."""
        defic = self.deficient()
        if defic is None or side == defic or self.qty[side] <= 0:
            return False
        return (abs(self.residual()) + self.clip) > self.residual_cap

    def _cap_probe(self) -> None:
        c = self.cap
        bound_sides = [s for s in ("up", "down") if self._cap_would_block(s)]
        bound = bool(bound_sides)
        if self._cap_last_t is not None and self._cap_bound:
            c["seconds_cap_bound"] += max(0.0, self.now - self._cap_last_t)
        self._cap_last_t = self.now
        # DENOMINATOR FIX: an attempt is every heavy-side opportunity, blocked
        # or not. Previously this was incremented only inside the blocked
        # branch, which made blocked_fraction vacuously 1.0.
        defic = self.deficient()
        for s in ("up", "down"):
            if defic is not None and s != defic and self.qty[s] > 0:
                c["acquisitions_attempted"] += 1
        for s in bound_sides:
            c["blocked_by_cap"] += 1
            c[f"blocked_{s}"] += 1
            if self.own_touch(s) is not None:
                # an upper bound on missed chances: a quotable touch existed
                # while the cap forbade acquiring there.
                c["touch_available_while_cap_bound"] += 1
        if bound and not self._cap_bound:
            c["entries_into_cap"] += 1
            if c["residual_at_first_bind"] is None:
                c["residual_at_first_bind"] = str(self.residual())
                c["matched_before_first_bind"] = str(self.matched())
        elif self._cap_bound and not bound:
            c["exits_from_cap"] += 1
            if abs(self.residual()) < self.residual_cap:
                # the opposite side filling is what re-opens acquisition
                c["reopened_by_opposite_fill"] += 1
        self._cap_bound = bound

    def reconcile_quotes(self) -> None:
        super().reconcile_quotes()
        self._cap_probe()            # AFTER decisions: cannot influence them

    def report(self, *, winner: Optional[str] = None) -> dict:
        out = super().report(winner=winner)
        c = dict(self.cap)
        if c["matched_before_first_bind"] is not None:
            c["matched_after_first_bind"] = str(
                self.matched() - D(c["matched_before_first_bind"]))
        att = c["acquisitions_attempted"]
        c["blocked_fraction"] = (c["blocked_by_cap"] / att) if att else None
        out["residual_cap_observer"] = c
        out["variant"] = self.variant
        return out


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

    def _replenish_price(self, side: str, allowed: Optional[D]) -> Optional[D]:
        """V3 keeps the level the filled leg held. `allowed` is the price the
        frozen `desired()` would pick, passed in so it is computed ONCE -- a
        second call would double-count the diagnostic block counters."""
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
            # PROBE, not a decision: deciding whether to replenish requires
            # asking what the frozen gate would allow, but that question must
            # not inflate the diagnostic block counters -- otherwise V3/V4
            # report blocks that V2 never recorded for the same behaviour. The
            # counters are snapshotted and restored around the probe.
            _blocked_snapshot = dict(self.blocked)
            allowed = self.desired(side)          # computed ONCE
            self.blocked = _blocked_snapshot
            want = self._replenish_price(side, allowed)
            if want is None:
                continue
            if allowed is None:
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

    def _replenish_price(self, side: str, allowed: Optional[D]) -> Optional[D]:
        """Re-choose from the current book. NOTE: this reproduces what the frozen
        engine's own `reconcile_quotes` already does, so V4 is expected to be
        BEHAVIOURALLY EQUIVALENT to V2 -- asserted by test, and reported rather
        than disguised as a distinct mechanism."""
        return allowed


BY_NAME = {V1: V1ExtendOnly, V2: V2NoBasisGate,
           V3: V3Continuous, V4: V4Readjust}


def build(name: str, *, clip: D = D(1), **kw) -> TwoSided:
    """`kw` exists so an attribution TEST can hold a frozen knob (e.g. the
    reactive take) equal across variants. The comparison runner passes none of
    it, so every variant runs on the frozen defaults."""
    if name not in BY_NAME:
        raise ValueError(f"unknown variant {name!r}")
    return BY_NAME[name](clip=clip, **kw)
