"""Partial-fill accounting. The centre of the Stage-6 experiment.

THE FAILURE THIS EXISTS TO PREVENT. The obvious way to write a two-leg
probe is: place leg one for Q, then place leg two for Q. That is wrong in a
way that only shows up with real fills, and it is wrong in the expensive
direction -- if the first leg fills 0.37 of 1.00 and the complement is sized
at the INTENDED 1.00, the second fill overshoots by 0.63 shares, which is
not a hedge but a new unmatched position on the other side. The protocol
therefore has one non-negotiable rule:

    THE COMPLEMENT IS SIZED FROM `filled_qty`, NEVER FROM `intended_qty`.

`Inventory.complement_qty()` reads only `filled_qty`, and
`intended_qty` is retained solely so a record can show the difference. The
offline suite asserts the rule directly, including the case where the two
differ.

EVERY PARTIAL FILL UPDATES STATE. `apply_fill` is called once per
execution, not once per order. It is idempotent per execution id, because a
venue poll can and does return the same execution twice and counting a fill
twice would corrupt both the VWAP and the residual bound. An execution
without an id is REFUSED rather than counted, because an uncountable fill is
worse than a missing one.

VWAP, NOT LAST PRICE. The pair-cost gate needs the average cost of what we
actually own, so `first_leg_vwap` is a quantity-weighted mean over the
executions seen. A partial fill at a better price does not entitle the
complement to a higher ceiling than the blend supports.

ROLE COMES FROM `execution.isAggressor` ONLY. Never from a bare `aggressor`
field -- a prior probe read that one and the venue sent False for a print it
had not classified, which is how a role got "resolved" without evidence.
Absent the key the role is UNRESOLVED and no maker claim is made. This
module records the role; it never lets an unresolved role become "maker".

NOTHING HERE IS REALIZED. `filled` is filled. Realized requires settlement
or cash confirmation and lives in `settlement.py`.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from analysis.l5.stage6 import bounds as B

ZERO = D("0")
QTY_Q = D("0.0001")
USD_Q = D("0.0001")


class InventoryBoundBreached(RuntimeError):
    """A bound was exceeded. Fail closed: the caller must stop and withdraw.

    Raised rather than returned, because a breached residual bound is not a
    condition to branch on -- it is a condition to stop on.
    """


class UncountableFill(RuntimeError):
    """An execution we cannot count exactly once."""


def _d(v: Any) -> D:
    return D(str(v))


def execution_role(execution: Dict[str, Any]) -> Dict[str, Any]:
    """Venue-confirmed liquidity role for ONE execution.

    `execution.isAggressor` is the only authoritative selector. A bare
    `aggressor` field, even if present, is ignored and reported as ignored.
    """
    has = "isAggressor" in execution
    return {
        "role": ("taker" if execution.get("isAggressor") else "maker")
        if has else None,
        "resolved": has,
        "source": "execution.isAggressor",
        "forbidden_field_present": "aggressor" in execution,
        "forbidden_field_not_used": "aggressor (a boolean)",
        "unresolved_reason": (None if has
                              else "no isAggressor on this execution"),
    }


class Leg:
    """One leg: what we asked for, and what the venue actually gave us."""

    def __init__(self, name: str, *, side: str, intended_qty: Any,
                 price: Any) -> None:
        self.name = name
        self.side = str(side).upper()
        self.intended_qty = _d(intended_qty)
        self.price = _d(price)
        self.filled_qty = ZERO
        self.cost_usd = ZERO
        self.executions: List[Dict[str, Any]] = []
        self._seen_ids: set = set()
        self.order_id: Optional[str] = None
        self.cancelled = False

    # ------------------------------------------------------------- fills
    def apply_fill(self, execution: Dict[str, Any]) -> Dict[str, Any]:
        """Count ONE execution, exactly once."""
        exec_id = execution.get("id") or execution.get("executionId")
        if not exec_id:
            raise UncountableFill(
                "%s: an execution with no id cannot be counted exactly "
                "once; refusing to count it at all" % self.name)
        if exec_id in self._seen_ids:
            return {"counted": False, "reason": "DUPLICATE_EXECUTION_ID",
                    "execution_id": str(exec_id),
                    "filled_qty": str(self.filled_qty)}
        q = _d(execution.get("quantity", execution.get("qty", 0)))
        if q <= 0:
            raise UncountableFill("%s: execution %s has quantity %r"
                                  % (self.name, exec_id, q))
        px = execution.get("price")
        if px is None:
            raise UncountableFill(
                "%s: execution %s carries no price; a fill without a price "
                "cannot enter a VWAP" % (self.name, exec_id))
        px = _d(px)
        self._seen_ids.add(exec_id)
        self.filled_qty = (self.filled_qty + q).quantize(QTY_Q)
        self.cost_usd = (self.cost_usd + q * px).quantize(USD_Q)
        role = execution_role(execution)
        self.executions.append({
            "execution_id": str(exec_id), "quantity": str(q),
            "price": str(px), "role": role["role"],
            "role_resolved": role["resolved"],
            "commissionNotionalCollected":
                execution.get("commissionNotionalCollected"),
        })
        return {"counted": True, "execution_id": str(exec_id),
                "quantity": str(q), "price": str(px),
                "filled_qty": str(self.filled_qty),
                "vwap": str(self.vwap()), "role": role}

    # ------------------------------------------------------------- views
    def vwap(self) -> Optional[D]:
        if self.filled_qty <= 0:
            return None
        return (self.cost_usd / self.filled_qty).quantize(USD_Q)

    def unfilled_qty(self) -> D:
        return max(ZERO, (self.intended_qty - self.filled_qty))

    def state(self) -> str:
        if self.filled_qty <= 0:
            return "CANCELLED_UNFILLED" if self.cancelled else "UNFILLED"
        if self.filled_qty >= self.intended_qty:
            return "FILLED"
        return "PARTIALLY_FILLED"

    def record(self) -> Dict[str, Any]:
        return {
            "leg": self.name, "side": self.side,
            "intended_qty": str(self.intended_qty),
            "filled_qty": str(self.filled_qty),
            "unfilled_qty": str(self.unfilled_qty()),
            "quoted_price": str(self.price),
            "vwap": (str(self.vwap()) if self.vwap() is not None else None),
            "cost_usd": str(self.cost_usd),
            "state": self.state(),
            "order_id": self.order_id,
            "cancelled": self.cancelled,
            "n_executions": len(self.executions),
            "executions": list(self.executions),
            "LANGUAGE": "FILLED is not REALIZED",
        }


#: the two sides of a matched pair, in the venue's own vocabulary
COMPLEMENT_OF = {"UP": "DOWN", "DOWN": "UP",
                 "YES": "NO", "NO": "YES",
                 "OUTCOME_SIDE_YES": "OUTCOME_SIDE_NO",
                 "OUTCOME_SIDE_NO": "OUTCOME_SIDE_YES"}


def complement_side(side: str) -> str:
    try:
        return COMPLEMENT_OF[str(side).upper()]
    except KeyError:
        raise ValueError("no complement side for %r" % (side,)) from None


class Inventory:
    """Both legs, the residual, and the bounds -- checked on every update."""

    def __init__(self, *, slug: str, first_side: str,
                 first_price: Any,
                 first_qty: Any = B.FIRST_LEG_QTY) -> None:
        B.assert_bounds_coherent()
        if _d(first_qty) != B.FIRST_LEG_QTY:
            raise InventoryBoundBreached(
                "first leg qty %s is not the frozen %s"
                % (first_qty, B.FIRST_LEG_QTY))
        if _d(first_price) > B.MAX_FIRST_LEG_PRICE:
            raise InventoryBoundBreached(
                "first leg price %s is above the frozen ceiling %s"
                % (first_price, B.MAX_FIRST_LEG_PRICE))
        self.slug = slug
        self.first = Leg("FIRST", side=first_side, intended_qty=first_qty,
                         price=first_price)
        self.complement: Optional[Leg] = None

    # ------------------------------------------------------- complement
    def complement_qty(self) -> D:
        """Shares to quote on the complement.

        READS `filled_qty` AND NOTHING ELSE. This is the rule the whole
        protocol turns on; `intended_qty` is deliberately not in scope
        here. Snapped DOWN to the venue's 0.01 quantity grid, because
        rounding UP would size the complement above the fill and create
        exactly the overshoot this module exists to prevent.
        """
        filled = self.first.filled_qty
        ticks = (filled / B.VENUE_MIN_QTY).to_integral_value(
            rounding="ROUND_FLOOR")
        return (ticks * B.VENUE_MIN_QTY).quantize(QTY_Q)

    def open_complement(self, *, price: Any) -> Leg:
        if self.complement is not None:
            raise InventoryBoundBreached(
                "a complement leg already exists; %s complement "
                "submission(s) are authorized"
                % B.MAX_COMPLEMENT_SUBMISSIONS)
        q = self.complement_qty()
        if q <= 0:
            raise InventoryBoundBreached(
                "the first leg has no counted fill, so there is nothing to "
                "complement. The complement is NEVER sized from the "
                "intended quantity.")
        if q > self.first.filled_qty:
            raise InventoryBoundBreached(
                "complement qty %s exceeds the first leg's actual fill %s"
                % (q, self.first.filled_qty))
        self.complement = Leg("COMPLEMENT",
                              side=complement_side(self.first.side),
                              intended_qty=q, price=price)
        return self.complement

    # ---------------------------------------------------------- residual
    def matched_qty(self) -> D:
        if self.complement is None:
            return ZERO
        return min(self.first.filled_qty, self.complement.filled_qty)

    def residual_qty(self) -> D:
        """Unmatched directional shares. Always >= 0 in this protocol,
        because the complement can never be sized above the first fill."""
        c = ZERO if self.complement is None else self.complement.filled_qty
        return (self.first.filled_qty - c).quantize(QTY_Q)

    def residual_side(self) -> Optional[str]:
        return self.first.side if self.residual_qty() > 0 else None

    def residual_notional_usd(self) -> D:
        """What the residual cost us -- the maximum it can lose, since a
        binary contract's floor is zero and both legs are BUYs."""
        r = self.residual_qty()
        if r <= 0:
            return ZERO
        vw = self.first.vwap() or ZERO
        return (r * vw).quantize(USD_Q)

    def imbalance_shares(self) -> D:
        c = ZERO if self.complement is None else self.complement.filled_qty
        return abs(self.first.filled_qty - c).quantize(QTY_Q)

    # ------------------------------------------------------------ bounds
    def bound_check(self) -> Dict[str, Any]:
        breaches = []
        if self.first.filled_qty > self.first.intended_qty:
            breaches.append("FIRST_LEG_OVERFILL:%s>%s"
                            % (self.first.filled_qty,
                               self.first.intended_qty))
        if self.residual_qty() > B.MAX_RESIDUAL_SHARES:
            breaches.append("RESIDUAL_SHARES:%s>%s"
                            % (self.residual_qty(), B.MAX_RESIDUAL_SHARES))
        if self.residual_notional_usd() > B.MAX_RESIDUAL_NOTIONAL_USD:
            breaches.append("RESIDUAL_NOTIONAL_USD:%s>%s"
                            % (self.residual_notional_usd(),
                               B.MAX_RESIDUAL_NOTIONAL_USD))
        if self.imbalance_shares() > B.MAX_INVENTORY_IMBALANCE_SHARES:
            breaches.append("IMBALANCE_SHARES:%s>%s"
                            % (self.imbalance_shares(),
                               B.MAX_INVENTORY_IMBALANCE_SHARES))
        if self.max_loss_usd() > B.MAX_POSSIBLE_LOSS_USD:
            breaches.append("MAX_LOSS_USD:%s>%s"
                            % (self.max_loss_usd(),
                               B.MAX_POSSIBLE_LOSS_USD))
        if self.complement is not None and \
                self.complement.filled_qty > self.complement.intended_qty:
            breaches.append("COMPLEMENT_OVERFILL:%s>%s"
                            % (self.complement.filled_qty,
                               self.complement.intended_qty))
        return {"ok": not breaches, "breaches": breaches,
                "bounds": B.summary()}

    def assert_bounds(self) -> None:
        chk = self.bound_check()
        if not chk["ok"]:
            raise InventoryBoundBreached(
                "STAGE-6-S6P bound breached: " + "; ".join(chk["breaches"]))

    def max_loss_usd(self) -> D:
        """Worst case from here: the residual settles to zero and the
        matched pair pays $1.00 per contract against its cost."""
        matched = self.matched_qty()
        paid = self.first.cost_usd + (ZERO if self.complement is None
                                      else self.complement.cost_usd)
        pair_payout = matched * D("1.00")
        return max(ZERO, (paid - pair_payout).quantize(USD_Q))

    # ------------------------------------------------------------ record
    def apply_fill(self, leg_name: str,
                   execution: Dict[str, Any]) -> Dict[str, Any]:
        """Count an execution on a named leg, then re-check every bound.

        The bound check happens AFTER every single partial fill, not at the
        end of a poll batch, so a bound cannot be crossed and uncrossed
        inside one loop without being seen.
        """
        # accept the leg's own name and the latch slot's name for it, so
        # the machine and the latch cannot disagree about which leg is
        # which and silently drop a fill on the floor.
        name = str(leg_name).upper()
        if name in ("FIRST", "FIRST_LEG"):
            leg = self.first
        elif name == "COMPLEMENT":
            leg = self.complement
        else:
            raise InventoryBoundBreached("unknown leg name %r" % (leg_name,))
        if leg is None:
            raise InventoryBoundBreached(
                "no %s leg exists to apply a fill to" % leg_name)
        out = leg.apply_fill(execution)
        self.assert_bounds()
        out["inventory"] = self.snapshot()
        return out

    def snapshot(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "first": self.first.record(),
            "complement": (self.complement.record()
                           if self.complement is not None else None),
            "complement_qty_from_actual_fill": str(self.complement_qty()),
            "complement_sized_from": "first.filled_qty",
            "complement_never_sized_from": "first.intended_qty",
            "matched_qty": str(self.matched_qty()),
            "residual_qty": str(self.residual_qty()),
            "residual_side": self.residual_side(),
            "residual_notional_usd": str(self.residual_notional_usd()),
            "imbalance_shares": str(self.imbalance_shares()),
            "max_loss_from_here_usd": str(self.max_loss_usd()),
            "residual_predictive_skill_assumed": False,
            "residual_note": ("the residual is carried because flattening "
                              "it would be forced-timer behaviour, NOT "
                              "because it is believed to have edge. The "
                              "repository's evidence does not support "
                              "residual directional skill."),
            "LANGUAGE": "FILLED is not REALIZED",
        }
