"""The live safety overlay. It may do LESS than the simulated arm, never more.

The frozen scorer latches PROFIT_LOCK_5 the instant its modelled floor reaches
$5, and in a simulator that is sound because clearing `qstate` cancels orders
that never really existed. Live, an emitted CANCEL is a REQUEST. Exposure does
not disappear because we asked it to, and a maker order can fill in the gap
between the request and the venue honouring it.

So the latch becomes a transaction, in this order and no other:

    LOCK CANDIDATE
      -> CANCEL ALL RESTING ACQUISITION ORDERS
      -> CONFIRM CANCELLATIONS / RECONCILE ANY RACE FILLS
      -> RECOMPUTE settlement_independent_floor FROM AUTHENTICATED
         INVENTORY / CASH / FEES
      -> LATCH ONLY IF THAT FLOOR >= $5

A race fill is applied FIRST and the floor recomputed after it, because a fill
that landed is inventory whatever our intent was. If the cancel/fill state
cannot be established, the transaction FAILS CLOSED: not locked, no new
acquisition risk, and the uncertainty is reported rather than resolved by
assumption.

The floor is recomputed with `charge_resting=False`, which is the frozen rule.
Charging resting orders would be a DIFFERENT POLICY and would invalidate the
25/25 parity, so the overlay handles the simulator/live difference
transactionally instead of by changing the rule.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_completion_shadow as S
from src import pmus_l5_native_engine as E

CANDIDATE, CANCELLING, RECONCILING = "CANDIDATE", "CANCELLING", "RECONCILING"
LATCHED, FAILED_CLOSED = "LATCHED", "FAILED_CLOSED"


class CancelUncertain(RuntimeError):
    """The venue did not tell us, clearly, what happened to our orders."""


class LockTransaction:
    """One attempt to turn a lock CANDIDATE into a latched LOCK5."""

    def __init__(self, engine: "E.NativeEngine", venue, *, journal=None,
                 cancel_all=None) -> None:
        self.eng = engine
        self.venue = venue
        #: how to cancel EVERY nonterminal session order. The money path owns
        #: the session registry, so it supplies this; cancelling via the venue's
        #: per-side handle alone left replaced-but-unconfirmed orders live
        #: through a latch, which is exposure a latched lock is supposed to end.
        self.cancel_all_fn = cancel_all
        self.journal = journal
        self.state = CANDIDATE
        self.steps: List[dict] = []
        self.race_fills: List[dict] = []
        self.reason: Optional[str] = None

    def _note(self, step: str, **kw: Any) -> None:
        row = {"step": step, "state": self.state, **kw}
        self.steps.append(row)
        if self.journal is not None:
            try:
                self.journal.write("lock_transaction", row)
            except Exception:                                  # noqa: BLE001
                pass

    def run(self, candidate: "E.Intent") -> Dict[str, Any]:
        """Returns the verdict. Only a LATCHED verdict permits the latch."""
        try:
            self._cancel_all()
            self._confirm_and_reconcile()
            return self._recompute_and_latch(candidate)
        except CancelUncertain as exc:
            return self._fail_closed("CANCEL_STATE_UNCERTAIN:%s" % exc)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return self._fail_closed("%s:%s" % (type(exc).__name__,
                                                str(exc)[:120]))

    # ------------------------------------------------------------- the steps

    def _cancel_all(self) -> None:
        self.state = CANCELLING
        if self.cancel_all_fn is not None:
            res = self.cancel_all_fn()
            self._note("cancel_requested", via="session_registry", result=res)
            return
        asked = [s for s in ("up", "down") if self.eng.resting.get(s)]
        self._note("cancel_requested", sides=asked)
        for side in asked:
            self.venue.cancel_resting(side)
        # AN EMITTED CANCEL IS NOT CONFIRMATION. Nothing about exposure is
        # concluded here; that is the next step's job.

    def _confirm_and_reconcile(self) -> None:
        self.state = RECONCILING
        # 1. AUTHENTICATED open orders. A read we cannot trust is uncertainty,
        #    not "probably none".
        try:
            still = self.venue.open_orders()
        except BaseException as exc:                           # noqa: BLE001
            raise CancelUncertain("open_orders unreadable: %s"
                                  % type(exc).__name__)
        if still is None:
            raise CancelUncertain("open_orders returned no answer")

        # 2. RACE FILLS FIRST. A fill that landed while we were cancelling is
        #    inventory, whatever our intent was, so it is booked BEFORE the
        #    floor is recomputed.
        try:
            fills = self.venue.unbooked_fills()
        except BaseException as exc:                           # noqa: BLE001
            raise CancelUncertain("fills unreadable: %s" % type(exc).__name__)
        if fills is None:
            raise CancelUncertain("fill state returned no answer")
        for f in fills:
            side = str(f["side"])
            qty, cost = D(str(f["qty"])), D(str(f["cost"]))
            self.eng.on_fill(side, qty, cost,
                             maker=bool(f.get("maker", True)),
                             fee=(None if f.get("fee") is None
                                  else D(str(f["fee"]))))
            self.race_fills.append({"side": side, "qty": str(qty),
                                    "cost": str(cost)})
        self._note("race_fills_booked", n=len(self.race_fills),
                   fills=self.race_fills)

        # 3. only now may we conclude anything about resting exposure
        if still:
            raise CancelUncertain("orders still live at the venue: %s"
                                  % (sorted(still) if isinstance(still, (list, tuple, set))
                                     else still))
        for side in ("up", "down"):
            self.eng.resting[side] = None
        self._note("cancellations_confirmed", open_orders=0)

    def _recompute_and_latch(self, candidate: "E.Intent") -> Dict[str, Any]:
        # THE FLOOR IS RE-DERIVED, never carried over from the candidate. The
        # modelled floor that raised the candidate was computed before the
        # race fills were known.
        floor = S.settlement_independent_floor(self.eng.bk)
        self._note("floor_recomputed", floor=str(floor),
                   modelled=str(candidate.get("floor_modelled")))
        if floor < self.eng.profit_lock_usd:
            return self._fail_closed(
                "FLOOR_BELOW_THRESHOLD_AFTER_RECONCILIATION:%s<%s"
                % (floor, self.eng.profit_lock_usd))
        self.state = LATCHED
        self.eng.confirm_lock(via=str(candidate.get("via")),
                              t=candidate.get("t"), floor=floor)
        self._note("latched", floor=str(floor), via=candidate.get("via"))
        return {"verdict": LATCHED, "floor": floor,
                "race_fills": self.race_fills, "steps": self.steps}

    def _fail_closed(self, reason: str) -> Dict[str, Any]:
        self.state = FAILED_CLOSED
        self.reason = reason
        self.eng.abort_lock(reason)
        self._note("failed_closed", reason=reason)
        return {"verdict": FAILED_CLOSED, "reason": reason,
                "race_fills": self.race_fills, "steps": self.steps}


def lock_gate_for(venue, *, journal=None):
    """The callable `NativeEngine(lock_gate=...)` takes. Never latches by itself."""
    def _gate(*_a, **_kw):
        return False        # a candidate is never a latch; the overlay decides
    _gate.venue = venue
    _gate.journal = journal
    return _gate
