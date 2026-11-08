"""Where the time goes between a first-leg fill and a second-leg quote.

WHY THIS EXISTS. The sequential replay showed the completion decision is
not the bottleneck -- the delay before one is even made is. But every
latency figure this program has quoted for that gap came from either log
line timestamps, which carry 0.03-9.6 s of collector lag, or from recorded
ladders sampled at 6.4-6.9 s. Neither can speak to a 2-second freshness
requirement. So this measures the path's OWN clock at the points that
matter, on the disarmed path, and says plainly what it cannot measure.

WHAT IT IS. A pure per-market accumulator. No I/O, no clock of its own, no
transport, no decision. Every input is passed in by the caller that already
holds it, and every output is a dict. It cannot refuse anything and holds
no limit.

THE SIX QUESTIONS, and which are answerable:

  1. first-leg fill DETECTION latency -- NOT DIRECTLY MEASURABLE. The
     venue's order-progress payload carries no timestamp (`cum_qty`,
     `avg_px`, `commission`, `state`, `venue_order_id` and no time), and
     `apply_fill` stamps none either. So the interval between the venue
     filling us and our learning of it is bounded by the ORDER POLL
     CADENCE and nothing narrower. `note_poll` measures the realized
     cadence so the bound is a measurement rather than a constant.
  2. book age when the controller evaluates -- measurable, and now carried
     on EVERY decision rather than only on a stale refusal.
  3. detection -> decision -- measurable, against our own detection.
  4. decision -> submission eligibility -- measurable.
  5. why a second leg was refused or delayed -- the decision's own reason,
     paired with the book age it was taken on.
  6. observer continuity across a rollover -- measurable as a count of
     observations either side of the roll.

NOTHING HERE IS A FILL, A PAIR OR A PROFIT.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

#: the deployed order-poll interval, which is what bounds question 1.
#: Passed in rather than imported so this module keeps no copy of a rule.
POLL_BOUND_NOTE = ("the venue's order-progress payload carries no "
                   "timestamp, so fill->detection is bounded by the poll "
                   "cadence and by nothing narrower")


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except BaseException:                                      # noqa: BLE001
        return None


class CompletionLatency:
    """One accumulator, keyed by market. Reporting only."""

    def __init__(self) -> None:
        self.m: Dict[str, Dict[str, Any]] = {}

    def _slot(self, market: str) -> Dict[str, Any]:
        return self.m.setdefault(str(market), {
            "polls": [], "detected_t": None, "detected_side": None,
            "decisions": [], "eligible": [], "rollovers": [],
            "first_decision_t": None,
            "observations_before_roll": 0, "observations_after_roll": 0,
        })

    # ------------------------------------------------------------ inputs
    def note_poll(self, market: str, t: Any) -> None:
        """One order-progress poll. Bounds question 1."""
        ft = _f(t)
        if ft is None:
            return
        s = self._slot(market)
        s["polls"].append(ft)
        if len(s["polls"]) > 256:
            del s["polls"][:-256]

    def note_fill_detected(self, market: str, side: str, t: Any) -> None:
        """The first tick on which we SAW a held position on `side`.

        Detection, not the fill. The two differ by an unmeasured amount
        bounded by the poll cadence, which is the whole point of
        `note_poll`.
        """
        ft = _f(t)
        if ft is None:
            return
        s = self._slot(market)
        if s["detected_t"] is None:
            s["detected_t"] = ft
            s["detected_side"] = str(side)

    def note_decision(self, market: str, t: Any, *, action: Any,
                      reason: Any, book_age_s: Any = None) -> None:
        ft = _f(t)
        if ft is None:
            return
        s = self._slot(market)
        det = s["detected_t"]
        if s["first_decision_t"] is None:
            s["first_decision_t"] = ft
        s["decisions"].append({
            "t": ft, "action": str(action), "reason": str(reason),
            "book_age_s": None if book_age_s is None else _f(book_age_s),
            "since_detection_s": (None if det is None
                                  else round(ft - det, 3)),
        })
        if len(s["decisions"]) > 256:
            del s["decisions"][:-256]

    def note_eligible(self, market: str, t: Any, *,
                      submitted: bool = False) -> None:
        """A decision that reached the point of being submittable."""
        ft = _f(t)
        if ft is None:
            return
        s = self._slot(market)
        last = s["decisions"][-1]["t"] if s["decisions"] else None
        s["eligible"].append({
            "t": ft, "submitted": bool(submitted),
            "since_decision_s": (None if last is None
                                 else round(ft - last, 3)),
        })
        if len(s["eligible"]) > 256:
            del s["eligible"][:-256]

    def note_rollover(self, market: str, t: Any) -> None:
        ft = _f(t)
        if ft is None:
            return
        s = self._slot(market)
        s["rollovers"].append(ft)

    def note_observation(self, market: str, t: Any) -> None:
        """One lifetime observation, counted either side of a roll so the
        observer's continuity across a rollover is a measurement."""
        ft = _f(t)
        if ft is None:
            return
        s = self._slot(market)
        if s["rollovers"]:
            s["observations_after_roll"] += 1
        else:
            s["observations_before_roll"] += 1

    # ------------------------------------------------------------ output
    def report(self, market: str) -> Dict[str, Any]:
        s = self.m.get(str(market))
        if s is None:
            return {"market": str(market), "known": False}
        polls = s["polls"]
        gaps = [round(b - a, 3) for a, b in zip(polls, polls[1:])]
        dec = s["decisions"]
        ages = [d["book_age_s"] for d in dec if d["book_age_s"] is not None]
        waits = [d["since_detection_s"] for d in dec
                 if d["since_detection_s"] is not None]
        firsts = [d for d in dec if d["action"] in ("COMPLETE", "REPRICE")]
        elig = [e["since_decision_s"] for e in s["eligible"]
                if e["since_decision_s"] is not None]
        reasons: Dict[str, int] = {}
        for d in dec:
            reasons[d["reason"]] = reasons.get(d["reason"], 0) + 1
        return {
            "market": str(market), "known": True,
            # 1 -- bounded, not measured
            "poll_gaps_s": {"n": len(gaps), "median": _median(gaps),
                            "p95": _pct(gaps, 0.95), "max": (max(gaps)
                                                             if gaps else None)},
            "fill_detection_latency": {
                "measurable": False,
                "bounded_by": "order_poll_cadence",
                "why": POLL_BOUND_NOTE},
            # 2
            "book_age_at_decision_s": {"n": len(ages), "median": _median(ages),
                                       "p95": _pct(ages, 0.95),
                                       "max": (max(ages) if ages else None)},
            # 3
            "detection_to_decision_s": {"n": len(waits),
                                        "median": _median(waits),
                                        "p95": _pct(waits, 0.95),
                                        "max": (max(waits) if waits else None)},
            # THE AGGREGATE ABOVE IS DOMINATED BY HOW LONG THE WINDOW
            # RAN, not by latency: every later decision in the same window
            # is further from the one detection. The figure that answers
            # "how long before we decided anything" is this one, and the
            # first deployed heartbeat is why it exists.
            "detection_to_first_decision_s": (
                None if (s["detected_t"] is None
                         or s["first_decision_t"] is None)
                else round(s["first_decision_t"] - s["detected_t"], 3)),
            "detection_to_first_actionable_s": (
                firsts[0]["since_detection_s"] if firsts else None),
            # 4
            "decision_to_eligible_s": {"n": len(elig), "median": _median(elig),
                                       "p95": _pct(elig, 0.95),
                                       "max": (max(elig) if elig else None)},
            # 5
            "decision_reasons": reasons,
            "decisions": len(dec),
            "actionable_decisions": len(firsts),
            # 6
            "rollovers": len(s["rollovers"]),
            "observations_before_roll": s["observations_before_roll"],
            "observations_after_roll": s["observations_after_roll"],
            "observer_continued_across_rollover": (
                bool(s["rollovers"]) and s["observations_after_roll"] > 0),
            "labels": ("reporting only. Every figure is this process's own "
                       "clock at the point named; fill->detection is a "
                       "BOUND and is labelled as one. No fill, pair or "
                       "profit is asserted here."),
        }

    def reports(self) -> List[Dict[str, Any]]:
        return [self.report(m) for m in sorted(self.m)]


def _median(xs: List[Any]) -> Optional[float]:
    v = sorted(x for x in xs if x is not None)
    if not v:
        return None
    return round(v[len(v) // 2], 3)


def _pct(xs: List[Any], q: float) -> Optional[float]:
    v = sorted(x for x in xs if x is not None)
    if not v:
        return None
    i = min(len(v) - 1, int(q * len(v)))
    return round(v[i], 3)
