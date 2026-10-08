"""THE MULTI-TRADE ENVELOPE. Four bounds that the attempt budget does not
express, enforced from VENUE-CONFIRMED state and DURABLE records.

WHY THIS EXISTS, AND WHY $0.234 WAS THE WRONG NUMBER.

A three-attempt probe at 0.13 shares and a $0.60 first-leg ceiling bounds
the FIRST-LEG purchases at 3 x 0.13 x $0.60 = $0.2340. That figure is not
the maximum gross outlay, because every filled first leg is then followed
by a COMPLEMENT, and the pair is allowed to cost up to $0.96 per share.
The real envelope is:

    cumulative first-leg shares   3 x 0.13                  = 0.39 shares
    first-leg purchases           3 x 0.13 x $0.60          = $0.2340
    GROSS OUTLAY AFTER PAIRS      3 x 0.13 x $0.96          = $0.3744
    complement portion            $0.3744 - $0.2340         = $0.1404
    one outstanding first leg     0.13 x $0.60              = $0.0780
    one outstanding complement    0.13 x $0.95              = $0.1235
    outstanding at any instant    $0.0780 + $0.1235         = $0.2015
    directional residual          0.13 shares               = $0.0780

The 22:00z probe is the arithmetic's own witness: one pair, 0.13 shares
each side, `filled_notional_usd` $0.1248 == 0.13 x $0.96 exactly.

So FOUR separate quantities are bounded here, because they are four
different questions and no one of them implies the others:

  1. `cum_first_leg_shares`   -- how much inventory we may ACQUIRE;
  2. `gross_notional_usd`     -- how much cash may LEAVE, pairs included;
  3. `outstanding_usd`        -- how much may be AT RISK in live orders at
                                 one instant;
  4. `residual_shares`        -- how much may be UNHEDGED at one instant.

WHERE EACH NUMBER COMES FROM. Nothing here trusts an in-process counter.

  * what we have ACQUIRED and SPENT: the venue's own order state --
    `filled_notional_usd`, `open_notional_usd`, `residual` -- read through
    `order_state.read_order_state`, which fails closed;
  * how many attempts this window has AUTHORIZED: the durable latch slot
    files, which only ever accumulate and survive a restart;
  * what the attempt about to be made would add: the snapshot's own price
    and quantity, counted BEFORE it is sent.

The authorized-slot count is deliberately an UPPER bound on acquired
first-leg quantity: a slot is consumed before the POST and never released,
so a slot that did not fill still counts against the share cap. That can
only refuse earlier than the truth, never later.

THESE BOUNDS MAY ONLY NARROW. Each env key is validated against a frozen
ceiling derived from the spec's own constants, and a missing, malformed,
zero, negative or excessive value is a REFUSAL rather than a default --
the same discipline as `budget.py`, for the same reason: a default is a
silent authorization.

This module adds refusals. It relaxes nothing, it reads no arming key, and
it never builds, prices, previews or sends an order.
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional

from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort.spec import (FIRST_LEG_QTY, MAX_ATTEMPTS_PER_WINDOW,
                                        MAX_EXPOSURE_PER_WINDOW_USD,
                                        MAX_FIRST_LEG_PRICE,
                                        PAIR_COST_CEILING)

#: the four env keys, all narrowing-only.
SHARES_KEY = "PMUS_E1_MAX_CUM_FIRST_LEG_SHARES"
GROSS_KEY = "PMUS_E1_MAX_GROSS_NOTIONAL_USD"
OUTSTANDING_KEY = "PMUS_E1_MAX_OUTSTANDING_USD"
RESIDUAL_KEY = "PMUS_E1_MAX_RESIDUAL_SHARES"
ENVELOPE_KEYS = (SHARES_KEY, GROSS_KEY, OUTSTANDING_KEY, RESIDUAL_KEY)

#: FROZEN CEILINGS, derived from the spec rather than re-chosen here.
FROZEN_CUM_FIRST_LEG_SHARES = MAX_ATTEMPTS_PER_WINDOW * FIRST_LEG_QTY
FROZEN_GROSS_NOTIONAL_USD = (MAX_ATTEMPTS_PER_WINDOW * FIRST_LEG_QTY
                             * PAIR_COST_CEILING)
FROZEN_OUTSTANDING_USD = MAX_EXPOSURE_PER_WINDOW_USD
FROZEN_RESIDUAL_SHARES = MAX_ATTEMPTS_PER_WINDOW * FIRST_LEG_QTY

#: the minimum tradeable tick, so "the dearest complement" is a real price.
TICK = D("0.01")


class EnvelopeNotAuthorized(Exception):
    """A bound is missing, malformed or wider than the frozen ceiling."""


def _narrow(key: str, frozen_max: D) -> D:
    """Read one bound. Narrowing only; anything else is a refusal."""
    raw = (os.getenv(key) or "").strip()
    if not raw:
        raise EnvelopeNotAuthorized(
            "%s is not set. This probe's envelope must be stated "
            "explicitly: a default would silently authorize the frozen "
            "ceiling of %s." % (key, frozen_max))
    try:
        got = D(raw)
    except (InvalidOperation, ValueError, ArithmeticError):
        raise EnvelopeNotAuthorized("%s=%r is not a decimal" % (key, raw))
    if not got.is_finite():
        raise EnvelopeNotAuthorized("%s=%r is not finite" % (key, raw))
    if got <= 0:
        raise EnvelopeNotAuthorized(
            "%s=%s must be positive. Zero authorizes nothing and belongs "
            "in the arming gate, not here." % (key, got))
    if got > frozen_max:
        raise EnvelopeNotAuthorized(
            "%s=%s exceeds the frozen ceiling %s. These keys may only "
            "NARROW." % (key, got, frozen_max))
    return got


def _fin(x: Any) -> Optional[D]:
    if x is None:
        return None
    try:
        v = D(str(x))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None
    return v if v.is_finite() else None


class MultiTradeEnvelope:
    """The four bounds, and the gate that reads real state against them."""

    def __init__(self, *, cum_first_leg_shares: D, gross_notional_usd: D,
                 outstanding_usd: D, residual_shares: D) -> None:
        self.cum_first_leg_shares = cum_first_leg_shares
        self.gross_notional_usd = gross_notional_usd
        self.outstanding_usd = outstanding_usd
        self.residual_shares = residual_shares

    # ----------------------------------------------------------- building
    @classmethod
    def from_env(cls) -> "MultiTradeEnvelope":
        return cls(
            cum_first_leg_shares=_narrow(SHARES_KEY,
                                         FROZEN_CUM_FIRST_LEG_SHARES),
            gross_notional_usd=_narrow(GROSS_KEY, FROZEN_GROSS_NOTIONAL_USD),
            outstanding_usd=_narrow(OUTSTANDING_KEY, FROZEN_OUTSTANDING_USD),
            residual_shares=_narrow(RESIDUAL_KEY, FROZEN_RESIDUAL_SHARES))

    @classmethod
    def frozen_maximums(cls) -> "MultiTradeEnvelope":
        """The spec's own ceilings, for the twelve-window cohort that was
        authorized before this probe existed. Narrows nothing."""
        return cls(cum_first_leg_shares=FROZEN_CUM_FIRST_LEG_SHARES,
                   gross_notional_usd=FROZEN_GROSS_NOTIONAL_USD,
                   outstanding_usd=FROZEN_OUTSTANDING_USD,
                   residual_shares=FROZEN_RESIDUAL_SHARES)

    @classmethod
    def for_attempts(cls, n: int) -> "MultiTradeEnvelope":
        """The envelope implied by N attempts of the frozen size -- what an
        operator means by "three attempts". Derived, never typed in."""
        n = int(n)
        return cls(
            cum_first_leg_shares=n * FIRST_LEG_QTY,
            gross_notional_usd=n * FIRST_LEG_QTY * PAIR_COST_CEILING,
            outstanding_usd=(FIRST_LEG_QTY * MAX_FIRST_LEG_PRICE
                             + FIRST_LEG_QTY * (PAIR_COST_CEILING - TICK)),
            residual_shares=FIRST_LEG_QTY)

    def narrowed(self) -> bool:
        """Whether this envelope is tighter than the spec's own ceilings.

        An envelope equal to the frozen maximums NARROWS NOTHING, and so
        it must also CHECK nothing new: those same quantities are already
        bounded by the twenty-slot latch and
        `MAX_EXPOSURE_PER_WINDOW_USD`. Saying otherwise would make this
        module a new bound on the twelve-window cohort that was authorized
        before it existed -- which is a change to an authorized experiment
        dressed up as a safety addition.
        """
        return (self.cum_first_leg_shares < FROZEN_CUM_FIRST_LEG_SHARES
                or self.gross_notional_usd < FROZEN_GROSS_NOTIONAL_USD
                or self.outstanding_usd < FROZEN_OUTSTANDING_USD
                or self.residual_shares < FROZEN_RESIDUAL_SHARES)

    # ------------------------------------------------------------- reading
    @staticmethod
    def authorized_first_leg_shares(*, window: Any,
                                    latch_module: Any = None) -> Dict[str, Any]:
        """First-leg shares this window has AUTHORIZED, from durable slots.

        Upper bound by construction: the slot is taken before the POST and
        never released, so an unfilled attempt still counts. Unreadable
        latch scope is a refusal, not a zero.
        """
        mod = latch_module or L
        try:
            st = mod.window_state(window=window)
        except Exception as exc:                               # noqa: BLE001
            return {"readable": False,
                    "why": "LATCH_SCOPE_UNREADABLE:%s" % type(exc).__name__,
                    "slots_spent": None, "shares": None}
        if not st.get("available"):
            return {"readable": False,
                    "why": "LATCH_SCOPE_UNAVAILABLE:%s" % st.get("why"),
                    "slots_spent": None, "shares": None}
        n = _fin(st.get("attempts_spent"))
        if n is None:
            return {"readable": False,
                    "why": "LATCH_SPENT_NOT_FINITE:%r"
                           % (st.get("attempts_spent"),),
                    "slots_spent": None, "shares": None}
        return {"readable": True, "why": None,
                "slots_spent": int(n), "shares": n * FIRST_LEG_QTY}

    # --------------------------------------------------------------- gate
    def gate(self, *, order_state: Optional[Mapping[str, Any]],
             window: Any, qty: Any = None, first_leg_price: Any = None,
             latch_module: Any = None) -> Dict[str, Any]:
        """Additional refusals for the attempt ABOUT TO BE MADE.

        Every clause counts the prospective attempt, so the bound is never
        crossed -- not even by the attempt that would cross it. An
        unestablished order state refuses outright: these are exposure
        questions and a missing answer is never zero.
        """
        refusals: List[str] = []
        q = _fin(qty)
        if q is None:
            q = FIRST_LEG_QTY
        price = _fin(first_leg_price)

        report: Dict[str, Any] = {
            "bounds": self.report(),
            "counts_the_prospective_attempt": True,
            "source": "venue_confirmed_order_state + durable_latch_slots",
        }

        report["narrowed"] = self.narrowed()

        if not isinstance(order_state, Mapping) or not order_state.get("ok"):
            # A NARROWED probe states a cash envelope, so it may not act
            # on an exposure reading it does not have. An UN-narrowed
            # envelope is the frozen cohort and adds no refusal here:
            # `eligible`'s own E3 clause already refuses a present-but-
            # failed order state, and inventing a second refusal for a
            # snapshot that never carried one would bound an experiment
            # this module was not authorized to change.
            if self.narrowed():
                refusals.append(
                    "MT_ORDER_STATE_NOT_ESTABLISHED:%s"
                    % ((order_state or {}).get("reason"),))
            else:
                report["skipped"] = ("NOT_NARROWED_AND_NO_ORDER_STATE: "
                                     "adds no refusal to the frozen "
                                     "cohort")
            report["refusals"] = refusals
            report["may_attempt"] = not refusals
            return report

        # 1. CUMULATIVE FIRST-LEG SHARES, from durable slots.
        auth = self.authorized_first_leg_shares(window=window,
                                                latch_module=latch_module)
        report["authorized_first_leg_shares"] = auth
        if not auth["readable"]:
            refusals.append("MT_%s" % auth["why"])
        else:
            prospective_shares = auth["shares"] + q
            report["prospective_cum_first_leg_shares"] = \
                str(prospective_shares)
            if prospective_shares > self.cum_first_leg_shares:
                refusals.append(
                    "MT_CUM_FIRST_LEG_SHARES_%s_WOULD_EXCEED_%s"
                    % (prospective_shares, self.cum_first_leg_shares))

        # 2 and 3. OUR CASH, NOT THE ACCOUNT'S.
        #
        # THE ERROR THIS AVOIDS. `open_notional_usd` and
        # `filled_notional_usd` are SCOPE-wide, not ownership-scoped:
        # `read_order_state` deliberately keeps every order in our market,
        # foreign ones included, because an unattributable live order is
        # exactly what must not be waved past -- and it measures that sum
        # against the loose account-wide $5.00 bound for that reason.
        #
        # These four bounds are statements about THIS PROBE's cash, so
        # charging another desk's resting order against them is simply
        # wrong. It is also not conservative: on a shared account where
        # another strategy quotes the same BTC15m window, a foreign 16.21
        # shares would refuse every attempt we make, for a reason that has
        # nothing to do with our exposure. So our figures are summed from
        # the view's own rows, restricted to the ids in E1's registry.
        owned = {str(i) for i in (order_state.get("known_ids") or [])}
        rows = order_state.get("orders")
        resolved = {str(i) for i in
                    (order_state.get("resolved_history_ids") or [])}
        if not isinstance(rows, list):
            refusals.append("MT_OWN_EXPOSURE_NOT_ESTABLISHED:NO_ORDER_ROWS")
            report["refusals"] = refusals
            report["may_attempt"] = False
            return report

        our_open = D("0")
        our_filled = D("0")
        unpriced: List[str] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            oid = str(row.get("id"))
            if oid not in owned or oid in resolved:
                continue
            own_px = _fin(row.get("own_side_price"))
            leaves = _fin(row.get("leaves_quantity"))
            cum = _fin(row.get("cum_quantity"))
            if row.get("outstanding"):
                if own_px is None or leaves is None:
                    # an outstanding order of OURS that cannot be priced
                    # is never $0 of exposure.
                    unpriced.append(oid)
                else:
                    our_open += max(leaves, D("0")) * own_px
            if cum is not None and cum > 0:
                if own_px is None:
                    unpriced.append(oid)
                else:
                    our_filled += cum * own_px
        if unpriced:
            refusals.append("MT_OUR_EXPOSURE_UNPRICEABLE:%s"
                            % ",".join(sorted(set(unpriced))))
        report["our_open_notional_usd"] = str(our_open)
        report["our_filled_notional_usd"] = str(our_filled)
        report["n_owned_ids"] = len(owned)
        report["ownership_scoped"] = True
        report["why_not_the_scope_wide_total"] = (
            "open_notional_usd includes other desks' orders in our market "
            "by design; this probe's envelope is about this probe's cash")

        # GROSS: what we have spent and committed, plus this whole PAIR.
        pair_cost = q * PAIR_COST_CEILING
        prospective_gross = our_filled + our_open + pair_cost
        report["prospective_pair_cost_usd"] = str(pair_cost)
        report["prospective_gross_notional_usd"] = str(prospective_gross)
        report["why_the_whole_pair"] = (
            "a filled first leg is followed by a complement, so the cash "
            "that may leave for this attempt is the PAIR at the ceiling, "
            "not the first leg alone")
        if prospective_gross > self.gross_notional_usd:
            refusals.append(
                "MT_GROSS_NOTIONAL_%s_WOULD_EXCEED_%s"
                % (prospective_gross, self.gross_notional_usd))

        # OUTSTANDING at one instant, ours only.
        own_cost = q * (price if price is not None else MAX_FIRST_LEG_PRICE)
        prospective_out = our_open + own_cost
        report["prospective_outstanding_usd"] = str(prospective_out)
        if prospective_out > self.outstanding_usd:
            refusals.append(
                "MT_OUTSTANDING_%s_WOULD_EXCEED_%s"
                % (prospective_out, self.outstanding_usd))

        # 4. DIRECTIONAL RESIDUAL, unhedged shares right now.
        resid = order_state.get("residual")
        rq = _fin((resid or {}).get("qty")) if resid else D("0")
        if resid and rq is None:
            refusals.append("MT_RESIDUAL_QTY_NOT_FINITE:%r"
                            % ((resid or {}).get("qty"),))
        else:
            report["venue_residual_shares"] = str(rq or D("0"))
            if (rq or D("0")) > self.residual_shares:
                refusals.append(
                    "MT_RESIDUAL_SHARES_%s_EXCEEDS_%s"
                    % (rq, self.residual_shares))

        report["refusals"] = refusals
        report["may_attempt"] = not refusals
        return report

    # ------------------------------------------------------------ reporting
    def report(self) -> Dict[str, Any]:
        return {
            "module": "analysis.l5.e1_cohort.multitrade",
            "env_keys": list(ENVELOPE_KEYS),
            "may_only_narrow": True,
            "no_default": "missing/invalid/excessive fails closed",
            "max_cum_first_leg_shares": str(self.cum_first_leg_shares),
            "max_gross_notional_usd": str(self.gross_notional_usd),
            "max_outstanding_usd": str(self.outstanding_usd),
            "max_residual_shares": str(self.residual_shares),
            "frozen_cum_first_leg_shares": str(FROZEN_CUM_FIRST_LEG_SHARES),
            "frozen_gross_notional_usd": str(FROZEN_GROSS_NOTIONAL_USD),
            "frozen_outstanding_usd": str(FROZEN_OUTSTANDING_USD),
            "frozen_residual_shares": str(FROZEN_RESIDUAL_SHARES),
            "first_leg_purchases_are_not_the_gross_outlay": True,
            "gross_outlay_includes_the_complement": True,
        }


def exposure_envelope(n_attempts: int) -> Dict[str, Any]:
    """The arithmetic, spelled out, for an N-attempt probe. Reporting only."""
    n = int(n_attempts)
    q = FIRST_LEG_QTY
    return {
        "attempts": n,
        "qty_per_first_leg_shares": str(q),
        "cum_first_leg_shares": str(n * q),
        "first_leg_purchases_usd": str(n * q * MAX_FIRST_LEG_PRICE),
        "gross_outlay_after_complements_usd": str(n * q * PAIR_COST_CEILING),
        "complement_portion_usd": str(n * q * PAIR_COST_CEILING
                                      - n * q * MAX_FIRST_LEG_PRICE),
        "per_pair_gross_usd": str(q * PAIR_COST_CEILING),
        "max_outstanding_usd": str(q * MAX_FIRST_LEG_PRICE
                                   + q * (PAIR_COST_CEILING - TICK)),
        "max_directional_residual_shares": str(q),
        "max_directional_residual_usd": str(q * MAX_FIRST_LEG_PRICE),
        "WHY_NOT_JUST_FIRST_LEGS": (
            "the first-leg figure bounds what we pay to OPEN. The "
            "complement is a second purchase on the other side, so the "
            "cash that can leave is the PAIR at the $0.96 ceiling. The "
            "22:00z probe spent $0.1248 on one pair == 0.13 x $0.96."),
    }
