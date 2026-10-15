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

from decimal import Decimal as D, ROUND_CEILING, InvalidOperation
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


#: set-but-invalid is a CONFIGURATION ERROR, never a silent disable
class DeficitBoundInvalid(ValueError):
    """The deficit bound was explicitly configured and cannot be used."""


def parse_deficit_bound(raw: Optional[str]) -> Optional[D]:
    """Runtime value -> the bound, or None when genuinely unset.

    FAIL CLOSED ON A BAD VALUE. Unset and blank mean "not configured", which
    leaves the gate disabled and changes nothing. But a value that was typed
    and cannot be used is an operator who believes a bound is in force when
    it is not, so it raises instead of falling back to None. The caller must
    not run unprotected on the strength of a typo.
    """
    if raw is None:
        return None
    txt = str(raw).strip()
    if not txt:
        return None
    try:
        v = D(txt)
    except (InvalidOperation, ValueError, ArithmeticError):
        raise DeficitBoundInvalid(
            "not a number: %r" % (txt[:40],))
    if not v.is_finite():
        raise DeficitBoundInvalid("not finite: %r" % (txt[:40],))
    # a NEGATIVE bound is legitimate and strict: it demands the book be
    # profitable on both outcomes. An absurd magnitude is a typo, not a
    # policy, and a bound above the whole mandate protects nothing.
    if v > D("1000") or v < D("-1000"):
        raise DeficitBoundInvalid("implausible magnitude: %s" % v)
    return v


class ValidationGates:
    """Run-scoped gates for one bounded validation. Refuse-only."""

    def __init__(self,
                 max_cash_deployed_usd: D = D("10"),
                 max_unmatched_contracts: D = D("5"),
                 max_cumulative_loss_usd: D = D("20"),
                 cash_policy: str = NO_RECYCLING,
                 max_windows: int = 20,
                 max_worst_case_deficit_usd: Optional[D] = None,
                 fee_theta_worst: D = D("0.0695"),
                 fee_extra_per_order_usd: D = D("0.01")) -> None:
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
        # DEFAULT DISABLED. None means the gate is not configured and never
        # refuses, so adding it changes no existing behaviour until an owner
        # sets a number.
        self.max_worst_case_deficit_usd = (
            None if max_worst_case_deficit_usd is None
            else _d(max_worst_case_deficit_usd))
        # the TAKER coefficient, always. The maker rebate is a credit this
        # accounting has never been entitled to assume, so the worst case is
        # priced as if every leg paid the taker fee.
        self.fee_theta_worst = _d(fee_theta_worst)
        # ONE CENT PER ORDER, AND IT IS LOAD-BEARING. Cent rounding breaks
        # the convexity the four-corner search relies on: a SMALL partial
        # fill on an expensive leg is net harmful, because the whole cent of
        # rounding outweighs the (1 - price) benefit of the quantity it adds.
        # Brute force over partial fills found exactly that, understating a
        # case by $0.0016. The harm per order from this effect cannot exceed
        # the rounding itself, since the quantity benefit is never negative,
        # so a flat cent per order dominates it and restores the corner
        # search to an upper bound. Verified by
        # test_the_four_corners_equal_the_exhaustive_worst_case.
        self.fee_extra_per_order_usd = _d(fee_extra_per_order_usd)

    def as_dict(self) -> Dict[str, Any]:
        return {"max_cash_deployed_usd": str(self.max_cash_deployed_usd),
                "max_unmatched_contracts": str(self.max_unmatched_contracts),
                "max_cumulative_loss_usd": str(self.max_cumulative_loss_usd),
                "cash_policy": self.cash_policy,
                "max_windows": self.max_windows,
                "max_worst_case_deficit_usd": (
                    None if self.max_worst_case_deficit_usd is None
                    else str(self.max_worst_case_deficit_usd)),
                "fee_theta_worst": str(self.fee_theta_worst),
                "fee_extra_per_order_usd": str(self.fee_extra_per_order_usd),
                "fee_model": ("taker Theta, rounded UP to the cent once per "
                              "ORDER. Per-FILL rounding is not modelled and "
                              "is bounded by the live order count; see "
                              "fee_extra_per_order_usd."),
                "worst_case_deficit_basis": (
                    "C - min(up_qty, dn_qty), where C is confirmed "
                    "acquisition cost plus this order plus the worst "
                    "subset of still-fillable outstanding orders, with "
                    "the TAKER fee charged on every leg. Below zero the "
                    "book pays out on BOTH settlement outcomes."),
                "cash_basis": ("filled (unreleased) + still-fillable "
                               "outstanding + the proposed order, "
                               "ACCOUNT-WIDE. Exposure adopted from the "
                               "venue counts: the account is shared and the "
                               "safe direction is to count it."),
                "fails_closed_on": "an unconfirmed or disputed ledger",
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

    # ------------------------------------------- worst-case settlement

    #: an unreadable price is charged at the most a share can cost
    UNKNOWN_PRICE_WORST_CASE = D("1")

    def fee_worst(self, qty: D, price: D) -> D:
        """Conservative fee on one leg: Theta * C * p * (1-p), TAKER Theta.

        The maker rebate is NEVER netted here. A credit we are not
        guaranteed, applied to a worst case, is not a worst case.
        """
        q = _d(qty); p = _d(price)
        if p < 0 or p > 1:
            p = self.UNKNOWN_PRICE_WORST_CASE
        raw = self.fee_theta_worst * q * p * (D(1) - p)
        if raw <= 0:
            return raw
        # ROUNDED UP TO THE CENT, ONCE PER ORDER. The venue bills commission
        # in whole cents -- our own records show -0.0300 and +0.2400, never a
        # fraction of one -- so an exact-Decimal fee can sit just under the
        # cent actually taken, and a sub-cent fee on a tiny clip reads as
        # very nearly free. Rounding up is the only safe direction.
        #
        # THE LIMIT OF THIS MODEL, STATED. Cent rounding is super-additive:
        # if the venue rounds per FILL rather than per order, an order that
        # fills in n pieces pays up to (n-1) extra cents, and no usefully
        # tight bound dominates that -- a 2.84-contract order could in
        # principle fill in 284 pieces of the 0.01 minimum and pay $2.84.
        # The per-order model is what our own commission records show, and
        # the exposure is bounded instead by the order count: with
        # max_live_orders at 2 the worst unmodelled rounding is a few cents.
        # `fee_extra_per_order_usd` exists to carry that allowance when an
        # owner wants it priced rather than argued.
        return (raw.quantize(D("0.01"), rounding=ROUND_CEILING)
                + self.fee_extra_per_order_usd)

    def worst_case_deficit(self, *,
                           up_qty: Any, dn_qty: Any,
                           up_cost: Any, dn_cost: Any,
                           order_side: str,
                           order_qty: Any, order_price: Any,
                           outstanding: Sequence[Mapping[str, Any]] = (),
                           ) -> Dict[str, Any]:
        """`C - min(up, dn)` for the book this order would create, maximised
        over which outstanding orders fill.

        WHY THIS QUANTITY. A book holding `a` UP and `b` DOWN for total
        executed cost `C` pays `a` if UP settles and `b` if DOWN settles, so
        its worst outcome is `min(a, b) - C`. The deficit is the negative of
        that. Strictly below zero, the book is profitable on BOTH outcomes
        and carries no directional risk at all; at `x` above zero, `x` is the
        most the book can lose however the window resolves.

        THE PROPOSED ORDER IS ASSUMED TO FILL. That is the question being
        asked -- what would this order leave us holding -- so a completing
        order is NOT exempt and is not special-cased: it is priced exactly
        like an opening one and simply happens to reduce the deficit.

        OUTSTANDING ORDERS ARE AN ADVERSARY, NOT AN ASSUMPTION. Each may or
        may not fill. Filling adds its cost AND its quantity, so it raises
        the deficit when its side is not the binding minimum and lowers it
        when it is. Within one side the choice is all-or-nothing -- adding
        more of a side that does not lift the minimum only ever costs -- so
        the maximum over every subset is attained at one of four corners:
        nothing fills, only UP fills, only DOWN fills, everything fills.
        All four are evaluated and the WORST is returned, which is exact
        rather than merely conservative.

        Returns {'ok': bool, 'deficit': D, 'scenario': str, 'scenarios': ...}
        or {'ok': False, 'reason': ...} when the state cannot be read. An
        unreadable input is never silently treated as zero.
        """
        side = str(order_side or "").upper()
        if side not in ("UP", "DOWN"):
            return {"ok": False, "reason": "DEFICIT_ORDER_SIDE_UNKNOWN"}
        try:
            a0 = _d(up_qty); b0 = _d(dn_qty)
            cu = _d(up_cost); cd = _d(dn_cost)
            oq = _d(order_qty); op = _d(order_price)
        except Exception:                                  # noqa: BLE001
            return {"ok": False, "reason": "DEFICIT_INVENTORY_UNREADABLE"}
        if a0 < 0 or b0 < 0 or cu < 0 or cd < 0 or oq <= 0:
            return {"ok": False, "reason": "DEFICIT_INVENTORY_IMPLAUSIBLE"}
        # QUANTITY WITHOUT COST is an incoherent book, not a free position.
        if (a0 > 0 and cu <= 0) or (b0 > 0 and cd <= 0):
            return {"ok": False, "reason": "DEFICIT_QUANTITY_WITHOUT_COST"}
        if op <= 0 or op > 1:
            op = self.UNKNOWN_PRICE_WORST_CASE

        # the book this order would create, before any outstanding order
        base_a = a0 + (oq if side == "UP" else D(0))
        base_b = b0 + (oq if side == "DOWN" else D(0))
        base_c = cu + cd + oq * op + self.fee_worst(oq, op)

        # each outstanding leg, bucketed by side; an unreadable one fails closed
        add = {"UP": [D(0), D(0)], "DOWN": [D(0), D(0)]}   # qty, cost
        for o in outstanding:
            q = _d(o.get("qty"))
            if q <= 0:
                continue
            px = o.get("price")
            px = self.UNKNOWN_PRICE_WORST_CASE if px is None else _d(px)
            if px <= 0 or px > 1:
                px = self.UNKNOWN_PRICE_WORST_CASE
            intent = str(o.get("intent") or o.get("side") or "").upper()
            if intent.endswith("BUY_SHORT") or intent == "DOWN":
                s2 = "DOWN"
            elif intent.endswith("BUY_LONG") or intent == "UP":
                s2 = "UP"
            else:
                return {"ok": False, "reason": "DEFICIT_OUTSTANDING_SIDE_UNKNOWN"}
            add[s2][0] += q
            add[s2][1] += q * px + self.fee_worst(q, px)

        corners = {
            "none": (D(0), D(0), D(0), D(0)),
            "up_only": (add["UP"][0], add["UP"][1], D(0), D(0)),
            "dn_only": (D(0), D(0), add["DOWN"][0], add["DOWN"][1]),
            "all": (add["UP"][0], add["UP"][1],
                    add["DOWN"][0], add["DOWN"][1]),
        }
        out: Dict[str, str] = {}
        worst = None; worst_name = ""
        for name, (ua, uc, da, dc) in corners.items():
            a = base_a + ua; b = base_b + da
            c = base_c + uc + dc
            d = c - min(a, b)
            out[name] = str(d)
            if worst is None or d > worst:
                worst = d; worst_name = name

        # THE NON-CONVEXITY ALLOWANCE, and it is why the corner search is
        # sound. With linear fees the deficit is convex in the fill
        # fractions, so its maximum sits at a vertex and four corners
        # suffice. Cent rounding breaks that: a SMALL partial fill on an
        # expensive leg is net harmful, because a whole cent of rounding
        # outweighs the (1 - price) benefit of the quantity it adds, and the
        # `none` corner charges no fee at all for an order a partial fill
        # does charge. Brute force over partial fills found exactly that.
        # The excess of any partial arrangement over the corner maximum is
        # bounded, per order, by the rounding granularity it can introduce --
        # one cent plus the per-order allowance -- so charging that much per
        # still-fillable order restores the corner maximum to a genuine
        # upper bound. Pinned by
        # test_the_four_corners_equal_the_exhaustive_worst_case.
        n_out = sum(1 for o in outstanding if _d(o.get("qty")) > 0)
        slack = (D("0.01") + self.fee_extra_per_order_usd) * D(n_out)
        worst = worst + slack
        out["rounding_slack"] = str(slack)
        return {"ok": True, "deficit": worst, "scenario": worst_name,
                "scenarios": out,
                "book": {"up_qty": str(base_a), "dn_qty": str(base_b),
                         "cost": str(base_c)},
                "labels": ("deficit = cost - min(up, dn), maximised over "
                           "which outstanding orders fill. Negative means "
                           "the book pays on BOTH outcomes.")}

    # -------------------------------------------------------- refusal

    def refuse(self, *,
               order_qty: D,
               order_price: D,
               order_intent: str,
               filled_legs: Sequence[Mapping[str, Any]] = (),
               outstanding: Sequence[Mapping[str, Any]] = (),
               unmatched_after: D = D(0),
               cumulative_realized: D = D(0),
               cumulative_unrealized_loss: Optional[D] = D(0),
               windows_used: int = 0,
               book_uncertain: Optional[str] = None,
               available_cash_usd: Optional[D] = None,
               inventory: Optional[Mapping[str, Any]] = None
               ) -> Optional[str]:
        """None to allow, else the reason. Never raises on ordinary input."""
        q = _d(order_qty)
        px = _d(order_price)

        # 0. THE BOOK ITSELF, BEFORE ANY FIGURE COMPUTED FROM IT. Every cash
        #    and inventory number below is read out of the ledger, so a
        #    ledger the venue has not confirmed makes all of them
        #    unauthoritative. Refusing here rather than relying on the
        #    deployed ORDER_STATE_UNCERTAIN one layer down keeps this gate
        #    honest about its own inputs: an incomplete or disputed
        #    reconciliation is not a small cash figure, it is an unknown one.
        if book_uncertain:
            return "BOOK_UNCERTAIN:%s" % str(book_uncertain)[:160]

        # 1. unmatched directional inventory
        if abs(_d(unmatched_after)) > self.max_unmatched_contracts:
            return ("UNMATCHED_CONTRACTS:%s>%s"
                    % (abs(_d(unmatched_after)), self.max_unmatched_contracts))

        # 2. cumulative loss for the whole run, realized PLUS a conservative
        #    unrealized mark. A loss is a NEGATIVE realized figure.
        #
        #    MISSING IS NOT ZERO WHEN PROTECTION DEPENDS ON IT. The unrealized
        #    term used to default to 0 and was never supplied by the caller,
        #    so the run-scoped ceiling counted only SETTLED losses and an open
        #    book about to lose could not trip it. It is now required whenever
        #    this ceiling is the live protection: None is a refusal, not a
        #    zero. The caller computes it from the reconciled book, and the
        #    deficit helper below is exactly the conservative figure -- the
        #    most the book can lose on either outcome.
        if cumulative_unrealized_loss is None:
            return "CUMULATIVE_UNREALIZED_UNAVAILABLE"
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
        # 4. WORST-CASE SETTLEMENT DEFICIT. Default disabled; when an owner
        #    configures it, no order may leave the book able to lose more
        #    than the bound on EITHER outcome. This is the only gate that
        #    knows a two-sided book can be safe at a cost the cash gate
        #    would otherwise treat as pure exposure.
        if self.max_worst_case_deficit_usd is not None:
            if not isinstance(inventory, Mapping):
                # FAIL CLOSED. Configured and un-evaluable is a refusal: the
                # bound exists precisely for the states we cannot see.
                return "WORST_CASE_DEFICIT_NO_INVENTORY"
            wc = self.worst_case_deficit(
                up_qty=inventory.get("up_qty"),
                dn_qty=inventory.get("dn_qty"),
                up_cost=inventory.get("up_cost"),
                dn_cost=inventory.get("dn_cost"),
                order_side=("DOWN" if str(order_intent or "").upper()
                            .endswith("BUY_SHORT") else "UP"),
                order_qty=q, order_price=px, outstanding=outstanding)
            if not wc.get("ok"):
                return "WORST_CASE_DEFICIT:%s" % wc.get("reason")
            if _d(wc["deficit"]) > self.max_worst_case_deficit_usd:
                return ("WORST_CASE_DEFICIT:%s>%s:%s"
                        % (wc["deficit"], self.max_worst_case_deficit_usd,
                           wc["scenario"]))

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
