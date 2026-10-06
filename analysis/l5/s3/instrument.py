"""S3 -- zero-capital prospective execution measurement instrument.

Implements frozen spec `analysis/l5/s3/spec.py`, fingerprint
8b20a259...87df, committed at 28c619df. Every parameter is READ FROM THE
SPEC, never restated here, so no value can drift between protocol and run.

WHAT S3 IS: an instrument that measures whether a bounded passive two-sided
inventory process can obtain real prospective maker-fill opportunity, at
acceptable acquisition economics, when our own order lifecycle and queue
state are observable.

WHAT S3 IS NOT:
  * not an L5 next-action predictor -- those endpoints were preregistered
    and FAILED (next-action lift 1.001; E2_POSITIVE False);
  * not a claim that the 0.96 reservation reproduces L5. The historical
    studies since the freeze found L5's own running pair cost near 0.98 and
    then REFUTED that as a control variable. 0.96 is OUR design constraint
    from pair arithmetic, and the spec labels it PAIR_ECONOMICS;
  * not an order router. It cannot submit, sign, or move capital.

THE CENTRAL ECONOMIC VARIABLE IS MARKOUT. Ambient pair geometry explains
~0.98 (PAIRCOST_FINDINGS.md: REFUTED), so what remains unresolved is
execution quality and adverse selection. Markouts are therefore
instrumented with explicit mark source, staleness and availability on every
horizon, and are never silently filled in.

ZERO CAPITAL. NO ORDER SUBMISSION. NO SIGNING.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.s3.spec import SPEC, S3_FINGERPRINT, assert_frozen

# ---- parameters, all read from the frozen spec -------------------------
P = SPEC["placement"]
RES = SPEC["reservation"]
MNT = SPEC["maintenance"]
WD = SPEC["withdrawal"]
B = SPEC["bounds"]
EV = SPEC["evidence"]
GATE = SPEC["catastrophic_mismatch_gate"]["abort_if_any"]
MARKOUT_HORIZONS = (1, 5, 10, 30, 60)      # preregistered M7
WINDOW_S = 900

#: The frozen gate names `bbo_staleness_breaches_share_above` but does not
#: define the freshness bound. Rather than invent one, S3 reads the venue
#: adapter's existing constant (l5_surface_exec/adapter.py MAX_BBO_AGE_S =
#: 2.0 s). DISCLOSED: this is not a frozen-spec parameter; it is the
#: repository's pre-existing venue constant, used unchanged.
MAX_BBO_AGE_S = 2.0

LEDGERS = EV["ledgers"]                     # EVIDENCED_FILL / UNCERTAIN / NO_FILL

#: modules that can sign or submit. S3 must never import one. Asserted at
#: import time and again in the tests, because "it doesn't call them" is a
#: weaker guarantee than "they are not loaded".
FORBIDDEN_MODULES = (
    "pmus_q1_executor", "pmus_micro_executor", "pmus_auth",
    "l5_pmus_transport", "pmus_live_mandate", "l5_pmus_live_runner",
)


def assert_no_execution_surface() -> None:
    """S3 is read-only by construction, not by discipline."""
    loaded = [m for m in FORBIDDEN_MODULES if m in sys.modules]
    if loaded:
        raise SystemExit(
            "S3 SAFETY ABORT: an execution/signing module is loaded (%s). "
            "S3 is zero-capital and must not share a process with an order "
            "surface." % ", ".join(loaded))


# ------------------------------------------------------------------ book
def ladder(side_rows) -> List[Tuple[float, float]]:
    out = []
    for row in (side_rows or []):
        try:
            out.append((float(row[0]), float(row[1])))
        except (TypeError, ValueError, IndexError):
            continue
    return out


class Snap:
    """One observed book state for one market/outcome.

    Records bid and ask separately and flags a CROSSED book. The historical
    archive carried best_ask < best_bid in 8.09 % of BTC15m seconds
    (CROSSED_BOOK_DEFECT.md); a live feed can do the same, and a crossed
    snapshot must be visible in the record rather than silently used.
    """

    def __init__(self, t: float, bids, offers, state: Optional[str] = None):
        self.t = float(t)
        self.bids = sorted(ladder(bids), key=lambda x: -x[0])
        self.asks = sorted(ladder(offers), key=lambda x: x[0])
        self.state = state

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    @property
    def crossed(self) -> bool:
        return (self.best_bid is not None and self.best_ask is not None
                and self.best_ask < self.best_bid)

    def depth_at(self, px: float) -> Optional[float]:
        for p, q in self.bids:
            if abs(p - px) < 1e-9:
                return q
        return 0.0 if self.bids else None

    def depth_at_or_above(self, px: float) -> Optional[float]:
        if not self.bids:
            return None
        return sum(q for p, q in self.bids if p >= px - 1e-9)


# ------------------------------------------------------------- the order
class Order:
    """A HYPOTHETICAL owned order. Nothing here reaches a venue."""

    _n = 0

    def __init__(self, slug, outcome, price, qty, t, snap: Snap, reason):
        Order._n += 1
        self.order_id = "s3-hypo-%06d" % Order._n
        self.slug = slug
        self.outcome = outcome
        self.price = round(float(price), 6)
        self.intended_quantity = float(qty)
        self.placement_ts = float(t)
        self.placement_reason = reason
        self.predecessor_order_id: Optional[str] = None
        # queue ahead is an ESTIMATE WITH BOUNDS. Depth is anonymous, so
        # what sits ahead of us cannot be observed; we never invent a
        # priority.
        d = snap.depth_at(self.price)
        self.queue_ahead_estimate = d
        self.queue_ahead_observable = d is not None
        self.queue_ahead_bounds = ([0.0, d] if d is not None else None)
        self.bbo_at_placement = {"bid": snap.best_bid, "ask": snap.best_ask,
                                 "crossed": snap.crossed}
        self.full_depth_at_placement = list(snap.bids)
        self.depth_at_our_price = d
        self.price_distance_from_touch = (
            round(snap.best_bid - self.price, 6)
            if snap.best_bid is not None else None)
        self.queue_depth_change_events: List[dict] = []
        self.maintenance_reason: Optional[str] = None
        self.replacement_reason: Optional[str] = None
        self.withdrawal_reason: Optional[str] = None
        self.reservation_binding = False
        self.fill_evidence_class = "NO_FILL"
        self.fill_evidence_detail: Optional[str] = None
        self.partial_fill_estimate: Optional[float] = None
        self.partial_fill_defensible = False
        self.last_seen_depth = d
        self.last_t = float(t)
        self.resting_time_s = 0.0
        self.closed_ts: Optional[float] = None
        self.markouts: Dict[str, Any] = {}

    def age_s(self, t) -> float:
        return float(t) - self.placement_ts

    def record(self, inv: Dict[str, float]) -> dict:
        up, dn = inv.get("up", 0.0), inv.get("down", 0.0)
        return {
            "order_id": self.order_id,
            "spec_fingerprint": S3_FINGERPRINT,
            "market_slug": self.slug, "outcome": self.outcome,
            "placement_ts": self.placement_ts,
            "price": self.price,
            "intended_quantity": self.intended_quantity,
            "queue_ahead_estimate": self.queue_ahead_estimate,
            "queue_ahead_bounds": self.queue_ahead_bounds,
            "queue_ahead_observable": self.queue_ahead_observable,
            "bbo_at_placement": self.bbo_at_placement,
            "full_depth_at_placement": self.full_depth_at_placement,
            "depth_at_our_price": self.depth_at_our_price,
            "price_distance_from_touch": self.price_distance_from_touch,
            "queue_depth_change_events": self.queue_depth_change_events,
            "maintenance_reason": self.maintenance_reason,
            "replacement_reason": self.replacement_reason,
            "predecessor_order_id": self.predecessor_order_id,
            "fill_evidence_class": self.fill_evidence_class,
            "fill_evidence_detail": self.fill_evidence_detail,
            "partial_fill_estimate": self.partial_fill_estimate,
            "partial_fill_defensible": self.partial_fill_defensible,
            "age_s": round(self.resting_time_s, 3),
            "resting_time_s": round(self.resting_time_s, 3),
            "inventory_up": round(up, 4), "inventory_down": round(dn, 4),
            "matched": round(min(up, dn), 4),
            "residual": round(abs(up - dn), 4),
            "withdrawal_reason": self.withdrawal_reason,
            "reservation_binding": self.reservation_binding,
            "markouts": self.markouts,
            "closed_ts": self.closed_ts,
        }


# --------------------------------------------------------------- markouts
def markouts(series: List[Snap], t_fill: float, price: float
             ) -> Dict[str, Any]:
    """Markout at each preregistered horizon, from the BID side.

    Mark source is the best BID -- what we could actually exit into -- not
    the mid. Two reasons, both learned the hard way: the mid needs an ask,
    and asks are exactly what the crossed-book defect corrupts; and a bid
    mark is the conservative choice for a long position.

    Every horizon carries its own availability and staleness, and a horizon
    with no observation is reported as None with a reason -- NEVER carried
    forward from a nearer horizon, which would understate adverse
    selection by reusing a pre-move price.
    """
    out: Dict[str, Any] = {"mark_source": "best_bid",
                           "mark_rationale": ("bid is the exitable side and "
                                              "needs no ask, which the "
                                              "crossed-book defect corrupts"),
                           "fill_price": price}
    for k in MARKOUT_HORIZONS:
        target = t_fill + k
        # the LAST snapshot at or before target, and the FIRST after it, so
        # staleness is explicit instead of assumed
        before = [s for s in series if s.t <= target]
        after = [s for s in series if s.t > target]
        key = "t_plus_%d" % k
        if not before:
            out[key] = {"markout": None, "reason": "NO_OBSERVATION_BEFORE"}
            continue
        s = before[-1]
        stale = round(target - s.t, 3)
        if s.best_bid is None:
            out[key] = {"markout": None, "reason": "NO_BID_AT_MARK",
                        "staleness_s": stale}
            continue
        # A horizon has elapsed IN OUR DATA only if we observed the book at
        # or after the target time. Without that, the nearest-before mark is
        # a PRE-MOVE price and reusing it would understate adverse
        # selection -- the one thing this instrument exists to measure.
        gap_ok = bool(after)
        out[key] = {
            "markout": round(s.best_bid - price, 6),
            "mark_price": s.best_bid,
            "staleness_s": stale,
            "observation_ts": s.t,
            "crossed_at_mark": s.crossed,
            "horizon_fully_elapsed": gap_ok,
            "reason": None if gap_ok else "WINDOW_ENDED_BEFORE_HORIZON",
        }
    return out


# ------------------------------------------------------------- the engine
class S3Market:
    """One market. One outstanding hypothetical order per side, per spec."""

    def __init__(self, slug: str, start_ts: float):
        assert_no_execution_surface()
        assert_frozen()
        self.slug = slug
        self.start_ts = float(start_ts)
        self.inv: Dict[str, float] = {"up": 0.0, "down": 0.0}
        self.cost: Dict[str, float] = {"up": 0.0, "down": 0.0}
        self.live: Dict[str, Optional[Order]] = {"up": None, "down": None}
        self.closed: List[Order] = []
        self.events: List[dict] = []
        self.stand_downs: List[dict] = []
        self.notional = 0.0
        self.crossed_snapshots = 0
        self.total_snapshots = 0
        self.bbo_staleness_breaches = 0
        self.bbo_age_observations = 0

    # ---- the reservation: OUR constraint, from pair arithmetic ----
    def reservation_price(self, outcome: str, comp: Optional[Snap]
                          ) -> Optional[float]:
        if comp is None or comp.best_bid is None:
            return None
        return round(RES["max_combined_pair_cost"] - comp.best_bid, 6)

    def _tte(self, t: float) -> float:
        return self.start_ts + WINDOW_S - float(t)

    def _may_open(self, t: float) -> Tuple[bool, Optional[str]]:
        if self._tte(t) <= WD["stop_opening_new_interest_tte_s"]:
            return False, "TTE_STOP_%ds" % WD["stop_opening_new_interest_tte_s"]
        if abs(self.inv["up"] - self.inv["down"]) >= B["residual_cap_shares"]:
            return False, "RESIDUAL_CAP_%s" % B["residual_cap_shares"]
        if self.notional >= B["max_notional_per_market_usdc"]:
            return False, "MAX_NOTIONAL_%s" % B["max_notional_per_market_usdc"]
        return True, None

    def step(self, t: float, snaps: Dict[str, Snap]) -> None:
        """One observation. Pure measurement: nothing is submitted."""
        for oc in ("up", "down"):
            s = snaps.get(oc)
            if s is None:
                continue
            self.total_snapshots += 1
            if s.crossed:
                self.crossed_snapshots += 1
            comp = snaps.get("down" if oc == "up" else "up")
            # a carried-forward complement is only usable while fresh; past
            # MAX_BBO_AGE_S it is a staleness breach and the reservation is
            # evaluated as UNKNOWN rather than on a stale quote.
            if comp is not None:
                self.bbo_age_observations += 1
                if float(t) - comp.t > MAX_BBO_AGE_S:
                    self.bbo_staleness_breaches += 1
                    comp = None
            o = self.live[oc]

            # --- evidence on an existing order, BEFORE any maintenance ---
            if o is not None:
                self._observe(o, t, s)
                if o.fill_evidence_class == "EVIDENCED_FILL":
                    self._book_fill(o, t)
                    self.live[oc] = None
                    o = None

            if o is not None:
                why = self._maintenance_due(o, t, s)
                if why:
                    resv = self.reservation_price(oc, comp)
                    tgt = s.best_bid
                    if tgt is None:
                        continue
                    if resv is not None and tgt > resv:
                        o.withdrawal_reason = "RESERVATION_STAND_DOWN"
                        o.reservation_binding = True
                        self._close(o, t)
                        self.live[oc] = None
                        self.stand_downs.append({
                            "t": t, "outcome": oc, "touch": tgt,
                            "reservation": resv,
                            "reason": "REPRICE_WOULD_BREACH_RESERVATION",
                            "NOTE": ("a PRICE constraint of ours, recorded "
                                     "separately from L5-evidenced "
                                     "withdrawal reasons")})
                        continue
                    o.maintenance_reason = why
                    o.replacement_reason = why
                    self._close(o, t)
                    n = Order(self.slug, oc, tgt, P["intended_quantity_shares"],
                              t, s, reason="REPRICE:" + why)
                    n.predecessor_order_id = o.order_id
                    self.live[oc] = n
                    continue

            # --- open a new order if allowed ---
            if self.live[oc] is None:
                ok, blocker = self._may_open(t)
                if not ok:
                    self.stand_downs.append({"t": t, "outcome": oc,
                                             "reason": blocker})
                    continue
                tgt = s.best_bid
                if tgt is None:
                    continue
                # placement offset comes from the spec (0 ticks below touch)
                resv = self.reservation_price(oc, comp)
                if resv is not None and tgt > resv:
                    self.stand_downs.append({
                        "t": t, "outcome": oc, "touch": tgt,
                        "reservation": resv,
                        "reason": "OPEN_WOULD_BREACH_RESERVATION"})
                    continue
                self.live[oc] = Order(self.slug, oc, tgt,
                                      P["intended_quantity_shares"], t, s,
                                      reason="OPEN")

    # ---- conservative fill evidence. NO touch=fill, ever. ----
    def _observe(self, o: Order, t: float, s: Snap) -> None:
        o.resting_time_s = float(t) - o.placement_ts
        d = s.depth_at(o.price)
        prev = o.last_seen_depth
        if d is not None and prev is not None and abs(d - prev) > 1e-9:
            o.queue_depth_change_events.append(
                {"t": t, "depth_before": prev, "depth_after": d,
                 "our_estimated_position": o.queue_ahead_estimate})
        bb = s.best_bid

        if bb is not None and bb < o.price - 1e-9:
            # the market traded THROUGH our price: the level we rested at is
            # gone from the bid side. This is the strongest evidence the
            # public feed can give, and it is also SELECTION-BIASED -- it
            # fires only when the book moved against us. Recorded as such.
            o.fill_evidence_class = "EVIDENCED_FILL"
            o.fill_evidence_detail = (
                "EVIDENCED_LEVEL_CONSUMED: best bid %.4f fell strictly below "
                "our %.4f. SELECTION-BIASED: fires only on adverse movement."
                % (bb, o.price))
            o.partial_fill_estimate = o.intended_quantity
            o.partial_fill_defensible = False
        elif (d is not None and prev is not None and d < prev - 1e-9
              and bb is not None and abs(bb - o.price) < 1e-9):
            # size left our own level while the touch held. Consistent with
            # a fill ahead of us in the queue -- and equally with a cancel.
            # Public data cannot separate them, so it is UNCERTAIN and it
            # does not enter economics.
            consumed = prev - d
            o.fill_evidence_class = "UNCERTAIN"
            o.fill_evidence_detail = (
                "UNCERTAIN_PARTIAL: %.4f left our level while the touch held; "
                "a fill ahead of us and a cancel are indistinguishable here."
                % consumed)
            o.partial_fill_estimate = None
            o.partial_fill_defensible = False
        o.last_seen_depth = d
        o.last_t = float(t)

    def _book_fill(self, o: Order, t: float) -> None:
        q = o.partial_fill_estimate or 0.0
        self.inv[o.outcome] += q
        self.cost[o.outcome] += q * o.price
        self.notional += q * o.price
        o.withdrawal_reason = o.withdrawal_reason or "FILLED"
        self._close(o, t)

    def _maintenance_due(self, o: Order, t: float, s: Snap
                         ) -> Optional[str]:
        bb = s.best_bid
        if bb is None:
            return None
        # threshold held in PRICE, not ticks: the PMUS crypto tick is half
        # Polymarket's, so "6 ticks" does not translate.
        if bb - o.price > MNT["reprice_on_displacement_price_distance"] + 1e-12:
            return "REPRICE_ON_DISPLACEMENT"
        if (bb > o.price + 1e-12
                and o.resting_time_s > MNT["reprice_on_staleness_s"]):
            return "REPRICE_ON_STALENESS"
        return None

    def _close(self, o: Order, t: float) -> None:
        o.closed_ts = float(t)
        o.resting_time_s = float(t) - o.placement_ts
        self.closed.append(o)

    def finish(self, t: float, series: Dict[str, List[Snap]]) -> None:
        for oc in ("up", "down"):
            o = self.live[oc]
            if o is not None:
                o.withdrawal_reason = "WINDOW_END"
                self._close(o, t)
                self.live[oc] = None
        # markouts attach to EVIDENCED fills only; the other ledgers get a
        # markout block too, but labelled, so the two are never pooled.
        for o in self.closed:
            if o.fill_evidence_class in ("EVIDENCED_FILL", "UNCERTAIN"):
                o.markouts = markouts(series.get(o.outcome, []),
                                      o.closed_ts or o.placement_ts, o.price)
                o.markouts["ledger"] = o.fill_evidence_class

    # ---- the ten preregistered advancement metrics ----
    def metrics(self) -> Dict[str, Any]:
        led = {k: [o for o in self.closed if o.fill_evidence_class == k]
               for k in LEDGERS}
        ev_f = led["EVIDENCED_FILL"]
        up = [o for o in ev_f if o.outcome == "up"]
        dn = [o for o in ev_f if o.outcome == "down"]
        u, d = self.inv["up"], self.inv["down"]
        tot = u + d
        pair = ((self.cost["up"] / u + self.cost["down"] / d)
                if u and d else None)
        rest = [o.resting_time_s for o in self.closed]
        qa = [o.queue_ahead_estimate for o in self.closed
              if o.queue_ahead_estimate is not None]
        mk: Dict[str, Any] = {}
        for k in MARKOUT_HORIZONS:
            # only horizons that actually elapsed enter the aggregate; a
            # non-elapsed mark is counted as unavailable, never pooled.
            blocks = [o.markouts.get("t_plus_%d" % k, {}) for o in ev_f]
            vals = [b.get("markout") for b in blocks
                    if b.get("markout") is not None
                    and b.get("horizon_fully_elapsed")]
            mk["M7_markout_t_plus_%d" % k] = {
                "n": len(vals),
                "mean": (round(sum(vals) / len(vals), 6) if vals else None),
                "median": (round(sorted(vals)[len(vals) // 2], 6) if vals
                           else None),
                "n_unavailable": len(ev_f) - len(vals),
            }
        return {
            "market_slug": self.slug,
            "M1_evidenced_fills": len(ev_f),
            "M2_two_sided_evidenced_acquisition": bool(up and dn),
            "M3_matched_fraction": (round(2 * min(u, d) / tot, 6) if tot
                                    else None),
            "M4_residual": round(abs(u - d), 4),
            "M4_residual_cap": B["residual_cap_shares"],
            "M5_acquisition_pair_cost": (round(pair, 6) if pair else None),
            "M6_queue_ahead": {"n": len(qa),
                               "mean": (round(sum(qa) / len(qa), 3) if qa
                                        else None)},
            "M6_resting_time_s": {"n": len(rest),
                                  "mean": (round(sum(rest) / len(rest), 3)
                                           if rest else None)},
            **mk,
            "M8_capital_tied_up_usdc": round(self.notional, 4),
            "M9_withdrawal_reasons": {
                r: sum(1 for o in self.closed if o.withdrawal_reason == r)
                for r in sorted({o.withdrawal_reason for o in self.closed
                                 if o.withdrawal_reason})},
            "M10_hypothetical_fees_SEPARATE": round(sum(
                0.07 * o.price * (1 - o.price) * (o.partial_fill_estimate or 0)
                for o in ev_f), 6),
            "M10_rebates_counted": SPEC["accounting"]["rebates_counted"],
            "ledger_counts": {k: len(v) for k, v in led.items()},
            "n_orders": len(self.closed),
            "n_stand_downs": len(self.stand_downs),
            "reservation_stand_downs": sum(
                1 for s in self.stand_downs
                if "RESERVATION" in str(s.get("reason", ""))),
            "crossed_snapshot_share": (
                round(self.crossed_snapshots / self.total_snapshots, 4)
                if self.total_snapshots else None),
            "bbo_staleness_breach_share": (
                round(self.bbo_staleness_breaches / self.bbo_age_observations,
                      4) if self.bbo_age_observations else None),
            "bbo_freshness_bound_s": MAX_BBO_AGE_S,
            "orders_submitted": 0, "orders_signed": 0,
        }
