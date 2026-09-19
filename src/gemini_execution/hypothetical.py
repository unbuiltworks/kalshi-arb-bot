"""HYPOTHETICAL FILLS IN A RUNNING LANE -- what the rung WOULD have caught.

WHY THIS EXISTS. `shadow_soak` never calls `Driver.place`, so no rung rests,
nothing fills and the inventory it recovers at entry is the inventory it
reports at exit. A lane built that way can prove discovery, banding, state
recovery, surface generation, terminal handling and rollover -- and it can
never show OPEN fill -> inventory change -> COMPLETE fill -> matched
inventory, because it has no fill mechanism at all. Reporting "0 first fills"
from such a lane would read as a market that declined our quote. It isn't
one.

WHAT THIS IS. The SAME queue accounting `tape_replay` uses offline, driven
forward cycle by cycle against the live public tape: `Quote.take` makes the
displayed queue eat first, `reaches` requires the OPPOSITE aggressor
direction, and a price change loses queue position. The policy is re-asked
after every hypothetical fill, so inventory moves before the next print is
looked at -- the whole point of the sequential engine.

TWO DECISION PATHS, AND WHICH IS WHICH. The rungs set at a CYCLE boundary
come from the lane's own `WindowGuard.step` plus the adapter -- the same
actions the lane records as `model_a_wanted` / `vetoed_by_adapter`. The
re-target that follows a hypothetical fill MID-CYCLE calls `tape_replay
.desired` instead, without the guard. That is deliberate: it is the exact
path the offline engine takes, so live and replay agree by construction, and
the guard's job is order safety -- one outstanding order, cohort binding --
which has no meaning for a rung that does not exist. It is stated here
because the evidence would otherwise read as though every rung passed the
guard.

WHAT THIS IS NOT. No order is submitted. No session exists. Nothing here is
SUBMITTED, FILLED, SETTLED or REALIZED, and a hypothetical fill is not a
cost basis. It is an UPPER BOUND on what a rung could have caught: Gemini
serves no book history, size ahead is the displayed size at placement only,
and our own presence would have changed the very flow being replayed --
which historical replay cannot settle in either direction.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution import tape_replay as TR

#: what a hypothetical fill is tagged with wherever it reaches an inventory
SOURCE = "SHADOW_LIVE_HYPOTHETICAL"

LABELS = ("HYPOTHETICAL. No order existed and nothing was submitted, filled, "
          "settled or realized. An upper bound: queue position is the "
          "displayed size at placement, and our own presence would have "
          "changed this flow in a direction replay cannot resolve.")


def normalize(prints: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """`public_flow` rows into the shape the replay engine reads.

    It speaks `qty`/`side`; `reaches` and `take` speak `amount`/`type`. The
    translation is here and nowhere else, so the two engines cannot drift.
    """
    out: List[Dict[str, Any]] = []
    for p in prints or []:
        ms = TR._dec(p.get("utc_ms"))
        amt = TR._dec(p.get("amount"))
        if amt is None:
            amt = TR._dec(p.get("qty"))
        if ms is None or amt is None:
            continue
        out.append({"tid": p.get("tid"), "utc_ms": ms, "amount": amt,
                    "price": p.get("price"),
                    "type": str(p.get("type") or p.get("side") or "").lower()})
    out.sort(key=lambda r: r["utc_ms"])
    return out


class HypotheticalBook:
    """The rungs this lane would have rested, walked against the real tape."""

    def __init__(self, *, symbol: str, policy: str,
                 default_qty: Any = "0.01") -> None:
        self.symbol = symbol
        self.policy = policy
        self.qty = TR._dec(default_qty) or D("0.01")
        self.live: Dict[str, TR.Quote] = {}
        self.fills: List[Dict[str, Any]] = []
        self.placed = 0
        self.requoted = 0
        self.seen: set = set()
        self.high_water_ms: Optional[D] = None

    # ------------------------------------------------------------ rungs
    def retarget(self, actions: Sequence[Mapping[str, Any]], *,
                 now_ms: Any, depth: Any) -> Dict[str, Any]:
        """Bring the hypothetical rungs in line with what the lane wants."""
        ev = TR._reconcile(self.live, list(actions or []),
                           now_ms=TR._dec(now_ms) or D("0"), depth=depth,
                           default_qty=self.qty)
        self.placed += ev["placed"]
        self.requoted += ev["requoted"]
        return ev

    # ------------------------------------------------------------ tape
    def _fresh(self, prints: Sequence[Mapping[str, Any]]
               ) -> List[Dict[str, Any]]:
        """Prints not already walked.

        Deduplicated by trade id where the venue gives one. A print with no
        id is admitted only if it is strictly newer than everything already
        walked, so a re-fetched tape cannot fill the same rung twice.
        """
        out = []
        for p in prints:
            tid = p.get("tid")
            if tid is not None:
                if tid in self.seen:
                    continue
                self.seen.add(tid)
                out.append(p)
                continue
            if self.high_water_ms is not None \
                    and p["utc_ms"] <= self.high_water_ms:
                continue
            out.append(p)
        if prints:
            newest = max(p["utc_ms"] for p in prints)
            self.high_water_ms = (newest if self.high_water_ms is None
                                  else max(self.high_water_ms, newest))
        return out

    def consume(self, prints: Sequence[Mapping[str, Any]], *, inv,
                book: Mapping[str, Any], depth: Any,
                tte_s: Any) -> List[Dict[str, Any]]:
        """Walk the new prints against the resting rungs, oldest first."""
        got_any: List[Dict[str, Any]] = []
        for p in self._fresh(normalize(prints)):
            for leg in list(self.live):
                q = self.live.get(leg)
                if q is None or not TR.reaches(leg, q.price, p):
                    continue
                got, _ate = q.take(p["amount"])
                if got <= 0:
                    continue
                was_flat = inv.residual() == 0 and inv.matched() == 0
                inv.apply_fill(leg, got, q.price, source=SOURCE)
                row = {"leg": leg, "qty": str(got), "price": str(q.price),
                       "kind": q.kind, "utc_ms": str(p["utc_ms"]),
                       "tte_s": tte_s, "tid": p.get("tid"),
                       "was_first_fill": was_flat,
                       "queue_known": q.queue_known,
                       "size_ahead_at_place": (str(q.ahead)
                                               if q.ahead is not None
                                               else None),
                       "print_amount": str(p["amount"]),
                       "labels": LABELS}
                self.fills.append(row)
                got_any.append(row)
                if q.filled >= q.qty:
                    del self.live[leg]
                # INVENTORY MOVED, SO WHAT THE POLICY WANTS MOVED -- before
                # the next print is looked at. This is the sequential step
                # the offline engine takes and the reason it is reused here.
                d = TR.desired(self.policy, symbol=self.symbol, tte_s=tte_s,
                               inv=inv, book=book or {})
                row["retargeted_by"] = "tape_replay.desired (no WindowGuard)"
                self.retarget(d["kept"], now_ms=p["utc_ms"], depth=depth)
        return got_any

    # ------------------------------------------------------------ report
    def report(self) -> Dict[str, Any]:
        from src.gemini_execution import continuous_probe as CP
        return {
            "rungs_placed": self.placed,
            "requotes": self.requoted,
            # A REQUOTE IS A NEW RUNG. It goes to the back of its level and
            # has to earn its place again, so the honest denominator for fill
            # conversion is every rung that ever rested -- not just the first
            # one on each leg. Counting only `placed` would divide fills by
            # two in a window that rested eleven rungs.
            "rungs_rested": self.placed + self.requoted,
            "fills": len(self.fills),
            "first_fills": sum(1 for f in self.fills if f["was_first_fill"]),
            "open_fills": sum(1 for f in self.fills
                              if f["kind"] == CP.OPEN),
            "extend_fills": sum(1 for f in self.fills
                                if f["kind"] == CP.EXTEND),
            "complete_fills": sum(1 for f in self.fills
                                  if f["kind"] == CP.COMPLETE),
            "fills_resting_on_an_unknown_queue": sum(
                1 for f in self.fills if not f["queue_known"]),
            "rows": self.fills,
            "labels": LABELS,
        }
