"""Why does MIRROR_RAW have lower amplitude than L5_NATIVE?

PHASE 1, MEASUREMENT ONLY. The replication discrepancy is not treated here as
a profitability problem to optimise. Nothing in this module proposes, applies
or implies a change to the band, controller, sizing, timing, persistence or
market selection. It answers one question: where along the chain do L5's
actions stop being reproduced, and which of four candidate explanations the
evidence actually supports.

THE CHAIN, walked per action, in order:

    L5 action -> observation latency -> PMUS state -> PMUS_PERMITTED?
              -> STRICT interaction? -> quantity acquired

An action that fails to reach the end is attributed to EXACTLY ONE cause, the
FIRST stage it failed at, so the causes partition the loss and cannot double
count it:

    NO_BOOK                  PMUS unreadable or one-sided at our receipt
    REQUIRES_CROSSING        L5's price was already at or through our touch,
                             so reproducing it as a maker was impossible
    NO_SUBSEQUENT_INTERACTION  the quote was placeable and nothing ever traded
    QUEUE_AMBIGUOUS          something traded, but not through our price, so
                             queue position decides and STRICT will not assume
    SIZE_TRANSLATION         reproduced, but q=1 acquired fewer units than L5
    OTHER                    anything the above do not cover, reported rather
                             than absorbed

FOUR HYPOTHESES, kept distinct because they imply different fixes and we are
not fixing anything yet:

    A  PMUS genuinely cannot reproduce enough of L5's actions
       -> losses concentrate in NO_BOOK + REQUIRES_CROSSING
    B  the q=1 translation itself destroys L5's relative inventory dynamics
       -> losses concentrate in SIZE_TRANSLATION, and the relative-size
          COUNTERFACTUAL below recovers the amplitude
    C  observation latency makes us miss the actions responsible for the
       overshoot
       -> the actions L5 used to build overshoot arrive slower, or arrive
          after the window is already closed
    D  STRICT is intentionally undercounting executions that would really fill
       -> losses concentrate in QUEUE_AMBIGUOUS + NO_SUBSEQUENT_INTERACTION,
          and PLAUSIBLE recovers the amplitude

The counterfactuals are MEASUREMENTS of what the amplitude would have been,
computed from already-recorded evidence. They are not a proposal to trade
either way, and they never enter an economic bound.
"""
from __future__ import annotations

import collections
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.l5_replication_fidelity import Path, _d, _venue_klass_and_overlay

#: the six causes, in the order the chain tests them. A cause earlier in this
#: tuple wins, because it is the first stage the action failed at.
CAUSES = ("NO_BOOK", "REQUIRES_CROSSING", "NO_SUBSEQUENT_INTERACTION",
          "QUEUE_AMBIGUOUS", "SIZE_TRANSLATION", "OTHER")

#: interaction classes that STRICT accepts. STRICT is queue-INDEPENDENT: only
#: a print through our price proves the queue ahead of us was cleared.
STRICT_CLASSES = ("TRADE_THROUGH_PRICE",)
#: PLAUSIBLE additionally accepts the queue-dependent classes. Used ONLY to
#: quantify hypothesis D; it never becomes the primary measurement.
PLAUSIBLE_CLASSES = STRICT_CLASSES + ("TRADE_AT_PRICE", "QUEUE_AMBIGUOUS",
                                      "OBSERVABLE_FILL_PLAUSIBLE")


class WeightedPath(Path):
    """An inventory path whose steps carry L5's relative size.

    `Path` counts actions, which is the q=1 translation: every L5 action moves
    inventory by one unit regardless of whether he bought 3 shares or 300. If
    that clamp is what flattens the amplitude, then replaying the SAME
    permitted actions with their relative sizes must restore it -- and if it
    does not, hypothesis B is not the explanation. That is the only reason
    this class exists.
    """

    def add_weighted(self, i: int, side: str, qty: float) -> None:
        if side == "UP":
            self.up += qty
        else:
            self.dn += qty
        if self._last is None:
            self._last, self._run = side, 1
        elif side == self._last:
            self._run += 1
        else:
            self.runs.append(self._run)
            self._run, self._last = 1, side
            if self.first_opposite_i is None:
                self.first_opposite_i = i
        if self.first_opposite_i is not None and i > self.first_opposite_i:
            self.after_opposite += 1
        r = self.up - self.dn
        self.imbalance.append(r)
        self.signs.append(1 if r > 0 else (-1 if r < 0 else 0))


#: attribution for an action whose STRICT stage could not be evaluated at all.
#: NOT one of the six causes: it is an absence of evidence about our pipeline,
#: not a finding about the venue, and collapsing it into QUEUE_AMBIGUOUS would
#: manufacture support for hypothesis D out of missing data.
UNEVALUATED = "STRICT_NOT_EVALUABLE"


def _interaction_class(row: Mapping[str, Any],
                       authenticated: Optional[Mapping[str, Any]]) -> Any:
    """The interaction class for this action, from AUTHENTICATED evidence.

    The observer's own rows carry public-book interaction only, which the
    classifier can never raise above QUEUE_AMBIGUOUS -- it says so itself in
    `classes_unreachable_here`. Scoring the STRICT stage against that source
    would attribute every permitted action to QUEUE_AMBIGUOUS and make
    hypothesis D true by construction, so it is refused: without an
    authenticated class this stage returns UNEVALUATED and the run reports
    itself as not STRICT-evaluable.
    """
    if authenticated is not None:
        key = row.get("action_key")
        if key in authenticated:
            got = authenticated[key]
            return (got or {}).get("klass") if isinstance(got, Mapping) else got
        return UNEVALUATED
    ev = row.get("interaction") or {}
    if ev.get("classes_unreachable_here"):
        return UNEVALUATED
    return ev.get("klass")


def decompose(actions: Sequence[Mapping[str, Any]],
              accept: Sequence[str] = STRICT_CLASSES,
              authenticated: Optional[Mapping[str, Any]] = None,
              ) -> Dict[str, Any]:
    """Walk one window's actions through the chain, attributing every loss.

    `authenticated` maps an action_key to its authenticated interaction class
    (or to the classified order carrying one), as produced by the frozen
    mirror-lifecycle scan. Without it the STRICT stage is not evaluable and
    the result says so rather than guessing.
    """
    acts = sorted(actions, key=lambda r: r["l5_event_utc"])
    native, permitted, acquired = Path(), Path(), Path()
    weighted = WeightedPath()

    cause = collections.Counter()
    units_lost = collections.Counter()
    per_action: List[Dict[str, Any]] = []
    lat_all: List[float] = []
    lat_reached: List[float] = []
    lat_lost: List[float] = []
    backlog = 0

    for i, r in enumerate(acts):
        l5 = r.get("l5") or {}
        side = l5.get("pmus_side")
        if side is None:
            continue
        native.add(i, side)

        qty = _d(l5.get("l5_size"))
        qf = float(qty) if qty is not None else 1.0
        lat = r.get("latency_s")
        if lat is not None:
            lat_all.append(float(lat))

        # stage: observation. A backlog action was received after its window
        # had already closed, so no PMUS state at receipt exists to price it
        # against. It is counted for hypothesis C and attributed at the stage
        # it actually failed, not given a cause of its own.
        is_backlog = bool(r.get("backlog"))
        if is_backlog:
            backlog += 1

        klass, _overlay = _venue_klass_and_overlay(r, r.get("copyability") or {})
        why: Optional[str] = None

        if klass == "NO_BOOK":
            why = "NO_BOOK"
        elif klass == "REQUIRES_CROSSING":
            why = "REQUIRES_CROSSING"
        elif klass != "MAKER_COPYABLE":
            why = "OTHER"
        else:
            permitted.add(i, side)
            seen = _interaction_class(r, authenticated)
            if seen in accept:
                acquired.add(i, side)
                weighted.add_weighted(i, side, qf)
            elif seen == UNEVALUATED:
                why = UNEVALUATED
            elif seen in (None, "NO_INTERACTION"):
                why = "NO_SUBSEQUENT_INTERACTION"
            else:
                why = "QUEUE_AMBIGUOUS"

        if why is None:
            if lat is not None:
                lat_reached.append(float(lat))
            # reproduced, but q=1 acquires one unit where L5 took qf of them
            if qf > 1.0:
                units_lost["SIZE_TRANSLATION"] += qf - 1.0
        else:
            cause[why] += 1
            units_lost[why] += qf
            if lat is not None:
                lat_lost.append(float(lat))

        per_action.append({
            "i": i, "side": side, "l5_size": qf, "latency_s": lat,
            "backlog": is_backlog, "venue_class": klass,
            "dropped_at": why, "reached_acquisition": why is None,
        })

    unevaluated = cause[UNEVALUATED]
    ns, ps, as_ = native.summary(), permitted.summary(), acquired.summary()
    ws = weighted.summary()
    n = ns["actions"]

    return {
        "label": "L5_AMPLITUDE_DECOMPOSITION_V1",
        "phase": "PHASE_1_MEASUREMENT_ONLY",
        "accept_classes": list(accept),
        "chain": {
            "l5_actions": n,
            "pmus_permitted": ps["actions"],
            "acquired_under_accept": as_["actions"],
        },
        "strict_evaluable": unevaluated == 0,
        "actions_without_authenticated_evidence": unevaluated,
        "evaluability_note": (
            "the STRICT stage needs AUTHENTICATED interaction evidence. "
            "Public-book rows cannot reach TRADE_THROUGH_PRICE at all, so "
            "scoring them would make hypothesis D true by construction; "
            "those actions are reported here instead of attributed."),
        "amplitude": {
            "L5_NATIVE": {
                "overshoot_past_zero": ns["overshoot_past_zero"],
                "longest_same_side_run": ns["longest_same_side_run"],
                "max_abs_imbalance": ns["max_abs_imbalance"],
                "inventory_crossed_zero": ns["inventory_crossed_zero"],
            },
            "MIRROR_RAW": {
                "overshoot_past_zero": as_["overshoot_past_zero"],
                "longest_same_side_run": as_["longest_same_side_run"],
                "max_abs_imbalance": as_["max_abs_imbalance"],
                "inventory_crossed_zero": as_["inventory_crossed_zero"],
            },
        },
        "attribution": {
            "actions_by_cause": {c: cause[c] for c in CAUSES if cause[c]},
            "actions_unattributable": unevaluated,
            "l5_units_by_cause": {c: round(units_lost[c], 4)
                                  for c in CAUSES if units_lost[c]},
            "note": ("each action is attributed to the FIRST stage it failed "
                     "at, so causes partition the loss without double "
                     "counting"),
        },
        "hypotheses": {
            "A_pmus_cannot_reproduce": {
                "actions": cause["NO_BOOK"] + cause["REQUIRES_CROSSING"],
                "pct_of_l5_actions": round(
                    100.0 * (cause["NO_BOOK"] + cause["REQUIRES_CROSSING"]) / n, 1)
                if n else None,
            },
            "B_q1_translation_destroys_dynamics": {
                "size_units_lost_to_q1": round(units_lost["SIZE_TRANSLATION"], 4),
                "counterfactual_relative_size": {
                    "overshoot_past_zero": ws["overshoot_past_zero"],
                    "longest_same_side_run": ws["longest_same_side_run"],
                    "inventory_crossed_zero": ws["inventory_crossed_zero"],
                    "note": ("the SAME acquired actions replayed with L5's "
                             "relative sizes. A MEASUREMENT of what the "
                             "amplitude would have been, not a proposal to "
                             "size differently."),
                },
            },
            "C_latency_misses_the_overshoot_actions": {
                "backlog_actions": backlog,
                "latency_s_p50_all": _p50(lat_all),
                "latency_s_p50_reached": _p50(lat_reached),
                "latency_s_p50_lost": _p50(lat_lost),
                "note": ("if latency were the cause, the actions we lose "
                         "would arrive materially later than the ones we "
                         "keep"),
            },
            "D_strict_undercounts_real_fills": {
                "actions": cause["QUEUE_AMBIGUOUS"] + cause["NO_SUBSEQUENT_INTERACTION"],
                "queue_ambiguous": cause["QUEUE_AMBIGUOUS"],
                "no_subsequent_interaction": cause["NO_SUBSEQUENT_INTERACTION"],
                "note": ("run decompose(..., accept=PLAUSIBLE_CLASSES) to see "
                         "the amplitude PLAUSIBLE recovers. PLAUSIBLE assumes "
                         "queue position and is never the primary measure."),
            },
        },
        "per_action": per_action,
        "labels": ("SHADOW/READ-ONLY measurement. No order exists. Nothing "
                   "here is submitted, filled, settled or realized, and "
                   "nothing here changes the frozen strategy."),
    }


def _p50(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[len(s) // 2], 2)


def compare_accept(actions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """STRICT vs PLAUSIBLE amplitude, for hypothesis D only.

    Kept as its own entry point so a reader cannot mistake the PLAUSIBLE
    number for the measurement: STRICT remains primary everywhere else.
    """
    strict = decompose(actions, STRICT_CLASSES)
    plausible = decompose(actions, PLAUSIBLE_CLASSES)
    return {
        "STRICT": strict["amplitude"]["MIRROR_RAW"],
        "PLAUSIBLE": plausible["amplitude"]["MIRROR_RAW"],
        "L5_NATIVE": strict["amplitude"]["L5_NATIVE"],
        "note": ("PLAUSIBLE is an upper bound that assumes queue position. A "
                 "gap that PLAUSIBLE closes is evidence for D; a gap it "
                 "leaves open is not."),
    }
