"""P2. Frozen decision vs mode-switch decision, side by side. Submits nothing.

For every frozen LOCK5 decision this records what frozen did and what the
fitted L5 mode-switch candidate WOULD have done, with the state both saw.
It returns nothing the caller can act on: `compare()` is a log row.

The candidate is `l5_mode_switch`, fitted on 10,992 imbalanced BTC-15m
states, where the only feature that predicts L5's next side out of sample is
his own previous action (0.700 accuracy vs a 0.502 baseline; absolute
residual scores 0.498 and residual/matched 0.493, worse than chance).

WHY IT IS ONLY A SHADOW. Our own execution-aware replay puts our
P(EXTEND|prev EXTEND) at 30.0% against L5's 70.3%, so the disagreement is
real -- but 96.4% of our consecutive same-side fills come from a REPRICED
order rather than a persistent resting one, and L5's persistence may be
partly the queue mechanics of a resting ladder rather than a decision.
Public fills cannot separate those. This measures disagreement; it does not
claim the candidate is better.
"""
from __future__ import annotations

import time
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import l5_mode_switch as SW


class DecisionShadow:
    """Append-only log of frozen-vs-candidate. No authority, no side effects."""

    def __init__(self, *, clock=time.time, model=None) -> None:
        self.clock = clock
        self.rows: List[Dict[str, Any]] = []
        self._model = model
        self._prev_extended: Optional[bool] = None

    @property
    def model(self):
        if self._model is None:
            self._model = SW.load_model()
        return self._model

    def observe_fill(self, side: str, *, held: Optional[str]) -> None:
        """Our own realised action, which is what the candidate conditions on.

        `held` is the overweight side BEFORE the fill. A fill on a balanced
        book sets no mode.
        """
        if held is None:
            self._prev_extended = None
            return
        self._prev_extended = (str(side) == str(held))

    def compare(self, *, frozen_intents, residual: D, matched: D,
                book: Mapping[str, Any], t: Any = None) -> Dict[str, Any]:
        """One log row. Called with what frozen decided, after it decided."""
        resid = D(str(residual))
        held = "up" if resid > 0 else ("down" if resid < 0 else None)
        frozen_sides = sorted({str(i["side"]) for i in (frozen_intents or [])
                               if "side" in i})
        cand = SW.preferred_side(held=held, prev_extended=self._prev_extended)
        mode = SW.mode_of(held=held, prev_extended=self._prev_extended)
        row = {
            "t": t if t is not None else self.clock(),
            "residual": str(resid), "matched": str(matched), "held": held,
            "prev_extended": self._prev_extended,
            "book": {k: (None if book.get(k) is None else str(book.get(k)))
                     for k in ("bid", "ask", "bid_qty", "ask_qty")},
            "frozen_sides": frozen_sides,
            "frozen_two_sided": len(frozen_sides) > 1,
            "candidate_side": cand,
            "candidate_mode": mode,
            # frozen quotes BOTH sides, so it expresses no preference at all;
            # a disagreement is recorded only where the candidate would have
            # narrowed to one side frozen also offered.
            "disagrees": bool(cand is not None and frozen_sides
                              and len(frozen_sides) > 1),
            "submitted": False,
        }
        self.rows.append(row)
        return row

    def report(self) -> Dict[str, Any]:
        n = len(self.rows)
        imb = [r for r in self.rows if r["held"] is not None]
        dis = [r for r in imb if r["disagrees"]]
        modes: Dict[str, int] = {}
        for r in imb:
            modes[r["candidate_mode"]] = modes.get(r["candidate_mode"], 0) + 1
        return {
            "LABEL": "L5_DECISION_SHADOW",
            "submits": False,
            "decisions": n,
            "imbalanced": len(imb),
            "would_narrow_to_one_side": len(dis),
            "narrow_rate": (round(len(dis) / len(imb), 4) if imb else None),
            "candidate_modes": modes,
            "labels": ("frozen TWO_SIDED_OUR_TOUCH expresses no side "
                       "preference, so 'disagrees' means the candidate would "
                       "have quoted ONE side where frozen quoted both. "
                       "Nothing here was submitted."),
        }
