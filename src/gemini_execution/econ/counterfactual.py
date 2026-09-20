"""BOTH PATHS AT EVERY VETO, scored on inventory-state economics.

WHY PAIR BASIS IS NOT THE SCORE. `L5_OPERATING_MODEL.md` 2d: 42.75% of L5's
33,765 historical COMPLETEs were above $1.00, and when the bands were scored by
what the shares actually earned the 1.00-1.02 cohort was the BEST performer
(+98 / +61 bp) while `<= 0.96` was NEGATIVE (-28 / -119 bp). A pair at 1.04 or
1.13 is locally negative in isolation; that is arithmetic and it is not in
dispute. What is in dispute is whether completing it was WRONG, and the local
basis cannot answer that, because completing also

  * RETIRES A DIRECTIONAL RESIDUAL whose own outcome is unknown and unbounded,
  * and frees the state to recycle.

So the score here is TOTAL INVENTORY-STATE ECONOMICS on both branches:

    ALLOW   matched value locked  +  residual left over, marked  -  fees
    VETO    (no new matched)      +  the WHOLE residual, marked  -  fees

and the comparison is their difference, tracked forward.

WHAT IS COMPUTABLE HERE AND WHAT IS NOT -- stated so the gap is not discovered
later as a surprise. Forking the INVENTORY is exact: both branches' positions,
costs and marks follow from the fill that did or did not happen. Forking the
TRAJECTORY is not: in the ALLOW branch the policy would have gone on to request
different rungs from a different inventory, and simulating that would mean
running a second full controller whose divergence compounds every cycle.

So this module reports:

  * both branches' inventory economics, marked at every subsequent cycle and
    at window end  -- EXACT;
  * residual retired by completing (veto_residual - allow_residual) -- EXACT;
  * subsequent recycling on the VETO path -- OBSERVED, because that is the
    path the arm actually walks;
  * subsequent recycling on the ALLOW path -- NOT OBSERVABLE from this arm,
    and explicitly emitted as null rather than estimated. The control service
    runs the allow-style policy unmodified, so the window-level comparison
    against IT is the evidence for that term. That is a further reason the
    control must keep running untouched.

THREE LAYERS, KEPT APART, BECAUSE THEY ARE NOT THE SAME KIND OF NUMBER:

  LAYER 1  immediate_inventory_value   the two branches valued AT THE FORK
                                       INSTANT, on the book that existed when
                                       the decision was made
  LAYER 2  counterfactual_mtm          the two branches re-valued at FIXED
                                       HORIZONS on the same subsequent tape.
                                       A bid-marked residual lives HERE and
                                       nowhere else.
  LAYER 3  settlement_value            only when the venue has said which leg
                                       won. This is the only layer that can
                                       ever be called realized, and only once
                                       the cash is confirmed.

A BID-MARKED RESIDUAL IS COUNTERFACTUAL MTM, NEVER REALIZED P&L. It is what the
position could have been sold for at a moment we did not sell it, on a venue
where we placed no order at all. Every layer-2 row carries `stage: shadow` and
`kind: COUNTERFACTUAL_MTM`; only layer 3 with a confirmed settlement carries
`stage: settled`, and even then `realized` is withheld until venue-confirmed
cash.

MARKS ARE MARKS. A residual is marked at the price it could actually be sold
into -- the bid -- never the mid, and never the settlement value of the side we
happen to hold.

SIGN CONVENTION. `allow_minus_veto` is ALLOW minus VETO on the same layer.
POSITIVE means COMPLETING would have produced better total inventory economics
than refusing -- i.e. the risk overlay COST us on that fork. Negative means the
overlay helped. Stated here so a positive number is never read the other way.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

from src.gemini_execution.econ import veto as VT

SCHEMA = "gemini_econ_veto_counterfactual/v1"

ALLOW, VETO = "ALLOW_COMPLETE", "VETO_COMPLETE"

LABELS = (
    "SHADOW/HYPOTHETICAL. Neither branch was traded; nothing here is "
    "submitted, filled, settled, credited or realized.",
    "A residual is marked at the BID -- the price it could actually be sold "
    "into -- never the mid and never the settlement value of the held side.",
    "The ALLOW branch's subsequent RECYCLING is not observable from this arm "
    "and is emitted as null, never estimated. The untouched control runs the "
    "allow-style policy and is the evidence for that term.",
)

#: fixed horizons for layer 2, in seconds from the fork. Preregistered.
MTM_HORIZONS_S = (30, 60, 120, 300)

IMMEDIATE = "immediate_inventory_value"
MTM = "counterfactual_mtm"
SETTLEMENT = "settlement_value"

SIGN_NOTE = (
    "allow_minus_veto > 0 means COMPLETING would have produced better total "
    "inventory economics than refusing -- the risk overlay COST us on that "
    "fork. < 0 means the overlay helped.")

#: every candidate feature captured AT THE FORK, so a model can be fitted
#: later from prospective data rather than re-derived from a tape we no longer
#: hold. Nothing is fitted now.
FORK_FEATURES = (
    "tte_s", "held_leg", "held_qty", "held_avg_cost", "complement_price",
    "ceiling", "required_gap_ticks", "spread", "mid", "book_imbalance",
    "side_alternation", "same_side_run_len", "prints_per_min", "qty_per_min",
    "largest_print_share", "n_prints", "flow_window_used_s",
    "time_since_first_fill_s", "window_mode", "cohort",
)

_ids = itertools.count(1)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _median_of(xs):
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def _bid_for(book: Mapping[str, Any], leg: str) -> Optional[D]:
    side = (book or {}).get(str(leg).lower()) or {}
    return _dec(side.get("bid"))


@dataclass
class Fork:
    """One veto decision, carried forward on both branches."""

    fork_id: int
    symbol: str
    cohort: str
    t0: float
    tte0: Optional[float]
    held_leg: str
    complement_leg: str
    held_qty: D
    held_avg_cost: D
    complement_price: D
    ceiling: Optional[str]
    regime: VT.FeeRegime = VT.VERIFIED_REGIME
    marks: List[Dict[str, Any]] = field(default_factory=list)
    veto_path_fills_after: int = 0
    resolved: Optional[Dict[str, Any]] = None
    #: LAYER 1, computed once on the book that existed at the decision
    immediate: Optional[Dict[str, Any]] = None
    #: LAYER 2, one row per horizon, each scored exactly once
    mtm: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: every candidate feature at the fork. Captured, never fitted.
    features: Dict[str, Any] = field(default_factory=dict)

    # ---- the two branches' static economics ---------------------------
    def allow_static(self) -> Dict[str, Any]:
        """What completing locks in, and what it leaves behind."""
        q = min(self.held_qty, self.held_qty)     # complement sized to the held leg
        econ = VT.pair_economics(
            held_qty=self.held_qty, held_avg_cost=self.held_avg_cost,
            complement_qty=q, complement_price=self.complement_price,
            regime=self.regime)
        return {
            "matched_qty": econ["matched_qty"],
            "pair_basis": econ["pair_basis_before_fees"],
            "locked_value": econ["net_pair_economics"],
            "fees": econ["fees_total"],
            "residual_qty": "0",
            "residual_leg": None,
        }

    def veto_static(self) -> Dict[str, Any]:
        return {
            "matched_qty": "0", "pair_basis": None, "locked_value": "0",
            "fees": "0",
            "residual_qty": str(self.held_qty),
            "residual_leg": self.held_leg,
        }

    # ---- LAYER 1: the fork instant ------------------------------------
    def set_immediate(self, *, book: Mapping[str, Any],
                      features: Optional[Mapping[str, Any]] = None
                      ) -> Dict[str, Any]:
        """Value both branches on the book that existed AT THE DECISION."""
        a, v = self.allow_static(), self.veto_static()
        bid = _bid_for(book, self.held_leg)
        allow_total = _dec(a["locked_value"]) or D("0")
        veto_total = (None if bid is None
                      else self.held_qty * (bid - self.held_avg_cost))
        self.features = {k: (features or {}).get(k) for k in FORK_FEATURES}
        self.immediate = {
            "layer": IMMEDIATE, "kind": "IMMEDIATE_INVENTORY_VALUE",
            "stage": "shadow", "t": self.t0, "tte_s": self.tte0,
            "mark_bid": None if bid is None else str(bid),
            ALLOW: {**a, "total_inventory_economics": str(allow_total)},
            VETO: {**v, "residual_mark": (None if veto_total is None
                                          else str(veto_total)),
                   "total_inventory_economics": (None if veto_total is None
                                                 else str(veto_total))},
            "allow_minus_veto": (None if veto_total is None
                                 else str(allow_total - veto_total)),
            "sign_note": SIGN_NOTE,
        }
        return self.immediate

    # ---- LAYER 2: fixed-horizon MTM on the subsequent tape ------------
    def advance(self, *, book: Mapping[str, Any], t: float) -> List[str]:
        """Score any horizon that has now elapsed, exactly once each."""
        done: List[str] = []
        age = float(t) - float(self.t0)
        bid = _bid_for(book, self.held_leg)
        if bid is None:
            return done
        a = self.allow_static()
        allow_total = _dec(a["locked_value"]) or D("0")
        for h in MTM_HORIZONS_S:
            k = str(h)
            if k in self.mtm or age < h:
                continue
            veto_total = self.held_qty * (bid - self.held_avg_cost)
            self.mtm[k] = {
                "layer": MTM, "kind": "COUNTERFACTUAL_MTM", "stage": "shadow",
                "horizon_s": h, "t": t, "mark_bid": str(bid),
                "allow_total": str(allow_total),
                "veto_total": str(veto_total),
                "allow_minus_veto": str(allow_total - veto_total),
                "note": ("a bid-marked residual is COUNTERFACTUAL MTM: what "
                         "the position could have been sold for at a moment we "
                         "did not sell it, having placed no order at all. "
                         "Never realized P&L."),
                "sign_note": SIGN_NOTE,
            }
            done.append(k)
        return done

    # ---- legacy running mark (kept: cheap, and useful between horizons)
    def mark(self, *, book: Mapping[str, Any], t: float,
             tte_s: Any = None, stage: str = "shadow") -> Dict[str, Any]:
        """Both branches' TOTAL inventory-state economics at this book."""
        a, v = self.allow_static(), self.veto_static()
        bid = _bid_for(book, self.held_leg)

        # ALLOW: the pair is locked; nothing is left to mark.
        allow_total = _dec(a["locked_value"]) or D("0")

        # VETO: the whole held leg is still directional. Mark it at what it
        # could be sold into.
        if bid is None:
            veto_resid_mark = None
            veto_total = None
        else:
            veto_resid_mark = self.held_qty * (bid - self.held_avg_cost)
            veto_total = veto_resid_mark

        row = {
            "schema": SCHEMA, "fork_id": self.fork_id, "stage": stage,
            "t": t, "tte_s": (None if tte_s is None else float(tte_s)),
            "held_leg": self.held_leg,
            "held_qty": str(self.held_qty),
            "held_avg_cost": str(self.held_avg_cost),
            "complement_price": str(self.complement_price),
            "ceiling": self.ceiling,
            "mark_bid": None if bid is None else str(bid),
            ALLOW: {**a, "residual_mark": "0",
                    "total_inventory_economics": str(allow_total)},
            VETO: {**v,
                   "residual_mark": (None if veto_resid_mark is None
                                     else str(veto_resid_mark)),
                   "total_inventory_economics": (None if veto_total is None
                                                 else str(veto_total))},
            "allow_minus_veto": (None if veto_total is None
                                 else str(allow_total - veto_total)),
            # THE TERM THE BRIEF ASKS FOR EXPLICITLY: did completing retire a
            # worse directional residual?
            "residual_retired_by_completing": str(self.held_qty),
            "veto_path_fills_after": self.veto_path_fills_after,
            "allow_path_recycling": None,
            "allow_path_recycling_why": (
                "NOT OBSERVABLE from this arm: the ALLOW branch would have "
                "requested different rungs from a different inventory. The "
                "untouched control runs the allow-style policy; the "
                "window-level comparison against it is the evidence."),
            "labels": list(LABELS),
        }
        self.marks.append(row)
        return row

    def resolve(self, *, book: Mapping[str, Any], t: float,
                settlement: Optional[str] = None) -> Dict[str, Any]:
        """Final scoring at window end, or at settlement when it is known.

        `settlement` is the winning leg ("YES"/"NO") when the venue has said.
        Absent it, the held leg is marked at the bid and the row is labelled a
        MARK, never a realization.
        """
        a, v = self.allow_static(), self.veto_static()
        allow_total = _dec(a["locked_value"]) or D("0")
        if settlement in ("YES", "NO"):
            # the residual pays $1 if it is the winning leg, $0 otherwise
            win = D("1") if settlement == self.held_leg else D("0")
            veto_total = self.held_qty * (win - self.held_avg_cost)
            basis = "SETTLED"
        else:
            bid = _bid_for(book, self.held_leg)
            veto_total = (None if bid is None
                          else self.held_qty * (bid - self.held_avg_cost))
            basis = "MARKED_AT_BID"
        out = {
            "schema": SCHEMA, "fork_id": self.fork_id,
            "layer": (SETTLEMENT if basis == "SETTLED" else MTM),
            "kind": ("SETTLEMENT_VALUE" if basis == "SETTLED"
                     else "COUNTERFACTUAL_MTM"),
            "resolution_basis": basis, "t": t,
            "settlement_leg": settlement,
            "sign_note": SIGN_NOTE,
            "immediate_inventory_value": self.immediate,
            "counterfactual_mtm_by_horizon_s": dict(self.mtm),
            "features_at_fork": dict(self.features),
            ALLOW: {**a, "total_inventory_economics": str(allow_total)},
            VETO: {**v, "total_inventory_economics": (
                None if veto_total is None else str(veto_total))},
            "allow_minus_veto": (None if veto_total is None
                                 else str(allow_total - veto_total)),
            "veto_path_fills_after": self.veto_path_fills_after,
            "allow_path_recycling": None,
            "stage": ("settled" if basis == "SETTLED" else "shadow"),
            "realized": None,
            "realized_note": ("withheld until venue-confirmed cash; a settled "
                              "contract states what it PAYS, not that we have "
                              "been paid"),
            "labels": list(LABELS),
        }
        self.resolved = out
        return out


@dataclass
class CounterfactualLedger:
    """Every fork this arm has opened, and their running comparison."""

    forks: List[Fork] = field(default_factory=list)

    def open(self, *, symbol: str, cohort: str, t: float, tte_s: Any,
             held_leg: str, complement_leg: str, held_qty: Any,
             held_avg_cost: Any, complement_price: Any,
             ceiling: Any = None, book: Optional[Mapping[str, Any]] = None,
             features: Optional[Mapping[str, Any]] = None,
             regime: VT.FeeRegime = VT.VERIFIED_REGIME) -> Optional[Fork]:
        q, c, p = (_dec(held_qty), _dec(held_avg_cost),
                   _dec(complement_price))
        if None in (q, c, p) or q <= 0:
            return None
        f = Fork(fork_id=next(_ids), symbol=symbol, cohort=cohort, t0=t,
                 tte0=(None if tte_s is None else float(tte_s)),
                 held_leg=held_leg, complement_leg=complement_leg,
                 held_qty=q, held_avg_cost=c, complement_price=p,
                 ceiling=(None if ceiling is None else str(ceiling)),
                 regime=regime)
        f.set_immediate(book=book or {}, features=features or {})
        self.forks.append(f)
        if len(self.forks) > 500:
            self.forks = self.forks[-300:]
        return f

    def mark_open(self, *, book: Mapping[str, Any], t: float,
                  tte_s: Any = None) -> int:
        """Advance LAYER 2 horizons, and keep the running mark between them."""
        n = 0
        for f in self.forks:
            if f.resolved is None:
                f.advance(book=book, t=t)
                f.mark(book=book, t=t, tte_s=tte_s)
                n += 1
        return n

    def note_fill(self) -> None:
        """A subsequent fill on the VETO path -- the path we actually walk."""
        for f in self.forks:
            if f.resolved is None:
                f.veto_path_fills_after += 1

    def resolve_open(self, *, book: Mapping[str, Any], t: float,
                     settlement: Optional[str] = None) -> int:
        n = 0
        for f in self.forks:
            if f.resolved is None:
                f.resolve(book=book, t=t, settlement=settlement)
                n += 1
        return n

    def report(self) -> Dict[str, Any]:
        done = [f.resolved for f in self.forks if f.resolved]
        deltas = [float(r["allow_minus_veto"]) for r in done
                  if r.get("allow_minus_veto") is not None]
        allow_better = sum(1 for d in deltas if d > 0)
        return {
            "schema": SCHEMA,
            "forks_opened": len(self.forks),
            "forks_resolved": len(done),
            "allow_minus_veto_mean": (sum(deltas) / len(deltas)
                                      if deltas else None),
            "allow_minus_veto_median": (sorted(deltas)[len(deltas) // 2]
                                        if deltas else None),
            "windows_where_ALLOW_would_have_been_better": allow_better,
            "windows_where_VETO_was_better": len(deltas) - allow_better,
            "settled_resolutions": sum(1 for r in done
                                       if r["resolution_basis"] == "SETTLED"),
            "layers": {
                "1_immediate": IMMEDIATE, "2_mtm": MTM,
                "3_settlement": SETTLEMENT,
                "note": ("a bid-marked residual is layer 2 COUNTERFACTUAL_MTM "
                         "and is never realized P&L; only layer 3 with "
                         "venue-confirmed cash could be"),
            },
            "mtm_horizons_s": list(MTM_HORIZONS_S),
            "allow_minus_veto_by_horizon_s": {
                str(h): _median_of([
                    float(f.mtm[str(h)]["allow_minus_veto"])
                    for f in self.forks if str(h) in f.mtm])
                for h in MTM_HORIZONS_S},
            "features_captured": list(FORK_FEATURES),
            "sign_note": SIGN_NOTE,
            "marked_resolutions": sum(
                1 for r in done if r["resolution_basis"] == "MARKED_AT_BID"),
            "labels": list(LABELS) + [
                "allow_minus_veto > 0 means COMPLETING would have produced "
                "better total inventory economics than refusing -- i.e. the "
                "RISK OVERLAY cost us on that fork. This counter exists to "
                "expose the overlay, not to defend it.",
            ],
        }
