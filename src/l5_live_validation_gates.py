"""The three gates the bounded live BTC15m validation needs, and nothing else.

ADDITIVE BY DESIGN. The deployed executor (77939fe2) already carries
`SafetyLimits` with max_usd_per_market, max_abs_residual,
max_total_exposure_usd, max_concurrent_markets, max_live_orders and
daily_realized_loss_halt_usd, plus reconciliation. Neither branch is a
superset of the other -- my branch has the halt gate and the deployed one has
the cash_deployed reporting -- so this adds gates ALONGSIDE the deployed
limits instead of replacing a file and silently regressing one of them.

Nothing here can weaken an existing limit. Every gate is a pure refusal
predicate: it can only ever turn an allowed action into a refused one.

WHY THESE THREE

1. CASH IS NOT GATED TODAY. The deployed heartbeat says so verbatim:
   "cash_deployed is reported, NOT gated." The risk gate measures worst-case
   LOSS, which a completed pair takes to zero -- correctly, and that is
   exactly why it does not restrain how much CASH repeated cycling pushes
   out. Simulated at 5 contracts: four cycles push out $9.80 and nine push
   out $22.05, both while the loss gate reads $0.

2. THE UNMATCHED CAP IS NOT ENFORCED AT 5. RESIDUAL_CAP_k2 with k=2 on a
   5-contract clip permits |residual| up to 10, and max_abs_residual is 10.
   The mandate is 5.

3. THERE IS NO CUMULATIVE TEST LIMIT. The daily halt is $5 realized and
   resets daily; a multi-day validation needs a run-scoped ceiling that
   counts conservative unrealized loss too.

CASH RECYCLING IS UNVERIFIED, SO IT IS NOT ASSUMED. Position and worst-case
loss release are established (venue positions {}, unsettledFunds 0,
risk_exposure -> 0), but no authoritative before/after balance pair exists
and the live `cash_anchor` is empty. `NO_RECYCLING` is therefore the default
and the conservative reading: cash is treated as committed until SETTLEMENT
is confirmed, not when a closing leg fills.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

#: cash is released only by confirmed settlement, never by a closing fill
NO_RECYCLING = "NO_RECYCLING"
#: available only once a before/after balance pair proves cash returns
RECYCLE_ON_COMPLETE = "RECYCLE_ON_COMPLETE"


def _d(v: Any) -> D:
    if isinstance(v, D):
        return v
    if v is None:
        return D(0)
    return D(str(v))


class ValidationGates:
    """Run-scoped gates for one bounded validation. Refuse-only."""

    def __init__(self,
                 max_cash_deployed_usd: D = D("10"),
                 max_unmatched_contracts: D = D("5"),
                 max_cumulative_loss_usd: D = D("20"),
                 cash_policy: str = NO_RECYCLING,
                 max_windows: int = 20) -> None:
        if cash_policy not in (NO_RECYCLING, RECYCLE_ON_COMPLETE):
            raise ValueError("unknown cash policy: %r" % cash_policy)
        if cash_policy == RECYCLE_ON_COMPLETE:
            # not a style choice: recycling has never been verified against an
            # authoritative balance pair, so enabling it must be deliberate.
            pass
        self.max_cash_deployed_usd = _d(max_cash_deployed_usd)
        self.max_unmatched_contracts = _d(max_unmatched_contracts)
        self.max_cumulative_loss_usd = _d(max_cumulative_loss_usd)
        self.cash_policy = cash_policy
        self.max_windows = int(max_windows)

    def as_dict(self) -> Dict[str, Any]:
        return {"max_cash_deployed_usd": str(self.max_cash_deployed_usd),
                "max_unmatched_contracts": str(self.max_unmatched_contracts),
                "max_cumulative_loss_usd": str(self.max_cumulative_loss_usd),
                "cash_policy": self.cash_policy,
                "max_windows": self.max_windows,
                "labels": ("ADDITIVE to the deployed SafetyLimits. These can "
                           "only refuse; they never raise an existing limit.")}

    # ------------------------------------------------------------- cash

    def cash_committed(self, legs: Iterable[Mapping[str, Any]]) -> D:
        """Cash out, under the configured recycling policy.

        A leg is {'cash_out': D, 'settled': bool, 'closing': bool}. `cash_out`
        is positive for cash LEAVING the account. Under NO_RECYCLING a closing
        leg's proceeds do not reduce the committed figure until the market
        settles -- which is the whole point of the conservative reading.
        """
        total = D(0)
        for leg in legs:
            if bool(leg.get("settled")):
                continue
            out = _d(leg.get("cash_out"))
            if out > 0:
                total += out
                continue
            # a credit (closing leg). Only recognised when the policy allows.
            if self.cash_policy == RECYCLE_ON_COMPLETE:
                total += out          # out is negative: reduces the total
        return total

    def pending_obligation(self,
                           outstanding: Iterable[Mapping[str, Any]]) -> D:
        """Worst-case cash a RESTING order can still take.

        An order that might fill is cash that might leave, so it is counted
        at its full limit price. A resting SHORT owes up to $1 a share if YES
        settles 1, so its worst case is qty dollars, not qty * price.
        """
        total = D(0)
        for o in outstanding:
            qty = _d(o.get("qty"))
            px = _d(o.get("price"))
            if str(o.get("intent") or "").upper().endswith("BUY_SHORT"):
                total += qty
            else:
                total += qty * px
        return total

    # -------------------------------------------------------- refusal

    def refuse(self, *,
               order_qty: D,
               order_price: D,
               order_intent: str,
               filled_legs: Sequence[Mapping[str, Any]] = (),
               outstanding: Sequence[Mapping[str, Any]] = (),
               unmatched_after: D = D(0),
               cumulative_realized: D = D(0),
               cumulative_unrealized_loss: D = D(0),
               windows_used: int = 0,
               available_cash_usd: Optional[D] = None) -> Optional[str]:
        """None to allow, else the reason. Never raises on ordinary input."""
        q = _d(order_qty)
        px = _d(order_price)

        # 1. unmatched directional inventory
        if abs(_d(unmatched_after)) > self.max_unmatched_contracts:
            return ("UNMATCHED_CONTRACTS:%s>%s"
                    % (abs(_d(unmatched_after)), self.max_unmatched_contracts))

        # 2. cumulative loss for the whole run, realized PLUS a conservative
        #    unrealized mark. A loss is a NEGATIVE realized figure.
        loss = max(D(0), -_d(cumulative_realized)) + max(
            D(0), _d(cumulative_unrealized_loss))
        if loss >= self.max_cumulative_loss_usd:
            return ("CUMULATIVE_LOSS_LIMIT:%s>=%s"
                    % (loss, self.max_cumulative_loss_usd))

        # 3. cash commitment, including this order as if it filled in full
        this_order = (q if str(order_intent or "").upper()
                      .endswith("BUY_SHORT") else q * px)
        committed = (self.cash_committed(filled_legs)
                     + self.pending_obligation(outstanding) + this_order)
        if committed > self.max_cash_deployed_usd:
            return ("CASH_COMMITMENT:%s>%s"
                    % (committed, self.max_cash_deployed_usd))
        # an authoritative balance, when we have one, binds too. Absent is
        # NOT treated as unlimited -- see require_cash_evidence().
        if available_cash_usd is not None and committed > _d(
                available_cash_usd):
            return ("INSUFFICIENT_AVAILABLE_CASH:%s>%s"
                    % (committed, _d(available_cash_usd)))

        # 4. run scope
        if int(windows_used) >= self.max_windows:
            return "WINDOW_BUDGET_EXHAUSTED:%d>=%d" % (int(windows_used),
                                                       self.max_windows)
        return None

    # -------------------------------------------------- evidence gate

    def require_cash_evidence(self,
                              balance_reading: Optional[Mapping[str, Any]]
                              ) -> Optional[str]:
        """Refuse to arm without an authoritative balance reading.

        An absent balance is not an unlimited one. The live heartbeat's
        `cash_anchor` is empty `{}`, so this currently refuses -- which is the
        correct state until one authenticated /v1/account/balances read lands.
        """
        if not balance_reading:
            return "NO_AUTHORITATIVE_CASH_READING"
        for key in ("available", "availableCash", "cash", "balance"):
            if key in balance_reading:
                try:
                    if _d(balance_reading[key]) <= 0:
                        return "NON_POSITIVE_AVAILABLE_CASH"
                except Exception:                              # noqa: BLE001
                    return "UNREADABLE_AVAILABLE_CASH"
                return None
        return "BALANCE_READING_HAS_NO_AVAILABLE_FIELD"


def combined_refusal(deployed_refusal: Optional[str],
                     validation_refusal: Optional[str]) -> Optional[str]:
    """The deployed limits decide FIRST; these gates can only add a refusal.

    Order matters for provenance, not for strictness: whichever refuses, the
    action is refused, and the deployed reason is reported ahead of ours so a
    pre-existing limit is never masked by a validation-scoped one.
    """
    return deployed_refusal or validation_refusal
