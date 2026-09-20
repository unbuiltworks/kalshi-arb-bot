"""ENTRY ADMISSION -- a first fill must justify itself economically.

"L5 WANTS A RUNG" IS NOT A REASON TO TAKE ONE. The frozen policy emits a
desired surface from inventory geometry. It has no opinion about whether the
resulting fill can ever be paired profitably ON THIS VENUE, because the venue
was not in its training. This module supplies that missing half, and it is the
reason a first fill here is rarer than the control's.

THE GATES ARE NAMED, PREREGISTERED AND INDEPENDENT. Not a fitted score. Each
returns pass/fail with its own reason, and a refusal names the gate that
refused. A single blended number would let a strong reading on one dimension
buy a weak one on another, which is exactly how a high-volume toxic window
outranks a quiet reversible one.

THE ORDERING PROBLEM THE BRIEF NAMES, HANDLED DIRECTLY. "A window with high
volume but one-direction toxic flow should not be preferred over a
lower-volume window with repeated reversible two-sided flow." Volume is
therefore NOT a gate and NOT a score input. What is gated is REVERSIBILITY --
alternation of aggressor direction, both directions actually present, and no
single print dominating the tape. A whale printing one way at high volume
fails `FLOW_REVERSIBLE` and `NOT_CONCENTRATED` no matter how large it is.

THE ASYMMETRY ON AN UNCALIBRATED HAZARD, STATED SO IT IS NOT READ AS A BUG.
`hazard.admits_waiting` fails CLOSED -- an uncalibrated hazard never justifies
continuing to wait. Entry is the opposite: an uncalibrated hazard does NOT
block entry, because blocking it would mean never taking the fills whose
outcomes are the only way the cells ever calibrate, and the sample would stay
empty forever by construction. Entry is instead carried by the gates that do
not need a fitted model, and the hazard blocks entry only once it is
calibrated AND below threshold. Waiting accumulates risk while producing
nothing; entering under the other gates produces the evidence.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.gemini_execution.econ import hazard as HZ
from src.gemini_execution.econ import veto as VT

SCHEMA = "gemini_econ_entry_admission/v1"

SHADOW, LIVE = "SHADOW", "LIVE"

#: How the spread is treated. BOOLEAN is the original arm, unchanged.
SPREAD_BOOLEAN = "BOOLEAN"
SPREAD_PASSIVE_PAIR = "PASSIVE_PAIR"

#: Gates whose UNKNOWN is RECORDED but does not refuse, during SHADOW only.
#:
#: HAZARD is here for the bootstrap reason in the module docstring: refusing on
#: an uncalibrated cell would prevent collecting the very outcomes that
#: calibrate it, so the sample would stay empty by construction.
#:
#: REFERENCE_AGREEMENT is here for an honest and less comfortable reason: this
#: build has a public spot tape (`preentry.SPOT_SYMBOL`) but NO strike-relative
#: settlement-reference proxy, so the gate cannot be evaluated yet. That is a
#: real gap, recorded as UNKNOWN on every row rather than hidden behind a
#: default of "agrees". It is listed advisory so the shadow can collect; it is
#: NOT permission to go live without it, and `phase=LIVE` removes it.
DEFAULT_ADVISORY: Tuple[str, ...] = ("HAZARD", "REFERENCE_AGREEMENT")


class Thresholds:
    """PREREGISTERED. Fixed before the prospective sample exists.

    Per cohort, because BTC5m and BTC15m are different policies and not one
    policy with a longer clock -- see `policy.py`.
    """

    __slots__ = ("cohort", "min_alternation", "max_largest_print_share",
                 "max_required_gap_ticks", "max_reference_disagreement",
                 "min_prints", "hazard_threshold", "min_edge",
                 "max_spread", "phase", "advisory_gates", "spread_mode")

    def __init__(self, *, cohort: str, min_alternation: float,
                 max_largest_print_share: float, max_required_gap_ticks: int,
                 max_reference_disagreement: str, min_prints: int,
                 hazard_threshold: float, min_edge: str, max_spread: str,
                 spread_mode: str = SPREAD_BOOLEAN,
                 phase: str = SHADOW,
                 advisory_gates: Sequence[str] = DEFAULT_ADVISORY):
        if phase not in (SHADOW, LIVE):
            raise ValueError("phase must be %r or %r, got %r"
                             % (SHADOW, LIVE, phase))
        # THE SAFETY PROPERTY IS STRUCTURAL, NOT A CONVENTION. A LIVE
        # threshold set cannot carry advisory gates at all: the constructor
        # empties them, so there is no configuration in which real capital is
        # committed on a gate that was only being watched.
        if spread_mode not in (SPREAD_BOOLEAN, SPREAD_PASSIVE_PAIR):
            raise ValueError("spread_mode must be %r or %r, got %r"
                             % (SPREAD_BOOLEAN, SPREAD_PASSIVE_PAIR,
                                spread_mode))
        self.spread_mode = spread_mode
        self.phase = phase
        self.advisory_gates = tuple() if phase == LIVE else tuple(advisory_gates)
        self.cohort = cohort
        self.min_alternation = min_alternation
        self.max_largest_print_share = max_largest_print_share
        self.max_required_gap_ticks = max_required_gap_ticks
        self.max_reference_disagreement = max_reference_disagreement
        self.min_prints = min_prints
        self.hazard_threshold = hazard_threshold
        self.min_edge = min_edge
        self.max_spread = max_spread

    def report(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _gate(name: str, ok: Optional[bool], why: Optional[str],
          **extra: Any) -> Dict[str, Any]:
    return {"gate": name, "pass": ok, "why": why, **extra}


def admit_entry(*, feat: Mapping[str, Any], thresholds: Thresholds,
                proposed_price: Any, inventory: Mapping[str, Any],
                hazard_estimate: Optional[Mapping[str, Any]] = None,
                reference_disagreement: Any = None,
                regime: VT.FeeRegime = VT.VERIFIED_REGIME,
                tick: Any = "0.01",
                flow_vouched: Optional[bool] = None,
                flow_why: Optional[str] = None) -> Dict[str, Any]:
    """Should we rest a FIRST rung here?

    `feat` is a `hazard.features` record -- the same causal extraction the
    hazard model reads, so entry and completion are scored off one view of the
    state rather than two that can drift apart.
    """
    gates: List[Dict[str, Any]] = []
    px = _dec(proposed_price)

    # ---- 1. ECONOMIC FEASIBILITY. If, at this entry price, no complement
    # price exists that clears the edge, the fill is unpairable before it
    # happens. This is the gate that would have stopped the 1.13 pair at its
    # ORIGIN rather than at its completion.
    if px is None:
        gates.append(_gate("ECONOMIC_FEASIBILITY", False,
                           "UNREADABLE proposed entry price"))
        cap = None
    else:
        ceil = VT.max_complement_price(held_avg_cost=px,
                                       min_edge=thresholds.min_edge,
                                       regime=regime, tick=tick)
        cap = ceil.get("ceiling")
        gates.append(_gate(
            "ECONOMIC_FEASIBILITY", bool(ceil.get("ok")),
            None if ceil.get("ok") else
            ("entering at %s admits no profitable complement: %s"
             % (px, ceil.get("why"))),
            entry_price=str(px), complement_ceiling=cap))

    # ---- 2. THE COMPLEMENT MUST BE PLAUSIBLY REACHABLE. A ceiling the market
    # is nowhere near is a ceiling we will sit behind until expiry.
    gap = feat.get("required_gap_ticks")
    if gap is None:
        gates.append(_gate("COMPLEMENT_REACHABLE", None,
                           "required_gap_ticks unreadable; NOT assumed near"))
    else:
        ok = gap <= thresholds.max_required_gap_ticks
        gates.append(_gate("COMPLEMENT_REACHABLE", ok,
                           None if ok else
                           "complement must travel %d ticks, above the "
                           "preregistered bound of %d"
                           % (gap, thresholds.max_required_gap_ticks),
                           required_gap_ticks=gap))

    # ---- 3. FLOW REVERSIBILITY. The gate the brief singles out. Volume is
    # deliberately absent.
    #
    # PROVENANCE PROBLEM, RECORDED RATHER THAN QUIETLY LIVED WITH. In the
    # fixed-cycle lane these features are computed from `habitat.public_flow`,
    # a polled REST read carrying the label "EVIDENCE ONLY: no price, size or
    # decision is taken from this". Gating an entry on it takes a decision from
    # it. The label exists because a polled tape has its own clock and its own
    # staleness relative to the book the controller decided on.
    #
    # The correct source is the maintained WEBSOCKET tape -- `l5_shadow
    # .gemini_ws` delivers real trade events with `reason == "trade"` and
    # millisecond venue timestamps, and `prints_since` returns None rather than
    # [] when it cannot vouch for an interval, so "we saw nothing" is
    # distinguishable from "we were not watching". That is what the
    # event-driven lane reads.
    #
    # Until this gate is sourced there, every decision it produces carries
    # `flow_source_provisional` and is to be read as PROVISIONAL evidence, not
    # as a measurement of the market.
    alt = feat.get("side_alternation")
    n = feat.get("n_prints") or 0
    if flow_vouched is False:
        # UNVOUCHED IS NOT EMPTY, and it is not zero prints either. The socket
        # could not vouch for the interval, so we know nothing about flow --
        # which refuses, because an unknown gate is never a pass.
        gates.append(_gate("FLOW_REVERSIBLE", None,
                           "flow coverage UNVOUCHED: %s"
                           % (flow_why or "socket could not vouch"),
                           flow_source="GEMINI_WS", flow_vouched=False))
        gates.append(_gate("NOT_CONCENTRATED", None,
                           "flow coverage UNVOUCHED; print sizes unknown",
                           flow_source="GEMINI_WS", flow_vouched=False))
    elif n < thresholds.min_prints:
        gates.append(_gate("FLOW_REVERSIBLE", False,
                           "only %d prints in the window, below the minimum "
                           "of %d; flow direction is not yet evidence"
                           % (n, thresholds.min_prints), n_prints=n,
                           flow_source="GEMINI_WS"))
    elif alt is None:
        gates.append(_gate("FLOW_REVERSIBLE", None,
                           "aggressor direction unreadable on this tape"))
    else:
        ok = alt >= thresholds.min_alternation
        gates.append(_gate("FLOW_REVERSIBLE", ok,
                           None if ok else
                           "alternation %.3f below %.3f -- flow is one-"
                           "directional and a resting rung on the far side is "
                           "adverse selection"
                           % (alt, thresholds.min_alternation),
                           side_alternation=alt, n_prints=n,
                           flow_source="GEMINI_WS"))

    # ---- 4. NOT CONCENTRATED. One print is not a two-sided market however
    # large it is.
    share = feat.get("largest_print_share")
    if flow_vouched is False:
        pass                       # already recorded UNVOUCHED above
    elif share is None:
        gates.append(_gate("NOT_CONCENTRATED", None,
                           "print sizes unreadable"))
    else:
        ok = share <= thresholds.max_largest_print_share
        gates.append(_gate("NOT_CONCENTRATED", ok,
                           None if ok else
                           "a single print is %.1f%% of window volume, above "
                           "%.1f%% -- this is one participant, not liquidity"
                           % (share * 100,
                              thresholds.max_largest_print_share * 100),
                           largest_print_share=share,
                           flow_source="GEMINI_WS"))

    # ---- 5. TWO-SIDED QUOTE. Both outcomes quoted on both sides, read from
    # the prices the contract publishes. NOT a depth test: `contract_depth` is
    # a separate read labelled EVIDENCE ONLY and may not gate anything, and the
    # contract object carries no depth of its own.
    ts = feat.get("two_sided_quote")
    gates.append(_gate("TWO_SIDED_QUOTE", (None if ts is None else bool(ts)),
                       None if ts else
                       "both outcomes are not quoted on both sides; a "
                       "complement cannot rest where there is no other side",
                       depth_evidence_only=feat.get("depth_evidence_only")))

    # ---- 6. SPREAD, treated one of two ways.
    #
    # BOOLEAN (the original arm, unchanged): a flared spread means the makers
    # have stepped away.
    #
    # PASSIVE_PAIR: no fixed maximum spread at all. Instead the two PASSIVE
    # acquisition prices must pay for themselves. `metadata.outcome_book`
    # states the mechanic -- a resting BUY joins that outcome's SELL side --
    # so the passive pair costs yes_bid + no_bid, and a WIDE spread pushes
    # that sum DOWN. Gating on spread being small therefore screened out the
    # states where passive pair economics are best, which is what profit
    # sprint #1 measured. Here spread is an economic input in the correct
    # direction rather than a boolean.
    spread = _dec(feat.get("spread"))
    if thresholds.spread_mode == SPREAD_PASSIVE_PAIR:
        yb, nb = _dec(feat.get("yes_bid")), _dec(feat.get("no_bid"))
        edge = _dec(thresholds.min_edge)
        if yb is None or nb is None or edge is None:
            gates.append(_gate("PASSIVE_PAIR_ECONOMIC", None,
                               "both outcomes' bids are not readable; the "
                               "passive pair cannot be priced"))
        elif not (D("0") < yb < D("1") and D("0") < nb < D("1")):
            gates.append(_gate("PASSIVE_PAIR_ECONOMIC", None,
                               "a passive bid is not a price in (0,1)"))
        else:
            mk = regime.maker_rate
            fees = (mk * yb * (D("1") - yb)) + (mk * nb * (D("1") - nb))
            cost = yb + nb + fees
            cap = D("1") - edge
            ok = cost <= cap
            gates.append(_gate(
                "PASSIVE_PAIR_ECONOMIC", ok,
                None if ok else
                ("passive pair costs %s (bids %s + %s, fees %s) above the "
                 "%s ceiling at edge %s" % (cost, yb, nb, fees, cap, edge)),
                yes_bid=str(yb), no_bid=str(nb), fees=str(fees),
                passive_pair_cost=str(cost), ceiling=str(cap),
                spread=(None if spread is None else str(spread)),
                rebate_excluded=True))
    else:
        mx = _dec(thresholds.max_spread)
        if spread is None or mx is None:
            gates.append(_gate("SPREAD", None, "spread unreadable"))
        else:
            ok = spread <= mx
            gates.append(_gate("SPREAD", ok, None if ok else
                               "spread %s above %s" % (spread, mx),
                               spread=str(spread)))

    # ---- 7. REFERENCE AGREEMENT. A prediction book that disagrees with the
    # settlement reference is a book about to reprice, and our resting rung is
    # the thing it reprices against.
    dis = _dec(reference_disagreement)
    lim = _dec(thresholds.max_reference_disagreement)
    if dis is None or lim is None:
        gates.append(_gate("REFERENCE_AGREEMENT", None,
                           "no reference proxy available on this read; "
                           "NOT assumed to agree"))
    else:
        ok = abs(dis) <= lim
        gates.append(_gate("REFERENCE_AGREEMENT", ok, None if ok else
                           "book disagrees with the settlement reference by "
                           "%s, above %s" % (dis, lim),
                           disagreement=str(dis)))

    # ---- 8. INVENTORY HEADROOM.
    resid = _dec((inventory or {}).get("directional_residual"))
    gates.append(_gate(
        "INVENTORY_HEADROOM", (None if resid is None else True),
        None if resid is not None else "inventory unreadable; UNREADABLE is "
        "not flat and entry is not admitted on an unknown book",
        directional_residual=None if resid is None else str(resid)))

    # ---- 9. HAZARD. Blocks only when CALIBRATED and below threshold. See the
    # asymmetry in the module docstring.
    if hazard_estimate is None:
        gates.append(_gate("HAZARD", None, "no hazard estimate supplied"))
    elif not hazard_estimate.get("calibrated"):
        gates.append(_gate(
            "HAZARD", None,
            "UNCALIBRATED -- does not block entry, by design: refusing here "
            "would prevent collecting the outcomes that calibrate the cell",
            cell=hazard_estimate.get("cell"), n=hazard_estimate.get("n")))
    else:
        p = hazard_estimate.get("p")
        ok = bool(p is not None and p >= thresholds.hazard_threshold)
        gates.append(_gate("HAZARD", ok, None if ok else
                           "calibrated P(acceptable opposite fill) %.4f below "
                           "%.4f" % (p or 0.0, thresholds.hazard_threshold),
                           p=p, cell=hazard_estimate.get("cell")))

    adv = set(thresholds.advisory_gates)
    for g in gates:
        g["advisory"] = g["gate"] in adv
    # An advisory gate that FAILS still refuses. Only its UNKNOWN is waived --
    # "we could not measure this" is what the waiver covers, never "we
    # measured it and it failed".
    failed = [g["gate"] for g in gates if g["pass"] is False]
    unknown = [g["gate"] for g in gates
               if g["pass"] is None and g["gate"] not in adv]
    waived = [g["gate"] for g in gates
              if g["pass"] is None and g["gate"] in adv]
    admitted = not failed and not unknown
    return {
        "schema": SCHEMA, "cohort": thresholds.cohort,
        "admitted": admitted, "gates": gates, "phase": thresholds.phase,
        "failed_gates": failed, "unknown_gates": unknown,
        "waived_unknown_gates": waived,
        "thresholds": thresholds.report(),
        "spread_mode": thresholds.spread_mode,
        "complement_ceiling": cap,
        "why": (None if admitted else
                ("refused by %s" % ", ".join(failed) if failed else
                 "UNKNOWN on %s; an unreadable gate is never a pass"
                 % ", ".join(unknown))),
        "labels": [
            "Volume is deliberately NOT a gate. Reversibility is.",
            "PASSIVE_PAIR_ECONOMIC excludes rebates and rewards from "
            "admission entirely; only trading economics may admit a state.",
            "An UNKNOWN gate refuses. UNREADABLE is not a pass.",
            "An uncalibrated hazard does not block ENTRY but never justifies "
            "WAITING; the asymmetry is intentional.",
            "An ADVISORY gate waives only its UNKNOWN, never a measured "
            "failure, and phase=LIVE carries no advisory gates at all.",
        ],
    }
