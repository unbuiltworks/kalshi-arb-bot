"""SHADOW-ONLY two-sided maker completion evaluator. It never submits.

WHAT THIS ADDS THAT `l5_maker_fill_model` DOES NOT HAVE. That module's own
docstring is explicit: it CLASSIFIES the intents the executor already
produced, and "does NOT generate new decisions from modelled inventory". So
it can say whether an opposite-side intent happened to arrive, never whether
a complement WE chose to rest would have completed. The owner-approved
question is the second one, so this module GENERATES the quotes -- a matched
UP/DOWN pair at the touch -- and asks the existing model what would have
happened to them.

IT TOUCHES NOTHING ON THE MONEY PATH. No store, no transport, no limit, no
gate. It reads a book snapshot and the authenticated trade prints the runner
already holds, and it returns a dict. Every number it produces is MODELLED:
hypothetical fills, hypothetical inventory, hypothetical settlement. None of
it is realized, submitted, or permitted to influence a real decision.

THE QUEUE DISCIPLINE IS INHERITED, not reinvented. `HypoOrder` freezes
queue-ahead at placement and never re-reads it, advances only on observed
trade volume in the direction that would hit us, and never treats a touch or
a cancellation ahead of us as progress. Partial fills are read off that same
`cleared` counter rather than modelled separately:

    filled_qty(conservative) = clamp(cleared - queue_ahead, 0, qty)

so a quote that is half-consumed is half-filled and not rounded either way.
Exact queue POSITION is never inferred: the conservative band puts the whole
displayed top-of-book size ahead of us and the optimistic band puts none of
it there, and both are reported.

GEOMETRY. The venue quotes one number per market, the UP/YES price. A maker
buy of UP rests at the bid at own cost `bid`; a maker buy of DOWN is a sell
of YES resting at the ask at own cost `1 - ask`. Both legs filled therefore
cost `bid + (1 - ask) = 1 - spread` and the pair redeems exactly $1.00.

THE GATE IS PROSPECTIVE ON THE MISSING LEG:
    flat           -> quote both sides iff  1 - spread <= threshold
    holding a side -> quote ONLY the complement, and only while
                      basis(held) + complement touch <= threshold

CAPITAL IS DEPLOYED-AND-COMMITTED, never an acceptable loss. An envelope
bounds, at every instant, modelled filled cost plus the full notional of
every resting modelled order on both sides.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING
from collections.abc import Mapping as _ABCMapping
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import l5_defensive_completion as DEF
from src import l5_maker_fill_model as FM
from src.l5_two_sided_probe import TAKER_THETA

TICK = D("0.01")

#: the venue taker fee per contract, as the live fee_floor record reported
#: it on the 2300z plan. Used ONLY to price a simulated defensive exit.
SIM_TAKER_FEE = D("0.026986850000")

#: the venue's completion cutoff, in seconds from window open.
SIM_COMPLETE_CUTOFF_S = 720.0

#: THE CLOCK THAT ACTUALLY BINDS, and the correction to a shadow that was
#: measuring a window the money path can never act in. `may_complete`
#: refuses on the EARLIER of `complete_cutoff_s()` (720) and
#: `cancel_cutoff(t_end)`, which at the deployed `L5_TS_CANCEL_MARGIN_S=300`
#: is 600 -- verified by running `may_complete` at the production margin,
#: and corroborated by 2315z logging PAST_TERMINAL 195 times. A guard armed
#: on 720 sat entirely inside dead time, so every simulated verdict it
#: produced described an action the engine could not have taken.
SIM_CANCEL_CUTOFF_S = 600.0

#: the single crossing leg's taker fee is priced per leg in `_defensive_eval`
#: (theta * p * (1-p)); `SIM_TAKER_FEE` above is the BOTH-LEGS pair figure
#: and overstates an unwind, whose first leg already filled as a maker.

#: the two quoting policies compared in shadow. COMPLEMENT_ONLY is what the
#: engine does today: while inventory is non-zero the adding leg is refused
#: outright. SKEWED replaces that cliff with a bounded taper and keeps a
#: resting leg that is still valid instead of cancelling it when the touch
#: moves. The taper's SHAPE is taken from L5's measured fills (his adding-side
#: clip falls and his reducing-side clip rises as inventory grows, ratio 0.971
#: -> 0.713 over 439,727 fills); the exact coefficients are OURS and are a
#: descriptive comparison, NOT a fitted profitability guarantee and NOT a claim
#: that L5 uses this formula.
COMPLEMENT_ONLY = "COMPLEMENT_ONLY"
SKEWED = "SKEWED"
#: THE POLICY THE MONEY PATH ACTUALLY RUNS, which neither of the two above
#: models. The live two-sided engine differs on three axes at once, and all
#: three had to be modelled together or the cell measures nothing we run:
#:
#:   1. IT RESTS BEHIND THE TOUCH. `depth_ticks` (live: 2) is subtracted from
#:      each leg's own price, so the pair costs 2*depth_ticks ticks LESS than
#:      the touch implies. At the 0.96 ceiling this is the whole discrepancy:
#:      2300z showed touch UP 0.26 / DOWN 0.73 -- a touch pair of 0.99, which
#:      the at-touch cell refused -- while the engine placed 0.24/0.71 for a
#:      pair of 0.95 and completed it. The at-touch cell reported 0 eligible
#:      ticks in both windows the live engine actually earned money in.
#:   2. ONE OUTSTANDING ORDER. It never quotes both legs at once; it places
#:      one, waits for a terminal state, then places the complement.
#:   3. THE COMPLEMENT LIMIT IS FROZEN at plan time and is NOT re-priced or
#:      withdrawn when the touch moves away. In 2300z that is precisely what
#:      earned the edge: the complement sat at 0.71 for 3m42s while the book
#:      quoted 0.74-0.79, refused to chase, and filled when the book returned.
LIVE_SEQUENTIAL = "LIVE_SEQUENTIAL"

#: SKEWED's policy version. v1 retained every resting leg UNCONDITIONALLY --
#: no distance test, no re-check of the pair economics after placement -- which
#: the 0215z audit showed is not the hypothesis at all: poly-maker's reconciler
#: keeps an order only while it still matches the CURRENT target. v2 revalidates
#: every retained leg each tick and cancels the ones that no longer qualify.
#: The tag travels on every cell so a pre-fix sample can never be averaged with
#: a post-fix one.
SKEW_VERSION = "v2-revalidated"
SKEW_VERSION_V1 = "v1-unconditional-retention"

#: bounds on the taper. The adding leg never vanishes and never grows; the
#: reducing leg never more than doubles. Both are clamps, not parameters to
#: tune, and they exist so one window cannot produce an unbounded clip.
SKEW_ADD_FLOOR = D("0.25")
SKEW_REDUCE_CEIL = D("2")

#: frozen sensitivity scenarios. NOT fitted and never chosen by outcome.
THRESHOLDS = (D("0.96"), D("0.97"), D("0.98"), D("0.99"))
ENVELOPES = (D("100"), D("200"), D("300"), D("500"))

#: the live production envelope, carried alongside so the shadow always
#: reports what the deployed cap would have done on the same tape.
LIVE_ENVELOPE = D("1")


def _d(v: Any) -> Optional[D]:
    return FM._d(v)


class Leg:
    """One modelled resting leg, with its own frozen queue and partials."""

    __slots__ = ("order", "side", "own_px", "book_px", "qty", "filled_cons",
                 "filled_opt", "placed_t")

    def __init__(self, slug: str, side: str, own_px: D, book_px: D, qty: D,
                 now: float, bid: Optional[D], ask: Optional[D],
                 displayed: Optional[D]):
        self.side, self.own_px, self.book_px, self.qty = side, own_px, book_px, qty
        self.placed_t = now
        self.order = FM.HypoOrder(slug, side, own_px, qty, now, bid=bid, ask=ask)
        # top-of-book depth is the queue we would join when quoting AT the
        # touch. The ladder is not needed for that and is not guessed at.
        self.order.queue_ahead = displayed
        self.order.level_depth = displayed
        self.filled_cons = D(0)
        self.filled_opt = D(0)

    def advance(self, prints: Sequence[Mapping[str, Any]]) -> Dict[str, D]:
        before_c, before_o = self.filled_cons, self.filled_opt
        self.order.advance(prints)
        q = self.order.queue_ahead
        cleared = self.order.cleared
        if q is None:
            cons = D(0)                      # unknown queue NEVER fills
        else:
            cons = max(D(0), min(self.qty, cleared - q))
        self.filled_cons = max(self.filled_cons, cons)
        self.filled_opt = max(self.filled_opt, min(self.qty, cleared))
        return {"cons": self.filled_cons - before_c,
                "opt": self.filled_opt - before_o}

    @property
    def left_cons(self) -> D:
        return self.qty - self.filled_cons


class Config:
    """One (threshold, envelope) cell. Conservative band drives the ledger."""

    def __init__(self, slug: str, thresh: D, envelope: D,
                 policy: str = COMPLEMENT_ONLY, depth_ticks: int = 0):
        self.slug, self.thresh, self.envelope = slug, thresh, envelope
        self.policy = policy
        #: ticks BEHIND the touch each leg rests. 0 reproduces the at-touch
        #: pricing every existing cell was built with, byte for byte.
        self.depth_ticks = int(depth_ticks)
        self.depth = D(self.depth_ticks) * TICK
        #: the complement limit frozen when the first leg was placed. Only
        #: LIVE_SEQUENTIAL reads it; None means "not holding a plan".
        self.frozen_cpx: Optional[D] = None
        self.retained_legs = 0
        self.replaced_legs = 0
        self.revalidation_cancels: Dict[str, int] = {}
        self.qty = {FM.UP: D(0), FM.DOWN: D(0)}
        self.cost = {FM.UP: D(0), FM.DOWN: D(0)}
        self.qty_opt = {FM.UP: D(0), FM.DOWN: D(0)}
        #: when the modelled residual of each band first became non-zero, so
        #: the dwell gate can be measured in shadow. None means flat.
        self._stranded_since = {"conservative": None, "optimistic": None}
        #: timing-only refusals, counted rather than stored
        self.defensive_timing_skips: Dict[str, int] = {}
        self.cost_opt = {FM.UP: D(0), FM.DOWN: D(0)}
        self.rest: Dict[str, Leg] = {}
        self.eligible = 0
        self.flat_quotes = 0
        self.complement_quotes = 0
        self.cancels = 0
        self.quoted_shares = D(0)
        self.partial_legs = 0
        self.peak_exposure = D(0)
        self.first_fill_offset: Optional[float] = None
        #: SIMULATED defensive verdicts, one per tick inside the guard
        #: window while a modelled residual exists. Never a real exit.
        self.defensive: List[Dict[str, Any]] = []

    # ----------------------------------------------------------- reads
    def spent(self) -> D:
        return self.cost[FM.UP] + self.cost[FM.DOWN]

    def committed(self) -> D:
        return sum((l.left_cons * l.own_px for l in self.rest.values()), D(0))

    def exposure(self) -> D:
        return self.spent() + self.committed()

    def residual(self) -> D:
        return self.qty[FM.UP] - self.qty[FM.DOWN]

    def matched(self) -> D:
        return min(self.qty[FM.UP], self.qty[FM.DOWN])

    def basis(self, side: str) -> Optional[D]:
        q = self.qty[side]
        return (self.cost[side] / q) if q else None

    def stale_reason(self, leg: "Leg", side: str, bid: D, ask: D
                     ) -> Optional[str]:
        """Why a retained leg no longer qualifies, or None to keep it.

        EVERY TEST REUSES A CHECK THIS MODULE ALREADY MAKES. No new threshold is
        introduced and nothing is tuned to the window that exposed the defect:

          PRICE_THROUGH_THE_MARKET -- a resting BUY priced better than the
            current touch on its own side is alone at the top of the book. It
            is not a deeper rung waiting its turn; it is a bid above the market
            that anything coming down will hit. Parameter-free: it is a sign
            test against the current touch, not a tick tolerance.
          PAIR_NO_LONGER_ECONOMIC -- the SAME frozen gate that admitted the
            quote, re-applied at the CURRENT complement price. A leg whose pair
            would not be admitted now is not admitted to stay.
          INVENTORY_FULL -- the taper's own u, unchanged: at u >= 1 the adding
            side is already at a full flat clip and must not grow.
          OVER_ENVELOPE -- the same exposure definition used everywhere here.
        """
        own_now = bid if side == FM.UP else (D(1) - ask)
        # 1. priced through the market on our own side
        if side == FM.UP and leg.book_px > bid:
            return "PRICE_THROUGH_THE_MARKET"
        if side == FM.DOWN and leg.book_px < ask:
            return "PRICE_THROUGH_THE_MARKET"
        # 2. the gate, re-applied now
        resid = self.residual()
        other = FM.DOWN if side == FM.UP else FM.UP
        if resid != 0 and ((resid > 0) != (side == FM.UP)):
            # this leg reduces inventory: score it against the held basis
            held = FM.UP if resid > 0 else FM.DOWN
            b = self.basis(held)
            if b is not None and b + leg.own_px > self.thresh:
                return "PAIR_NO_LONGER_ECONOMIC"
        else:
            complement_now = (D(1) - ask) if side == FM.UP else bid
            if leg.own_px + complement_now > self.thresh:
                return "PAIR_NO_LONGER_ECONOMIC"
        # 3. inventory, by the taper's own measure
        if resid != 0 and ((resid > 0) == (side == FM.UP)):
            pair_now = bid + (D(1) - ask)
            flat_clip = ((self.envelope / pair_now).quantize(
                TICK, rounding=ROUND_FLOOR)) if pair_now > 0 else D(0)
            if flat_clip <= 0 or abs(resid) >= flat_clip:
                return "INVENTORY_FULL"
        # 4. capital
        if self.exposure() > self.envelope:
            return "OVER_ENVELOPE"
        return None

    # ----------------------------------------------------------- tick
    def step(self, bid: D, ask: D, bid_sh: Optional[D], ask_sh: Optional[D],
             prints: Sequence[Mapping[str, Any]], now: float,
             window_start: Optional[float]) -> None:
        self._step(bid, ask, bid_sh, ask_sh, prints, now, window_start)
        # AFTER quoting as well as before: a window that posts once and never
        # re-quotes would otherwise report a peak exposure of zero.
        if self.policy == LIVE_SEQUENTIAL and window_start is not None:
            self._defensive_eval(bid, ask, bid_sh, ask_sh, now, window_start)
        exp = self.exposure()
        if exp > self.peak_exposure:
            self.peak_exposure = exp

    def _step(self, bid: D, ask: D, bid_sh: Optional[D], ask_sh: Optional[D],
              prints: Sequence[Mapping[str, Any]], now: float,
              window_start: Optional[float]) -> None:
        for side, leg in list(self.rest.items()):
            got = leg.advance(prints)
            if got["cons"] > 0:
                self.qty[side] += got["cons"]
                self.cost[side] += got["cons"] * leg.own_px
                if self.first_fill_offset is None and window_start:
                    self.first_fill_offset = round(now - window_start, 1)
            if got["opt"] > 0:
                self.qty_opt[side] += got["opt"]
                self.cost_opt[side] += got["opt"] * leg.own_px
            if leg.left_cons <= 0:
                self.rest.pop(side, None)
            elif leg.filled_cons > 0:
                self.partial_legs += 1
        # A quote whose touch has moved: COMPLEMENT_ONLY cancels it, SKEWED
        # KEEPS it. Keeping costs nothing in this model -- a resting order only
        # fills when prints cross its own price -- and it is the behaviour the
        # ladder evidence supports: a rung that is still valid keeps its queue
        # position instead of re-joining the back of a new one.
        for side, leg in list(self.rest.items()):
            ref = bid if side == FM.UP else ask
            if leg.book_px == ref:
                continue
            if self.policy == LIVE_SEQUENTIAL:
                # FROZEN. The live engine does not re-price or withdraw a
                # resting leg because the touch moved; it waits. Keeping it
                # costs nothing here -- a leg still only fills when prints
                # cross its own price.
                self.retained_legs += 1
                continue
            if self.policy == SKEWED:
                why = self.stale_reason(leg, side, bid, ask)
                if why is None:
                    self.retained_legs += 1
                    continue
                # a leg that no longer qualifies is withdrawn, exactly as
                # COMPLEMENT_ONLY would withdraw it. What already filled stays.
                self.rest.pop(side, None)
                self.cancels += 1
                self.replaced_legs += 1
                self.revalidation_cancels[why] = \
                    self.revalidation_cancels.get(why, 0) + 1
                continue
            self.rest.pop(side, None)
            self.cancels += 1
            self.replaced_legs += 1
        exp = self.exposure()
        if exp > self.peak_exposure:
            self.peak_exposure = exp
        room = self.envelope - exp
        # BEHIND THE TOUCH. A maker buy of UP rests below the bid and a maker
        # buy of DOWN rests above the ask, so both own prices fall by `depth`.
        # depth 0 leaves the historical at-touch basis untouched.
        up_px, dn_px = bid - self.depth, (D(1) - ask) - self.depth
        if up_px <= 0 or dn_px <= 0:
            return
        resid = self.residual()
        if resid == 0:
            if up_px + dn_px > self.thresh:
                return
            self.eligible += 1
            if self.rest or room <= 0:
                return
            clip = (room / (up_px + dn_px)).quantize(TICK, rounding=ROUND_FLOOR)
            # a clip larger than the displayed size is not a real quote
            for d in (bid_sh, ask_sh):
                if d is not None:
                    clip = min(clip, d)
            if clip < TICK:
                return
            if self.policy == LIVE_SEQUENTIAL:
                # ONE OUTSTANDING ORDER, and the CHEAPER leg goes first: if it
                # fills alone the residual it strands is the smaller of the
                # two. The 2300z plan put UP at 0.24 ahead of DOWN at 0.71 and
                # bounded its alone-loss at 0.95 rather than 2.7225.
                first = FM.UP if up_px <= dn_px else FM.DOWN
                px = up_px if first == FM.UP else dn_px
                ref = bid if first == FM.UP else ask
                disp = bid_sh if first == FM.UP else ask_sh
                c = clip if disp is None else min(clip, disp)
                if c < TICK:
                    return
                # the complement limit is fixed HERE, at plan time, and is
                # what the cell will wait for however far the touch wanders.
                self.frozen_cpx = dn_px if first == FM.UP else up_px
                self.flat_quotes += 1
                self.quoted_shares += c
                self.rest[first] = Leg(self.slug, first, px, ref, c, now,
                                       bid, ask, disp)
                return
            self.flat_quotes += 1
            self.quoted_shares += clip * 2
            self.rest[FM.UP] = Leg(self.slug, FM.UP, up_px, bid, clip, now,
                                   bid, ask, bid_sh)
            self.rest[FM.DOWN] = Leg(self.slug, FM.DOWN, dn_px, ask, clip, now,
                                     bid, ask, ask_sh)
            return
        need = FM.DOWN if resid > 0 else FM.UP
        held = FM.UP if resid > 0 else FM.DOWN
        b = self.basis(held)
        cpx = dn_px if need == FM.DOWN else up_px
        if self.policy == LIVE_SEQUENTIAL:
            # the FROZEN limit, not a fresh read of the book. Re-deriving it
            # each tick is chasing, which is what the 0.96 ceiling refused
            # seven times in 2300z while the frozen 0.71 waited and filled.
            if self.frozen_cpx is not None:
                cpx = self.frozen_cpx
            # one outstanding order TOTAL: a partially filled first leg still
            # occupies the slot, so the complement waits for it to terminate.
            if self.rest:
                return
        # u is how full the inventory is against one flat pair clip. It is the
        # taper's only input and is clamped to [0, 1].
        flat_clip = (self.envelope / (up_px + dn_px)).quantize(
            TICK, rounding=ROUND_FLOOR)
        u = min(D(1), abs(resid) / flat_clip) if flat_clip > 0 else D(1)
        if b is not None and b + cpx <= self.thresh and need not in self.rest \
                and room > 0:
            self.eligible += 1
            disp = ask_sh if need == FM.DOWN else bid_sh
            want = abs(resid)
            if self.policy == SKEWED:
                # the reducing leg grows with inventory, bounded
                want = (want * min(SKEW_REDUCE_CEIL, D(1) + u)).quantize(
                    TICK, rounding=ROUND_FLOOR)
            clip = min((room / cpx).quantize(TICK, rounding=ROUND_FLOOR), want)
            if disp is not None:
                clip = min(clip, disp)
            if clip >= TICK:
                self.complement_quotes += 1
                self.quoted_shares += clip
                self.rest[need] = Leg(self.slug, need, cpx,
                                      ask if need == FM.DOWN else bid,
                                      clip, now, bid, ask, disp)
                room = self.envelope - self.exposure()
        # THE CLIFF, REPLACED. COMPLEMENT_ONLY refuses the adding leg outright
        # while inventory is non-zero. SKEWED tapers it instead, which is the
        # shape L5's fills show, and still refuses it when the pair itself is
        # uneconomic or the envelope is full.
        if self.policy != SKEWED or held in self.rest or room <= 0:
            return
        apx = up_px if need == FM.DOWN else dn_px
        if up_px + dn_px > self.thresh:
            return
        disp = bid_sh if held == FM.UP else ask_sh
        base = (room / apx).quantize(TICK, rounding=ROUND_FLOOR)
        base = (base * max(SKEW_ADD_FLOOR, D(1) - u)).quantize(
            TICK, rounding=ROUND_FLOOR)
        clip = base if disp is None else min(base, disp)
        if clip < TICK:
            return
        self.flat_quotes += 1
        self.quoted_shares += clip
        self.rest[held] = Leg(self.slug, held, apx,
                              bid if held == FM.UP else ask,
                              clip, now, bid, ask, disp)

    # ----------------------------------------------------------- report
    def _defensive_eval(self, bid: D, ask: D, bid_sh: Optional[D],
                        ask_sh: Optional[D], now: float,
                        window_start: float) -> None:
        """Price a SIMULATED defensive exit for the modelled residual.

        Both bands are evaluated because they can hold different
        inventories: the conservative band is the ledger, the optimistic
        one is the upper bound, and a rule that only works on the
        optimistic book is not a rule.
        """
        off = now - window_start
        # THE TIMING GATE NOW BELONGS TO `DEF.decide`, NOT TO THIS LOOP.
        # Returning here on `arm_window_open` is what made every cell report
        # defensive_evals 0 outside the last minute -- the shadow could not
        # see the decision it exists to measure. `decide` is handed the
        # stranding clock and refuses on its own terms, so both the dwell gate
        # and the old arm window are observable from the same records.
        for band, qty_map, cost_map in (("conservative", self.qty,
                                         self.cost),
                                        ("optimistic", self.qty_opt,
                                         self.cost_opt)):
            resid = qty_map[FM.UP] - qty_map[FM.DOWN]
            if resid == 0:
                self._stranded_since[band] = None
                continue
            if self._stranded_since[band] is None:
                self._stranded_since[band] = now
            held = FM.UP if resid > 0 else FM.DOWN
            q = abs(resid)
            held_qty = qty_map[held]
            basis = (cost_map[held] / held_qty) if held_qty > 0 else None
            if basis is None or basis <= 0:
                continue
            # EXECUTABLE, not a maker hope: a long UP is sold INTO the bid,
            # a long DOWN is closed by buying UP at the ask. The displayed
            # size on that side is the only liquidity evidence there is.
            if held == FM.UP:
                exit_px, depth = (D(1) - bid), bid_sh
            else:
                exit_px, depth = ask, ask_sh
            v = DEF.decide(
                now_t=now, window_start=window_start,
                complete_cutoff_s=SIM_COMPLETE_CUTOFF_S,
                cancel_cutoff_s=SIM_CANCEL_CUTOFF_S,
                unmatched_qty=q, first_leg_cost=basis,
                exit_price=exit_px, displayed_depth=depth,
                buy_side=(FM.DOWN if held == FM.UP else FM.UP),
                maker_complement_price=self.frozen_cpx,
                taker_fee_per_contract=(TAKER_THETA * exit_px
                                        * (D(1) - exit_px)),
                stranded_since_t=self._stranded_since[band],
                simulate=True)
            # RECORD THE DECISIONS, NOT EVERY TICK. A pure TIMING refusal is
            # not a decision about this position's economics and there are
            # hundreds per window, so it is counted and not stored. Anything
            # that reached the economics, and everything that acts, is kept.
            if v.get("reason") in ("COMPLEMENT_STILL_WITHIN_DWELL",
                                   "OUTSIDE_DEFENSIVE_ARM_WINDOW",
                                   "INSUFFICIENT_EXECUTION_TIME"):
                self.defensive_timing_skips[band] = \
                    self.defensive_timing_skips.get(band, 0) + 1
                continue
            self.defensive.append({
                "band": band, "t_offset_s": round(off, 1),
                "stranded_for_s": round(now - self._stranded_since[band], 1),
                "arm_window_open": DEF.arm_window_open(
                    now, window_start, SIM_COMPLETE_CUTOFF_S,
                    cancel_cutoff_s=SIM_CANCEL_CUTOFF_S),
                "held": held, "unmatched": str(q),
                "basis": str(basis), "bid": str(bid), "ask": str(ask),
                "exit_price_own": str(exit_px),
                "displayed_depth": (None if depth is None else str(depth)),
                "frozen_complement": (None if self.frozen_cpx is None
                                      else str(self.frozen_cpx)),
                "verdict": v,
            })

    def report(self, settle_up: Optional[bool]) -> Dict[str, Any]:
        up, dn = self.qty[FM.UP], self.qty[FM.DOWN]
        m = self.matched()
        bu = self.basis(FM.UP) or D(0)
        bd = self.basis(FM.DOWN) or D(0)
        matched_cost = m * (bu + bd)
        matched_pnl = m * D(1) - matched_cost
        resid = up - dn
        resid_cost = (resid * bu) if resid > 0 else (-resid * bd)
        resid_pnl = None
        if settle_up is not None:
            if resid > 0:
                resid_pnl = resid * ((D(1) if settle_up else D(0)) - bu)
            else:
                resid_pnl = -resid * ((D(0) if settle_up else D(1)) - bd)
        out = {
            "threshold": str(self.thresh), "envelope": str(self.envelope),
            "policy": self.policy,
            #: WHICH PRICE BASIS this cell measured. 0 is at-touch, the
            #: historical basis; 2 is the live engine's. Two cells at the same
            #: threshold and different depths are not comparable and this is
            #: how a reader tells them apart.
            "depth_ticks": self.depth_ticks,
            "frozen_complement": (None if self.policy != LIVE_SEQUENTIAL
                                  else (None if self.frozen_cpx is None
                                        else str(self.frozen_cpx))),
            "legs_retained_through_a_touch_move": self.retained_legs,
            "legs_replaced_on_a_touch_move": self.replaced_legs,
            "policy_version": (SKEW_VERSION if self.policy == SKEWED else None),
            #: SIMULATED defensive verdicts inside the guard window. Empty
            #: for every at-touch arm. Never a real exit, never a fill.
            "defensive_evals": len(self.defensive),
            "defensive_would_act": sum(
                1 for d in self.defensive if d["verdict"].get("act")),
            "defensive_detail": self.defensive[:40],
            "revalidation_cancels": dict(self.revalidation_cancels),
            "eligible_ticks": self.eligible,
            "flat_quotes": self.flat_quotes,
            "complement_quotes": self.complement_quotes,
            "cancels": self.cancels,
            "partial_legs": self.partial_legs,
            "quoted_shares": str(self.quoted_shares),
            "filled_shares_conservative": str(up + dn),
            "filled_shares_optimistic": str(self.qty_opt[FM.UP] + self.qty_opt[FM.DOWN]),
            "up": str(up), "dn": str(dn),
            "matched_shares": str(m), "residual_shares": str(resid),
            "matched_cost": str(matched_cost), "matched_pnl_modelled": str(matched_pnl),
            "residual_cost": str(resid_cost),
            "residual_pnl_modelled": (None if resid_pnl is None else str(resid_pnl)),
            "net_pnl_modelled": (None if resid_pnl is None
                                 else str(matched_pnl + resid_pnl)),
            "cash_deployed": str(self.spent()),
            "committed_resting": str(self.committed()),
            "peak_exposure": str(self.peak_exposure),
            "envelope_binding": self.peak_exposure >= self.envelope - TICK,
            "first_fill_offset_s": self.first_fill_offset,
            "labels": ("MODELLED. Not submitted, not filled, not realized. "
                       "Conservative band puts the whole displayed size "
                       "ahead of us; the optimistic band puts none of it "
                       "there. Matched pairs redeem exactly $1.00; residual "
                       "is scored only once the window's outcome is known."),
        }
        return out


class TwoSidedShadow:
    """One window's evaluator across every frozen (threshold, envelope) cell."""

    def __init__(self, slug: str, window_start: Optional[float] = None,
                 thresholds: Sequence[D] = THRESHOLDS,
                 envelopes: Sequence[D] = ENVELOPES):
        self.slug = slug
        self.window_start = window_start
        self.configs: List[Config] = [
            Config(slug, t, e) for e in tuple(envelopes) + (LIVE_ENVELOPE,)
            for t in thresholds]
        # the comparison arm, at the only gate the live book reaches, so the
        # two policies are measured on the same windows and the same prints.
        self.configs += [Config(slug, D("0.99"), e, SKEWED)
                         for e in tuple(envelopes) + (LIVE_ENVELOPE,)]
        # THE ARM THAT MEASURES WHAT WE ACTUALLY RUN: the live ceiling, the
        # live envelope, priced two ticks behind the touch, one order at a
        # time, complement frozen. Added rather than substituted so every
        # historical at-touch cell keeps reporting exactly what it always did
        # and the two bases can be read side by side.
        self.configs += [Config(slug, D("0.96"), e, LIVE_SEQUENTIAL,
                                depth_ticks=2)
                         for e in (LIVE_ENVELOPE, D("100"))]
        self.ticks = 0
        self.two_sided_ticks = 0
        self.prints_consumed = 0
        self.coverage_gaps = 0
        self.last_row: Optional[tuple] = None
        self._seen: set = set()
        self.spreads: Dict[str, int] = {}

    # prints arrive as the recorder's rolling list; anchor by ROW IDENTITY
    # because an index cursor freezes at the 500-row cap and a timestamp
    # cursor drops every print that shared a frame.
    def _new_prints(self, rows: Sequence[Mapping[str, Any]]
                    ) -> List[Mapping[str, Any]]:
        rows = list(rows or ())
        if not rows:
            return []
        out: List[Mapping[str, Any]] = []
        if self.last_row is None:
            out = rows
        else:
            idx = None
            for i, r in enumerate(rows):
                if FM.ModelSession.row_id(r) == self.last_row:
                    idx = i
                    break
            if idx is None:
                self.coverage_gaps += 1          # the buffer rolled past us
                out = rows
            else:
                out = rows[idx + 1:]
        fresh = []
        for r in out:
            rid = FM.ModelSession.row_id(r)
            if rid in self._seen:
                continue
            self._seen.add(rid)
            fresh.append(r)
        if rows:
            self.last_row = FM.ModelSession.row_id(rows[-1])
        self.prints_consumed += len(fresh)
        return fresh

    def step(self, snapshot: Optional[Mapping[str, Any]],
             prints: Sequence[Mapping[str, Any]], now: float) -> None:
        self.ticks += 1
        fresh = self._new_prints(prints)
        if not snapshot:
            return
        bid, ask = _d(snapshot.get("bid")), _d(snapshot.get("ask"))
        if bid is None or ask is None or ask <= bid:
            return                      # one-sided or locked: not quotable
        self.two_sided_ticks += 1
        key = str(ask - bid)
        self.spreads[key] = self.spreads.get(key, 0) + 1
        bid_sh = _d(snapshot.get("bid_shares") or snapshot.get("bid_qty"))
        ask_sh = _d(snapshot.get("ask_shares") or snapshot.get("ask_qty"))
        for c in self.configs:
            c.step(bid, ask, bid_sh, ask_sh, fresh, now, self.window_start)

    def report(self, settle_up: Optional[bool] = None) -> Dict[str, Any]:
        return {
            "LABEL": "L5_TWO_SIDED_SHADOW",
            "market": self.slug,
            "ticks": self.ticks,
            "two_sided_ticks": self.two_sided_ticks,
            "prints_consumed": self.prints_consumed,
            "coverage_gaps": self.coverage_gaps,
            "spread_histogram": dict(sorted(self.spreads.items())),
            "settlement_known": settle_up is not None,
            "skew_policy_version": SKEW_VERSION,
            "cells": [c.report(settle_up) for c in self.configs],
            "discipline": ("queue-ahead frozen at placement from the "
                           "displayed top-of-book size and never re-read; "
                           "only direction-correct trade volume advances it; "
                           "a touch is never a fill; a cancellation ahead of "
                           "us is never progress; partials are kept as "
                           "partials; capital is deployed+committed, not an "
                           "acceptable loss; thresholds are frozen and are "
                           "not fitted to these windows."),
        }


# ---------------------------------------------------------------- scoring
#: a parked window's lifecycle, reported explicitly so a window that has not
#: settled is never mistaken for one that settled flat.
PENDING, FINALIZED, ERROR = "PENDING", "FINALIZED", "ERROR"

#: how long a parked window may wait for an authoritative settlement before it
#: is reported ERROR. Generous: BTC15m settles within minutes, and a window
#: that takes longer than this is evidence of something wrong, not of patience.
SETTLE_DEADLINE_S = 3600.0


def score_report(report: Mapping[str, Any], settle_up: bool) -> Dict[str, Any]:
    """Score a parked window's report against an authoritative outcome.

    A PURE FUNCTION of (report, outcome), which is what makes restart-safety
    testable: the parked report carries everything scoring needs, so a process
    that dies between parking and settling loses nothing but the wait.

    The residual's basis is recovered as `residual_cost / |residual_shares|`,
    which is exactly how it was accumulated. A matched pair redeems $1.00
    whichever way the market settles, so `matched_pnl_modelled` is unchanged
    by the outcome and is not recomputed here.
    """
    out = dict(report)
    cells = []
    for c in report.get("cells") or ():
        c = dict(c)
        resid = D(str(c.get("residual_shares") or 0))
        rcost = D(str(c.get("residual_cost") or 0))
        mp = D(str(c.get("matched_pnl_modelled") or 0))
        if resid == 0:
            rp = D(0)
        else:
            basis = rcost / abs(resid)
            if resid > 0:          # long UP
                rp = resid * ((D(1) if settle_up else D(0)) - basis)
            else:                  # long DOWN
                rp = -resid * ((D(0) if settle_up else D(1)) - basis)
        c["residual_pnl_modelled"] = str(rp)
        c["net_pnl_modelled"] = str(mp + rp)
        c["state"] = FINALIZED
        cells.append(c)
    out["cells"] = cells
    out["state"] = FINALIZED
    out["settlement_known"] = True
    out["settled_up"] = bool(settle_up)
    return out


def pending_report(report: Mapping[str, Any], *, reason: str = "",
                   state: str = PENDING) -> Dict[str, Any]:
    """Mark a window that has NOT been scored. Never zero-filled.

    `net_pnl_modelled` stays None on every cell. A pending window contributes
    nothing to any total and must never be counted as a flat result.
    """
    out = dict(report)
    out["cells"] = [dict(c, state=state, net_pnl_modelled=None,
                         residual_pnl_modelled=None)
                    for c in (report.get("cells") or ())]
    out["state"] = state
    out["settlement_known"] = False
    if reason:
        out["state_reason"] = reason
    return out


#: The platform truncates a log line at exactly this many bytes. Measured,
#: not assumed: every full `L5_TWO_SIDED_SHADOW` line on deployment a342fccc
#: arrived at len 81920 with its tail gone, and the runner's own heartbeat
#: comment records the same limit.
LOG_LINE_LIMIT = 81920

#: What one emitted report may cost. Well under the limit above, and under a
#: 64 KiB pipe buffer, which is the number that actually matters -- see
#: `bounded_for_log`.
LOG_BUDGET_BYTES = 48000


def _cell_digest(cell: Mapping[str, Any]) -> Dict[str, Any]:
    """One cell reduced to the figures the economics are read from.

    Everything dropped here is still in the ledger on the volume: `park` and
    `finalize` store the WHOLE report, so this reduces the log copy only.
    """
    keep = ("name", "label", "depth_ticks", "side", "resting",
            "matched_shares", "residual_shares", "residual_cost",
            "matched_pnl_modelled", "residual_pnl_modelled",
            "net_pnl_modelled", "state", "fills", "capital_deployed")
    out = {k: cell[k] for k in keep if k in cell}
    out["_reduced"] = True
    return out


def bounded_for_log(report: Mapping[str, Any], *,
                    budget: int = LOG_BUDGET_BYTES) -> Dict[str, Any]:
    """The report, small enough to reach the log intact.

    WHY THIS EXISTS, MEASURED ON THE LIVE SERVICE. The full report serialises
    to more than 80 KB. Three things follow, and the first two were costing
    real time on the window's opening:

      * a write larger than the pipe buffer between the process and the log
        shipper BLOCKS until the shipper drains it. Measured on deployment
        a342fccc: the 29,109-byte interim line costs 0.000-0.002 s, while the
        81,920-byte full line costs 6.158 s in `two_sided_close` and 6.180 s
        in `two_sided_settle` -- the two stages that emit it. After the
        93-second diagnostic was moved off `roll`, this became the single
        largest remaining cost of a window roll.
      * the line is TRUNCATED at LOG_LINE_LIMIT anyway, so the tail has been
        discarded in production all along. Paying six seconds for a record
        that arrives incomplete is the worst of both.
      * nothing is lost by reducing it. `ShadowLedger.park` and `finalize`
        write the WHOLE report to the volume, which is the durable copy the
        scoring actually reads; this is the observability copy.

    REDUCES, NEVER SUMMARISES AWAY. The cells are replaced by digests
    carrying the P&L and inventory figures, the count is stated, and
    `cells_reduced` says it happened. A report already inside budget is
    returned unchanged, so the common small case is byte-identical.
    """
    try:
        raw = json.dumps(report, default=str)
    except BaseException:                                      # noqa: BLE001
        return dict(report)
    if len(raw) <= budget:
        return dict(report)
    out = dict(report)
    cells = list(out.get("cells") or ())
    out["cells"] = [_cell_digest(c) if isinstance(c, Mapping) else c
                    for c in cells]
    out["cells_reduced"] = {
        "cells": len(cells), "full_bytes": len(raw), "budget_bytes": budget,
        "labels": ("the per-cell detail is in the shadow ledger on the "
                   "volume, which `park` and `finalize` write in full. It is "
                   "dropped HERE because a line this size blocks the "
                   "process for about six seconds and is truncated at "
                   "%d bytes anyway." % LOG_LINE_LIMIT)}
    if _fits(out, budget):
        return out

    # STAGE 2. The histogram is the next largest field and the ledger has it,
    # so the log keeps only its size.
    hist = out.get("spread_histogram")
    if isinstance(hist, Mapping):
        out["spread_histogram"] = {"_buckets": len(hist), "_reduced": True}
    if _fits(out, budget):
        return out

    # STAGE 3. Enough cells and even the digests do not fit. Replace them with
    # the roll-up the economics are actually read from, which is a FIXED size
    # whatever the grid does. 400 cells x ~200 bytes of digest is 80 KB, so a
    # large grid reaches here; this is what makes the function bounded rather
    # than merely smaller.
    out["cells"] = _cells_rollup(out.get("cells") or ())
    if _fits(out, budget):
        return out

    # STAGE 4. The guaranteed floor: the identifying fields, the roll-up, and
    # nothing else. Fixed size by construction, so this always fits, and it
    # states that it is the floor rather than pretending to be the report.
    floor: Dict[str, Any] = {"LABEL": out.get("LABEL"),
                             "market": out.get("market"),
                             "cells": out.get("cells"),
                             "cells_reduced": out.get("cells_reduced")}
    for k in ("window_start", "window_end", "ticks", "state", "outcome",
              "net_pnl_modelled", "matched_pnl_modelled",
              "residual_pnl_modelled", "capital_deployed", "fills",
              "discipline_label"):
        if k in out:
            floor[k] = _clamp_scalar(out[k])
    floor["_log_floor"] = True
    return floor


def _fits(obj: Any, budget: int) -> bool:
    try:
        return len(json.dumps(obj, default=str)) <= budget
    except BaseException:                                      # noqa: BLE001
        return True          # unserialisable: nothing further to do with it


def _clamp_scalar(v: Any, *, limit: int = 240) -> Any:
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    t = v if isinstance(v, str) else str(v)
    return t if len(t) <= limit else t[:limit] + "...[clamped]"


def _cells_rollup(cells: Any) -> Dict[str, Any]:
    """The cell list as totals. Fixed size however many cells there are."""
    rows = [c for c in cells if isinstance(c, Mapping)]
    sums = {k: D("0") for k in ("matched_shares", "residual_shares",
                                "residual_cost", "matched_pnl_modelled",
                                "residual_pnl_modelled", "net_pnl_modelled",
                                "capital_deployed")}
    states: Dict[str, int] = {}
    resting = 0
    for c in rows:
        for k in sums:
            try:
                sums[k] += D(str(c.get(k, "0") or "0"))
            except BaseException:                              # noqa: BLE001
                pass
        st = str(c.get("state", "?"))
        states[st] = states.get(st, 0) + 1
        if c.get("resting"):
            resting += 1
    return {"_rollup": True, "count": len(rows), "by_state": states,
            "resting": resting,
            "totals": {k: str(v) for k, v in sums.items()}}


#: Heartbeat fields that must NEVER be reduced. Every safety decision and
#: every figure the owner is owed during a live window is read from one of
#: these, so they are preserved whole however large the rest grows.
HB_NEVER_REDUCE = (
    "utc", "role", "market", "armed", "preflight", "owner_arm_token",
    "halt_local_env", "halt_latch", "shadow_lock", "approval_state",
    "windows_remaining", "submission_gate", "seeded", "submissions_spent",
    "realized_today", "day_realized", "orders_uncertain", "open_orders",
    "live_orders", "defensive_spent_usd", "peak_usd", "cash_deployed_usd",
    "campaign", "unresolved_accounting", "venue_requests", "http_429",
    "fill_calibration", "positions", "inventory", "fees", "rebates",
    "auth_book", "t_in_window_s", "LABEL",
    # THE ARMING PREFLIGHT VERDICT. Boot-only and on its own
    # line was how the 2026-11-06 20:00z window was lost: the
    # preflight said ok=false fail=["absolute_deadline"] 26
    # minutes beforehand and nobody was reading that line.
    "authorization_preflight",
    # THE LIFETIME COMPLETION BUDGET. Its balance had to be
    # INFERRED after the first live window because the
    # heartbeat never carried it.
    "defensive_budget",
)


def bounded_hb(hb: Mapping[str, Any], *,
               budget: int = LOG_BUDGET_BYTES) -> Dict[str, Any]:
    """The heartbeat, small enough that its SAFETY fields arrive.

    WHY, MEASURED. On deployment 84909719 the `L5_LIVE_HB` line arrives at
    exactly 81,921 bytes -- truncated at the platform limit -- and the
    fields past the cut are the ones a live window has to be policed by:
    `realized_today`, `orders_uncertain`, `defensive_spent_usd`,
    `cash_deployed_usd`, `open_orders`. They were simply absent from the
    tape. Field sizes at that moment:

        shadow_controller   61,450      <- 75% of the line
        retired_markets      4,550
        stats_age_s          4,072
        stats                3,945
        ...
        submission_gate        748
        campaign               968
        day_realized           385

    So the line was spent on shadow-lane observability while the safety
    state fell off the end. This reduces the LARGEST reducible field first
    and never touches anything in `HB_NEVER_REDUCE`, so the safety fields
    cannot be the ones dropped. A heartbeat already inside budget is
    returned unchanged.
    """
    try:
        if len(json.dumps(hb, default=str)) <= budget:
            return dict(hb)
    except BaseException:                                      # noqa: BLE001
        return dict(hb)
    out = dict(hb)
    dropped: Dict[str, Any] = {}
    while True:
        try:
            if len(json.dumps(out, default=str)) <= budget:
                break
        except BaseException:                                  # noqa: BLE001
            break
        biggest, big_sz = None, -1
        for k, v in out.items():
            if k in HB_NEVER_REDUCE or k in dropped:
                continue
            try:
                sz = len(json.dumps(v, default=str))
            except BaseException:                              # noqa: BLE001
                sz = 0
            if sz > big_sz:
                biggest, big_sz = k, sz
        if biggest is None:
            break                      # only protected fields remain
        dropped[biggest] = big_sz
        out[biggest] = {"_reduced": True, "bytes": big_sz}
    if dropped:
        out["hb_reduced"] = {
            "fields": dropped,
            "budget_bytes": budget,
            "labels": ("reduced so the SAFETY fields reach the tape. The "
                       "line was truncated at 81,920 bytes and the fields "
                       "past the cut were the ones a live window is "
                       "policed by. Nothing in HB_NEVER_REDUCE is ever "
                       "reduced here."),
        }
    return out


# =====================================================================
# THE DURABLE SHADOW LEDGER -- APPEND-ONLY, AND WHY (#209)
#
# THE HYPOTHESIS IS NO LONGER A HYPOTHESIS. The v1 `save` docstring listed
# this file as the remaining candidate for the residual rollover cost and
# refused to act on it until the running service reported its own number.
# It has. Deployment 18deb02d, executor service, its own `L5_SHADOW_LEDGER`
# line at 2026-11-08T00:06:24Z:
#
#   {"finalized": 2294, "pending": 0, "bytes": 292732040,
#    "last_save_s": 6.7605, "saves_this_process": 9, "max_pending": 16}
#
# 2,294 scored windows, 292.7 MB, 6.76 s to write, twice a window, on a
# network-attached volume -- and a scored report is never read back by
# anything on the money path. Local disk at the same mean record size
# (292732040 / 2294 = 127,608 B) reproduces the shape:
#
#      N | file MB | v1 save s | 2x/window | v1 load s
#     96 |    12.2 |     0.105 |     0.209 |     0.060
#    480 |    61.2 |     0.491 |     0.981 |     0.311
#    960 |   122.5 |     0.637 |     1.273 |     0.460
#   1440 |   183.7 |     0.927 |     1.854 |     0.730
#   2294 |   292.7 |     1.772 |     3.544 |     1.428
#
# WHAT CHANGED. `finalized` is append-only by its own contract -- a window
# is scored exactly once and never revised -- so it does not belong in a
# document that is rewritten. It now lives in a log beside the manifest,
# one JSON line per scored window, appended once:
#
#   <path>                   manifest: format, pending, counts. Small,
#                            rewritten atomically; `pending` is bounded at
#                            `max_pending`, so its size cannot grow.
#   <path>.finalized.jsonl   append-only log, one {"market", "record"} line
#                            per scored window. Never rewritten.
#   <path>.finalized.idx     slug -> (offset, length). A CACHE, always
#                            validated against the log and rebuilt from it
#                            on any mismatch, so losing it costs a scan and
#                            never a record.
#
# WHAT IS PRESERVED. Every historical record (migration reads the v1
# document and writes each record into the log, verifies all of them are
# present and parse, and only then replaces the manifest); restart recovery;
# idempotency, which is now structural rather than incidental -- a slug
# already in the log is never appended again, so a window cannot be scored
# twice however many times `finalize` is called; and settlement scoring,
# which is untouched.
#
# WHAT IT MAY NEVER DO. Block a trading decision. This is shadow telemetry:
# `save` cannot raise, cannot refuse, and reports its own latency against
# `SAVE_BUDGET_S` instead of enforcing anything. A failed write loses
# modelled evidence and is counted and named; it never propagates into the
# settle pass or the rollover.
# =====================================================================

#: on-disk manifest format. 1 is the original single-document format whose
#: whole-file rewrite reached 6.76 s; 2 keeps `pending` in the manifest and
#: every finalized record in the append-only log beside it.
LEDGER_FORMAT = 2

#: a save slower than this is COUNTED AND REPORTED. It is telemetry, not a
#: limit -- nothing here may refuse to persist. The point is that the number
#: which reached 6.76 s in production is now visible per save.
SAVE_BUDGET_S = 0.25

#: finalized records held in memory by the lazy view. The log is read by
#: offset, so a 128 KB scored report is parsed only when something asks for
#: it and 2,294 of them are never resident at once.
RECORD_CACHE = 8

#: index entries verified against the LOG'S OWN BYTES before the index is
#: trusted. The index is a cache and the log is the authority, so acceptance
#: is spot-checked rather than assumed. See `_spot_check`.
IDX_SPOT_CHECKS = 16

#: every log line is `{"market": "<slug>", "record": {...}}` and this reads
#: the slug off the front of it, so indexing or verifying a line costs ~60
#: bytes rather than parsing a 128 KB body.
_MARKET_PREFIX = re.compile(br'^\{"market": "((?:[^"\\]|\\.)*)"')

#: where the v1 document is kept once its records are provably in the log.
#: It is RENAMED, not copied, so the migration costs no extra write -- and
#: it makes a rollback to the pre-#209 code a rename back rather than a
#: re-migration. v1 reading a v2 manifest finds no `finalized` key, starts
#: with an empty scoring history and re-scores windows into a fresh v1
#: document: no record is destroyed, and rolling forward recovers everything
#: from the log, but it is not a revert. With this, it is.
V1_BACKUP_SUFFIX = ".v1-pre209"

#: `digest_state` values. The manifest carries a digest of the slug set, so
#: the index can be checked against a file it did not write.
DIGEST_AGREES = "SLUG_SET_AGREES_WITH_MANIFEST"
DIGEST_REBUILT = "REBUILT_AFTER_DIGEST_MISMATCH"
DIGEST_NONE = "NO_DIGEST_IN_MANIFEST"
DIGEST_STALE = "MANIFEST_DIGEST_OLDER_THAN_THE_LOG"

#: `integrity` values. Only the first means the log and the manifest agree.
INTEGRITY_OK = "LOG_AGREES_WITH_MANIFEST"
INTEGRITY_SHORT = "RECORDS_MISSING_FROM_LOG"
INTEGRITY_UNWITNESSED = "NO_MANIFEST_COUNT_TO_CHECK_AGAINST"

#: `migration_state` values.
NATIVE = "FORMAT_2_NATIVE"
MIGRATED = "MIGRATED_FROM_V1"
NOT_VERIFIED = "MIGRATION_NOT_VERIFIED"


def _fsync_dir(d: str) -> None:
    """Make a rename durable. Best effort: some filesystems refuse it."""
    try:
        fd = os.open(d or ".", os.O_RDONLY)
    except BaseException:                                      # noqa: BLE001
        return
    try:
        os.fsync(fd)
    except BaseException:                                      # noqa: BLE001
        pass
    finally:
        os.close(fd)


class FinalizedRecords(_ABCMapping):
    """Append-only, lazily read mapping of slug -> scored report.

    IT IS A MAPPING ON PURPOSE. `state["finalized"]` was a dict and the
    ledger's own methods, the tests and the analysis tooling all read it as
    one, so this keeps that interface and changes only where the records
    live. `__contains__` and `__len__` answer from the offset index without
    touching a record; `__getitem__` reads one record's bytes by offset.

    `__setitem__` stages a record and `flush` appends it. A slug already in
    the log is DROPPED rather than appended, which is what makes scoring
    once a property of the storage and not merely of the caller's `if`.

    WHEN A RECORD'S BYTES CANNOT BE READ, `__contains__` stays True and
    `__getitem__` raises KeyError. That is deliberate: the slug WAS scored,
    and the safety-relevant half of this ledger is refusing to score it
    twice. Returning a fabricated record would be worse than an error.
    """

    def __init__(self, log_path: str, idx_path: str) -> None:
        self.log_path = log_path
        self.idx_path = idx_path
        self._index: Dict[str, Any] = {}       # slug -> (offset, length)
        self._dirty: Dict[str, Any] = {}       # staged, not yet appended
        self._cache: Dict[str, Any] = {}
        self._log_end = 0                      # after the last COMPLETE line
        self.index_state = "EMPTY"
        self.records_unreadable = 0
        self.truncated_tail_bytes = 0
        self.duplicates_dropped = 0
        self.index_refusals = 0        # a damaged index refused at load
        self.index_spot_mismatches = 0  # entries naming the wrong bytes
        self.slug_mismatches = 0       # caught on a read, healed by rescan
        #: SCORED, BUT NOT READABLE IN THE LOG. Two kinds of evidence live in
        #: the index and they are not the same: the OFFSETS are a cache, and
        #: the log overrules them; the SLUG SET is the record that a window
        #: was scored at all. Rebuilding geometry from the log must not throw
        #: that away, or a window whose bytes were destroyed would look
        #: unscored and could be scored a second time -- and refusing to
        #: score twice is the half of this ledger that matters. So a slug the
        #: index named and the log no longer yields stays IN the mapping,
        #: reads raise, and the count is reported. Fail closed, and loudly.
        self._orphans: Dict[str, Any] = {}

    # ------------------------------------------------------------ loading
    def load(self) -> None:
        """Index the log. Reads the small index file, or scans the log.

        THE INDEX IS NEVER TRUSTED ON ITS WORD. A cache that merely ENDS at
        the right byte can still hide a record -- drop one entry from the
        middle and the slug it named becomes invisible, which would let an
        already-scored window be scored again. So the index is accepted only
        when it TILES the log (`_read_idx`) and a bounded sample of its
        entries is verified against the log's own bytes (`_spot_check`). Any
        failure rebuilds from the log, which is the authority, and says which
        check refused it. A record that is on disk is never hidden by a
        damaged index; the worst it costs is a scan.
        """
        self._index, self._dirty, self._cache = {}, {}, {}
        self._log_end, self.index_state = 0, "EMPTY"
        if not os.path.exists(self.log_path):
            return
        size = os.path.getsize(self.log_path)
        idx, end = None, 0
        try:
            idx, end = self._read_idx()
        except BaseException:                                  # noqa: BLE001
            idx = None
        if idx is None or end > size:
            # no index, an index that does not tile the log, or one AHEAD of
            # it: the log is the only authority, so rebuild from it.
            self.index_refusals += 1
            self.rescan("REBUILT_BY_SCAN", known=self._idx_slugs())
            return
        self._index, self._log_end = idx, end
        bad = self._spot_check()
        if bad:
            # the index named bytes that are not the record it claimed.
            self.index_spot_mismatches += bad
            self.index_refusals += 1
            self.rescan("REBUILT_AFTER_SPOT_CHECK_FAILED")
            return
        self.index_state = "READ_FROM_INDEX"
        if end < size:
            # records were appended and the index write was lost, or the
            # process died mid-append. Both are read off the log itself.
            self._scan_from(end)
            self.index_state = "INDEX_CAUGHT_UP_WITH_LOG"

    def _idx_slugs(self) -> Any:
        """The slug column alone, geometry ignored.

        A refused index is refused for its OFFSETS. The slugs it names are
        still a record that those windows were scored, and that evidence is
        what stops one being scored twice, so it is harvested even when the
        arithmetic is rejected.
        """
        out = set()
        try:
            with open(self.idx_path, "r") as fh:
                for line in fh:
                    if not line.endswith("\n"):
                        break        # torn by a crash: NOT an entry, and its
                                     # slug column may be half a slug, which
                                     # would invent a missing record
                    parts = line[:-1].split("\t")
                    if len(parts) != 3 or not parts[0]:
                        continue
                    try:
                        int(parts[1]), int(parts[2])
                    except BaseException:                      # noqa: BLE001
                        continue
                    out.add(parts[0])
        except BaseException:                                  # noqa: BLE001
            pass
        return out

    def rescan(self, state: str = "REBUILT_BY_SCAN", known: Any = None) -> None:
        """Rebuild geometry from the log, KEEPING the slug set it had.

        The log decides where every record is. It does not get to decide
        which windows were scored, because a destroyed record would then
        become a window we would score again. Slugs we knew and the log no
        longer yields become orphans: still present, still unscorable,
        unreadable, and counted.
        """
        known = set(known or ()) | set(self._index) | set(self._orphans)
        self._index, self._cache = {}, {}
        self._log_end = 0
        self._scan_from(0)
        self._orphans = {s: None for s in known if s not in self._index}
        self.index_state = state
        try:
            self._write_idx()
        except BaseException:                                  # noqa: BLE001
            pass

    def slug_digest(self) -> str:
        """A digest of WHICH windows were scored, geometry excluded.

        Written into the manifest, which is a different file written in the
        same save, so the index can be checked against something it did not
        produce. That is what closes the one case the tiling and the sample
        between them cannot see: a single entry that RENAMES a slug keeps the
        geometry and the count intact, so without an outside witness the
        window it renamed would stay hidden until something read it.
        """
        h = hashlib.sha256()
        for slug in sorted(self.keys()):
            h.update(slug.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()

    def readable_count(self) -> int:
        """Records whose bytes are in the log, orphans excluded."""
        extra = sum(1 for s in self._dirty if s not in self._index)
        return len(self._index) + extra

    def orphan_slugs(self) -> List[str]:
        return sorted(self._orphans)

    def _read_idx(self) -> Any:
        """Read the index, and REFUSE it unless it tiles the log.

        The entries must cover the log from byte 0, end to end, with no gap
        and no overlap, because that is what the writer produces. A missing
        entry leaves a gap, a shifted one leaves an overlap, and a phantom
        one collides -- so the arithmetic alone catches the damage that an
        endpoint check cannot see. Raising here sends `load` to the log.
        """
        idx: Dict[str, Any] = {}
        spans: List[Any] = []
        with open(self.idx_path, "r") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    continue
                slug, off, n = line.split("\t")          # ValueError -> scan
                off, n = int(off), int(n)
                if off < 0 or n <= 0:
                    raise ValueError("INDEX_SPAN_NOT_POSITIVE")
                if slug in idx:
                    raise ValueError("INDEX_NAMES_A_SLUG_TWICE")
                idx[slug] = (off, n)
                spans.append((off, n))
        cur = 0
        for off, n in sorted(spans):
            if off != cur:
                raise ValueError("INDEX_DOES_NOT_TILE_THE_LOG")
            cur = off + n
        return idx, cur

    def _slug_of(self, raw: bytes) -> str:
        """The slug on the front of a log line, without parsing the body."""
        m = _MARKET_PREFIX.match(raw[:512])
        if m:
            return str(json.loads('"' + m.group(1).decode("utf-8") + '"'))
        return str(json.loads(raw.decode("utf-8"))["market"])

    def _spot_check(self, checks: int = IDX_SPOT_CHECKS) -> int:
        """Verify index entries against the log's bytes. Returns mismatches.

        Reads the slug off the front of the line at each sampled offset, so
        the whole check costs a few hundred bytes. The first and last entries
        are always checked and the rest are spread evenly, which catches
        wholesale damage at load; a single mis-paired entry that escapes the
        sample is caught by `_read` the first time that slug is touched.
        """
        items = sorted(self._index.items(), key=lambda kv: kv[1][0])
        if not items:
            return 0
        picks = {0, len(items) - 1}
        step = max(1, len(items) // max(1, checks - len(picks)))
        for i in range(0, len(items), step):
            if len(picks) >= checks:
                break
            picks.add(i)
        bad = 0
        try:
            with open(self.log_path, "rb") as fh:
                for i in sorted(picks):
                    slug, (off, n) = items[i]
                    fh.seek(off)
                    raw = fh.read(min(n, 512))
                    try:
                        if self._slug_of(raw) != slug:
                            bad += 1
                    except BaseException:                      # noqa: BLE001
                        bad += 1
        except BaseException:                                  # noqa: BLE001
            return len(picks)                 # cannot verify -> do not trust
        return bad

    def _scan_from(self, start: int) -> None:
        """Index the log from `start`, which must begin a line."""
        off = start
        tail = 0
        with open(self.log_path, "rb") as fh:
            fh.seek(start)
            for raw in fh:
                n = len(raw)
                if not raw.endswith(b"\n"):
                    tail = n            # a crash mid-append is NOT a record
                    break
                try:
                    slug = self._slug_of(raw)
                except BaseException:                          # noqa: BLE001
                    self.records_unreadable += 1
                else:
                    if slug in self._index:
                        self.duplicates_dropped += 1
                    self._index.setdefault(slug, (off, n))
                off += n
        self._log_end = off
        self.truncated_tail_bytes = tail

    # ------------------------------------------------------------ mapping
    def __getitem__(self, slug: str) -> Any:
        return self._read(str(slug), healed=False)

    def _read(self, slug: str, *, healed: bool) -> Any:
        """One record, and PROOF it is the one that was asked for.

        The line carries its own slug, so the index's claim is checked
        against the record rather than believed. A mismatch means the cache
        named the wrong bytes: the log is the authority, so the index is
        rebuilt from it and the read is tried once more. That heals every
        other mis-paired entry at the same time, including the ones that
        would otherwise have made a scored window look unscored.
        """
        if slug in self._dirty:
            return self._dirty[slug]
        if slug in self._cache:
            return self._cache[slug]
        loc = self._index.get(slug)
        if loc is None:
            if slug in self._orphans:
                self.records_unreadable += 1    # scored; bytes not in the log
            raise KeyError(slug)
        off, n = loc
        try:
            with open(self.log_path, "rb") as fh:
                fh.seek(off)
                raw = fh.read(n)
            obj = json.loads(raw.decode("utf-8"))
            got, rec = str(obj["market"]), obj["record"]
        except BaseException:                                  # noqa: BLE001
            self.records_unreadable += 1
            raise KeyError(slug)
        if got != slug:
            self.slug_mismatches += 1
            if healed:
                raise KeyError(slug)
            self.rescan("REBUILT_AFTER_SLUG_MISMATCH")
            return self._read(slug, healed=True)
        if len(self._cache) >= RECORD_CACHE:
            self._cache.clear()
        self._cache[slug] = rec
        return rec

    def __setitem__(self, slug: str, rec: Any) -> None:
        slug = str(slug)
        if slug in self._index or slug in self._orphans:
            self.duplicates_dropped += 1       # scored once, by storage
            return
        self._dirty[slug] = rec

    def __contains__(self, slug: Any) -> bool:
        slug = str(slug)
        return (slug in self._index or slug in self._dirty
                or slug in self._orphans)

    def __len__(self) -> int:
        orph = sum(1 for s in self._orphans
                   if s not in self._index and s not in self._dirty)
        return self.readable_count() + orph

    def __iter__(self) -> Any:
        for slug in self._index:
            yield slug
        for slug in self._dirty:
            if slug not in self._index:
                yield slug
        for slug in self._orphans:
            if slug not in self._index and slug not in self._dirty:
                yield slug

    def __eq__(self, other: Any) -> bool:
        """Cheap on the only comparison anything makes: `== {}`."""
        if not isinstance(other, _ABCMapping):
            return NotImplemented
        if len(self) != len(other):
            return False
        if not len(self):
            return True
        return self.as_dict() == dict(other)

    def __ne__(self, other: Any) -> bool:
        eq = self.__eq__(other)
        return eq if eq is NotImplemented else not eq

    def __repr__(self) -> str:
        return "FinalizedRecords(%d records, %s)" % (len(self),
                                                     self.index_state)

    def as_dict(self) -> Dict[str, Any]:
        """Every record, read back. Expensive and used only off the hot path."""
        out: Dict[str, Any] = {}
        for slug in self:
            try:
                out[slug] = self[slug]
            except KeyError:
                continue                       # counted in records_unreadable
        return out

    # ------------------------------------------------------------ writing
    def absorb(self, other: Any) -> int:
        """Stage a plain mapping someone assigned over this view."""
        staged = 0
        for slug, rec in dict(other or {}).items():
            slug = str(slug)
            if (slug in self._index or slug in self._dirty
                    or slug in self._orphans):
                continue
            self._dirty[slug] = rec
            staged += 1
        return staged

    def flush(self) -> int:
        """Append the staged records. Returns bytes written."""
        if not self._dirty:
            return 0
        pending = [(s, r) for s, r in self._dirty.items()
                   if s not in self._index]
        for slug in list(self._dirty):
            if slug in self._index:
                self._dirty.pop(slug, None)
                self.duplicates_dropped += 1
        if not pending:
            return 0
        off = self._log_end
        added: List[Any] = []
        written = 0
        exists = os.path.exists(self.log_path)
        fh = open(self.log_path, "r+b" if exists else "w+b")
        try:
            fh.seek(off)
            for slug, rec in pending:
                raw = (json.dumps({"market": slug, "record": rec},
                                  default=str) + "\n").encode("utf-8")
                fh.write(raw)
                added.append((slug, off, len(raw)))
                off += len(raw)
                written += len(raw)
            # anything beyond what we just wrote is a partial line from a
            # crash mid-append. It was never acknowledged and is not a record.
            fh.truncate()
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fh.close()
        for slug, o, n in added:
            self._index[slug] = (o, n)
            self._dirty.pop(slug, None)
        self._log_end = off
        self.truncated_tail_bytes = 0
        try:
            self._write_idx()
        except BaseException:                                  # noqa: BLE001
            # BELT AND BRACES. The records are already appended, fsynced and
            # indexed in memory; the index FILE is a cache whose loss costs a
            # scan. An index failure must never be reported as a save failure.
            pass
        return written

    def _write_idx(self) -> None:
        """Rewrite the whole index. It is ~45 B a record: 103 KB at 2,294."""
        tmp = self.idx_path + ".tmp"
        try:
            with open(tmp, "w") as fh:
                for slug, (off, n) in self._index.items():
                    fh.write("%s\t%d\t%d\n" % (slug, off, n))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.idx_path)
            _fsync_dir(os.path.dirname(self.idx_path))
        except BaseException:                                  # noqa: BLE001
            # the index is a cache. Losing it costs a scan, never a record.
            try:
                os.unlink(tmp)
            except BaseException:                              # noqa: BLE001
                pass

    def log_bytes(self) -> Optional[int]:
        try:
            return os.path.getsize(self.log_path)
        except BaseException:                                  # noqa: BLE001
            return None


class ShadowLedger:
    """Durable, shadow-only record of parked and finalized windows.

    SEPARATE FROM THE INVENTORY LEDGER ON PURPOSE. Nothing here is read by any
    gate, any limit or any accounting path; it exists so that scoring survives
    a restart and so that a window cannot be scored twice. It holds modelled
    numbers only.

    `pending` is a small bounded map in the manifest. `finalized` is the
    append-only log described above, reached through `FinalizedRecords`, so
    a save costs one appended record rather than the whole history.
    """

    def __init__(self, path: str, *, max_pending: int = 16):
        self.path = path
        self.max_pending = int(max_pending)
        self.log_path = path + ".finalized.jsonl"
        self.idx_path = path + ".finalized.idx"
        self.format = LEDGER_FORMAT
        self.migration_state = NATIVE
        self.migrated_records = 0
        self.quarantined: Optional[str] = None
        #: the manifest's own count, an INDEPENDENT witness to how many
        #: windows were scored. It is what makes a lost record impossible to
        #: pass off as an empty history.
        self.expected_finalized: Optional[int] = None
        self.records_missing = 0
        self.integrity = INTEGRITY_UNWITNESSED
        self.digest_state = DIGEST_NONE
        #: where the pre-migration v1 document was kept, so a rollback to the
        #: pre-#209 code is a rename back and not a re-migration.
        self.v1_backup: Optional[str] = None
        self.v1_backup: Optional[str] = None
        self.saves = 0
        self.slow_saves = 0
        self.last_save_s: Optional[float] = None
        self.last_save_bytes: Optional[int] = None
        self.last_save_bytes_written: Optional[int] = None
        self.last_save_error: Optional[str] = None
        self.load_s: Optional[float] = None
        self._legacy_fallback = False
        self.records = FinalizedRecords(self.log_path, self.idx_path)
        self.state: Dict[str, Any] = {"pending": {}, "finalized": self.records}
        self.load()

    # ------------------------------------------------------------- loading
    def load(self) -> None:
        t0 = time.monotonic()
        self.records = FinalizedRecords(self.log_path, self.idx_path)
        self.state = {"pending": {}, "finalized": self.records}
        try:
            self.records.load()
        except BaseException:                                  # noqa: BLE001
            # a corrupt log must never stop the executor. The records that
            # are readable are indexed; the rest are counted.
            pass
        got: Any = None
        try:
            with open(self.path, "r") as fh:
                got = json.load(fh)
        except FileNotFoundError:
            got = None
        except BaseException:                                  # noqa: BLE001
            # a corrupt manifest is KEPT, not overwritten in place: it is the
            # only copy of whatever it held. v1 started empty and silently
            # left the file to be replaced by the next save.
            self._quarantine()
            got = None
        if not isinstance(got, Mapping):
            self.load_s = round(time.monotonic() - t0, 4)
            return
        self.state["pending"] = dict(got.get("pending") or {})
        legacy = dict(got.get("finalized") or {})
        fmt = got.get("format")
        count = got.get("finalized_count")
        digest = got.get("finalized_digest")
        got = None                                 # release the v1 document
        try:
            fmt = int(fmt)
        except BaseException:                                  # noqa: BLE001
            fmt = 1
        if fmt < LEDGER_FORMAT or legacy:
            self._migrate_v1(legacy)
        self._check_digest(digest)
        self._check_against_manifest(count)
        self.load_s = round(time.monotonic() - t0, 4)

    def _check_digest(self, digest: Any) -> None:
        """Check the index's slug set against the manifest's witness.

        ONCE. A disagreement sends the load to the log, which is the
        authority; if the log then still disagrees, the manifest's digest is
        simply older than the log -- a save that appended and died before
        rewriting the manifest -- which is benign and reported rather than
        chased. The slug set is preserved across the rebuild, so a renamed
        entry cannot take a scored window with it.
        """
        r = self.records
        if not isinstance(r, FinalizedRecords) or not digest:
            self.digest_state = DIGEST_NONE
            return
        digest = str(digest)
        if r.slug_digest() == digest:
            self.digest_state = DIGEST_AGREES
            return
        try:
            r.rescan(DIGEST_REBUILT, known=r._idx_slugs())
            r.index_refusals += 1
        except BaseException:                                  # noqa: BLE001
            pass
        self.digest_state = (DIGEST_AGREES if r.slug_digest() == digest
                             else DIGEST_STALE)

    def _check_against_manifest(self, count: Any) -> None:
        """Compare what the log holds against what the manifest witnessed.

        A damaged index is caught by `FinalizedRecords.load`. This catches
        the other direction -- records missing from the LOG -- and refuses to
        let it pass silently: the log is rescanned first, because the index
        is only a cache, and a shortfall that survives the rescan is named
        with its size in `size_report`. Nothing is deleted and nothing is
        repaired by guesswork; the discrepancy is reported for a human.
        """
        r = self.records
        orphans = len(r.orphan_slugs()) if isinstance(r, FinalizedRecords) else 0
        try:
            self.expected_finalized = int(count)
        except BaseException:                                  # noqa: BLE001
            self.expected_finalized = None
        exp = self.expected_finalized
        readable = (r.readable_count() if isinstance(r, FinalizedRecords)
                    else len(r or {}))
        if exp is not None and readable < exp and not orphans:
            # the log holds fewer records than the manifest witnessed and no
            # orphan explains it. The index is only a cache, so read the log
            # itself before believing a shortfall.
            try:
                r.rescan("REBUILT_FOR_COUNT_SHORTFALL")
            except BaseException:                              # noqa: BLE001
                pass
            orphans = len(r.orphan_slugs())
            readable = r.readable_count()
        gap = 0 if exp is None else max(0, exp - readable)
        self.records_missing = max(orphans, gap)
        if self.records_missing:
            self.integrity = INTEGRITY_SHORT
        elif exp is None:
            self.integrity = INTEGRITY_UNWITNESSED
        else:
            self.integrity = INTEGRITY_OK

    def _quarantine(self) -> None:
        """Move an unparseable manifest aside rather than lose it."""
        dest = "%s.unparseable-%d" % (self.path, int(time.time()))
        try:
            os.replace(self.path, dest)
            _fsync_dir(os.path.dirname(self.path))
            self.quarantined = dest
        except BaseException:                                  # noqa: BLE001
            self.quarantined = None

    def _migrate_v1(self, legacy: Dict[str, Any]) -> None:
        """Move a v1 document's records into the log, then verify, then swap.

        ORDER MATTERS AND IS THE WHOLE SAFETY ARGUMENT. The records are
        written and fsynced to the log FIRST; every one of them is then read
        back out of the log and parsed; the manifest is replaced LAST. A
        crash at any point leaves the v1 document untouched and the migration
        simply runs again. Nothing is deleted: the v1 document is replaced
        only once its contents are provably readable in the log.
        """
        n = len(legacy)
        slugs = set(legacy)
        try:
            self.records.absorb(legacy)
            self.records.flush()
            legacy.clear()                         # release ~1 GB of objects
            check = FinalizedRecords(self.log_path, self.idx_path)
            check.load()
            missing = [s for s in slugs if s not in check]
            unreadable = []
            for s in slugs:
                if s in check:
                    try:
                        check[s]
                    except KeyError:
                        unreadable.append(s)
        except BaseException:                                  # noqa: BLE001
            missing, unreadable = list(slugs), []
        if missing or unreadable:
            # DO NOT TOUCH THE V1 DOCUMENT. It is the only copy that is
            # provably complete, and rewriting it from records we have just
            # failed to read back would be the one way to actually lose one.
            # Scoring carries on against the log; manifest writes are
            # suspended, so `pending` stops being durable until a later
            # start migrates cleanly. That is modelled evidence for one
            # window against the whole scoring history.
            self.migration_state = NOT_VERIFIED
            self._legacy_fallback = True
            self.migrated_records = 0
            return
        self.migration_state = MIGRATED if n else NATIVE
        self.migrated_records = n
        try:
            self._write_manifest(backup=self.path + V1_BACKUP_SUFFIX if n
                                 else None)
        except BaseException:                                  # noqa: BLE001
            # the log holds everything; the next save rewrites the manifest.
            pass

    # ------------------------------------------------------------- saving
    def save(self) -> None:
        """Append what is new. Only the small manifest is ever rewritten.

        IT CANNOT RAISE AND IT CANNOT REFUSE. This is shadow telemetry on a
        path that also rolls live windows, so a write failure is counted and
        named in `size_report` and never propagated. Latency is measured
        against `SAVE_BUDGET_S` and reported, not enforced.
        """
        t0 = time.monotonic()
        written = 0
        err = None
        try:
            fin = self.state.get("finalized")
            if fin is not self.records:
                # someone assigned a plain mapping over the view. Take its
                # records and put the view back, so the next save is cheap.
                self.records.absorb(fin or {})
                self.state["finalized"] = self.records
            written = self.records.flush()
            if not self._legacy_fallback:
                written += self._write_manifest()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            err = type(exc).__name__
        self.last_save_s = round(time.monotonic() - t0, 4)
        self.last_save_bytes_written = written
        self.last_save_error = err
        self.saves += 1
        if self.last_save_s > SAVE_BUDGET_S:
            self.slow_saves += 1
        try:
            self.last_save_bytes = os.path.getsize(self.path)
        except BaseException:                                  # noqa: BLE001
            self.last_save_bytes = None

    def _write_manifest(self, backup: Optional[str] = None) -> int:
        """Write the manifest atomically; `backup` keeps what was there.

        The new manifest is written and fsynced to a temp FIRST, so the
        window in which `path` does not exist is two renames wide rather
        than a write wide. An existing backup is never clobbered: the
        pre-migration document is kept as it was, once.
        """
        doc = {
            "format": LEDGER_FORMAT,
            "pending": self.state.get("pending") or {},
            "finalized_count": len(self.records),
            "finalized_digest": self.records.slug_digest(),
            "finalized_log": os.path.basename(self.log_path),
            "labels": ("`finalized` lives in the append-only log named "
                       "here, one JSON line a scored window. This manifest "
                       "carries only `pending`, which is bounded, so a save "
                       "costs the records it adds and not the history."),
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        if backup and not os.path.exists(backup):
            try:
                os.replace(self.path, backup)
                self.v1_backup = backup
            except FileNotFoundError:
                pass
            except BaseException:                              # noqa: BLE001
                # the records are already in the log and verified; a backup
                # that cannot be taken is a convenience, not the safety net.
                pass
        os.replace(tmp, self.path)
        _fsync_dir(os.path.dirname(self.path))
        return os.path.getsize(self.path)

    def size_report(self) -> Dict[str, Any]:
        """What the ledger costs, for one log line per roll."""
        r = self.records if isinstance(self.records, FinalizedRecords) else None
        return {"finalized": len(self.state.get("finalized") or {}),
                "pending": len(self.state.get("pending") or {}),
                "bytes": getattr(self, "last_save_bytes", None),
                "last_save_s": getattr(self, "last_save_s", None),
                "saves_this_process": int(getattr(self, "saves", 0)),
                "max_pending": self.max_pending,
                "format": self.format,
                "log_bytes": r.log_bytes() if r is not None else None,
                "bytes_written": self.last_save_bytes_written,
                "save_error": self.last_save_error,
                "slow_saves": self.slow_saves,
                "save_budget_s": SAVE_BUDGET_S,
                "over_budget": (self.last_save_s is not None
                                and self.last_save_s > SAVE_BUDGET_S),
                "load_s": self.load_s,
                "migration_state": self.migration_state,
                "migrated_records": self.migrated_records,
                "manifest_writes": ("SUSPENDED_MIGRATION_NOT_VERIFIED"
                                    if self._legacy_fallback else "ACTIVE"),
                "pending_durable": not self._legacy_fallback,
                "index_state": r.index_state if r is not None else None,
                "integrity": self.integrity,
                "digest_state": self.digest_state,
                "expected_finalized": self.expected_finalized,
                "records_missing": self.records_missing,
                "index_refusals": (r.index_refusals
                                   if r is not None else None),
                "index_spot_mismatches": (r.index_spot_mismatches
                                          if r is not None else None),
                "slug_mismatches": (r.slug_mismatches
                                    if r is not None else None),
                "readable_records": (r.readable_count()
                                     if r is not None else None),
                "orphan_slugs": (len(r.orphan_slugs())
                                 if r is not None else None),
                "records_unreadable": (r.records_unreadable
                                       if r is not None else None),
                "duplicates_dropped": (r.duplicates_dropped
                                       if r is not None else None),
                "truncated_tail_bytes": (r.truncated_tail_bytes
                                         if r is not None else None),
                "quarantined": self.quarantined,
                "v1_backup": self.v1_backup,
                "labels": ("`finalized` is an APPEND-ONLY log, one JSON "
                           "line a scored window, and a save appends the "
                           "records it adds. The whole-file rewrite this "
                           "replaced reached 292.7 MB and 6.76 s a save, "
                           "twice a window, on the volume. `bytes` is the "
                           "manifest; `log_bytes` is the history.")}

    # ------------------------------------------------------------ parking
    def park(self, report: Mapping[str, Any], *, now: float) -> Dict[str, Any]:
        slug = str(report.get("market") or "")
        if not slug or slug in self.state["finalized"]:
            return pending_report(report, reason="ALREADY_FINALIZED",
                                  state=FINALIZED)
        rec = pending_report(report)
        rec["parked_at"] = now
        self.state["pending"][slug] = rec
        # oldest first out, so a stuck window cannot starve new ones
        while len(self.state["pending"]) > self.max_pending:
            oldest = min(self.state["pending"].items(),
                         key=lambda kv: kv[1].get("parked_at") or 0)[0]
            self.state["pending"].pop(oldest, None)
        self.save()
        return rec

    def pending_slugs(self) -> List[str]:
        return sorted(self.state["pending"])

    # ------------------------------------------------------------ scoring
    def finalize(self, slug: str, settle_up: bool) -> Optional[Dict[str, Any]]:
        """Score a parked window ONCE. Returns None if there is nothing to do."""
        if slug in self.state["finalized"]:
            return None                       # idempotent, across restarts
        rec = self.state["pending"].pop(slug, None)
        if rec is None:
            return None
        scored = score_report(rec, settle_up)
        self.state["finalized"][slug] = scored
        self.save()
        return scored

    def expire(self, now: float) -> List[Dict[str, Any]]:
        """Windows that waited too long for a settlement become ERROR, not 0."""
        out = []
        for slug, rec in list(self.state["pending"].items()):
            if now - float(rec.get("parked_at") or now) < SETTLE_DEADLINE_S:
                continue
            self.state["pending"].pop(slug, None)
            bad = pending_report(rec, reason="SETTLEMENT_NOT_OBSERVED",
                                 state=ERROR)
            self.state["finalized"][slug] = bad
            out.append(bad)
        if out:
            self.save()
        return out


# =====================================================================
# TRADE-THROUGH OBSERVER -- additive, read-only, and deliberately NOT a
# fill model.
#
# WHY IT IS SEPARATE FROM EVERYTHING ABOVE. The Config cells model FILLS:
# they carry queue-ahead, partial consumption and a conservative band, and
# they quote AT THE TOUCH, where the pair costs `1 - spread`. On a one-tick
# BTC15m book that is 0.99, whose one cent of gross edge is less than the
# 3.44-cent worst-case taker fee at p~0.45 -- so the touch baseline cannot
# be profitable however well it fills, and its modelled fills are not
# executable profits. The economically viable quote rests BEHIND the touch,
# and nothing above measures that.
#
# This observer answers the one question that decides the strategy: at a
# depth whose economics work, how often does the market actually TRADE
# THROUGH both resting prices inside one window? It records observations,
# not conclusions, and it never claims a fill:
#
#   never_reached  -- no print on the wrong side of our price
#   touched        -- a print AT our price; queue-dependent, NOT a fill
#   traded_through -- a print strictly through our price, which is the
#                     NECESSARY condition for a maker fill and never the
#                     sufficient one, because queue-ahead is unmeasured
#
# Queue-ahead is carried as the displayed size at placement and reported as
# a band: conservative puts all of it ahead of us, optimistic none. Both
# bounds are emitted and neither is called a fill.
#
# It owns no transport, no ledger, no limit and no gate, and the existing
# collector's outputs are untouched -- it emits its own record.

#: tick depths observed in parallel. 0 is the existing touch baseline,
#: kept only for comparison; 2 is the first depth that clears the fee floor.
TT_DEPTHS = (0, 2)

NEVER = "never_reached"
TOUCHED = "touched"
THROUGH = "traded_through"


class TradeThroughLeg:
    """One hypothetical resting price, and what the tape did to it."""

    def __init__(self, side: str, own_px: D, yes_px: D, qty: D,
                 queue_ahead: Optional[D], placed_t: float) -> None:
        self.side = side
        self.own_px = own_px          # cost in this side's own terms
        self.yes_px = yes_px          # the YES price the tape is quoted in
        self.qty = qty
        self.queue_ahead = queue_ahead
        self.placed_t = placed_t
        self.state = NEVER
        self.first_touch_t: Optional[float] = None
        self.first_through_t: Optional[float] = None
        self.through_vol = D(0)
        self.touch_vol = D(0)

    def consume(self, px: D, qty: D, direction: str, t: float) -> None:
        """One print. UP rests on the bid and is hit by a SELL aggressor;
        DOWN rests as a YES ask and is lifted by a BUY aggressor."""
        if self.side == FM.UP:
            if direction != "sell":
                return
            through, at = px < self.yes_px, px == self.yes_px
        else:
            if direction != "buy":
                return
            through, at = px > self.yes_px, px == self.yes_px
        if through:
            self.through_vol += qty
            if self.first_through_t is None:
                self.first_through_t = t
            self.state = THROUGH
        elif at:
            self.touch_vol += qty
            if self.first_touch_t is None:
                self.first_touch_t = t
            if self.state == NEVER:
                self.state = TOUCHED

    def bounds(self) -> Dict[str, str]:
        """Conservative and optimistic filled quantity. NEITHER is a fill."""
        qa = self.queue_ahead
        opt = min(self.qty, self.through_vol)
        if qa is None:
            cons = D(0)               # unknown queue never fills
        else:
            cons = min(self.qty, max(D(0), self.through_vol - qa))
        return {"filled_optimistic": str(opt), "filled_conservative": str(cons),
                "queue_ahead_at_placement": (
                    None if qa is None else str(qa)),
                "labels": ("optimistic assumes we were first in the queue, "
                           "conservative assumes the whole displayed size "
                           "was ahead of us. An unknown queue never fills. "
                           "Neither bound is a confirmed fill.")}

    def row(self) -> Dict[str, Any]:
        return dict({"side": self.side, "own_price": str(self.own_px),
                     "yes_price": str(self.yes_px), "qty": str(self.qty),
                     "state": self.state,
                     "placed_t": self.placed_t,
                     "first_touch_t": self.first_touch_t,
                     "first_through_t": self.first_through_t,
                     "through_volume": str(self.through_vol),
                     "touch_volume": str(self.touch_vol),
                     "seconds_to_through": (
                         None if self.first_through_t is None
                         else round(self.first_through_t - self.placed_t, 3))},
                    **self.bounds())


class TradeThroughObserver:
    """One window, one depth: both legs priced once, then the tape applied.

    Prices are frozen at placement and never re-quoted: a moving quote
    measures a different experiment every tick.
    """

    TICK = D("0.01")
    #: the probe's own terminal deadline; nothing is counted past it
    CANCEL_MARGIN_S = 180.0
    #: worst-case fee coefficient. The maker rebate is never assumed.
    THETA = D("0.0695")
    #: the pair must clear the worst-case fee on both legs
    MIN_EDGE = D("0.04")

    def __init__(self, slug: str, depth_ticks: int, qty: D,
                 window_start: Optional[float] = None,
                 window_s: int = 900) -> None:
        self.slug = slug
        self.depth_ticks = int(depth_ticks)
        self.qty = D(str(qty))
        self.window_start = window_start
        self.window_s = int(window_s)
        self.legs: Dict[str, TradeThroughLeg] = {}
        self.placed_t: Optional[float] = None
        self.pair_cost: Optional[D] = None
        self.spread_at_placement: Optional[D] = None
        self.bid_at_placement: Optional[D] = None
        self.refusal: Optional[str] = None
        self.prints = 0
        self._seen: set = set()

    # ------------------------------------------------------------ timing
    def deadline(self) -> Optional[float]:
        if self.window_start is None:
            return None
        return self.window_start + self.window_s - self.CANCEL_MARGIN_S

    def past_deadline(self, now: float) -> bool:
        d = self.deadline()
        return d is not None and now > d

    # ------------------------------------------------------------ pricing
    def place(self, bid: D, ask: D, bid_sh: Optional[D],
              ask_sh: Optional[D], now: float) -> Optional[str]:
        """Freeze both prices once. Returns a refusal reason, or None."""
        if self.legs or self.refusal:
            return self.refusal
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask <= bid:
            self.refusal = "BOOK_NOT_TWO_SIDED"
            return self.refusal
        off = D(self.depth_ticks) * self.TICK
        up_own = bid - off                  # UP cost, rests at this YES bid
        dn_own = (D(1) - ask) - off         # DOWN cost
        dn_yes = D(1) - dn_own              # the YES ask it is equivalent to
        if up_own <= 0 or dn_own <= 0 or up_own >= 1 or dn_own >= 1:
            self.refusal = "PRICE_OUT_OF_BAND"
            return self.refusal
        self.pair_cost = up_own + dn_own
        self.spread_at_placement = ask - bid
        self.bid_at_placement = bid
        self.placed_t = now
        self.legs = {
            FM.UP: TradeThroughLeg(FM.UP, up_own, up_own, self.qty,
                                   bid_sh, now),
            FM.DOWN: TradeThroughLeg(FM.DOWN, dn_own, dn_yes, self.qty,
                                     ask_sh, now),
        }
        return None

    # ------------------------------------------------------------ the tape
    def consume(self, prints: Sequence[Mapping[str, Any]], now: float) -> None:
        if not self.legs or self.past_deadline(now):
            return
        for p in prints or ():
            key = (p.get("t"), p.get("px"), p.get("qty"), p.get("dir"))
            if key in self._seen:
                continue
            self._seen.add(key)
            t = p.get("t")
            if t is None or self.placed_t is None or t <= self.placed_t:
                continue
            d = self.deadline()
            if d is not None and t > d:
                continue
            px = _d(p.get("px")); q = _d(p.get("qty")) or D(0)
            if px is None:
                continue
            direction = str(p.get("dir") or "").lower()
            self.prints += 1
            for leg in self.legs.values():
                leg.consume(px, q, direction, t)

    # --------------------------------------------------------- economics
    def fee(self, qty: D, price: D) -> D:
        raw = self.THETA * qty * price * (D(1) - price)
        return (raw.quantize(D("0.01"), rounding=ROUND_CEILING)
                if raw > 0 else D(0))

    def settlement(self, settle_up: Optional[bool]) -> Dict[str, Any]:
        """Both outcomes, using the FROZEN recorded prices and taker fees.

        The one-sided case is settled, not marked: if only one leg was
        traded through, the position is that leg alone and it either
        redeems or expires worthless.
        """
        if not self.legs:
            return {"evaluable": False, "reason": self.refusal or "NOT_PLACED"}
        up, dn = self.legs[FM.UP], self.legs[FM.DOWN]
        both = up.state == THROUGH and dn.state == THROUGH
        one = (up.state == THROUGH) != (dn.state == THROUGH)
        out: Dict[str, Any] = {
            "evaluable": True, "both_through": both, "one_through": one,
            "neither_through": not both and not one,
            "pair_cost": str(self.pair_cost),
            "edge_per_contract": str(D(1) - self.pair_cost),
            "clears_fee_floor": bool((D(1) - self.pair_cost) >= self.MIN_EDGE),
        }
        if both:
            cost = (self.qty * up.own_px + self.qty * dn.own_px
                    + self.fee(self.qty, up.own_px)
                    + self.fee(self.qty, dn.own_px))
            out["cost_if_both_filled"] = str(cost)
            out["pnl_if_up_settles"] = str(self.qty - cost)
            out["pnl_if_dn_settles"] = str(self.qty - cost)
            out["both_outcomes_positive"] = bool(self.qty - cost > 0)
        elif one:
            leg = up if up.state == THROUGH else dn
            cost = self.qty * leg.own_px + self.fee(self.qty, leg.own_px)
            out["one_sided_side"] = leg.side
            out["cost_if_one_filled"] = str(cost)
            if settle_up is None:
                out["one_sided_pnl"] = None
                out["one_sided_pnl_note"] = (
                    "unscored: needs the venue's own binary resolution")
            else:
                wins = (leg.side == FM.UP) == bool(settle_up)
                out["one_sided_pnl"] = str(
                    (self.qty if wins else D(0)) - cost)
                out["one_sided_won"] = bool(wins)
        return out

    def report(self, settle_up: Optional[bool] = None) -> Dict[str, Any]:
        gap = None
        if self.legs:
            a = self.legs[FM.UP].first_through_t
            b = self.legs[FM.DOWN].first_through_t
            if a is not None and b is not None:
                gap = round(abs(a - b), 3)
        first = None
        if self.legs:
            a = self.legs[FM.UP].first_through_t
            b = self.legs[FM.DOWN].first_through_t
            if a is not None and (b is None or a <= b):
                first = FM.UP
            elif b is not None:
                first = FM.DOWN
        return {"LABEL": "L5_TRADE_THROUGH", "slug": self.slug,
                "depth_ticks": self.depth_ticks,
                "qty_per_leg": str(self.qty),
                "window_start": self.window_start,
                "cancellation_deadline": self.deadline(),
                "placed_t": self.placed_t,
                "bid_at_placement": (None if self.bid_at_placement is None
                                     else str(self.bid_at_placement)),
                "spread_at_placement": (
                    None if self.spread_at_placement is None
                    else str(self.spread_at_placement)),
                "refusal": self.refusal,
                "prints_consumed": self.prints,
                "legs": ([l.row() for l in self.legs.values()]
                         if self.legs else []),
                "first_leg_through": first,
                "seconds_between_legs": gap,
                "settlement": self.settlement(settle_up),
                "labels": ("OBSERVED trade-through, never a fill. A print AT "
                           "our price is `touched` and stays queue-dependent; "
                           "only a print strictly THROUGH it is the necessary "
                           "condition for a maker fill, and queue-ahead "
                           "unmeasured means it is never the sufficient one. "
                           "The touch baseline (depth 0) is kept for "
                           "comparison and its modelled fills are NOT "
                           "executable profits.")}


def wilson_interval(k: int, n: int, z: D = D("1.96")) -> Dict[str, Any]:
    """95% confidence interval for a proportion, small-sample safe.

    Reported because 5/8 and 500/800 are the same point estimate and
    completely different evidence.
    """
    if n <= 0:
        return {"k": k, "n": n, "p": None, "lo": None, "hi": None}
    from decimal import getcontext
    getcontext().prec = 28
    p = D(k) / D(n); nn = D(n); z2 = z * z
    denom = D(1) + z2 / nn
    centre = (p + z2 / (D(2) * nn)) / denom
    inner = (p * (D(1) - p) / nn + z2 / (D(4) * nn * nn))
    rad = (z * inner.sqrt()) / denom
    lo = max(D(0), centre - rad); hi = min(D(1), centre + rad)
    return {"k": k, "n": n, "p": str(round(p, 4)),
            "lo": str(round(lo, 4)), "hi": str(round(hi, 4)),
            "method": "Wilson score, z=1.96"}


def aggregate(reports: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Per-depth frequencies WITH the market-level rows preserved.

    THRESHOLDS ARE NOT FITTED HERE. This counts; it does not choose a depth,
    a spread band or a time of day. Anything selected on these windows must
    be validated on different ones.
    """
    by_depth: Dict[int, Dict[str, Any]] = {}
    for r in reports:
        d = int(r.get("depth_ticks", -1))
        s = r.get("settlement") or {}
        cell = by_depth.setdefault(d, {"n": 0, "both": 0, "one": 0,
                                       "neither": 0, "gaps": [],
                                       "spreads": {}, "markets": []})
        if not s.get("evaluable"):
            continue
        cell["n"] += 1
        if s.get("both_through"):
            cell["both"] += 1
        elif s.get("one_through"):
            cell["one"] += 1
        else:
            cell["neither"] += 1
        g = r.get("seconds_between_legs")
        if g is not None:
            cell["gaps"].append(g)
        sp = r.get("spread_at_placement")
        if sp is not None:
            cell["spreads"][sp] = cell["spreads"].get(sp, 0) + 1
        cell["markets"].append({
            "slug": r.get("slug"), "spread": sp,
            "both": bool(s.get("both_through")),
            "one": bool(s.get("one_through")),
            "first_leg": r.get("first_leg_through"),
            "gap_s": g, "pair_cost": s.get("pair_cost"),
            "clears_fee_floor": s.get("clears_fee_floor"),
            "one_sided_pnl": s.get("one_sided_pnl")})
    out: Dict[str, Any] = {"LABEL": "L5_TRADE_THROUGH_SUMMARY", "depths": {}}
    for d, c in sorted(by_depth.items()):
        gaps = sorted(c["gaps"])
        out["depths"][str(d)] = {
            "n_windows": c["n"],
            "both_through": wilson_interval(c["both"], c["n"]),
            "one_through": wilson_interval(c["one"], c["n"]),
            "neither_through": wilson_interval(c["neither"], c["n"]),
            "seconds_between_legs": {
                "n": len(gaps),
                "min": (gaps[0] if gaps else None),
                "median": (gaps[len(gaps) // 2] if gaps else None),
                "max": (gaps[-1] if gaps else None)},
            "spread_regimes": c["spreads"],
            "markets": c["markets"]}
    out["labels"] = ("counts only; no threshold, depth or regime is selected "
                     "here. Trade-through is never a confirmed fill.")
    return out
