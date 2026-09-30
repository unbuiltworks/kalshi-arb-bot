"""Replay input must be strictly time-ordered, and inversions must be SEEN.

WHY THIS MODULE EXISTS. The 49-window out-of-sample result was computed on
input that `pmus_completeness_rule.load_tape` returned in FILE order, and the
derived tape carried two chronological inversions (0445z, 1400z). A `book`
row sets the engine's ladder, so a book read before the earlier `bbo` that
precedes it in time hands that decision a ladder from its own future. One
window moved by $3.93 at clip 30.

The frozen-25 path never showed this because `audit_economic_complete.load()`
sorts, and that sort silently repaired FIVE inversions in the frozen tape.
Passing the frozen checkpoint therefore validated the ENGINE and said
nothing about the LOADER -- which is exactly why sorting silently is not
enough. A silent sort would have repaired the OOS tape too and we would
never have learned the artefact existed.

So `ordered()` does both: it returns rows in timestamp order AND reports how
many inversions it had to repair. A caller that wants a guarantee rather
than a report uses `assert_monotonic`, which RAISES -- fail closed.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class NonMonotonicReplayInput(ValueError):
    """Replay input was not in timestamp order. Carries the evidence."""

    def __init__(self, report: Mapping[str, Any]) -> None:
        self.report = dict(report)
        super().__init__(
            "replay input is not time-ordered: %d inversion(s), first at "
            "index %s (%s before %s)"
            % (report.get("inversions"), report.get("first_index"),
               report.get("first_later"), report.get("first_earlier")))


def _key(row: Mapping[str, Any]) -> str:
    """The sort key. `t` is an ISO-8601 string with a fixed offset across
    this corpus, so lexical order IS chronological order; anything without a
    `t` sorts first and is reported as unkeyed rather than dropped."""
    t = row.get("t")
    return "" if t is None else str(t)


def inspect(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """How far from time-ordered is this, without changing anything."""
    inversions = 0
    first_index = first_later = first_earlier = None
    unkeyed = 0
    prev: Optional[str] = None
    for i, r in enumerate(rows):
        k = _key(r)
        if not k:
            unkeyed += 1
        if prev is not None and k and k < prev:
            inversions += 1
            if first_index is None:
                first_index, first_later, first_earlier = i, prev, k
        if k:
            prev = k
    return {"rows": len(rows), "inversions": inversions,
            "first_index": first_index, "first_later": first_later,
            "first_earlier": first_earlier, "unkeyed": unkeyed,
            "monotonic": inversions == 0}


def ordered(rows: Iterable[Mapping[str, Any]]
            ) -> Tuple[List[Mapping[str, Any]], Dict[str, Any]]:
    """(time-ordered rows, report). The report is not optional output.

    A stable sort, so rows sharing a timestamp keep their observed order --
    there is no information available to break that tie and inventing one
    would be a second silent decision.
    """
    rows = list(rows)
    before = inspect(rows)
    out = sorted(rows, key=_key)
    after = inspect(out)
    report = {"repaired_inversions": before["inversions"],
              "was_monotonic": before["monotonic"],
              "is_monotonic": after["monotonic"],
              "rows": before["rows"], "unkeyed": before["unkeyed"],
              "first_inversion_index": before["first_index"],
              "first_inversion_pair": [before["first_later"],
                                       before["first_earlier"]],
              "labels": ("a repaired inversion is a DEFECT IN THE INPUT, not "
                         "a detail: a book row consumed before the earlier "
                         "bbo it precedes hands that decision a ladder from "
                         "its own future")}
    return out, report


def assert_monotonic(rows: Sequence[Mapping[str, Any]]) -> None:
    """Fail closed. For any path that must not merely repair, but refuse."""
    rep = inspect(rows)
    if not rep["monotonic"]:
        raise NonMonotonicReplayInput(rep)
