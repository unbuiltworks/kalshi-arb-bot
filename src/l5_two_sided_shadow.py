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

import json
import os

from decimal import Decimal as D, ROUND_FLOOR
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import l5_maker_fill_model as FM

TICK = D("0.01")

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
                 policy: str = COMPLEMENT_ONLY):
        self.slug, self.thresh, self.envelope = slug, thresh, envelope
        self.policy = policy
        self.retained_legs = 0
        self.replaced_legs = 0
        self.qty = {FM.UP: D(0), FM.DOWN: D(0)}
        self.cost = {FM.UP: D(0), FM.DOWN: D(0)}
        self.qty_opt = {FM.UP: D(0), FM.DOWN: D(0)}
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

    # ----------------------------------------------------------- tick
    def step(self, bid: D, ask: D, bid_sh: Optional[D], ask_sh: Optional[D],
             prints: Sequence[Mapping[str, Any]], now: float,
             window_start: Optional[float]) -> None:
        self._step(bid, ask, bid_sh, ask_sh, prints, now, window_start)
        # AFTER quoting as well as before: a window that posts once and never
        # re-quotes would otherwise report a peak exposure of zero.
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
            if self.policy == SKEWED:
                self.retained_legs += 1
                continue
            self.rest.pop(side, None)
            self.cancels += 1
            self.replaced_legs += 1
        exp = self.exposure()
        if exp > self.peak_exposure:
            self.peak_exposure = exp
        room = self.envelope - exp
        up_px, dn_px = bid, D(1) - ask
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
            "legs_retained_through_a_touch_move": self.retained_legs,
            "legs_replaced_on_a_touch_move": self.replaced_legs,
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


class ShadowLedger:
    """Durable, shadow-only record of parked and finalized windows.

    SEPARATE FROM THE INVENTORY LEDGER ON PURPOSE. Nothing here is read by any
    gate, any limit or any accounting path; it exists so that scoring survives
    a restart and so that a window cannot be scored twice. It holds modelled
    numbers only.
    """

    def __init__(self, path: str, *, max_pending: int = 16):
        self.path = path
        self.max_pending = int(max_pending)
        self.state: Dict[str, Any] = {"pending": {}, "finalized": {}}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r") as fh:
                got = json.load(fh)
            if isinstance(got, Mapping):
                self.state = {"pending": dict(got.get("pending") or {}),
                              "finalized": dict(got.get("finalized") or {})}
        except FileNotFoundError:
            pass
        except Exception:                                      # noqa: BLE001
            # a corrupt shadow sidecar must never stop the executor; it is
            # modelled data, and starting empty loses evidence, not money.
            self.state = {"pending": {}, "finalized": {}}

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.state, fh)
        os.replace(tmp, self.path)

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
